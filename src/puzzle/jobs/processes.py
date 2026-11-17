"""A solver's whole process tree, owned and stopped as one (ADR 0090, BE-3).

A solve is often a wrapper and what it starts (`run_aster.bat` and its Python, `mpiexec`
and its ranks, a shell script and the solver). Stopping only the direct child left the
rest running, still using the cores and writing into the run folder, and a grandchild
holding the output pipe kept the run from ever returning. So:

- POSIX: the solve starts in a session of its own (`start_new_session`), and a stop
  signals its whole process group, TERM then KILL after a grace period.
- Windows: the solve is put in a job object that kills everything in it when stopped
  (`TerminateJobObject`) and when the job's handle closes, which Windows does when
  Puzzle exits, however it exits. Where a job can't be had, `taskkill /T /F` stops the
  tree by its parent's id.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess

GRACE_S = 5.0


def kill_tree_sh(pid_file: str) -> str:
    """A POSIX shell script stopping the process whose pid is in `pid_file` and all it
    started, found through /proc (images have no ps or pkill): TERM to every one, then
    KILL to those left. For solves inside a container or a WSL distribution, where
    killing the process alone left its children (a wrapper's solver) running."""
    return (
        'tree() { for s in /proc/[0-9]*/stat; do read -r p c st pp rest < "$s" 2>/dev/null '
        '|| continue; [ "$pp" = "$1" ] && tree "$p"; done; echo "$1"; }; '
        f"top=$(cat '{pid_file}' 2>/dev/null) && [ -n \"$top\" ] && "
        'pids=$(tree "$top") && { kill -TERM $pids 2>/dev/null; sleep 1; '
        "kill -KILL $pids 2>/dev/null; }; true"
    )


_KILL_ON_JOB_CLOSE = 0x00002000
_EXTENDED_LIMITS = 9
_CREATE_NO_WINDOW = 0x08000000


class Tree:
    """A started process and, on Windows, the job object holding it and its children."""

    def __init__(self, proc: subprocess.Popen, job=None) -> None:
        self.proc, self.job = proc, job

    def stop(self, grace_s: float = GRACE_S) -> None:
        """The whole tree stopped: politely first, then for certain."""
        proc = self.proc
        if os.name == "nt":
            if self.job is not None:
                _terminate_job(self.job)
            if proc.poll() is None:
                with contextlib.suppress(OSError, subprocess.SubprocessError):
                    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                                   capture_output=True, timeout=30,
                                   creationflags=_CREATE_NO_WINDOW)  # fmt: skip
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=grace_s)
            if proc.poll() is None:
                proc.kill()
            return
        _signal_group(proc, signal.SIGTERM)
        try:
            proc.wait(timeout=grace_s)
        except subprocess.TimeoutExpired:
            _signal_group(proc, signal.SIGKILL)
            proc.kill()
        # Children that ignored TERM after their parent went: the group again, for certain.
        _signal_group(proc, signal.SIGKILL)

    def close(self) -> None:
        """The run is over: on Windows the job's handle closed, which ends anything of
        the solve's still running (a daemon a wrapper left behind)."""
        if self.job is not None:
            with contextlib.suppress(OSError):
                _close(self.job)
            self.job = None


def popen(argv: list[str], **kwargs) -> Tree:
    """`subprocess.Popen(argv, **kwargs)` started as a tree this module can stop."""
    if os.name != "nt":
        proc = subprocess.Popen(argv, start_new_session=True, **kwargs)
        return Tree(proc)
    proc = subprocess.Popen(argv, **kwargs)
    return Tree(proc, _job_for(proc))


def _signal_group(proc: subprocess.Popen, sig) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(proc.pid, sig)


def _kernel():
    import ctypes

    return ctypes.WinDLL("kernel32", use_last_error=True)


def _job_for(proc: subprocess.Popen):
    """A job object holding `proc` (and so what it starts from now on), killing them
    all when closed; None where one can't be made or joined."""
    try:
        import ctypes
        from ctypes import wintypes

        from puzzle.operators.sandbox_win import JOBOBJECT_EXTENDED_LIMIT_INFORMATION

        kernel = _kernel()
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.OpenProcess.restype = wintypes.HANDLE
        job = kernel.CreateJobObjectW(None, None)
        if not job:
            return None
        limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        limits.BasicLimitInformation.LimitFlags = _KILL_ON_JOB_CLOSE
        kernel.SetInformationJobObject(wintypes.HANDLE(job), _EXTENDED_LIMITS,
                                       ctypes.byref(limits), ctypes.sizeof(limits))  # fmt: skip
        handle = int(proc._handle)  # noqa: SLF001 - Popen's own process handle
        if not kernel.AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(handle)):
            kernel.CloseHandle(wintypes.HANDLE(job))
            return None
        return job
    except (OSError, AttributeError, ImportError, ValueError):
        return None


def _terminate_job(job) -> None:
    from ctypes import wintypes

    with contextlib.suppress(OSError):
        _kernel().TerminateJobObject(wintypes.HANDLE(job), 1)


def _close(job) -> None:
    from ctypes import wintypes

    _kernel().CloseHandle(wintypes.HANDLE(job))
