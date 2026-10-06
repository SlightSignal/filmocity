"""Tie the application and its subprocesses together, including onefile bootloader death."""
import os
import sys
import threading


def bind_lifetime():
    if os.name != "nt": return
    if globals().get('_APPLICATION_JOB'): return
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]; kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]; kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]; kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]; kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]; kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]

    class Basic(ctypes.Structure):
        _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64), ("flags", wintypes.DWORD), ("min_ws", ctypes.c_size_t), ("max_ws", ctypes.c_size_t), ("active", wintypes.DWORD), ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]
    class Limits(ctypes.Structure):
        _fields_ = [("basic", Basic), ("io", ctypes.c_uint64 * 6), ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]
    job = kernel.CreateJobObjectW(None, None)
    if not job: raise ctypes.WinError(ctypes.get_last_error())
    info = Limits(); info.basic.flags = 0x2000  # KILL_ON_JOB_CLOSE
    if not kernel.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)) or not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()):
        error = ctypes.get_last_error(); kernel.CloseHandle(job); raise ctypes.WinError(error)
    # Keep this handle until process exit; inherited subprocesses join the same job.
    globals()["_APPLICATION_JOB"] = job
    if getattr(sys, "frozen", False):
        parent = kernel.OpenProcess(0x00100000, False, os.getppid())  # SYNCHRONIZE
        if not parent: raise ctypes.WinError(ctypes.get_last_error())
        def monitor():
            outcome = kernel.WaitForSingleObject(parent, 0xFFFFFFFF)
            kernel.CloseHandle(parent)
            if outcome == 0: os._exit(1)  # close the job even if the bootloader was force-killed
        threading.Thread(target=monitor, name="bootloader-lifetime", daemon=True).start()
