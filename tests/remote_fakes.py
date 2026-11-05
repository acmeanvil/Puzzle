# ruff: noqa: E501 - shell scripts
"""Stand-ins for ssh, rsync and a Slurm or PBS scheduler (ADR 0021 tests).

They act on this machine: the "remote home" is a scratch folder, `ssh host CMD` runs
CMD there with sh, `rsync ... host:PATH` copies to and from that folder with the real
rsync, and sbatch/squeue/sacct/scancel (qsub/qstat/qdel) run each job in the
background, keeping its state in files. So a remote run goes through everything
Puzzle does (the driver and job scripts, staging, streaming, fetching, cancelling)
with real solvers, without a second machine.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

SSH = r"""#!/bin/sh
# fake ssh: skip options, take the target, run the rest in the remote home
while [ $# -gt 0 ]; do
  case "$1" in
    -o|-p|-i|-l) shift 2;;
    -*) shift;;
    *) break;;
  esac
done
target="$1"; shift
[ "$target" = "unreachable" ] && { echo "ssh: connect to host unreachable: No route to host" >&2; exit 255; }
cd "$FAKE_REMOTE_HOME" || exit 255
export HOME="$FAKE_REMOTE_HOME"
export PATH="$FAKE_BIN:$PATH"
# a session of its own, as sshd starts a remote command
exec "$FAKE_PYTHON" -c 'import os, sys; os.setsid(); os.execvp("sh", ["sh", "-c", sys.argv[1]])' "$*"
"""

RSYNC = r"""#!/bin/sh
# fake rsync: host:path -> the remote home's path; -e and its command dropped
args=""
skip=0
for a in "$@"; do
  if [ $skip = 1 ]; then skip=0; continue; fi
  case "$a" in
    -e) skip=1; continue;;
    *:*) a="$FAKE_REMOTE_HOME/${a#*:}";;
  esac
  args="$args '$a'"
done
eval exec /usr/bin/rsync $args
"""

SBATCH = r"""#!/bin/sh
# fake sbatch --parsable SCRIPT: run it in the background, print the job id
script="$2"; [ -z "$script" ] && script="$1"
grep -q "partition=rejected" "$script" && { echo "sbatch: error: invalid partition specified: rejected" >&2; exit 1; }
jobs="$FAKE_REMOTE_HOME/.fake-jobs"; mkdir -p "$jobs"
id=$(( $(ls "$jobs" | wc -l) + 1000 ))
out=$(sed -n 's/^#SBATCH --output=//p;s/^#PBS -o //p' "$script")
limit=$(sed -n 's/^#SBATCH --time=//p;s/^#PBS -l walltime=//p' "$script")
secs=$(echo "$limit" | awk -F: '{print $1*3600+$2*60+$3}')
( sh "$script" > "${out:-job.out}" 2>&1 & child=$!
  echo $child > "$jobs/$id.pid"
  ( sleep "$secs"; kill $child 2>/dev/null && echo TIMEOUT > "$jobs/$id.timeout" ) & timer=$!
  wait $child; rc=$?
  kill $timer 2>/dev/null
  echo $rc > "$jobs/$id.rc" ) >/dev/null 2>&1 &
echo "$id"
"""

SQUEUE = r"""#!/bin/sh
# fake squeue -h -j ID -o %T
while [ $# -gt 0 ]; do [ "$1" = "-j" ] && id="$2"; shift; done
[ -f "$FAKE_REMOTE_HOME/.fake-jobs/$id.rc" ] || echo RUNNING
"""

SACCT = r"""#!/bin/sh
# fake sacct -n -X -j ID -o State,ExitCode
while [ $# -gt 0 ]; do [ "$1" = "-j" ] && id="$2"; shift; done
jobs="$FAKE_REMOTE_HOME/.fake-jobs"
if [ -f "$jobs/$id.timeout" ]; then echo "TIMEOUT 0:15"; exit 0; fi
if [ -f "$jobs/$id.cancelled" ]; then echo "CANCELLED 0:15"; exit 0; fi
rc=$(cat "$jobs/$id.rc" 2>/dev/null || echo 1)
if [ "$rc" = 0 ]; then echo "COMPLETED 0:0"; else echo "FAILED $rc:0"; fi
"""

SCANCEL = r"""#!/bin/sh
jobs="$FAKE_REMOTE_HOME/.fake-jobs"
touch "$jobs/$1.cancelled"
kill "$(cat "$jobs/$1.pid")" 2>/dev/null
exit 0
"""

QSUB = r"""#!/bin/sh
exec "$FAKE_BIN/sbatch" --parsable "$1"
"""

QSTAT = r"""#!/bin/sh
# fake qstat ID (running?) and qstat -xf ID (Exit_status)
if [ "$1" = "-xf" ]; then
  id="$2"; jobs="$FAKE_REMOTE_HOME/.fake-jobs"
  [ -f "$jobs/$id.timeout" ] && { echo "    Exit_status = -29"; exit 0; }
  echo "    job_state = F"; echo "    Exit_status = $(cat "$jobs/$id.rc" 2>/dev/null || echo 1)"
  exit 0
fi
[ -f "$FAKE_REMOTE_HOME/.fake-jobs/$1.rc" ] && exit 1
exit 0
"""

QDEL = SCANCEL

PROGRAMS = {"ssh": SSH, "rsync": RSYNC, "sbatch": SBATCH, "squeue": SQUEUE, "sacct": SACCT,
            "scancel": SCANCEL, "qsub": QSUB, "qstat": QSTAT, "qdel": QDEL}  # fmt: skip


def install(tmp: Path, monkeypatch) -> tuple[Path, Path]:
    """The fake programs in tmp/bin and a remote home tmp/remote; (bin, home)."""
    bin_dir, home = tmp / "fake-bin", tmp / "remote-home"
    bin_dir.mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    for name, text in PROGRAMS.items():
        path = bin_dir / name
        path.write_text(text)
        path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("FAKE_REMOTE_HOME", str(home))
    monkeypatch.setenv("FAKE_BIN", str(bin_dir))
    monkeypatch.setenv("FAKE_PYTHON", sys.executable)
    monkeypatch.setenv("PUZZLE_REMOTE_POLL_S", "1")
    return bin_dir, home


def host(bin_dir: Path, name="lab", **values):
    """A Host reached through the fakes."""
    from puzzle.hosts import Host

    envs = Path(os.environ.get("PIXI_ENVS", Path(__file__).parents[1] / ".pixi" / "envs"))
    values.setdefault("envs_dir", str(envs))
    return Host(name=name, address=values.pop("address", "lab.example"),
                ssh=[str(bin_dir / "ssh")], rsync=[str(bin_dir / "rsync")], **values)  # fmt: skip
