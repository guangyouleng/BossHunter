"""Non-destructive process liveness checks on POSIX and Windows."""
import os
import sys


def _windows_kernel():
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
    kernel.GetProcessTimes.restype = wintypes.BOOL
    return kernel


def _windows_birth(kernel, handle):
    import ctypes
    from ctypes import wintypes
    created, exited, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
    if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                  ctypes.byref(kernel_time), ctypes.byref(user_time)):
        return None
    return (created.dwHighDateTime << 32 | created.dwLowDateTime) or None


def process_birth(pid):
    """Windows FILETIME identifies a process instance even after PID reuse."""
    if sys.platform != 'win32':
        return None
    kernel = _windows_kernel()
    handle = kernel.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION only.
    if not handle:
        return None
    try:
        return _windows_birth(kernel, handle)
    finally:
        kernel.CloseHandle(handle)


def process_alive(pid, expected_birth=None):
    if sys.platform == 'win32':
        import ctypes
        kernel = _windows_kernel()
        # Read-only wait/query rights. Never request terminate rights or signal.
        access = 0x00100000 | (0x1000 if expected_birth is not None else 0)
        handle = kernel.OpenProcess(access, False, pid)
        if not handle:
            # An inaccessible/unknown owner must not have its live lease stolen.
            return ctypes.get_last_error() not in (87, 1168)
        try:
            if kernel.WaitForSingleObject(handle, 0) == 0:
                return False
            if expected_birth is not None:
                actual = _windows_birth(kernel, handle)
                return actual is None or actual == expected_birth
            return True
        finally:
            kernel.CloseHandle(handle)
    if expected_birth is not None:
        return False  # A Windows process cannot own a lease on this host OS.
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except ProcessLookupError:
        return False
