"""Read-only comparison of known addon export fragments in an explicitly selected WoW process.

Uses query/read permissions only. No writes, injection, debugger attach, protection
changes, privilege adjustment or process suspension. Never saves unrelated memory.
This probe proves byte readability, not live Lua-object ownership/lifetime.
"""
from __future__ import annotations
import argparse
import ctypes as C
from ctypes import wintypes as W
import hashlib
import json
from pathlib import Path
import time


class MBI(C.Structure):
    _fields_ = [("BaseAddress", C.c_void_p), ("AllocationBase", C.c_void_p),
                ("AllocationProtect", W.DWORD), ("PartitionId", W.WORD),
                ("RegionSize", C.c_size_t), ("State", W.DWORD),
                ("Protect", W.DWORD), ("Type", W.DWORD)]


class Reader:
    def __init__(self, pid: int, expected_exe: Path):
        self.api = C.WinDLL("kernel32", use_last_error=True)
        self.api.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
        self.api.OpenProcess.restype = W.HANDLE
        self.api.CloseHandle.argtypes = [W.HANDLE]
        self.api.ReadProcessMemory.argtypes = [W.HANDLE, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)]
        self.api.ReadProcessMemory.restype = W.BOOL
        self.api.VirtualQueryEx.argtypes = [W.HANDLE, C.c_void_p, C.POINTER(MBI), C.c_size_t]
        self.api.VirtualQueryEx.restype = C.c_size_t
        self.api.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)]
        self.api.QueryFullProcessImageNameW.restype = W.BOOL
        self.handle = self.api.OpenProcess(0x0400 | 0x0010, False, pid)
        if not self.handle:
            raise C.WinError(C.get_last_error())
        path, length = C.create_unicode_buffer(32768), W.DWORD(32768)
        if not self.api.QueryFullProcessImageNameW(self.handle, 0, path, C.byref(length)):
            self.close()
            raise C.WinError(C.get_last_error())
        self.image = path.value
        if Path(self.image).resolve() != expected_exe.resolve():
            self.close()
            raise ValueError("Selected PID does not match expected game executable")

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None

    def regions(self):
        address = 0
        regions = []
        while address < 0x7FFFFFFFFFFF:
            info = MBI()
            if not self.api.VirtualQueryEx(self.handle, address, C.byref(info), C.sizeof(info)):
                break
            end = (info.BaseAddress or 0) + info.RegionSize
            if end <= address:
                break
            # Private committed, non-executable readable pages only. Guarded and
            # inaccessible pages are excluded, never made readable by this tool.
            if info.State == 0x1000 and info.Type == 0x20000 and info.Protect in (0x02, 0x04, 0x08):
                regions.append((info.BaseAddress, info.RegionSize))
            address = end
        return sorted(regions, key=lambda entry: entry[1])

    def read(self, address, size):
        buffer = C.create_string_buffer(size)
        count = C.c_size_t()
        ok = self.api.ReadProcessMemory(self.handle, address, buffer, size, C.byref(count))
        if not ok or count.value != size:
            return None
        return buffer.raw


