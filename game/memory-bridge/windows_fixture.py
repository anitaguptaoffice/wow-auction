"""Owned child-process fixture for read-only integration tests; never touches WoW."""
import ctypes as C
import json
import os
import struct
import sys
import threading
import time
from transport import HEADER

if __name__ == "__main__":
    api = C.WinDLL("kernel32")
    name = C.create_unicode_buffer(32768)
    api.GetModuleFileNameW(None, name, len(name))
    retained = []
    state = {"generation": 4, "move": False}
    def commands():
        for line in sys.stdin:
            if line.strip() == "move":
                state["move"] = True
    threading.Thread(target=commands, daemon=True).start()
    anchor = struct.unpack("<Q", struct.pack("<d", 8675309123987.0))[0]
    array = (C.c_uint64 * 6)()
    array[0] = anchor
    retained.append(array)
    print(json.dumps(dict(pid=os.getpid(), image=name.value)), flush=True)
    seq = 0
    while True:
        if state["move"]:
            state["generation"] += 1
            array = (C.c_uint64 * 6)()
            array[0] = anchor
            retained.append(array)
            state["move"] = False
        seq += 1
        head = f"WAM2|1-2-3-{state['generation']}|{seq}|H|0|0|0|1\n".encode().ljust(HEADER, b" ")
        text = C.create_string_buffer(b"_" * 32 + head)
        retained.append(text)
        array[3] = C.addressof(text)
        time.sleep(.1)
