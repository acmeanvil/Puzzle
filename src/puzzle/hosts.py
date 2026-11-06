"""Remote hosts (ADR 0021): machines a study can run on, reached by SSH.

Hosts are the user's, never a project's: `<user data dir>/hosts.toml`, one table per
host. A host says how to reach it (an SSH alias or address, a user, a port), where runs
go there (`work_dir`), how solvers run there (`runtime`: the pixi environments in
`envs_dir`, or Docker, Podman or Apptainer with Puzzle's images), what a run may use
(`cores`, `memory_gb`), and whether a scheduler queues the runs (`scheduler`: none,
slurm or pbs, with its `partition`, `account` and default `walltime`).

Credentials are never stored: SSH takes them from the user's agent, keys and
`~/.ssh/config` (an alias there is the simplest `address`). Puzzle runs the system's
`ssh` and `rsync` (or `scp` when there is no rsync) with BatchMode, so a host that
would ask for a password is reported as unreachable instead of hanging.

A study names its host in `Study.remote` (out of the content hashes: a result is the
same wherever it was computed, so a run key never changes with the host).
"""

from __future__ import annotations

import math
import re
import shlex
import shutil
import subprocess
import tomllib
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from puzzle.errors import PuzzleError

RUNTIMES = [
    ("Pixi environments there", "native"),
    ("Docker", "docker"),
    ("Podman", "podman"),
    ("Apptainer (Singularity)", "apptainer"),
]
SCHEDULERS = [("None: run at once", "none"), ("Slurm", "slurm"), ("PBS", "pbs")]
CONNECT_TIMEOUT_S = 15
# What may go into a #SBATCH or #PBS line: these are written into the job script, so
# anything else (a space, a newline, a ;) could add directives or commands there.
WALLTIME = re.compile(r"(\d+-)?\d+(:\d{1,2}){0,2}")  # minutes, MM:SS, HH:MM:SS, D-HH[:MM[:SS]]
QUEUE = re.compile(r"[A-Za-z0-9_.+@-]+(,[A-Za-z0-9_.+@-]+)*")  # one, or a Slurm list
ACCOUNT = re.compile(r"[A-Za-z0-9_.+@-]+")


@dataclass
class Host:
    name: str
    address: str  # an SSH alias from ~/.ssh/config, a host name or an IP address
    user: str = ""  # empty: SSH's own default (the config's, else the local user)
    port: int = 0  # 0: SSH's own default
    runtime: str = "native"
    envs_dir: str = "~/puzzle/.pixi/envs"  # native: the pixi solver environments there
    work_dir: str = "~/puzzle-runs"  # where each run's case folder goes
    cores: int = 0  # 0: all of the host's (or the scheduler's allocation)
    memory_gb: float = 0.0  # 0: unknown
    scheduler: str = "none"
    partition: str = ""  # Slurm partition or PBS queue
    account: str = ""
    walltime: str = "01:00:00"  # the scheduler's limit for a run, HH:MM:SS
    images: dict[str, str] = field(default_factory=dict)  # solver -> image (or .sif path)
    keep_case: bool = False  # leave the remote case folder after the results are fetched
    python: str = ""  # the job runner's Python 3.10+ there (ADR 0067); empty: the first found
    # The commands that reach it; tests and unusual set-ups (a jump host) change them.
    ssh: list[str] = field(default_factory=lambda: ["ssh"])
    rsync: list[str] = field(default_factory=lambda: ["rsync"])

    def __post_init__(self) -> None:
        if self.runtime not in {v for _, v in RUNTIMES}:
            raise PuzzleError(f"host '{self.name}': runtime is one of native, docker, podman, "
                              f"apptainer, not '{self.runtime}'", code="invalid")  # fmt: skip
        if self.scheduler not in {v for _, v in SCHEDULERS}:
            raise PuzzleError(f"host '{self.name}': scheduler is none, slurm or pbs, not "
                              f"'{self.scheduler}'", code="invalid")  # fmt: skip
        for key, kind in (("port", int), ("cores", int), ("memory_gb", (int, float))):
            value = getattr(self, key)
            if isinstance(value, bool) or not isinstance(value, kind) or value < 0 or (
                    isinstance(value, float) and not math.isfinite(value)):  # fmt: skip
                raise PuzzleError(f"host '{self.name}': {key} is a number >= 0, not {value!r}",
                                  code="invalid", path=f"{self.name}: {key}")  # fmt: skip
        check_queue_values(f"host '{self.name}'", self.scheduler, walltime=self.walltime,
                           partition=self.partition, account=self.account)  # fmt: skip

    @property
    def target(self) -> str:
        """user@address, as ssh and rsync take it."""
        return f"{self.user}@{self.address}" if self.user else self.address

    def ssh_argv(self, *remote: str) -> list[str]:
        """ssh to this host running `remote` (one shell command line)."""
        argv = [*self.ssh, "-o", "BatchMode=yes", "-o", f"ConnectTimeout={CONNECT_TIMEOUT_S}"]
        if self.port:
            argv += ["-p", str(self.port)]
        return [*argv, self.target, *remote]

    def run(self, command: str, timeout: float = 60.0) -> subprocess.CompletedProcess:
        """A short command there, its output captured (a check, a cancel, a probe)."""
        try:
            return subprocess.run(self.ssh_argv(command), capture_output=True, text=True,
                                  timeout=timeout)  # fmt: skip
        except subprocess.TimeoutExpired:
            raise PuzzleError(f"host '{self.name}' did not answer in {timeout:g} s",
                              code="host_unreachable") from None  # fmt: skip
        except FileNotFoundError:
            raise PuzzleError(f"'{self.ssh[0]}' was not found: install OpenSSH to run on "
                              "remote hosts", code="host_unreachable") from None  # fmt: skip


def queue_value_problem(key: str, value: str, scheduler: str = "") -> str | None:
    """What is wrong with a walltime, partition or account for a job script; None if
    it is fine (empty is: the scheduler's default)."""
    if not value:
        return None
    if not isinstance(value, str):
        return f"{key} is text, not {value!r}"
    if key == "walltime":
        if not WALLTIME.fullmatch(value):
            return (f"walltime '{value}' is not a time limit: HH:MM:SS, minutes, MM:SS or "
                    "D-HH:MM:SS")  # fmt: skip
        if scheduler == "pbs" and "-" in value:
            return f"walltime '{value}': PBS takes HH:MM:SS, not days"
        return None
    pattern = QUEUE if key == "partition" else ACCOUNT
    if not pattern.fullmatch(value):
        return f"{key} '{value}' may hold only letters, digits and _ - . + @" + (
            " (a comma between partitions)" if key == "partition" else ""
        )
    return None


def check_queue_values(where: str, scheduler: str = "", **values: str) -> None:
    """Refuse a walltime, partition or account that a job script can't take as it is
    (code `invalid`, naming the field)."""
    for key, value in values.items():
        said = queue_value_problem(key, value, scheduler)
        if said is not None:
            raise PuzzleError(f"{where}: {said}", code="invalid", path=f"{where}: {key}",
                              details={"field": key})  # fmt: skip


def hosts_path() -> Path:
    from puzzle.materials import user_data_dir

    return user_data_dir() / "hosts.toml"


def load() -> dict[str, Host]:
    """The user's hosts by name (none when there is no hosts.toml)."""
    path = hosts_path()
    if not path.exists():
        return {}
    data = tomllib.loads(path.read_text())
    known = {f.name for f in fields(Host)}
    out = {}
    for name, table in data.get("host", {}).items():
        values = {k: v for k, v in table.items() if k in known and k != "name"}
        out[name] = Host(name=name, **values)
    return out


def save(hosts: dict[str, Host]) -> Path:
    """hosts.toml written whole, atomically; only what differs from the defaults."""
    import tomli_w

    blank = Host(name="", address="")
    tables = {}
    for name, h in sorted(hosts.items()):
        values = {k: v for k, v in asdict(h).items()
                  if k != "name" and (k == "address" or v != getattr(blank, k))}  # fmt: skip
        tables[name] = values
    path = hosts_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".toml.tmp")
    tmp.write_text(tomli_w.dumps({"host": tables}))
    tmp.replace(path)
    return path


def get(name: str) -> Host:
    hosts = load()
    if name not in hosts:
        known = ", ".join(sorted(hosts)) or "none yet (puzzle hosts add)"
        raise PuzzleError(f"no host '{name}'; known: {known}", code="unknown_host")
    return hosts[name]


def add(host: Host) -> Path:
    hosts = load()
    hosts[host.name] = host
    return save(hosts)


def remove(name: str) -> Path:
    hosts = load()
    if hosts.pop(name, None) is None:
        raise PuzzleError(f"no host '{name}'", code="unknown_host")
    return save(hosts)


@dataclass
class Check:
    """What a host has: reachable, its cores, and which solvers run there."""

    host: str
    reachable: bool
    message: str = ""
    cores: int | None = None
    solvers: dict[str, bool] = field(default_factory=dict)
    scheduler: bool | None = None  # its submit command found

    def words(self) -> str:
        if not self.reachable:
            return f"{self.host}: not reachable ({self.message})"
        have = [s for s, ok in self.solvers.items() if ok] or ["no solvers"]
        sched = (
            ""
            if self.scheduler is None
            else (", scheduler ready" if self.scheduler else ", scheduler command missing")
        )
        return f"{self.host}: reachable, {self.cores or '?'} cores, {', '.join(have)}{sched}"


def check(host: Host) -> Check:
    """Reach the host and list what runs there, in one SSH round trip."""
    from puzzle.jobs.remote import probe_script

    try:
        done = host.run(probe_script(host), timeout=CONNECT_TIMEOUT_S + 30)
    except PuzzleError as exc:
        return Check(host.name, False, exc.message)
    if done.returncode == 255:  # ssh's own failure: no route, refused, no key
        said = (done.stderr or "").strip().splitlines()
        return Check(host.name, False, said[-1] if said else "ssh failed")
    found = {}
    for line in done.stdout.splitlines():
        key, _, value = line.partition("=")
        found[key.strip()] = value.strip()
    from puzzle.jobs.runtime import EXECUTABLES

    solvers = {s: found.get(f"solver.{s}") == "yes" for s in [*EXECUTABLES, "fenicsx"]}
    scheduler = None if host.scheduler == "none" else found.get("scheduler") == "yes"
    cores = int(found["cores"]) if found.get("cores", "").isdigit() else None
    return Check(host.name, True, cores=cores, solvers=solvers, scheduler=scheduler)


def transfer_tool(host: Host) -> list[str] | None:
    """rsync when it is here (it copies only what changed), else None (scp)."""
    return host.rsync if shutil.which(host.rsync[0]) else None


def quote(path: str) -> str:
    """A remote path for the shell there, keeping a leading ~ expandable."""
    if path.startswith("~/"):
        return '"$HOME"/' + shlex.quote(path[2:])
    return shlex.quote(path)
