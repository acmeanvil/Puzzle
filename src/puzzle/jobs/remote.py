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
it, follows its output until it ends, and exits with the job's exit code; the job id
is kept in the case folder (`puzzle-job.id`). What went wrong is said on a line
starting `[puzzle]` that `failure` turns into a coded error: `queue_rejected`,
`walltime_exceeded`, `host_unreachable`, `transfer_failed`.
"""

from __future__ import annotations

import contextlib
import hashlib
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from puzzle.errors import PuzzleError
from puzzle.hosts import Host, quote
from puzzle.jobs.runtime import (
    DEFAULT_IMAGES,
    EXECUTABLES,
    PIXI_SOLVER_ENVS,
    THREAD_VARIABLES,
    Runtime,
)

DRIVER = "puzzle-run.sh"
JOB_SCRIPT = "puzzle-job.sh"
JOB_ID = "puzzle-job.id"
JOB_LOG = "puzzle-job.log"
MARK = "[puzzle]"
POLL_S = 5
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
        (workdir / DRIVER).write_text(self.driver(command))
        if self.host.scheduler != "none":
            (workdir / JOB_SCRIPT).write_text(self.job_script(command))
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
        return [*lines, *(f"export {name}=$T" for name in THREAD_VARIABLES)]

    def solve_lines(self, command: list[str]) -> list[str]:
        """The shell lines that run the solver in the case folder."""
        lines = self._thread_lines()
        runtime = self.host.runtime
        if runtime == "native":
            envs = quote(self.host.envs_dir)
            exe, args = _remote_program(command)
            lines += [
                f"ENVS={envs}",
                f"for e in {' '.join(REMOTE_ENVS)}; do",
                '  [ -d "$ENVS/$e/bin" ] && PATH="$ENVS/$e/bin:$PATH"',
                "done",
                f"for e in {' '.join(REMOTE_ENVS)}; do",
                f'  if [ -x "$ENVS/$e/bin/{exe.split("/")[-1]}" ]; then',
                '    export CONDA_PREFIX="$ENVS/$e"; PATH="$ENVS/$e/bin:$PATH"',
                '    [ -d "$ENVS/$e/share/elmersolver" ] && export ELMER_HOME="$ENVS/$e"',
                "    break",
                "  fi",
                "done",
                "export PATH",
                " ".join(
                    [exe if exe.startswith("$") else shlex.quote(exe), *map(shlex.quote, args)]
                ),  # fmt: skip
            ]
            return lines
        image = self.host.images.get(self.solver) or DEFAULT_IMAGES.get(self.solver)
        if image is None:
            raise PuzzleError(f"no container image for {self.solver} on {self.host.name}; "
                              "name one in the host's images", code="unsupported")  # fmt: skip
        env = " ".join(f"-e {name}=${name}" for name in THREAD_VARIABLES)
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
            submit = f"sbatch --parsable {JOB_SCRIPT}"
            state = 'squeue -h -j "$id" -o %T 2>/dev/null'
            code = 'sacct -n -X -j "$id" -o State,ExitCode 2>/dev/null | head -1'
            timeout = "TIMEOUT"
        else:
            submit = f"qsub {JOB_SCRIPT}"
            state = 'qstat "$id" >/dev/null 2>&1 && echo running'
            code = 'qstat -xf "$id" 2>/dev/null | grep -E "Exit_status|job_state"'
            timeout = "Exit_status = -29"
        return "\n".join([
            *head,
            f": > {JOB_LOG}",
            f'id=$({submit} 2>puzzle-submit.err) || {{ echo "{MARK} queue rejected: '
            f'$(cat puzzle-submit.err)"; exit 97; }}',
            'id=$(echo "$id" | cut -d";" -f1 | tr -d "[:space:]")',
            f'echo "$id" > {JOB_ID}',
            f'echo "{MARK} submitted job $id"',
            f"tail -n +1 -f {JOB_LOG} & follow=$!",
            f"while [ -n \"$({state})\" ]; do sleep {poll_seconds()}; done",
            "sleep 2; kill $follow 2>/dev/null; wait $follow 2>/dev/null",
            f"ended=$({code})",
            f'echo "{MARK} job $id ended: $ended"',
            f'case "$ended" in *"{timeout}"*) echo "{MARK} walltime exceeded"; exit 124;; esac',
            'rc=$(echo "$ended" | sed -n "s/.*Exit_status = \\([0-9-]*\\).*/\\1/p")',
            '[ -z "$rc" ] && rc=$(echo "$ended" | awk \'{print $2}\' | cut -d: -f1)',
            'exit "${rc:-1}"',
        ]) + "\n"  # fmt: skip

    def job_script(self, command: list[str]) -> str:
        """The scheduler's job script: resources as directives, then the solve."""
        r, h = self.resources, self.host
        walltime = r.walltime or h.walltime
        partition, account = r.partition or h.partition, r.account or h.account
        memory = r.memory_gb or h.memory_gb
        cpus = str(self.threads() or 1)
        if h.scheduler == "slurm":
            directives = ["#SBATCH --job-name=puzzle", f"#SBATCH --output={JOB_LOG}",
                          f"#SBATCH --ntasks={r.processes}", f"#SBATCH --cpus-per-task={cpus}",
                          f"#SBATCH --time={walltime}"]  # fmt: skip
            if memory:
                directives.append(f"#SBATCH --mem={int(memory * 1024)}M")
            if partition:
                directives.append(f"#SBATCH --partition={partition}")
            if account:
                directives.append(f"#SBATCH --account={account}")
            go = ['cd "$SLURM_SUBMIT_DIR" 2>/dev/null || cd "$(dirname "$0")"']
        else:
            directives = ["#PBS -N puzzle", f"#PBS -o {JOB_LOG}", "#PBS -j oe",
                          f"#PBS -l select=1:ncpus={r.processes * int(cpus)}"
                          f":mpiprocs={r.processes}" + (f":mem={int(memory)}gb" if memory else ""),
                          f"#PBS -l walltime={walltime}"]  # fmt: skip
            if partition:
                directives.append(f"#PBS -q {partition}")
            if account:
                directives.append(f"#PBS -A {account}")
            go = ['cd "$PBS_O_WORKDIR" 2>/dev/null || cd "$(dirname "$0")"']
        return "\n".join(["#!/bin/sh", *directives, *go, *self.solve_lines(command)]) + "\n"

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
        done = subprocess.run(argv, capture_output=True, text=True)
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


def _remote_program(command: list[str]) -> tuple[str, list[str]]:
    """The program as the host finds it: a local pixi environment's program becomes the
    same environment's there ($ENVS/<env>/bin/<name>); anything else by name on PATH."""
    first = Path(command[0])
    parts = first.parts
    if first.is_absolute() and "envs" in parts:
        i = len(parts) - 1 - parts[::-1].index("envs")
        if i + 2 < len(parts):
            env, name = parts[i + 1], first.name
            return f'"$ENVS/{env}/bin/{name}"', command[1:]
    return (first.name if first.is_absolute() else command[0]), command[1:]


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
    return None


def _unreachable(host: Host, done: subprocess.CompletedProcess) -> PuzzleError:
    said = (done.stderr or done.stdout or "").strip().splitlines()
    return PuzzleError(f"could not reach {host.name}: " + (said[-1] if said else "ssh failed"),
                       code="host_unreachable", details={"host": host.name})  # fmt: skip
