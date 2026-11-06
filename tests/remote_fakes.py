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
# fake sbatch --parsable SCRIPT: run it detached (as a scheduler would), print the job id
script="$2"; [ -z "$script" ] && script="$1"
grep -q "partition=rejected" "$script" && { echo "sbatch: error: invalid partition specified: rejected" >&2; exit 1; }
jobs="$FAKE_REMOTE_HOME/.fake-jobs"; mkdir -p "$jobs"
id=$(cat "$jobs/next-id" 2>/dev/null || echo 1000); echo $((id + 1)) > "$jobs/next-id"
out=$(sed -n 's/^#SBATCH --output=//p;s/^#PBS -o //p' "$script")
limit=$(sed -n 's/^#SBATCH --time=//p;s/^#PBS -l walltime=//p' "$script")
secs=$(echo "${limit:-01:00:00}" | awk -F: '{print $1*3600+$2*60+$3}')
monitor='sh "$3" > "$4" 2>&1 & child=$!
echo $child > "$1/$2.pid"
( sleep "$5"; kill $child 2>/dev/null && echo TIMEOUT > "$1/$2.timeout" ) & timer=$!
wait $child; rc=$?
kill $timer 2>/dev/null
echo $rc > "$1/$2.rc"'
# a fresh session with default signals, as slurmstepd starts a job (the driver's submit
# ignores TERM, which a child would otherwise keep)
"$FAKE_PYTHON" -c 'import os, signal, sys
for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP): signal.signal(s, signal.SIG_DFL)
os.setsid(); os.execvp("sh", ["sh", "-c", *sys.argv[1:]])' \
  "$monitor" monitor "$jobs" "$id" "$script" "${out:-job.out}" "$secs" >/dev/null 2>&1 &
# a slow controller: the id comes back late (FAKE_SBATCH_DELAY seconds)
[ -n "$FAKE_SBATCH_DELAY" ] && sleep "$FAKE_SBATCH_DELAY"
echo "$id"
"""

SQUEUE = r"""#!/bin/sh
# fake squeue -h -j ID -o %T; FAKE_SQUEUE_FAILS=N: the first N calls fail (a busy
# controller); FAKE_SQUEUE_FORGETS=1: a finished job is an invalid id, as on some Slurms
while [ $# -gt 0 ]; do [ "$1" = "-j" ] && id="$2"; shift; done
jobs="$FAKE_REMOTE_HOME/.fake-jobs"
if [ -n "$FAKE_SQUEUE_FAILS" ]; then
  n=$(cat "$jobs/squeue.calls" 2>/dev/null || echo 0); echo $((n + 1)) > "$jobs/squeue.calls"
  [ "$n" -lt "$FAKE_SQUEUE_FAILS" ] && { echo "squeue: error: Socket timed out on send/recv operation" >&2; exit 1; }
fi
if [ -f "$jobs/$id.rc" ]; then
  [ -n "$FAKE_SQUEUE_FORGETS" ] && { echo "slurm_load_jobs error: Invalid job id specified" >&2; exit 1; }
  exit 0
fi
echo RUNNING
"""

SACCT = r"""#!/bin/sh
# fake sacct -n -X -P -j ID -o State[,ExitCode]; FAKE_SACCT_STATE: how a finished job
# ended ("OUT_OF_MEMORY|0:125"); FAKE_NO_ACCOUNTING=1: accounting disabled
[ -n "$FAKE_NO_ACCOUNTING" ] && { echo "sacct: error: Slurm accounting storage is disabled" >&2; exit 1; }
while [ $# -gt 0 ]; do
  case "$1" in -j) id="$2";; -o) fields="$2";; esac; shift
done
jobs="$FAKE_REMOTE_HOME/.fake-jobs"
if [ ! -f "$jobs/$id.rc" ]; then line="RUNNING|0:0"
elif [ -n "$FAKE_SACCT_STATE" ]; then line="$FAKE_SACCT_STATE"
elif [ -f "$jobs/$id.timeout" ]; then line="TIMEOUT|0:15"
elif [ -f "$jobs/$id.cancelled" ]; then line="CANCELLED by 501|0:15"
else rc=$(cat "$jobs/$id.rc"); if [ "$rc" = 0 ]; then line="COMPLETED|0:0"; else line="FAILED|$rc:0"; fi
fi
[ "$fields" = "State" ] && line="${line%%|*}"
echo "$line"
"""

SCANCEL = r"""#!/bin/sh
jobs="$FAKE_REMOTE_HOME/.fake-jobs"
touch "$jobs/$1.cancelled"
pid=$(cat "$jobs/$1.pid" 2>/dev/null)
[ -n "$pid" ] && { pkill -TERM -P "$pid" 2>/dev/null; kill "$pid" 2>/dev/null; }
exit 0
"""

QSUB = r"""#!/bin/sh
exec "$FAKE_BIN/sbatch" --parsable "$1"
"""

QSTAT = r"""#!/bin/sh
# fake qstat -f ID (job_state while it runs, unknown after) and qstat -xf ID (history:
# Exit_status, or FAKE_PBS_EXIT; FAKE_NO_HISTORY=1: no -x, as without job history)
jobs="$FAKE_REMOTE_HOME/.fake-jobs"
if [ "$1" = "-xf" ]; then
  [ -n "$FAKE_NO_HISTORY" ] && { echo "qstat: invalid option -- 'x'" >&2; exit 2; }
  id="$2"
  [ -f "$jobs/$id.timeout" ] && { echo "    job_state = F"; echo "    Exit_status = -29"; exit 0; }
  echo "    job_state = F"; echo "    Exit_status = ${FAKE_PBS_EXIT:-$(cat "$jobs/$id.rc" 2>/dev/null || echo 1)}"
  exit 0
fi
id="$2"
[ -f "$jobs/$id.rc" ] && { echo "qstat: Unknown Job Id $id" >&2; exit 153; }
echo "Job Id: $id"; echo "    job_state = R"
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
