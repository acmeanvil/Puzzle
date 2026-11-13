"""Repair a solver environment of the installed app (ADR 0068 decision 6).

The installer lays each solver environment out as `<prefix>/envs/<name>` and leaves its
exact package list beside it (`conda-meta/initial-state.explicit.txt`: every package by
URL and checksum, what the installer was written from), and ships conda itself. A
broken environment (a deleted DLL, a half-finished update, a program an antivirus took)
is rebuilt from that list with conda, the same packages again: they are downloaded once
more, since the installer clears its package cache after installing. The old folder is
moved aside first and put back if the rebuild fails, so a failed repair loses nothing.

A pixi checkout has no such lists; `pixi install -e <name>` repairs there, and Repair
says so.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

LIST = Path("conda-meta") / "initial-state.explicit.txt"


@dataclass
class Repairable:
    name: str
    path: Path
    packages: int


def _prefix() -> Path:
    return Path(sys.prefix)


def conda(prefix: Path | None = None) -> Path | None:
    """The installed app's own conda (constructor puts it in the base)."""
    base = prefix or _prefix()
    names = ["Scripts/conda.exe", "_conda.exe", "condabin/conda.bat"] if os.name == "nt" \
        else ["bin/conda", "_conda", "condabin/conda"]  # fmt: skip
    return next((base / n for n in names if (base / n).is_file()), None)


def environments(prefix: Path | None = None) -> list[Repairable]:
    """The environments Repair can rebuild: those with the installer's package list."""
    base = prefix or _prefix()
    out = []
    for env in sorted((base / "envs").glob("*")) if (base / "envs").is_dir() else []:
        listed = env / LIST
        if listed.is_file():
            lines = listed.read_text(encoding="utf-8", errors="replace").splitlines()
            count = sum(1 for line in lines if line.startswith(("http", "file:")))
            out.append(Repairable(env.name, env, count))
    return out


class RepairError(RuntimeError):
    pass


def why_not(prefix: Path | None = None) -> str | None:
    """Why Repair can't run here, or None."""
    if conda(prefix) is None:
        return ("this isn't the installed app (no conda in it); in a pixi checkout, "
                "`pixi install -e <name>` repairs an environment")  # fmt: skip
    if not environments(prefix):
        return "no environment here lists its packages"
    return None


def repair(name: str, say=print, prefix: Path | None = None) -> Path:
    """Rebuild environment `name` from its package list (module docstring)."""
    base = prefix or _prefix()
    exe = conda(base)
    found = next((e for e in environments(base) if e.name == name), None)
    if exe is None or found is None:
        raise RepairError(why_not(base) or f"no environment '{name}' to repair")
    listed = found.path.parent / f".{name}.explicit.txt"
    shutil.copy2(found.path / LIST, listed)  # the folder moves aside next
    aside = found.path.with_name(f".{name}.old")
    if aside.exists():
        shutil.rmtree(aside, ignore_errors=True)
    say(f"moving {name} aside and reinstalling its {found.packages} packages")
    found.path.rename(aside)
    argv = [str(exe), "create", "--yes", "--quiet", "--prefix", str(found.path),
            "--file", str(listed)]  # fmt: skip
    try:
        done = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=3600)  # fmt: skip
        error = " | ".join((done.stderr or done.stdout).strip().splitlines()[-3:])
        ok = done.returncode == 0
    except (OSError, subprocess.SubprocessError) as exc:
        error, ok = str(exc), False
    if not ok:
        shutil.rmtree(found.path, ignore_errors=True)
        aside.rename(found.path)  # as it was
        listed.unlink(missing_ok=True)
        raise RepairError(f"conda couldn't reinstall {name} ({error}); it is left as it was")
    (found.path / LIST).parent.mkdir(parents=True, exist_ok=True)
    shutil.move(listed, found.path / LIST)  # kept for the next repair
    shutil.rmtree(aside, ignore_errors=True)
    say(f"{name} reinstalled")
    return found.path
