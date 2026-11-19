"""What the computer has: physical and logical cores, memory (ADR 0053).

Solver runs default to the physical cores: finite element assembly and sparse factoring
are limited by memory bandwidth and floating point units, which hyperthreads share, so a
second thread per core rarely helps and can slow a run. Each answer is cached; anything
that can't be read falls back to the logical count (cores) or None (memory).
"""

from __future__ import annotations

import os
import subprocess
import sys
from functools import cache


def logical_cores() -> int:
    return max(1, os.cpu_count() or 1)


@cache
def physical_cores() -> int:
    """Physical cores of every processor, or the logical count if they can't be read."""
    try:
        if sys.platform == "darwin":
            out = subprocess.run(["sysctl", "-n", "hw.physicalcpu"], capture_output=True,
                                 text=True, encoding="utf-8", errors="replace", timeout=5,
                                 check=True).stdout  # fmt: skip
            return max(1, int(out.strip()))
        if sys.platform.startswith("linux"):
            return _linux_cores() or logical_cores()
        if os.name == "nt":
            found = _windows_cores()
            if found:
                return found
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_Processor | Measure-Object NumberOfCores -Sum).Sum"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
                check=True,
            ).stdout  # fmt: skip
            return max(1, int(out.strip()))
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return logical_cores()


def _windows_cores() -> int | None:
    """Physical cores from Windows itself (one record per core): PowerShell took 1.2 s,
    on the GUI's thread as the window opened, where Settings reads this for its choices."""
    import ctypes

    try:
        kernel = ctypes.windll.kernel32
        size = ctypes.c_ulong(0)
        kernel.GetLogicalProcessorInformationEx(0, None, ctypes.byref(size))  # 0: cores
        if not size.value:
            return None
        buffer = ctypes.create_string_buffer(size.value)
        if not kernel.GetLogicalProcessorInformationEx(0, buffer, ctypes.byref(size)):
            return None
        count, at = 0, 0
        while at + 8 <= size.value:  # each record: relationship (4 bytes), its size (4)
            length = int.from_bytes(buffer.raw[at + 4 : at + 8], "little")
            if length <= 0:
                break
            count += 1
            at += length
        return count or None
    except (AttributeError, OSError, ValueError):
        return None


DEFAULT_CORES = 4


def default_cores(physical: int | None = None) -> int:
    """Cores per run when Settings > Solvers leaves it automatic (the maintainer,
    2026-11-08): 4 where there are more, else one fewer than the physical cores, so the
    computer stays usable while it solves (and ADR 0053 measured little gain past 2-4)."""
    n = physical_cores() if physical is None else physical
    return DEFAULT_CORES if n > DEFAULT_CORES else max(1, n - 1)


def _linux_cores() -> int | None:
    """Distinct (physical id, core id) pairs in /proc/cpuinfo."""
    try:
        text = open("/proc/cpuinfo", encoding="utf-8").read()  # noqa: SIM115 - one read
    except OSError:
        return None
    pairs, physical, core = set(), "0", None
    for line in text.splitlines() + [""]:
        key, _, value = line.partition(":")
        key = key.strip()
        if key == "physical id":
            physical = value.strip()
        elif key == "core id":
            core = value.strip()
        elif not line.strip():
            if core is not None:
                pairs.add((physical, core))
            physical, core = "0", None
    return len(pairs) or None


@cache
def memory_bytes() -> int | None:
    """Installed memory, or None if it can't be read."""
    try:
        if sys.platform == "darwin":
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True,
                                 text=True, encoding="utf-8", errors="replace", timeout=5,
                                 check=True).stdout  # fmt: skip
            return int(out.strip())
        if hasattr(os, "sysconf") and "SC_PHYS_PAGES" in os.sysconf_names:
            return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        if os.name == "nt":
            import ctypes

            class Status(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                            ("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong),
                            ("page_total", ctypes.c_ulonglong), ("page_free", ctypes.c_ulonglong),
                            ("virtual_total", ctypes.c_ulonglong),
                            ("virtual_free", ctypes.c_ulonglong),
                            ("extended", ctypes.c_ulonglong)]  # fmt: skip

            s = Status()
            s.length = ctypes.sizeof(Status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s)):
                return int(s.total)
    except (OSError, ValueError, subprocess.SubprocessError, AttributeError):
        pass
    return None
