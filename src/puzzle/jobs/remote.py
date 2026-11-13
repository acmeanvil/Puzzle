"""Running a solver on a remote host (ADR 0021): stage the case, run, stream, fetch.

`RemoteRuntime` sits beside the native and container runtimes behind the same job
interface (`run_job`), so adapters are unchanged:

1. **prepare:** a driver script, `puzzle-run.sh`, is written into the case folder
   and the folder is copied to `<work_dir>/<case>` there (rsync, else scp);
2. **wrap:** the job's process is `ssh host sh <case>/puzzle-run.sh`, whose output is
   the solver's, streamed back line by line into the log and the progress parser;
3. **finish:** the case folder comes back whole (rsync, else scp), so the adapter
   reads its results as from a local run, and the remote copy is removed (unless the
   host keeps cases); the transfer's size and time go in the log;
4. **cancel:** the remote run is stopped too (its process group, or the scheduler's
   job), not just the local ssh.

Without a scheduler the driver runs the solver at once: natively from the pixi
environments in the host's `envs_dir` (the same environment names as here, so an
absolute path to a local pixi Python is turned into the remote one), or in Docker,
Podman or Apptainer with Puzzle's images. With Slurm or PBS the driver writes a job
script from the run's resources (processes, threads, memory, the walltime), submits
it, follows its output until it ends (a failed query retried, not taken as the end),
and exits with the job's exit code read from the scheduler's record of how it ended
(sacct's State, PBS's Exit_status; the job script's own exit status when there is no
record); the job id is kept in the case folder (`puzzle-job.id`), and a cancel that
comes while the submit runs stops the job as soon as its id is known. What went wrong
is said on a line starting `[puzzle]` that `failure` turns into a coded error:
`queue_rejected`, `walltime_exceeded`, `out_of_memory`, `job_lost` (node failure,
preemption, a cancel from outside, or an unknown outcome), `host_unreachable`,
`transfer_failed`.
"""

from __future__ import annotations

import contextlib
import hashlib
import re
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath

from puzzle.errors import PuzzleError
from puzzle.hosts import Host, quote
from puzzle.jobs.runtime import (
    DEFAULT_IMAGES,
    EXECUTABLES,
    PIXI_SOLVER_ENVS,
    SERIAL_BLAS,
    THREAD_VARIABLES,
    Runtime,
)

DRIVER = "puzzle-run.sh"
JOB_SCRIPT = "puzzle-job.sh"
JOB_ID = "puzzle-job.id"
JOB_LOG = "puzzle-job.log"
JOB_RC = "puzzle-job.rc"
MARK = "[puzzle]"
POLL_S = 5
# Failed scheduler queries in a row before the driver gives up on following a job
# (PUZZLE_REMOTE_RETRIES; at the poll interval, a minute).
QUERY_RETRIES = 12
# Environments on the host, searched for the solver's program (as pixi_solver_dirs here).
REMOTE_ENVS = (*PIXI_SOLVER_ENVS, "scripts")
# What never goes back and forth: the local log the job writes, Python caches.
EXCLUDE = ("solver.log", "__pycache__")


@dataclass
class Transfer:
    """One copy, for the log: which way, how much, how long."""

    direction: str
    bytes: int
    seconds: float

    def words(self) -> str:
        mb = self.bytes / 1e6
        return f"{MARK} {self.direction} {mb:.1f} MB in {self.seconds:.1f} s"


@dataclass(frozen=True)
class Resources:
    """What a run asks the host for (Study.remote, else the host's defaults)."""

    processes: int = 1
    threads: int = 0  # per process; 0: the host's cores shared out
    memory_gb: float = 0.0
    walltime: str = ""
    partition: str = ""
    account: str = ""


@dataclass(frozen=True)
class RemoteRuntime(Runtime):
    host: Host
    solver: str
    resources: Resources = field(default_factory=Resources)

    # --- where -----------------------------------------------------------------------

    def remote_dir(self, workdir: Path) -> str:
        """The case folder there: under the host's work_dir, named after the local
        case and a hash of its path, so two projects never share one."""
        here = str(workdir.resolve())
        tag = hashlib.sha256(here.encode()).hexdigest()[:10]
        base = self.host.work_dir.rstrip("/")
        base = base[2:] if base.startswith("~/") else base  # ssh, rsync and scp start at home
        return str(PurePosixPath(base) / f"{workdir.parent.name}-{workdir.name}-{tag}")

    def describe(self) -> str:
        how = self.host.runtime if self.host.scheduler == "none" else self.host.scheduler
        return f"{self.host.name} ({how})"

    def memory(self) -> int | None:
        gb = self.resources.memory_gb or self.host.memory_gb
        return int(gb * 1e9) if gb else None

    def available(self, executable: str) -> bool:
        """Whether `executable` runs there (one probe per host and program)."""
        return _available(self.host, self.solver, executable)

    # --- the job ---------------------------------------------------------------------

    def wrap(self, command: list[str], workdir: Path) -> list[str]:
        return self.host.ssh_argv(f"sh {quote(self.remote_dir(workdir) + '/' + DRIVER)}")

    def environment(self, argv: list[str]) -> dict[str, str] | None:
        return None

    def prepare(self, command: list[str], workdir: Path, say=None) -> None:
        """The driver (and job script) written, then the case copied there."""
        # Run by sh on a Unix host, so LF even when written on Windows.
        (workdir / DRIVER).write_text(self.driver(command), encoding="utf-8", newline="\n")
        if self.host.scheduler != "none":
            (workdir / JOB_SCRIPT).write_text(
                self.job_script(command), encoding="utf-8", newline="\n"
            )
        remote = self.remote_dir(workdir)
        made = self.host.run(f"mkdir -p {quote(remote)}")
        if made.returncode != 0:
            raise _unreachable(self.host, made)
        moved = self._copy(workdir, remote, up=True)
        if say is not None:
            say(moved.words())

    def finish(self, workdir: Path, say=None) -> None:
        """The case folder back whole; the remote copy removed unless kept."""
        remote = self.remote_dir(workdir)
        moved = self._copy(workdir, remote, up=False)
        if say is not None:
            say(moved.words())
        if not self.host.keep_case:
            self.host.run(f"rm -rf {quote(remote)}")

    def cancel(self, workdir: Path) -> None:
        """Stop the run there: its scheduler job, or its process group."""
        remote = quote(self.remote_dir(workdir))
        stop = {"slurm": "scancel", "pbs": "qdel"}.get(self.host.scheduler)
        parts = [f"cd {remote} 2>/dev/null || exit 0"]
        if stop:
            parts.append(f'[ -f {JOB_ID} ] && {stop} "$(cat {JOB_ID})"')
        # The driver's whole process group, so the solver and its children stop too.
        parts.append('[ -s puzzle-run.pgid ] && kill -TERM -- "-$(cat puzzle-run.pgid)" '
                     '2>/dev/null; [ -f puzzle-run.pid ] && kill -TERM "$(cat puzzle-run.pid)" '
                     "2>/dev/null")  # fmt: skip
        with contextlib.suppress(PuzzleError):  # unreachable: the local ssh stops anyway
            self.host.run("; ".join(parts) + "; true", timeout=30)

    # --- scripts ---------------------------------------------------------------------

    def threads(self) -> int:
        """Threads per process: the resources', else the host's cores shared out; 0
        when the host doesn't say (the driver counts its cores then)."""
        if self.resources.threads:
            return self.resources.threads
        if self.host.cores:
            return max(1, self.host.cores // max(1, self.resources.processes))
        return 0

    def _thread_lines(self) -> list[str]:
        n = self.threads()
        if n:
            lines = [f"T={n}"]
        else:  # the cores there, shared among the processes
            p = max(1, self.resources.processes)
            lines = ["T=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 1)",
                     f"T=$((T / {p})); [ \"$T\" -ge 1 ] || T=1"]  # fmt: skip
        serial = [f"export {name}={value}" for name, value in SERIAL_BLAS.items()]
        return [*lines, *(f"export {name}=$T" for name in THREAD_VARIABLES), *serial]

    def solve_lines(self, command: list[str]) -> list[str]:
        """The shell lines that run the solver in the case folder."""
        lines = self._thread_lines()
        runtime = self.host.runtime
        if runtime == "native":
            envs = quote(self.host.envs_dir)
            exe, name, env, args = _remote_program(command)
            # The program's own environment when it came from one, else the first holding it.
            where = shlex.quote(env) if env else " ".join(REMOTE_ENVS)
            lines += [
                f"ENVS={envs}",
                f"for e in {' '.join(REMOTE_ENVS)}; do",
                '  [ -d "$ENVS/$e/bin" ] && PATH="$ENVS/$e/bin:$PATH"',
                "done",
                f"for e in {where}; do",
                f'  if [ -x "$ENVS/$e/bin/"{shlex.quote(name)} ]; then',
                '    export CONDA_PREFIX="$ENVS/$e"; PATH="$ENVS/$e/bin:$PATH"',
                '    [ -d "$ENVS/$e/share/elmersolver" ] && export ELMER_HOME="$ENVS/$e"',
                "    break",
                "  fi",
                "done",
                "export PATH",
                " ".join([exe, *map(shlex.quote, args)]),
            ]
            return lines
        image = self.host.images.get(self.solver) or DEFAULT_IMAGES.get(self.solver)
        if image is None:
            raise PuzzleError(f"no container image for {self.solver} on {self.host.name}; "
                              "name one in the host's images", code="unsupported")  # fmt: skip
        env = " ".join(f"-e {name}=${name}" for name in (*THREAD_VARIABLES, *SERIAL_BLAS))
        argv = " ".join(map(shlex.quote, command))
        if runtime == "apptainer":
            where = image if image.endswith(".sif") or "://" in image else f"docker://{image}"
            lines.append(f'apptainer exec --cleanenv --bind "$PWD":/work --pwd /work '
                         f"{shlex.quote(where)} {argv}")  # fmt: skip
        else:
            lines.append(f'{runtime} run --rm -v "$PWD":/work -w /work --user "$(id -u):$(id -g)" '
                         f"-e HOME=/tmp {env} {shlex.quote(image)} {argv}")  # fmt: skip
        return lines

    def driver(self, command: list[str]) -> str:
        """puzzle-run.sh: the solve at once, or the scheduler's submit and follow."""
        head = ["#!/bin/sh", 'cd "$(dirname "$0")" || exit 96', "echo $$ > puzzle-run.pid",
                # its process group, which a cancel stops whole (sshd made it the leader)
                'ps -o pgid= -p $$ 2>/dev/null | tr -d " " > puzzle-run.pgid']  # fmt: skip
        scheduler = self.host.scheduler
        if scheduler == "none":
            # Its own process group, so a cancel stops the solver and its children.
            # sshd starts it as a session leader: kill -- -pid stops the solver too.
            return "\n".join([*head, *self.solve_lines(command)]) + "\n"
        if scheduler == "slurm":
            submit, stop = f"sbatch --parsable {JOB_SCRIPT}", "scancel"
        else:
            submit, stop = f"qsub {JOB_SCRIPT}", "qdel"
        return "\n".join([
            *head,
            f": > {JOB_LOG}",
            # A cancel (TERM to this process group) stops the job once its id is known:
            # at once if it is, else as soon as the submit returns it.
            'id=""; follow=""; stopping=""',
            f'puzzle_stop() {{ stopping=1; [ -n "$follow" ] && kill $follow 2>/dev/null; '
            f'if [ -n "$id" ]; then {stop} "$id" >/dev/null 2>&1; '
            f'echo "{MARK} cancelled job $id"; exit 130; fi; }}',
            "trap puzzle_stop TERM INT HUP",
            # The submit itself ignores the cancel, so a job it makes is never orphaned.
            f"out=$(trap '' TERM INT HUP; {submit} 2>puzzle-submit.err) || {{ "
            f'[ -n "$stopping" ] && exit 130; '
            f'echo "{MARK} queue rejected: $(cat puzzle-submit.err)"; exit 97; }}',
            'id=$(echo "$out" | cut -d";" -f1 | tr -d "[:space:]")',
            f'echo "$id" > {JOB_ID}.tmp && mv {JOB_ID}.tmp {JOB_ID}',
            f'[ -n "$stopping" ] && {{ {stop} "$id" >/dev/null 2>&1; '
            f'echo "{MARK} cancelled job $id"; exit 130; }}',
            f'echo "{MARK} submitted job $id"',
            f"tail -n +1 -f {JOB_LOG} & follow=$!",
            *_follow_lines(scheduler),
            'sleep 2; kill $follow 2>/dev/null; wait $follow 2>/dev/null; follow=""',
            *_outcome_lines(scheduler),
        ]) + "\n"  # fmt: skip

    def job_script(self, command: list[str]) -> str:
        """The scheduler's job script: resources as directives, then the solve."""
        r, h = self.resources, self.host
        walltime = r.walltime or h.walltime
        partition, account = r.partition or h.partition, r.account or h.account
        from puzzle.hosts import check_queue_values

        check_queue_values(f"the run on {h.name}", h.scheduler, walltime=walltime,
                           partition=partition, account=account)  # fmt: skip
        memory = float(r.memory_gb or h.memory_gb or 0)
        processes, cpus = int(r.processes), str(self.threads() or 1)
        if h.scheduler == "slurm":
            directives = ["#SBATCH --job-name=puzzle", f"#SBATCH --output={JOB_LOG}",
                          f"#SBATCH --ntasks={processes}",
                          f"#SBATCH --cpus-per-task={cpus}"]  # fmt: skip
            if walltime:
                directives.append(f"#SBATCH --time={walltime}")
            if memory:
                directives.append(f"#SBATCH --mem={int(memory * 1024)}M")
            if partition:
                directives.append(f"#SBATCH --partition={partition}")
            if account:
                directives.append(f"#SBATCH --account={account}")
            go = ['cd "$SLURM_SUBMIT_DIR" 2>/dev/null || cd "$(dirname "$0")"']
        else:
            directives = ["#PBS -N puzzle", f"#PBS -o {JOB_LOG}", "#PBS -j oe",
                          f"#PBS -l select=1:ncpus={processes * int(cpus)}"
                          f":mpiprocs={processes}"
                          + (f":mem={int(memory)}gb" if memory else "")]  # fmt: skip
            if walltime:
                directives.append(f"#PBS -l walltime={walltime}")
            if partition:
                directives.append(f"#PBS -q {partition}")
            if account:
                directives.append(f"#PBS -A {account}")
            go = ['cd "$PBS_O_WORKDIR" 2>/dev/null || cd "$(dirname "$0")"']
        # The solver's exit status kept beside the case: the outcome when the scheduler
        # keeps no accounting (sacct off, PBS without job history).
        keep = [f"rc=$?; echo $rc > {JOB_RC}.tmp && mv {JOB_RC}.tmp {JOB_RC}", 'exit "$rc"']
        lines = ["#!/bin/sh", *directives, *go, *self.solve_lines(command), *keep]
        return "\n".join(lines) + "\n"

    # --- copying ---------------------------------------------------------------------

    def _copy(self, workdir: Path, remote: str, up: bool) -> Transfer:
        from puzzle.hosts import transfer_tool

        start = time.monotonic()
        tool = transfer_tool(self.host)
        far = f"{self.host.target}:{remote}/"
        ssh = " ".join(map(shlex.quote, self.host.ssh_argv()[:-1]))  # ssh and its options
        if tool is not None:
            argv = [*tool, "-a", "--delete" if up else "--update", "-e", ssh,
                    *[f"--exclude={x}" for x in EXCLUDE]]  # fmt: skip
            argv += [f"{workdir}/", far] if up else [far, f"{workdir}/"]
        else:
            scp = ["scp", "-q", "-r", "-o", "BatchMode=yes"]
            if self.host.port:
                scp += ["-P", str(self.host.port)]
            argv = [*scp, f"{workdir}/.", far] if up else [*scp, f"{far}.", f"{workdir}/"]
        done = subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
        if done.returncode != 0:
            said = (done.stderr or done.stdout).strip().splitlines()[-3:]
            raise PuzzleError(
                f"copying the case {'to' if up else 'from'} {self.host.name} failed: "
                + " / ".join(said), code="transfer_failed",
                details={"host": self.host.name, "remote": remote},
            )  # fmt: skip
        size = sum(p.stat().st_size for p in workdir.rglob("*") if p.is_file())
        return Transfer("sent" if up else "fetched", size, time.monotonic() - start)


def poll_seconds() -> int:
    """How often the driver asks the scheduler (PUZZLE_REMOTE_POLL_S; tests use 1)."""
    import os

    return max(1, int(os.environ.get("PUZZLE_REMOTE_POLL_S") or POLL_S))


def query_retries() -> int:
    """Failed queries before the driver gives up on a job (PUZZLE_REMOTE_RETRIES)."""
    import os

    return max(1, int(os.environ.get("PUZZLE_REMOTE_RETRIES") or QUERY_RETRIES))


def _remote_program(command: list[str]) -> tuple[str, str, str, list[str]]:
    """The program as the host finds it: (the shell word that runs it, its name, its
    pixi environment or "", the arguments). A local pixi environment's program becomes
    the same environment's there ("$ENVS"/<env>/bin/<name>, the rest quoted, so an
    envs_dir with spaces works); anything else is found by name on PATH."""
    raw = command[0]
    # Read as written, whatever this machine is: a Windows path (C:\...\envs\fenicsx\
    # python.exe) names the host's fenicsx/bin/python as a POSIX one does.
    windows = bool(re.match(r"^[A-Za-z]:[\\/]", raw)) or ("\\" in raw and "/" not in raw)
    first = PureWindowsPath(raw) if windows else PurePosixPath(raw)
    exe = first.stem if first.suffix.lower() == ".exe" else first.name
    parts = first.parts
    if first.is_absolute() and "envs" in parts:
        i = len(parts) - 1 - parts[::-1].index("envs")
        if i + 2 < len(parts):
            env = parts[i + 1]
            return '"$ENVS"/' + shlex.quote(f"{env}/bin/{exe}"), exe, env, command[1:]
    name = exe if first.is_absolute() else raw

    return shlex.quote(name), name, "", command[1:]


# Slurm states of a job not yet over; PBS job_state letters likewise.
SLURM_ACTIVE = (
    "PENDING RUNNING CONFIGURING COMPLETING REQUEUED REQUEUE_FED REQUEUE_HOLD "
    "RESIZING SUSPENDED SIGNALING STAGE_OUT STOPPED SPECIAL_EXIT RESV_DEL_HOLD"
)
PBS_ACTIVE = "B E H M Q R S T U W"


def _follow_lines(scheduler: str) -> list[str]:
    """Wait while the job is queued or running. A query that fails (the controller
    busy, a network blip) is retried, not taken as the job's end: only the scheduler
    saying the job is unknown or over ends the wait."""
    if scheduler == "slurm":
        query = [
            'st=$(squeue -h -j "$id" -o %T 2>puzzle-queue.err); q=$?',
            # Slurm forgets a finished job after a while: "Invalid job id" is its end.
            'if [ $q -ne 0 ] && grep -qi "invalid job id" puzzle-queue.err; then st=""; q=0; fi',
            # squeue failed: accounting may still say whether it is running.
            'if [ $q -ne 0 ]; then st=$(sacct -n -X -P -j "$id" -o State 2>/dev/null '
            '| head -1 | cut -d" " -f1); [ -n "$st" ] && q=0; fi',
        ]
        active = SLURM_ACTIVE
    else:
        query = [
            'all=$(qstat -f "$id" 2>puzzle-queue.err); q=$?',
            'st=$(echo "$all" | sed -n "s/^ *job_state = //p" | head -1)',
            'if [ $q -ne 0 ] && grep -qiE "unknown job|has finished" puzzle-queue.err; then '
            'st=""; q=0; fi',
        ]
        active = PBS_ACTIVE
    return [
        "fail=0",
        "while :; do",
        *(f"  {line}" for line in query),
        '  if [ -n "$st" ]; then',
        f'    case " {active} " in *" $st "*) fail=0; sleep {poll_seconds()}; continue;; esac',
        "    break  # over: completed, failed, finished",
        "  fi",
        "  [ $q -eq 0 ] && break  # no longer known: over",
        "  fail=$((fail + 1))",
        f"  if [ $fail -ge {query_retries()} ]; then",
        "    sleep 2; kill $follow 2>/dev/null",
        f'    echo "{MARK} job $id outcome unknown: lost track of it, the scheduler did '
        'not answer ($(tr "\\n" " " < puzzle-queue.err))"; exit 99',
        "  fi",
        f"  sleep {poll_seconds()}",
        "done",
    ]


def _outcome_lines(scheduler: str) -> list[str]:
    """The job's exit code from the scheduler's own record of how it ended: a walltime,
    a memory limit or the scheduler's own ending (node failure, preemption) each said
    on a [puzzle] line with an exit code of its own; the job script's exit status when
    the scheduler keeps no record."""
    walltime = f'echo "{MARK} walltime exceeded"; exit 124'
    memory = f'echo "{MARK} out of memory: job $id used more than it asked for"; exit 98'
    ended = f'echo "{MARK} the scheduler ended job $id: $why"; exit 99'
    no_record = [
        f"if [ -s {JOB_RC} ]; then rc=$(cat {JOB_RC}); "
        f'echo "{MARK} job $id: no scheduler record; its script exited $rc"; '
        f'case "$rc" in ""|*[!0-9]*) rc=1;; esac; exit "$rc"; fi',
        f'echo "{MARK} job $id outcome unknown: the scheduler keeps no record of it '
        "(accounting or job history off) and its script wrote no exit status "
        '(stopped, out of memory?)"; exit 99',
    ]
    if scheduler == "slurm":
        return [
            'ended=$(sacct -n -X -P -j "$id" -o State,ExitCode 2>/dev/null | head -1)',
            f'echo "{MARK} job $id ended: ${{ended:-no accounting record}}"',
            'state=$(echo "$ended" | cut -d"|" -f1); code=$(echo "$ended" | cut -d"|" -f2)',
            'rc=${code%%:*}; sig=${code#*:}; case "$rc" in ""|*[!0-9]*) rc=1;; esac',
            'case "$state" in',
            '  COMPLETED*) exit "$rc";;',
            f"  TIMEOUT*|DEADLINE*) {walltime};;",
            f"  OUT_OF_MEMORY*) {memory};;",
            f'  FAILED*) [ "$rc" -ne 0 ] && exit "$rc"; why="FAILED (signal $sig)"; {ended};;',
            '  "") ;;',
            f'  *) why="$state"; {ended};;',
            "esac",
            *no_record,
        ]
    return [
        # PBS Pro keeps finished jobs with -x (job history); Torque in qstat -f a while.
        'all=$(qstat -xf "$id" 2>/dev/null || qstat -f "$id" 2>/dev/null)',
        'rc=$(echo "$all" | sed -n "s/^ *[Ee]xit_status = \\(-\\{0,1\\}[0-9]*\\).*/\\1/p" '
        "| head -1)",
        f'echo "{MARK} job $id ended: exit status ${{rc:-unknown}}"',
        f'grep -q "job killed: walltime" {JOB_LOG} 2>/dev/null && {{ {walltime}; }}',
        f'grep -qE "job killed: (mem|vmem)" {JOB_LOG} 2>/dev/null && {{ {memory}; }}',
        'case "$rc" in',
        f"  -29) {walltime};;",
        f"  -27|-26) {memory};;",
        f'  -*) why="PBS exit status $rc"; {ended};;',
        "  '') ;;",
        f'  *) [ "$rc" -ge 256 ] && {{ why="signal $((rc - 256))"; {ended}; }}; exit "$rc";;',
        "esac",
        *no_record,
    ]


_PROBED: dict[tuple, bool] = {}


def _available(host: Host, solver: str, executable: str) -> bool:
    key = (host.name, host.address, host.runtime, executable)
    if key not in _PROBED:
        if host.runtime == "native":
            envs = quote(host.envs_dir)
            test = " || ".join(f"[ -x {envs}/{e}/bin/{shlex.quote(executable)} ]"
                               for e in REMOTE_ENVS)  # fmt: skip
            line = f"{test} || command -v {shlex.quote(executable)} >/dev/null"
        else:
            line = "true"  # the image carries its solver; a missing one fails the run
        try:
            _PROBED[key] = host.run(line, timeout=30).returncode == 0
        except PuzzleError:
            _PROBED[key] = False
    return _PROBED[key]


def probe_script(host: Host) -> str:
    """One shell line set that prints key=value pairs: cores, each solver, scheduler."""
    envs = quote(host.envs_dir)
    lines = ['echo "cores=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null)"']
    programs = {**EXECUTABLES, "fenicsx": "python"}
    for solver, exe in programs.items():
        if host.runtime == "native":
            envs_for = ["fenicsx"] if solver == "fenicsx" else list(REMOTE_ENVS)
            test = " || ".join(f"[ -x {envs}/{e}/bin/{exe} ]" for e in envs_for)
            if solver != "fenicsx":
                test += f" || command -v {exe} >/dev/null 2>&1"
        else:
            engine = "apptainer" if host.runtime == "apptainer" else host.runtime
            test = f"command -v {engine} >/dev/null 2>&1"
        lines.append(f'if {test}; then echo "solver.{solver}=yes"; '
                     f'else echo "solver.{solver}=no"; fi')  # fmt: skip
    if host.scheduler != "none":
        submit = "sbatch" if host.scheduler == "slurm" else "qsub"
        lines.append(f'if command -v {submit} >/dev/null 2>&1; then echo scheduler=yes; '
                     "else echo scheduler=no; fi")  # fmt: skip
    return "; ".join(lines)


def failure(log_tail: list[str], returncode: int | None, host: Host) -> PuzzleError | None:
    """A remote run's failure as a coded error, from the driver's [puzzle] lines and
    ssh's exit code; None when it is the solver's own failure."""
    said = [line for line in log_tail if line.startswith(MARK)]
    text = " ".join(said)
    if returncode == 255 and not said:
        return PuzzleError(f"lost the connection to {host.name}: " + " / ".join(log_tail[-2:]),
                           code="host_unreachable", details={"host": host.name})  # fmt: skip
    if returncode == 97 or "queue rejected" in text:
        return PuzzleError(f"{host.name}'s scheduler rejected the job: {text}",
                           code="queue_rejected", details={"host": host.name})  # fmt: skip
    if returncode == 124 or "walltime exceeded" in text:
        return PuzzleError(f"the job ran past its walltime on {host.name}; ask for more "
                           "(the study's Run on settings) or a coarser mesh",
                           code="walltime_exceeded", details={"host": host.name})  # fmt: skip
    if "out of memory" in text:
        return PuzzleError(f"the job ran out of memory on {host.name}; ask for more (the "
                           "study's Run on settings) or a coarser mesh: " + text,
                           code="out_of_memory", details={"host": host.name})  # fmt: skip
    if "outcome unknown" in text or "the scheduler ended job" in text:
        return PuzzleError(f"the job on {host.name} did not finish normally: {text}",
                           code="job_lost", details={"host": host.name})  # fmt: skip
    return None


def _unreachable(host: Host, done: subprocess.CompletedProcess) -> PuzzleError:
    said = (done.stderr or done.stdout or "").strip().splitlines()
    return PuzzleError(f"could not reach {host.name}: " + (said[-1] if said else "ssh failed"),
                       code="host_unreachable", details={"host": host.name})  # fmt: skip
