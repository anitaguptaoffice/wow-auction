import heapq
import json
from pathlib import Path
import random
import struct
import sys
import tempfile
import subprocess
import time
import unittest
import zlib

from lupa.lua51 import LuaRuntime
from transport import BLOCK, HEADER, Receiver, parse
from receive import read_frame, find_live
from reader import Reader

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from tests.test_market_pipeline import _lua_snapshot
from app.services.auction_importer import decode_snapshot, validate_snapshot


class Sender:
    def __init__(self, sink=None):
        self.lua = LuaRuntime(encoding=None)
        self.frames, self.timers = [], []
        self.clock, self.seq = 1., 0
        self.sink = sink
        g = self.lua.globals()
        g[b"schedule"] = self.schedule
        g[b"clock"] = lambda: self.clock
        g[b"profile"] = lambda: time.perf_counter() * 1000
        self.lua.execute(b"""
            C_Timer={After=function(delay,fn) schedule(delay,fn) end}
            GetTime=function() return clock() end
            time=function() return 1793208000 end
            date=function() return "2026-10-29" end
            debugprofilestop=function() return profile() end
            AuctionSearchOverlay={SetPhase=function(...) end, SetExportProgress=function(...) LastProgress={...} end}
        """)
        self.lua.execute((ROOT / "game/addons/AuctionSearchExample/MemoryExport.lua").read_bytes())
        self.load = self.lua.eval(b"function(source) local f=assert(loadstring(source)); local env={}; setfenv(f,env); f(); return env.AuctionSearchDB end")

    def schedule(self, delay, callback):
        self.seq += 1
        heapq.heappush(self.timers, (self.clock + max(delay, .001), self.seq, callback))

    def capture(self):
        frame = self.lua.globals()[b"AuctionSearchMemoryBridge"][2]
        if self.sink:
            self.sink(frame)
        elif not self.frames or frame != self.frames[-1]:
            self.frames.append(frame)

    def start(self, source=None):
        self.database = self.load(source or _lua_snapshot().encode())
        self.lua.globals()[b"AuctionSearchExport"][b"RestoreLatest"](self.database)
        self.lua.execute(b"AuctionSearchExport.Start()")
        self.capture()

    def drain(self):
        while self.lua.eval(b"AuctionSearchExport.IsBusy()"):
            self.clock, _, callback = heapq.heappop(self.timers)
            callback()
            self.capture()
        self.capture()


def frame(kind, index, count, payload, session="1-2-3-4", seq=1):
    head = f"WAM2|{session}|{seq}|{kind}|{index}|{count}|{len(payload)}|{zlib.adler32(payload)}\n".encode()
    return head.ljust(HEADER, b" ") + payload


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.receiver = Receiver(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def prepared(self):
        sender = Sender()
        sender.start()
        sender.drain()
        return sender

    def test_actual_lua_sender_and_importer(self):
        sender = self.prepared()
        result = None
        for packet in sender.frames:
            result = self.receiver.accept(packet) or result
        self.assertTrue(result["transportComplete"])
        scans = validate_snapshot(decode_snapshot(Path(result["file"])))
        self.assertEqual(scans[0].record_count, 6)
        self.assertEqual(scans[0].realm_name, "测试服务器")
        original = sender.database
        received = sender.load(Path(result["file"]).read_bytes())
        compare = sender.lua.eval(b"""function(a,b)
            local x,y
            for _,d in pairs(a.auctions) do for _,s in pairs(d.scans) do x=s end end
            for _,d in pairs(b.auctions) do for _,s in pairs(d.scans) do y=s end end
            local function eq(a,b)
                assert(type(a)==type(b))
                if type(a)~="table" then assert(a==b); return end
                for k,v in pairs(a) do eq(v,b[k]) end
                for k in pairs(b) do assert(a[k]~=nil) end
            end
            eq(x,y)
        end""")
        compare(original, received)

    def test_loss_reorder_duplicate_restart(self):
        raw = b"x" * BLOCK + b"tail"
        manifest = frame("M", 0, 2, f"lua-raw-blocks:2:{len(raw)}:{zlib.adler32(raw)}:123:1".encode())
        first = frame("D", 1, 2, raw[:BLOCK])
        last = frame("D", 2, 2, raw[BLOCK:])
        self.receiver.accept(last)
        result = self.receiver.accept(manifest)
        self.assertEqual(result["missing"], [1])
        self.assertFalse((self.root / "1-2-3-4/snapshot.lua").exists())
        self.receiver = Receiver(self.root)
        self.receiver.accept(last)
        result = self.receiver.accept(first)
        self.assertEqual(Path(result["file"]).read_bytes(), raw)

    def test_corrupt_block_and_whole_checksum(self):
        packet = frame("D", 1, 1, b"abcdef")
        with self.assertRaises(ValueError):
            self.receiver.accept(packet[:-1] + b"g")
        self.receiver.accept(packet)
        with self.assertRaises(ValueError):
            self.receiver.accept(frame("M", 0, 1, b"lua-raw-blocks:1:6:1:123:1"))
        self.assertFalse((self.root / "1-2-3-4/snapshot.lua").exists())

    def test_disk_corruption_repaired_on_restart(self):
        self.receiver.accept(frame("D", 1, 1, b"abc"))
        (self.root / "1-2-3-4/1.packet").write_bytes(b"broken")
        receiver = Receiver(self.root)
        status = receiver.accept(frame("M", 0, 1, f"lua-raw-blocks:1:3:{zlib.adler32(b'abc')}:123:1".encode()))
        self.assertEqual(status["missing"], [1])
        self.assertTrue(receiver.accept(frame("D", 1, 1, b"abc"))["transportComplete"])

    def test_sessions_do_not_mix(self):
        self.receiver.accept(frame("D", 1, 1, b"abc"))
        result = self.receiver.accept(frame("M", 0, 1, f"lua-raw-blocks:1:3:{zlib.adler32(b'abc')}:123:1".encode(), session="1-2-3-5"))
        self.assertEqual(result["missing"], [1])

    def test_duplicate_conflict_and_bounds(self):
        self.receiver.accept(frame("D", 1, 1, b"abc"))
        with self.assertRaises(ValueError):
            self.receiver.accept(frame("D", 1, 1, b"def"))
        with self.assertRaises(ValueError):
            parse(frame("D", 9000, 9000, b"x"))
        with self.assertRaises(ValueError):
            parse(frame("D", 1, 1, b"x" * (BLOCK + 1)))

    def test_pointer_changed_during_read(self):
        packet = frame("D", 1, 1, b"abc")
        class Fake:
            calls = 0
            def read(self, address, size):
                if address == 100:
                    self.calls += 1
                    return struct.pack("<Q", 0x20000 if self.calls == 1 else 0x30000)
                return packet[:size]
        self.assertIsNone(read_frame(Fake(), 100, 32))

    def test_cancel_and_resend(self):
        sender = self.prepared()
        sender.frames = []
        sender.lua.execute(b'AuctionSearchExport.Resend("all")')
        sender.capture()
        sender.lua.execute(b"AuctionSearchExport.Cancel()")
        self.assertFalse(sender.lua.eval(b"AuctionSearchExport.IsBusy()"))
        sender.lua.execute(b'AuctionSearchExport.Resend("1")')
        sender.drain()
        result = None
        for packet in sender.frames:
            result = self.receiver.accept(packet) or result
        self.assertTrue(result["transportComplete"])

    def test_windows_real_read_and_relocation(self):
        process = subprocess.Popen([sys.executable, str(Path(__file__).with_name("windows_fixture.py"))],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        reader = None
        try:
            identity = json.loads(process.stdout.readline())
            reader = Reader(identity["pid"], Path(identity["image"]))
            binding = find_live(reader, seconds=15)
            self.assertIsNotNone(binding)
            slot, skip, packet = binding
            self.assertEqual(parse(packet)[0]["session"], "1-2-3-4")
            process.stdin.write("move\n")
            process.stdin.flush()
            time.sleep(.3)
            updated = find_live(reader, seconds=15)
            self.assertIsNotNone(updated)
            self.assertNotEqual(updated[0], slot)
            self.assertEqual(parse(updated[2])[0]["session"], "1-2-3-5")
        finally:
            if reader:
                reader.close()
            process.terminate()
            process.wait(timeout=5)
            process.stdin.close()
            process.stdout.close()


if __name__ == "__main__":
    unittest.main()
