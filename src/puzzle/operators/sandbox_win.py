"""Script operators sandboxed on Windows (ADR 0062 phase 4, ADR 0072): an AppContainer.

An AppContainer is the sandbox Windows gives its own store apps and browsers' renderers:
a process in one reaches only what its container's SID was granted, and no network
without a capability. So a script runs:

- in the container `puzzle.scripts` (made once with `CreateAppContainerProfile`), no
  capabilities, so no network;
- able to read and run the scripts environment (granted read and execute once, the
  folder's ACL, `grant`), to read Windows' own folders (open to every container), and
  to read and write its own work folder (granted per run); not the user's files, the
  project or anything else;
- inside a job object that kills it, and anything it starts, when the run ends or Puzzle
  goes, and caps it at one process.

`run(argv, cwd, env, timeout_s)` returns (exit code, timed out). It raises OSError
when the container can't be made (an old Windows): `scripts.run` then says so.
"""

from __future__ import annotations

import ctypes
import subprocess
from ctypes import wintypes
from pathlib import Path

NAME = "puzzle.scripts"
_GRANTED: set[str] = set()  # folders this process granted the container already

EXTENDED_STARTUPINFO_PRESENT = 0x00080000
CREATE_SUSPENDED = 0x00000004
CREATE_NO_WINDOW = 0x08000000
CREATE_UNICODE_ENVIRONMENT = 0x00000400
PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES = 0x00020009
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
JobObjectExtendedLimitInformation = 9
WAIT_TIMEOUT = 0x102
ALREADY_EXISTS = -2147024713  # HRESULT_FROM_WIN32(ERROR_ALREADY_EXISTS)


class SECURITY_CAPABILITIES(ctypes.Structure):  # noqa: N801 - Windows' name
    _fields_ = [("AppContainerSid", ctypes.c_void_p), ("Capabilities", ctypes.c_void_p),
                ("CapabilityCount", wintypes.DWORD), ("Reserved", wintypes.DWORD)]  # fmt: skip


class STARTUPINFOW(ctypes.Structure):  # noqa: N801
    _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
                ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
                ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD), ("dwXSize", wintypes.DWORD),
                ("dwYSize", wintypes.DWORD), ("dwXCountChars", wintypes.DWORD),
                ("dwYCountChars", wintypes.DWORD), ("dwFillAttribute", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("wShowWindow", wintypes.WORD),
                ("cbReserved2", wintypes.WORD), ("lpReserved2", ctypes.c_void_p),
                ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE),
                ("hStdError", wintypes.HANDLE)]  # fmt: skip


class STARTUPINFOEXW(ctypes.Structure):  # noqa: N801
    _fields_ = [("StartupInfo", STARTUPINFOW), ("lpAttributeList", ctypes.c_void_p)]


class PROCESS_INFORMATION(ctypes.Structure):  # noqa: N801
    _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]  # fmt: skip


class IO_COUNTERS(ctypes.Structure):  # noqa: N801
    _fields_ = [(n, ctypes.c_ulonglong) for n in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]  # fmt: skip


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):  # noqa: N801
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong), ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD), ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]  # fmt: skip


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):  # noqa: N801
    _fields_ = [("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS), ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t)]  # fmt: skip


def _check(ok, what: str):
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error(), what)
    return ok


def container_sid() -> tuple[ctypes.c_void_p, str]:
    """The container's SID (made on first use) and its string form."""
    userenv = ctypes.WinDLL("userenv", use_last_error=True)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    sid = ctypes.c_void_p()
    hr = userenv.CreateAppContainerProfile(
        ctypes.c_wchar_p(NAME),
        ctypes.c_wchar_p("Puzzle scripts"),
        ctypes.c_wchar_p("Puzzle's script operators, sandboxed"),
        None,
        0,
        ctypes.byref(sid),
    )
    if hr == ALREADY_EXISTS:
        hr = userenv.DeriveAppContainerSidFromAppContainerName(ctypes.c_wchar_p(NAME),
                                                               ctypes.byref(sid))  # fmt: skip
    if hr != 0:
        raise OSError(f"no AppContainer for scripts (HRESULT {hr & 0xFFFFFFFF:#x})")
    text = ctypes.c_wchar_p()
    _check(advapi.ConvertSidToStringSidW(sid, ctypes.byref(text)), "ConvertSidToStringSid")
    return sid, text.value


def granted(path: Path, sid_text: str) -> bool:
    """Whether `path`'s own ACL names the container already."""
    done = subprocess.run(["icacls", str(path)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          creationflags=CREATE_NO_WINDOW)  # fmt: skip
    return sid_text in done.stdout


def grant(folder: Path, sid_text: str, rights: str, probe: Path | None = None) -> None:
    """Give the container `rights` (icacls: RX read and run, M modify) on a folder and
    everything in it: inherited by what is made in it later, and explicitly on what is
    there, since files with protected ACLs (pixi's hard links from its cache) inherit
    nothing. Skipped when `probe` (a file in it) has the grant already, or this process
    granted it."""
    key = f"{folder}|{rights}"
    if key in _GRANTED or (probe is not None and granted(probe, sid_text)):
        _GRANTED.add(key)
        return
    for spec in (f"*{sid_text}:(OI)(CI){rights}", f"*{sid_text}:{rights}"):
        done = subprocess.run(["icacls", str(folder), "/grant", spec, "/T", "/C", "/Q"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              creationflags=CREATE_NO_WINDOW)  # fmt: skip
        if done.returncode != 0:
            raise OSError(f"couldn't grant the script sandbox {folder}: "
                          f"{done.stdout or done.stderr}")  # fmt: skip
    _GRANTED.add(key)


def _environment_block(env: dict[str, str]) -> ctypes.Array:
    text = "".join(f"{k}={v}\0" for k, v in sorted(env.items())) + "\0"
    return ctypes.create_unicode_buffer(text, len(text))


def run(argv: list[str], cwd: Path, env: dict[str, str], timeout_s: float,
        readable: list[Path]) -> tuple[int, bool]:  # fmt: skip
    """Run `argv` in the container: (exit code, whether it ran past `timeout_s`)."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.CreateProcessW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    sid, sid_text = container_sid()
    for folder in readable:
        grant(folder, sid_text, "RX", probe=Path(argv[0]))
    grant(cwd, sid_text, "M")

    caps = SECURITY_CAPABILITIES(sid.value, None, 0, 0)
    size = ctypes.c_size_t()
    kernel.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
    attrs = ctypes.create_string_buffer(size.value)
    _check(kernel.InitializeProcThreadAttributeList(attrs, 1, 0, ctypes.byref(size)),
           "InitializeProcThreadAttributeList")  # fmt: skip
    _check(
        kernel.UpdateProcThreadAttribute(
            attrs,
            0,
            ctypes.c_size_t(PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES),
            ctypes.byref(caps),
            ctypes.sizeof(caps),
            None,
            None,
        ),
        "UpdateProcThreadAttribute",
    )
    info = STARTUPINFOEXW()
    info.StartupInfo.cb = ctypes.sizeof(STARTUPINFOEXW)
    info.lpAttributeList = ctypes.cast(attrs, ctypes.c_void_p)
    proc = PROCESS_INFORMATION()
    line = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
    block = _environment_block(env)
    flags = (EXTENDED_STARTUPINFO_PRESENT | CREATE_SUSPENDED | CREATE_NO_WINDOW
             | CREATE_UNICODE_ENVIRONMENT)  # fmt: skip
    job = kernel.CreateJobObjectW(None, None)
    try:
        _check(job, "CreateJobObject")
        limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        limits.BasicLimitInformation.LimitFlags = (JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                                                   | JOB_OBJECT_LIMIT_ACTIVE_PROCESS)  # fmt: skip
        limits.BasicLimitInformation.ActiveProcessLimit = 1
        _check(kernel.SetInformationJobObject(job, JobObjectExtendedLimitInformation,
                                              ctypes.byref(limits), ctypes.sizeof(limits)),
               "SetInformationJobObject")  # fmt: skip
        _check(
            kernel.CreateProcessW(
                argv[0],
                line,
                None,
                None,
                False,
                flags,
                ctypes.cast(block, ctypes.c_void_p),
                str(cwd),
                ctypes.byref(info),
                ctypes.byref(proc),
            ),
            "CreateProcess",
        )
        try:
            _check(kernel.AssignProcessToJobObject(job, proc.hProcess), "AssignProcessToJobObject")
            kernel.ResumeThread(proc.hThread)
            late = kernel.WaitForSingleObject(proc.hProcess, int(timeout_s * 1000)) == WAIT_TIMEOUT
            if late:
                kernel.TerminateJobObject(job, 1)
                kernel.WaitForSingleObject(proc.hProcess, 5000)
            code = wintypes.DWORD()
            kernel.GetExitCodeProcess(proc.hProcess, ctypes.byref(code))
            return int(code.value), late
        finally:
            kernel.CloseHandle(proc.hThread)
            kernel.CloseHandle(proc.hProcess)
    finally:
        if job:
            kernel.CloseHandle(job)
        kernel.DeleteProcThreadAttributeList(attrs)
