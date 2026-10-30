"""Read-only WoW companion; reattaches after reload, character changes and restarts."""
import argparse
import ctypes as C
from ctypes import wintypes as W
import json
from pathlib import Path
import struct
import time

from reader import Reader
from transport import HEADER, Receiver, atomic, header, parse

MAGIC = struct.pack("<d", 8675309123987.0)


def read_frame(reader, slot, skip):
    before = reader.read(slot, 8)
    if before is None:
        return None
    pointer = struct.unpack("<Q", before)[0]
    if not 0x10000 <= pointer <= 0x7FFFFFFFFFFF:
        return None
    head = reader.read(pointer + skip, HEADER)
    if head is None:
        return None
    try:
        info = header(head)
        packet = reader.read(pointer + skip, HEADER + info["length"])
        if packet is None or reader.read(slot, 8) != before or packet[:HEADER] != head:
            return None
        parse(packet)
        return packet
    except ValueError:
        return None


def candidates(reader, anchor):
    # Same array allocation as the anchor. Do not depend on a hard-coded TValue stride.
    for slot in range(anchor - 128, anchor + 264, 8):
        value = reader.read(slot, 8)
        if value is None:
            continue
        pointer = struct.unpack("<Q", value)[0]
        if not 0x10000 <= pointer <= 0x7FFFFFFFFFFF:
            continue
        head = reader.read(pointer, 64)
        if head is None:
            continue
        for skip in (32, 24, 16, 40, 48, 0):
            if head[skip:skip + 5] == b"WAM2|":
                packet = read_frame(reader, slot, skip)
                if packet:
                    yield slot, skip, packet


def find_live(reader, seconds=30):
    started, scanned, checked = time.monotonic(), 0, set()
    for base, size in reader.regions():
        tail = b""
        for offset in range(0, size, 1024 * 1024):
            if time.monotonic() - started > seconds or scanned >= 16 * 2**30:
                return None
            length = min(1024 * 1024, size - offset)
            data = reader.read(base + offset, length)
            scanned += length
            if data is None:
                tail = b""
                continue
            joined = tail + data
            position = joined.find(MAGIC)
            while position >= 0:
                anchor = base + offset - len(tail) + position
                if anchor % 8 == 0:
                    for slot, skip, packet in candidates(reader, anchor):
                        if (slot, skip) in checked:
                            continue
                        checked.add((slot, skip))
                        initial = header(packet[:HEADER])
                        deadline = time.monotonic() + 1.6
                        while time.monotonic() < deadline:
                            time.sleep(.025)
                            updated = read_frame(reader, slot, skip)
                            if not updated:
                                break
                            info = header(updated[:HEADER])
                            if info["session"] == initial["session"] and info["seq"] > initial["seq"]:
                                return slot, skip, updated
                position = joined.find(MAGIC, position + 1)
            tail = data[-7:]
            time.sleep(.001)  # Initial discovery is deliberately bounded and paced.
    return None


class ProcessEntry(C.Structure):
    _fields_ = [("dwSize", W.DWORD), ("cntUsage", W.DWORD), ("th32ProcessID", W.DWORD),
                ("th32DefaultHeapID", C.c_size_t), ("th32ModuleID", W.DWORD),
                ("cntThreads", W.DWORD), ("th32ParentProcessID", W.DWORD),
                ("pcPriClassBase", W.LONG), ("dwFlags", W.DWORD), ("szExeFile", W.WCHAR * 260)]


def process_ids():
    api = C.WinDLL("kernel32", use_last_error=True)
    api.CreateToolhelp32Snapshot.argtypes = [W.DWORD, W.DWORD]
    api.CreateToolhelp32Snapshot.restype = W.HANDLE
    api.Process32FirstW.argtypes = [W.HANDLE, C.POINTER(ProcessEntry)]
    api.Process32NextW.argtypes = [W.HANDLE, C.POINTER(ProcessEntry)]
    api.CloseHandle.argtypes = [W.HANDLE]
    snapshot = api.CreateToolhelp32Snapshot(2, 0)
    if snapshot == C.c_void_p(-1).value:
        return []
    result, entry = [], ProcessEntry()
    entry.dwSize = C.sizeof(entry)
    try:
        valid = api.Process32FirstW(snapshot, C.byref(entry))
        while valid:
            if entry.szExeFile.lower() == "wow.exe":
                result.append(entry.th32ProcessID)
            valid = api.Process32NextW(snapshot, C.byref(entry))
    finally:
        api.CloseHandle(snapshot)
    return result


def alive(reader):
    code = W.DWORD()
    reader.api.GetExitCodeProcess.argtypes = [W.HANDLE, C.POINTER(W.DWORD)]
    return reader.api.GetExitCodeProcess(reader.handle, C.byref(code)) and code.value == 259


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[2] / "data/memory-export")
    args = parser.parse_args()
    receiver = Receiver(args.output)
    reader, binding, pid = None, None, None
    last_seq, last_change, last_print = None, 0, 0
    last_message = None

    def report(message, **details):
        nonlocal last_message
        status = dict(message=message, updatedAt=time.time(), pid=pid, **details)
        atomic(args.output / "connection.json", json.dumps(status, ensure_ascii=False).encode())
        if message != last_message:
            print(message, flush=True)
            last_message = message

    # The Windows lock prevents two companions racing on the same output files.
    import msvcrt
    with (args.output / "receiver.lock").open("a+b") as lock:
        lock.seek(0)
        if not lock.read(1):
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise SystemExit("已有接收器运行，请勿重复启动")
        from dashboard import start
        dashboard = None
        try:
            dashboard = start(args.output)
            print("接收进度：http://127.0.0.1:8766", flush=True)
        except OSError as error:
            print("进度网页未启动，接收仍继续：" + str(error), flush=True)
        try:
            while True:
                if reader and not alive(reader):
                    reader.close()
                    reader, binding, pid = None, None, None
                if not reader:
                    for candidate in process_ids():
                        try:
                            reader = Reader(candidate, args.exe)
                            pid = candidate
                            break
                        except (OSError, ValueError):
                            continue
                    if not reader:
                        report("等待游戏启动；无需管理员权限")
                        time.sleep(2)
                        continue
                if not binding:
                    report("正在定位插件；请登录角色并启用 AuctionSearch 2.8.0")
                    binding = find_live(reader)
                    if not binding:
                        report("未找到活动插件，5 秒后重试；请确认已重启或 /reload")
                        time.sleep(5)
                        continue
                    last_seq, last_change = None, time.monotonic()
                    report("已连接活动插件，等待点击“导出到电脑”")
                slot, skip, _ = binding
                packet = read_frame(reader, slot, skip)
                now = time.monotonic()
                if packet:
                    info = header(packet[:HEADER])
                    identity = (info["session"], info["seq"])
                    if identity != last_seq:
                        last_seq, last_change = identity, now
                        try:
                            status = receiver.accept(packet)
                            if status and (status["transportComplete"] or now - last_print > .5):
                                last_print = now
                                if status["transportComplete"]:
                                    report("校验通过、已保存：" + status["file"], **status)
                                else:
                                    report(f"已校验接收 {status['received']} / {status['total']} 块", **status)
                        except (ValueError, OSError, UnicodeError) as error:
                            report("接收错误，未确认完成：" + str(error))
                if now - last_change > 5:
                    binding = None
                    report("插件地址失效或心跳停止，正在重新定位；已接收的块保留")
                time.sleep(.005)
        finally:
            if reader:
                reader.close()
            if dashboard:
                dashboard.shutdown()


if __name__ == "__main__":
    main()
