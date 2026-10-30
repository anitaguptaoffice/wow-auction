"""WAM2 framing and durable snapshots. Never executes Lua or publishes incomplete data."""
import hashlib
import json
import os
from pathlib import Path
import re
import time
import zlib

HEADER = 256
BLOCK = 262144
MAX_BLOCKS = 8192
PATTERN = re.compile(rb"WAM2\|([0-9]+(?:-[0-9]+){3})\|(\d+)\|([HMD])\|(\d+)\|(\d+)\|(\d+)\|(\d+)\n")


def header(data):
    if len(data) != HEADER:
        raise ValueError("Short header")
    match = PATTERN.match(data)
    if not match or data[match.end():] != b" " * (HEADER - match.end()):
        raise ValueError("Invalid frame header")
    session, seq, kind, index, count, length, checksum = match.groups()
    seq, index, count, length, checksum = map(int, (seq, index, count, length, checksum))
    if not (0 < seq < 2**53 and 0 <= index <= count <= MAX_BLOCKS and
            0 <= length <= BLOCK and 0 <= checksum <= 0xffffffff):
        raise ValueError("Frame exceeds bounds")
    if kind == b"H" and (index or count or length or checksum != 1):
        raise ValueError("Invalid heartbeat")
    if kind == b"M" and (index or not count or length > 200):
        raise ValueError("Invalid manifest frame")
    if kind == b"D" and (not index or not length):
        raise ValueError("Invalid data frame")
    return dict(session=session.decode(), seq=seq, kind=kind.decode(), index=index,
                count=count, length=length, checksum=checksum)


def parse(data):
    info = header(data[:HEADER])
    payload = data[HEADER:]
    if len(payload) != info["length"] or zlib.adler32(payload) != info["checksum"]:
        raise ValueError("Frame length/checksum mismatch")
    return info, payload


def replace(source, target):
    for attempt in range(10):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(.02 * (attempt + 1))


def atomic(path, data):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    replace(temporary, path)


class Receiver:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.sessions = {}

    def state(self, session):
        if session not in self.sessions:
            folder = self.root / session
            folder.mkdir(exist_ok=True)
            state = dict(folder=folder, blocks={}, manifest=None, complete=False, started=time.monotonic())
            manifest = folder / "manifest.packet"
            if manifest.exists():
                try:
                    info, payload = parse(manifest.read_bytes())
                    if info["session"] != session or info["kind"] != "M":
                        raise ValueError("Wrong stored manifest")
                    state["manifest"] = self.manifest(info, payload)
                except ValueError:
                    replace(manifest, folder / "manifest.bad")
            for path in folder.glob("*.packet"):
                if path == manifest:
                    continue
                try:
                    info, payload = parse(path.read_bytes())
                    if info["session"] != session or info["kind"] != "D" or path.stem != str(info["index"]):
                        raise ValueError("Wrong stored block")
                    state["blocks"][info["index"]] = (info["count"], len(payload), info["checksum"])
                except ValueError:
                    replace(path, path.with_suffix(".bad"))
            self.sessions[session] = state
        return self.sessions[session]

    @staticmethod
    def manifest(info, payload):
        fields = payload.decode("ascii").split(":")
        if len(fields) != 6 or fields[0] != "lua-raw-blocks":
            raise ValueError("Unsupported manifest")
        count, size, checksum, timestamp, records = map(int, fields[1:])
        if not (count == info["count"] and 1 <= count <= MAX_BLOCKS and
                (count - 1) * BLOCK < size <= count * BLOCK and
                0 <= checksum <= 0xffffffff and timestamp > 0 and 0 <= records <= 10000000):
            raise ValueError("Invalid manifest values")
        return dict(blocks=count, bytes=size, checksum=checksum, timestamp=timestamp, records=records)

    def accept(self, packet):
        info, payload = parse(packet)
        if info["kind"] == "H":
            return None
        state = self.state(info["session"])
        if state["complete"]:
            return None
        folder = state["folder"]
        if info["kind"] == "M":
            manifest = self.manifest(info, payload)
            if state["manifest"] and manifest != state["manifest"]:
                raise ValueError("Manifest changed within one session")
            if not state["manifest"]:
                atomic(folder / "manifest.packet", packet)
            state["manifest"] = manifest
        else:
            index = info["index"]
            signature = (info["count"], len(payload), info["checksum"])
            if index in state["blocks"]:
                if signature != state["blocks"][index]:
                    raise ValueError("Conflicting duplicate block")
                # Comparing complete payload also rejects an Adler collision.
                if parse((folder / f"{index}.packet").read_bytes())[1] != payload:
                    raise ValueError("Conflicting duplicate contents")
            else:
                atomic(folder / f"{index}.packet", packet)
                state["blocks"][index] = signature
        manifest = state["manifest"]
        count = manifest["blocks"] if manifest else info["count"]
        if any(signature[0] != count or index > count for index, signature in state["blocks"].items()):
            raise ValueError("Block set disagrees with manifest")
        missing = [index for index in range(1, count + 1) if index not in state["blocks"]]
        status = dict(session=info["session"], received=len(state["blocks"]), total=count, missing=missing,
                      transportComplete=False, semanticValidation="pending",
                      bytes=sum(entry[1] for entry in state["blocks"].values()))
        if manifest and not missing:
            checksum, size, sha = 1, 0, hashlib.sha256()
            temporary = folder / "snapshot.lua.tmp"
            with temporary.open("wb") as output:
                for index in range(1, count + 1):
                    block_info, block = parse((folder / f"{index}.packet").read_bytes())
                    if block_info["session"] != info["session"] or block_info["index"] != index or block_info["count"] != count:
                        raise ValueError("Stored block identity changed")
                    expected = BLOCK if index < count else manifest["bytes"] - BLOCK * (count - 1)
                    if len(block) != expected:
                        raise ValueError("Wrong block length")
                    checksum = zlib.adler32(block, checksum)
                    sha.update(block)
                    size += len(block)
                    output.write(block)
                output.flush()
                os.fsync(output.fileno())
            if size != manifest["bytes"] or checksum != manifest["checksum"]:
                raise ValueError("Whole snapshot checksum mismatch; final file NOT published")
            replace(temporary, folder / "snapshot.lua")
            status.update(transportComplete=True, file=str((folder / "snapshot.lua").resolve()),
                          sha256=sha.hexdigest(), records=manifest["records"],
                          seconds=time.monotonic() - state["started"])
            state["complete"] = True
        atomic(folder / "status.json", json.dumps(status, ensure_ascii=False, indent=2).encode())
        atomic(self.root / "latest-status.json", json.dumps(status, ensure_ascii=False, indent=2).encode())
        return status
