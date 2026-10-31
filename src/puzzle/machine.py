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
                                 text=True, timeout=5, check=True).stdout  # fmt: skip
            return max(1, int(out.strip()))
        if sys.platform.startswith("linux"):
            return _linux_cores() or logical_cores()
        if os.name == "nt":
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_Processor | Measure-Object NumberOfCores -Sum).Sum"],
                capture_output=True, text=True, timeout=20, check=True,
            ).stdout  # fmt: skip
            return max(1, int(out.strip()))
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return logical_cores()


def _linux_cores() -> int | None:
    """Distinct (physical id, core id) pairs in /proc/cpuinfo."""
    try:
        text = open("/proc/cpuinfo").read()  # noqa: SIM115 - one read
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
                                 text=True, timeout=5, check=True).stdout  # fmt: skip
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
