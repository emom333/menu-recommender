"""Exit the process immediately, without running native library shutdown code.

Some native libraries (Qt and others) can crash while Windows unloads DLLs at
process exit.  All data is committed before this is called."""
from __future__ import annotations

import os
import sys


def hard_exit(code: int) -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None:
                stream.flush()
        except Exception:  # noqa: BLE001
            pass
    if sys.platform.startswith("win"):
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k32.TerminateProcess.restype = wintypes.BOOL
        k32.TerminateProcess(k32.GetCurrentProcess(), int(code))
    os._exit(int(code))
