# ruff: noqa: E501 - shell scripts
"""Stand-ins for ssh, rsync and a Slurm or PBS scheduler (ADR 0021 tests).

They act on this machine: the "remote home" is a scratch folder, `ssh host CMD` runs
CMD there with sh, `rsync ... host:PATH` copies to and from that folder with the real
rsync, and sbatch/squeue/sacct/scancel (qsub/qstat/qdel) run each job in the
background, keeping its state in files. So a remote run goes through everything
Puzzle does (the driver and job scripts, staging, streaming, fetching, cancelling)
with real solvers, without a second machine. They speak both what Puzzle's own driver
asks (`sbatch --parsable`, `squeue -h -j ID -o %T`, `qstat -f`/`-xf`) and what PSI/J
asks from the job runner (ADR 0067: `sbatch SCRIPT`, `squeue -O ... --me`, `scancel
-Q`, `scontrol show hostnames`, `qstat -f -F json -x`).
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
# fake sbatch [--parsable] SCRIPT (qsub SCRIPT too): run the script detached, as a
# scheduler would, and print its id ("Submitted batch job N", or N with --parsable).
# The job runs in the folder --chdir names (PSI/J's scripts), else here, by the
# interpreter its #! line names, with SLURM_JOB_ID, PBS_JOBID and the like set.
# FAKE_PENDING_S: it waits that long in the queue first; FAKE_SBATCH_DELAY: the
# controller is slow to say the id.
parsable=""
while [ $# -gt 1 ]; do [ "$1" = "--parsable" ] && parsable=1; shift; done
script="$1"
grep -Eq 'partition="?rejected' "$script" && { echo "sbatch: error: invalid partition specified: rejected" >&2; exit 1; }
jobs="$FAKE_REMOTE_HOME/.fake-jobs"; mkdir -p "$jobs"
id=$(cat "$jobs/next-id" 2>/dev/null || echo 1000); echo $((id + 1)) > "$jobs/next-id"
echo "$script" > "$jobs/$id.sub"
"$FAKE_PYTHON" "$FAKE_BIN/fake-scheduler.py" monitor "$jobs" "$id" "$script" "$PWD" >/dev/null 2>&1 &
[ -n "$FAKE_SBATCH_DELAY" ] && sleep "$FAKE_SBATCH_DELAY"
if [ -n "$parsable" ]; then echo "$id"; else echo "Submitted batch job $id"; fi
"""

SQUEUE = r"""#!/bin/sh
# fake squeue: the driver's `squeue -h -j ID -o %T`, or PSI/J's `squeue -O
# JobArrayID,StateCompact,Reason -t all --me` (a header, then every job of the user's:
# queued, running, or ended, as Slurm lists an ended job until MinJobAge).
# FAKE_SQUEUE_FAILS=N: the first N calls fail (a busy controller); FAKE_SQUEUE_FORGETS=1:
# an ended job is no longer listed (to the driver's query: an invalid id, as on some Slurms)
jobs="$FAKE_REMOTE_HOME/.fake-jobs"
if [ -n "$FAKE_SQUEUE_FAILS" ]; then
  n=$(cat "$jobs/squeue.calls" 2>/dev/null || echo 0); echo $((n + 1)) > "$jobs/squeue.calls"
  [ "$n" -lt "$FAKE_SQUEUE_FAILS" ] && { echo "squeue: error: Socket timed out on send/recv operation" >&2; exit 1; }
fi
case " $* " in *" -O "*) exec "$FAKE_PYTHON" "$FAKE_BIN/fake-scheduler.py" squeue "$jobs";; esac
while [ $# -gt 0 ]; do [ "$1" = "-j" ] && id="$2"; shift; done
if [ -f "$jobs/$id.rc" ]; then
  [ -n "$FAKE_SQUEUE_FORGETS" ] && { echo "slurm_load_jobs error: Invalid job id specified" >&2; exit 1; }
  exit 0
fi
[ -f "$jobs/$id.started" ] || { echo PENDING; exit 0; }
echo RUNNING
"""

SACCT = r"""#!/bin/sh
# fake sacct -n -X -P -j ID -o State[,ExitCode]; FAKE_SACCT_STATE: how a finished job
# ended ("OUT_OF_MEMORY|0:125"); FAKE_NO_ACCOUNTING=1: accounting disabled;
# FAKE_SACCT_LAG=N: the first N queries after the end still say RUNNING (slurmdbd lags)
[ -n "$FAKE_NO_ACCOUNTING" ] && { echo "sacct: error: Slurm accounting storage is disabled" >&2; exit 1; }
while [ $# -gt 0 ]; do
  case "$1" in -j) id="$2";; -o) fields="$2";; esac; shift
done
jobs="$FAKE_REMOTE_HOME/.fake-jobs"
lagged=""
if [ -f "$jobs/$id.rc" ] && [ -n "$FAKE_SACCT_LAG" ]; then
  n=$(cat "$jobs/$id.sacct" 2>/dev/null || echo 0); echo $((n + 1)) > "$jobs/$id.sacct"
  [ "$n" -lt "$FAKE_SACCT_LAG" ] && lagged=1
fi
if [ ! -f "$jobs/$id.started" ] && [ ! -f "$jobs/$id.rc" ]; then line="PENDING|0:0"
elif [ ! -f "$jobs/$id.rc" ] || [ -n "$lagged" ]; then line="RUNNING|0:0"
elif [ -n "$FAKE_SACCT_STATE" ]; then line="$FAKE_SACCT_STATE"
elif [ -f "$jobs/$id.timeout" ]; then line="TIMEOUT|0:15"
elif [ -f "$jobs/$id.cancelled" ]; then line="CANCELLED by 501|0:15"
else rc=$(cat "$jobs/$id.rc"); if [ "$rc" = 0 ]; then line="COMPLETED|0:0"; else line="FAILED|$rc:0"; fi
fi
[ "$fields" = "State" ] && line="${line%%|*}"
echo "$line"
"""

SCANCEL = r"""#!/bin/sh
# fake scancel [-Q] ID (and qdel ID): the job's processes stopped together, as a
# scheduler stops a job's cgroup; each call noted in .fake-jobs/cancels
while [ $# -gt 1 ]; do shift; done
jobs="$FAKE_REMOTE_HOME/.fake-jobs"
echo "$(basename "$0") $1" >> "$jobs/cancels"
touch "$jobs/$1.cancelled"
pid=$(cat "$jobs/$1.pid" 2>/dev/null)
[ -n "$pid" ] && kill -TERM -- "-$pid" 2>/dev/null
exit 0
"""

SCONTROL = r"""#!/bin/sh
# fake scontrol show hostnames: the job's one node
echo localhost
"""

QSUB = r"""#!/bin/sh
exec "$FAKE_BIN/sbatch" --parsable "$1"
"""

QSTAT = r"""#!/bin/sh
# fake qstat -f ID (job_state while it runs, unknown after) and qstat -xf ID (history:
# Exit_status, or FAKE_PBS_EXIT; FAKE_NO_HISTORY=1: no -x, as without job history);
# PSI/J's `qstat -f -F json -x ID...` as PBS Pro prints it
jobs="$FAKE_REMOTE_HOME/.fake-jobs"
case " $* " in *" -F "*) exec "$FAKE_PYTHON" "$FAKE_BIN/fake-scheduler.py" qstat "$jobs" "$@";; esac
if [ "$1" = "-xf" ]; then
  [ -n "$FAKE_NO_HISTORY" ] && { echo "qstat: invalid option -- 'x'" >&2; exit 2; }
  id="$2"
  [ -f "$jobs/$id.timeout" ] && { echo "    job_state = F"; echo "    Exit_status = -29"; exit 0; }
  echo "    job_state = F"; echo "    Exit_status = ${FAKE_PBS_EXIT:-$(cat "$jobs/$id.rc" 2>/dev/null || echo 1)}"
  exit 0
fi
id="$2"
[ -f "$jobs/$id.rc" ] && { echo "qstat: Unknown Job Id $id" >&2; exit 153; }
echo "Job Id: $id"
if [ -f "$jobs/$id.started" ]; then echo "    job_state = R"; else echo "    job_state = Q"; fi
"""

QDEL = SCANCEL

#: The scheduler's side in Python: a job run as slurmstepd or a PBS mom would (a session
#: of its own, its walltime enforced on the whole group), and the listings that need
#: more than sh (squeue's table of every job, qstat's JSON).
SCHEDULER = r'''
import json, os, re, signal, subprocess, sys, time
from pathlib import Path

# A sacct state's compact squeue state and reason (FAKE_SACCT_STATE drives both).
COMPACT = {"COMPLETED": ("CD", "None"), "FAILED": ("F", "NonZeroExitCode"),
           "OUT_OF_MEMORY": ("OOM", "OutOfMemory"), "TIMEOUT": ("TO", "TimeLimit"),
           "NODE_FAIL": ("NF", "NodeDown"), "CANCELLED": ("CA", "None"),
           "PREEMPTED": ("PR", "None"), "BOOT_FAIL": ("BF", "None"),
           "DEADLINE": ("DL", "None")}


def directive(script, *prefixes):
    for line in Path(script).read_text().splitlines():
        for p in prefixes:
            if line.startswith(p):
                return line[len(p):].strip().strip('"')
    return None


def seconds(limit):
    days = 0
    if "-" in limit:
        d, limit = limit.split("-", 1)
        days = int(d)
    parts = [int(x) for x in limit.split(":")]
    if len(parts) == 1:
        h, m, s = 0, parts[0], 0
    elif len(parts) == 2:
        h, m, s = 0, parts[0], parts[1]
    else:
        h, m, s = parts[-3:]
    return days * 86400 + h * 3600 + m * 60 + s


def spool(jobs, id_, script):
    """The script copied at submission, as slurmctld spools it. PSI/J's are bash 4
    (`exec &>> file`); macOS's /bin/bash is 3.2, so there the copy says it the old way."""
    text = Path(script).read_text()
    if text.startswith("#!/bin/bash"):
        old = subprocess.run(["/bin/bash", "-c", "exit $(( BASH_VERSINFO[0] < 4 ))"]).returncode
        if old:
            text = "\n".join(re.sub(r"^exec &>> (.*)$", r"exec >> \1 2>&1", line)
                             for line in text.splitlines()) + "\n"
    copy = jobs / f"{id_}.script"
    copy.write_text(text)
    return str(copy)


def monitor(jobs, id_, script, submit_dir):
    for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(s, signal.SIG_DFL)
    os.setsid()
    jobs = Path(jobs)
    script = spool(jobs, id_, script)
    end = time.time() + float(os.environ.get("FAKE_PENDING_S") or 0)
    while time.time() < end and not (jobs / f"{id_}.cancelled").exists():
        time.sleep(0.05)

    def ended(rc):
        (jobs / f"{id_}.rc.tmp").write_text(f"{rc}\n")
        os.replace(jobs / f"{id_}.rc.tmp", jobs / f"{id_}.rc")

    if (jobs / f"{id_}.cancelled").exists():  # cancelled while it waited
        return ended(143)
    where = directive(script, "#SBATCH --chdir=") or submit_dir
    out = directive(script, "#SBATCH --output=", "#SBATCH -o ", "#PBS -o ") or "job.out"
    limit = seconds(directive(script, "#SBATCH --time=", "#PBS -l walltime=") or "1:00:00")
    shebang = Path(script).read_text().splitlines()[0]
    interpreter = shebang[2:].split() if shebang.startswith("#!") else ["sh"]
    nodes = jobs / f"{id_}.nodes"
    nodes.write_text("localhost\n")
    env = dict(os.environ, SLURM_JOB_ID=id_, SLURM_SUBMIT_DIR=submit_dir, PBS_JOBID=id_,
               PBS_O_WORKDIR=submit_dir, PBS_NODEFILE=str(nodes))
    with open(Path(where) / out, "ab") as log:
        proc = subprocess.Popen([*interpreter, script], cwd=where, env=env, stdout=log,
                                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                start_new_session=True)
    (jobs / f"{id_}.pid").write_text(f"{proc.pid}\n")
    (jobs / f"{id_}.started").touch()
    try:
        rc = proc.wait(timeout=limit)
    except subprocess.TimeoutExpired:
        (jobs / f"{id_}.timeout").write_text("TIMEOUT\n")
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            rc = proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            rc = proc.wait()
    ended(128 - rc if rc < 0 else rc)


def ids(jobs):
    return sorted(p.name[:-4] for p in Path(jobs).glob("*.sub"))


def state(jobs, id_):
    """(compact state, reason, exit status or None while it is queued or running)."""
    jobs = Path(jobs)
    if not (jobs / f"{id_}.rc").exists():
        if (jobs / f"{id_}.started").exists():
            return "R", "None", None
        return "PD", "Priority", None
    rc = int((jobs / f"{id_}.rc").read_text())
    said = os.environ.get("FAKE_SACCT_STATE", "").split("|")[0].split(" ")[0]
    if said in COMPACT:
        return (*COMPACT[said], rc)
    if (jobs / f"{id_}.timeout").exists():
        return "TO", "TimeLimit", rc
    if (jobs / f"{id_}.cancelled").exists():
        return "CA", "None", rc
    return ("CD", "None", rc) if rc == 0 else ("F", "NonZeroExitCode", rc)


def squeue(jobs):
    print(f"{'ARRAY_JOB_ID':<20}{'ST':<6}REASON")
    for id_ in ids(jobs):
        st, reason, rc = state(jobs, id_)
        if rc is not None and os.environ.get("FAKE_SQUEUE_FORGETS"):
            continue
        print(f"{id_:<20}{st:<6}{reason}")


def qstat(jobs, *args):
    """`qstat -f -F json -x ID...`: job_state Q, R or F with Exit_status (-29 past the
    walltime, FAKE_PBS_EXIT); without job history (FAKE_NO_HISTORY) a finished job is
    an unknown id, said on stderr with exit status 153, the others still printed."""
    out, unknown = {}, []
    for id_ in [a for a in args if not a.startswith("-") and a != "json"]:
        if not (Path(jobs) / f"{id_}.sub").exists():
            unknown.append(id_)
            continue
        st, _reason, rc = state(jobs, id_)
        if rc is None:
            out[id_] = {"job_state": "R" if st == "R" else "Q", "comment": "fake"}
        elif os.environ.get("FAKE_NO_HISTORY"):
            unknown.append(id_)
        else:
            if (Path(jobs) / f"{id_}.timeout").exists():
                rc = -29
            out[id_] = {"job_state": "F", "Exit_status": int(os.environ.get("FAKE_PBS_EXIT") or rc)}
    if out:
        print(json.dumps({"timestamp": int(time.time()), "pbs_version": "2022.1.0",
                          "pbs_server": "fake", "Jobs": out}, indent=4))
    for id_ in unknown:
        print(f"qstat: Unknown Job Id {id_}", file=sys.stderr)
    return 153 if unknown else 0


if __name__ == "__main__":
    what, *rest = sys.argv[1:]
    sys.exit({"monitor": monitor, "squeue": squeue, "qstat": qstat}[what](*rest))
'''

PROGRAMS = {"ssh": SSH, "rsync": RSYNC, "sbatch": SBATCH, "squeue": SQUEUE, "sacct": SACCT,
            "scancel": SCANCEL, "scontrol": SCONTROL, "qsub": QSUB, "qstat": QSTAT,
            "qdel": QDEL, "fake-scheduler.py": SCHEDULER}  # fmt: skip


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
