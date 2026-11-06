"""How the runner starts and follows a job: itself, or through a scheduler via PSI/J.

Both run the same `puzzle-solve.sh`, written into the case folder. It sends its output
to `solver.log` and writes its exit status to `puzzle-job.rc` (atomically) when it ends,
so a job's outcome survives the runner itself stopping: whoever looks next reads it.
"""

from __future__ import annotations

import contextlib
import importlib.util
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
import types
from datetime import timedelta
from pathlib import Path

from puzzle.runner.jobs import Job

SCRIPT = "puzzle-solve.sh"
RC = "puzzle-job.rc"
KILL_AFTER_S = 10.0  # a cancelled job's grace before SIGKILL


def solve_script(job: Job) -> str:
    """The job's shell script: its own lines, or its argv quoted, inside a function so
    that the exit status is written whatever the body does."""
    spec = job.spec
    if spec.get("script"):
        body = list(spec["script"])
    else:
        body = [" ".join(shlex.quote(a) for a in spec["argv"])]
    log = shlex.quote(spec.get("log", "solver.log"))
    return "\n".join([
        "#!/bin/sh",
        f"exec >> {log} 2>&1",
        "puzzle_solve() {",
        *[f"  {line}" for line in body],
        "}",
        "puzzle_solve",
        "rc=$?",
        f'printf "%s\\n" "$rc" > {RC}.tmp && mv {RC}.tmp {RC}',
        'exit "$rc"',
    ]) + "\n"  # fmt: skip


def write_script(job: Job) -> Path:
    folder = Path(job.spec["dir"])
    path = folder / SCRIPT
    path.write_text(solve_script(job), encoding="utf-8")
    (folder / RC).unlink(missing_ok=True)
    return path


def read_rc(job: Job) -> int | None:
    try:
        return int((Path(job.spec["dir"]) / RC).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class ProcessBackend:
    """Jobs without a scheduler: a process in its own session, so it outlives the runner
    and a cancel stops the solver and its children together."""

    name = "none"

    def __init__(self):
        self.procs: dict[str, subprocess.Popen] = {}
        self.kill_at: dict[str, float] = {}

    def start(self, job: Job) -> None:
        script = write_script(job)
        env = job.spec.get("env") or None
        proc = subprocess.Popen(
            ["/bin/sh", script.name], cwd=job.spec["dir"], env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )  # fmt: skip
        self.procs[job.id] = proc
        job.native_id = str(proc.pid)
        job.state, job.started = "running", time.time()

    def attach(self, job: Job) -> None:
        """Nothing to do: `poll` finds the process by its id, or its exit status."""

    def poll(self, job: Job) -> None:
        proc = self.procs.get(job.id)
        if proc is not None:
            done = proc.poll() is not None
        else:
            done = not (job.native_id and _alive(int(job.native_id)))
        if job.cancel_requested and not done:
            at = self.kill_at.get(job.id)
            if at is not None and time.time() > at:
                self._signal(job, signal.SIGKILL)
            return
        if not done:
            return
        self.procs.pop(job.id, None)
        self.kill_at.pop(job.id, None)
        rc = read_rc(job)
        job.ended = time.time()
        if job.cancel_requested:
            job.state, job.exit_code = "cancelled", rc
        elif rc is None:
            job.state = "job_lost"
            job.message = "the solve stopped without writing its exit status (killed?)"
        else:
            job.exit_code = rc
            job.state = "completed" if rc == 0 else "failed"

    def cancel(self, job: Job) -> None:
        job.cancel_requested = True
        self._signal(job, signal.SIGTERM)
        self.kill_at[job.id] = time.time() + KILL_AFTER_S

    def _signal(self, job: Job, sig: int) -> None:
        if job.native_id:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(int(job.native_id), sig)


def parse_walltime(text: str) -> timedelta | None:
    """[D-]HH:MM:SS, MM:SS or minutes (the forms hosts.toml takes)."""
    if not text:
        return None
    days = 0
    if "-" in text:
        d, text = text.split("-", 1)
        days = int(d)
    parts = [int(p) for p in text.split(":")]
    if len(parts) == 1:
        h, m, s = 0, parts[0], 0
    elif len(parts) == 2:
        h, m, s = 0, parts[0], parts[1]
    else:
        h, m, s = parts[-3:]
    return timedelta(days=days, hours=h, minutes=m, seconds=s)


#: sacct's states for a job not over yet. Accounting can lag the queue: a job gone from
#: squeue may still be RUNNING in sacct for a few seconds, which is not its end.
NOT_ENDED = frozenset({"PENDING", "RUNNING", "COMPLETING", "CONFIGURING", "REQUEUED",
                       "RESIZING", "SUSPENDED", "STAGE_OUT", "SIGNALING", "STOPPED"})  # fmt: skip
ACCOUNTING_WAIT_S = 60.0  # how long a lagging record is asked again before it is let go
QUEUE_PATIENCE_S = 600.0  # how long a queue that doesn't answer is waited out
#: PSI/J's words when it fails jobs because the queue query failed, not the jobs.
POLL_FAILURES = ("Failed to poll for job status", "Error updating job statuses")


class PsijBackend:
    """Jobs through a batch scheduler (Slurm, PBS, LSF) by PSI/J, which runs `sbatch`,
    `squeue` and the like itself, so this runs on the scheduler's login node. PSI/J's
    states come from the queue; a job that has left it is completed, with the exit
    status of its script. How it ended is then read from the scheduler's own record
    (`final_state`), which tells out of memory, node failures and timeouts apart.

    What PSI/J is shielded from here: a queue listing it can't parse (a reason or state
    it doesn't know, a reason with spaces, PBS ids that have left the history), which
    it would take as every job failing; its failing all jobs when the queue query fails
    a few times (they are attached again, until QUEUE_PATIENCE_S); and the files it
    leaves (each job's `.out`, and an attached job's submit script)."""

    def __init__(self, name: str, root: Path, poll_s: float = 10, error_threshold: int = 12):
        _without_local_executor()
        import psij
        from psij.executors.batch.batch_scheduler_executor import BatchSchedulerExecutorConfig

        self.name = name
        self.psij = psij
        work = Path(root) / "psij"
        work.mkdir(parents=True, exist_ok=True)
        config = BatchSchedulerExecutorConfig(
            work_directory=work, queue_polling_interval=poll_s,
            initial_queue_polling_delay=min(1, poll_s),
            queue_polling_error_threshold=error_threshold,
        )  # fmt: skip
        self.executor = psij.JobExecutor.get_instance(name, config=config)
        if name == "slurm":
            self.executor.parse_status_output = _slurm_status(self.executor)
        elif name == "pbs":
            self.executor.parse_status_output = _pbs_status(self.executor)
        self.jobs: dict[str, object] = {}
        self.states: dict[str, object] = {}
        self.ended_at: dict[str, float] = {}  # when PSI/J first said a job was over
        self.trouble: dict[str, float] = {}  # since when the queue hasn't answered
        self.lock = threading.Lock()

    def _spec(self, job: Job):
        psij = self.psij
        r = job.spec.get("resources") or {}
        resources = psij.ResourceSpecV1(
            process_count=max(1, int(r.get("processes") or 1)),
            cpu_cores_per_process=max(1, int(r.get("threads") or 1)),
            gpu_cores_per_process=int(r.get("gpus") or 0) or None,
            memory=int(float(r.get("memory_gb") or 0) * 1024**3) or None,
        )  # fmt: skip
        attributes = psij.JobAttributes(
            queue_name=r.get("partition") or None, account=r.get("account") or None,
            **({"duration": d} if (d := parse_walltime(r.get("walltime") or "")) else {}),
        )  # fmt: skip
        env = {k: v for k, v in (job.spec.get("env") or {}).items()}
        # One copy of the script (the "single" launcher): it starts the solver's own
        # processes (mpirun) itself, as without a scheduler.
        return psij.JobSpec(
            executable="/bin/sh", arguments=[SCRIPT], directory=job.spec["dir"],
            name="puzzle", environment=env or None, resources=resources,
            attributes=attributes, launcher="single",
        )  # fmt: skip

    def _job(self, job: Job, spec=None):
        """A PSI/J job named for ours, so its submit script is `<our id>.job` and
        PSI/J's own clean-up finds it after an attach too."""
        pjob = self.psij.Job(spec)
        pjob._id = job.id

        def changed(_job, status) -> None:
            with self.lock:
                self.states[job.id] = status

        pjob.set_job_status_callback(changed)
        self.jobs[job.id] = pjob
        return pjob

    def start(self, job: Job) -> None:
        write_script(job)
        pjob = self._job(job, self._spec(job))
        self.executor.submit(pjob)
        job.native_id = str(pjob.native_id)

    def attach(self, job: Job) -> None:
        with self.lock:
            self.states.pop(job.id, None)
        self.executor.attach(self._job(job), job.native_id)

    def poll(self, job: Job) -> None:
        with self.lock:
            status = self.states.get(job.id)
        if status is None:
            return
        state = status.state.name
        lost_queue = state == "FAILED" and (status.message or "").startswith(POLL_FAILURES)
        if not lost_queue:
            self.trouble.pop(job.id, None)
        if state == "ACTIVE" and job.state == "queued":
            job.state, job.started = "running", time.time()
        if not status.state.final:
            return
        now = time.time()
        if lost_queue:  # the query failed, not the job: follow it again, for a while
            since = self.trouble.setdefault(job.id, now)
            if now - since < QUEUE_PATIENCE_S:
                self.attach(job)
                return
        rc = read_rc(job)
        if rc is None and not lost_queue:
            rc = status.exit_code  # PSI/J's own exit code file, when ours is missing
        # sacct's ExitCode is PSI/J's job script's, which ends `echo $? > ID.ec` and so
        # exits 0 whatever the solve did: no word on the solve. Its exit status is ours.
        said, _code, words = final_state(self.name, job.native_id)
        if said in NOT_ENDED:  # accounting hasn't caught up: ask again next time
            if now - self.ended_at.setdefault(job.id, now) < ACCOUNTING_WAIT_S:
                return
            said, words = None, ""
        job.ended = now
        if job.started is None:
            job.started = job.ended
        message = words or (status.message or "")
        job.state, job.exit_code, job.message = classify(
            said, None, rc, state, job.cancel_requested, message
        )
        if lost_queue and job.state == "job_lost":
            job.message = f"the scheduler's queue stopped answering ({status.message})"
        self._clean(job)

    def _clean(self, job: Job) -> None:
        """PSI/J's files for the job, once it has ended: its output (only the launcher's
        own lines: the solve writes solver.log), exit code, node list and submit script."""
        work = Path(self.executor.work_directory)
        out = work / f"{job.native_id}.out"
        if job.exit_code is None and job.state != "cancelled" and not job.message:
            with contextlib.suppress(OSError):  # the job never got to our script: why
                lines = [x for x in out.read_text(errors="replace", encoding="utf-8").splitlines()
                         if x.strip() and x != "_PSI_J_LAUNCHER_DONE"]  # fmt: skip
                job.message = "; ".join(lines[-3:])
        keep = getattr(self.executor.config, "keep_files", False)  # PSIJ_BATCH_KEEP_FILES
        for name in [] if keep else (f"{job.native_id}.out", f"{job.native_id}.ec",
                     f"{job.native_id}.nodefile", f"{job.id}.job"):  # fmt: skip
            (work / name).unlink(missing_ok=True)
        self.jobs.pop(job.id, None)
        self.states.pop(job.id, None)
        self.ended_at.pop(job.id, None)
        self.trouble.pop(job.id, None)

    def cancel(self, job: Job) -> None:
        job.cancel_requested = True
        pjob = self.jobs.get(job.id)
        if pjob is not None:
            with contextlib.suppress(Exception):
                self.executor.cancel(pjob)
            return
        stop = {"slurm": ["scancel", "-Q"], "pbs": ["qdel"], "lsf": ["bkill"]}[self.name]
        _run([*stop, job.native_id])


def _without_local_executor() -> None:
    """Let PSI/J load without psutil. Its `psij.executors` package imports the local
    executor, which needs psutil (compiled, so not in the runner's pure-Python bundle),
    and every batch executor imports that package: without psutil none would load. The
    package is stood in for by a bare one over the same folder, before PSI/J's plugin
    discovery (at `import psij`) loads them; the local executor alone stays unloadable."""
    if "psij.executors" in sys.modules or importlib.util.find_spec("psutil") is not None:
        return
    spec = importlib.util.find_spec("psij")
    if spec is None or not spec.submodule_search_locations:
        return  # no PSI/J: `import psij` says so
    package = types.ModuleType("psij.executors")
    package.__path__ = [str(Path(next(iter(spec.submodule_search_locations))) / "executors")]
    package.__package__ = "psij.executors"
    sys.modules["psij.executors"] = package


def _slurm_status(executor):
    """PSI/J's reading of `squeue -O JobArrayID,StateCompact,Reason`, made tolerant: its
    own asserts three columns and a state and reason it knows, so one job of the user's
    with a reason such as `OutOfMemory` or `ReqNodeNotAvail, UnavailableNodes:n1` made
    every poll fail (and, after a few, every job FAILED). A state it doesn't know is
    taken as queued (a job still listed has not ended)."""
    from psij import JobState, JobStatus
    from psij.executors.batch.batch_scheduler_executor import check_status_exit_code

    states, reasons = executor._STATE_MAP, executor._REASONS_MAP

    def parse(exit_code: int, out: str) -> dict:
        check_status_exit_code("squeue", exit_code, out)
        found = {}
        for line in out.splitlines()[1:]:
            cols = line.split(None, 2)
            if len(cols) < 2:
                continue
            state = states.get(cols[1], JobState.QUEUED)
            reason = cols[2].strip() if len(cols) > 2 else ""
            message = reasons.get(reason, reason) if state == JobState.FAILED else None
            found[cols[0]] = JobStatus(state, message=message or None)
        return found

    return parse


def _pbs_status(executor):
    """PSI/J's reading of `qstat -f -F json -x IDS`, tolerating the ids qstat no longer
    knows (finished, with job history off or expired): qstat prints the rest and exits
    153, which PSI/J took as every job failing. An id left out is over to PSI/J, which
    then reads its exit code file; `final_state` and our own exit status decide."""
    import json

    parse_all = executor.parse_status_output
    gone = ("Unknown Job Id", "has finished")

    def parse(exit_code: int, out: str) -> dict:
        if exit_code == 0 or not any(g in out for g in gone):
            return parse_all(exit_code, out)
        start = out.find("{")
        report = json.JSONDecoder().raw_decode(out[start:])[0] if start >= 0 else {}
        return parse_all(0, json.dumps({"Jobs": report.get("Jobs") or {}}))

    return parse


def _run(argv: list[str]) -> str | None:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout if done.returncode == 0 else None


def final_state(scheduler: str, native_id: str) -> tuple[str | None, int | None, str]:
    """How the scheduler says a job ended: (state, exit code, words), or (None, None, "")
    when it keeps no record (no accounting, no history)."""
    if scheduler == "slurm":
        out = _run(["sacct", "-n", "-X", "-P", "-o", "State,ExitCode", "-j", native_id])
        line = (out or "").strip().splitlines()[:1]
        if not line or "|" not in line[0]:
            return None, None, ""
        state, code = line[0].split("|", 1)
        try:
            rc = int(code.split(":")[0])
        except ValueError:
            rc = None
        return state.split()[0], rc, state
    if scheduler == "pbs":
        out = _run(["qstat", "-xf", native_id]) or _run(["qstat", "-f", native_id]) or ""
        for line in out.splitlines():
            if "Exit_status" in line:
                try:
                    rc = int(line.split("=")[1])
                except (IndexError, ValueError):
                    return None, None, ""
                if rc == -29:
                    return "TIMEOUT", rc, "Exit_status = -29 (walltime)"
                if rc in (-27, -26):
                    return "OUT_OF_MEMORY", rc, f"Exit_status = {rc} (memory)"
                if rc < 0 or rc >= 256:
                    return "LOST", rc, f"Exit_status = {rc}"
                return "COMPLETED" if rc == 0 else "FAILED", rc, ""
    return None, None, ""


def classify(said: str | None, code: int | None, rc: int | None, psij_state: str,
             cancelled: bool, words: str) -> tuple[str, int | None, str]:  # fmt: skip
    """One job state from the scheduler's word, the script's own exit status and PSI/J's.
    A job is a success only when something says it exited 0; an unknown end is lost."""
    if cancelled:
        return "cancelled", rc, ""
    if said in ("TIMEOUT", "DEADLINE"):
        return "walltime_exceeded", rc, f"the job ran past its walltime ({words})"
    if said == "OUT_OF_MEMORY":
        return "out_of_memory", rc, f"the scheduler stopped the job: out of memory ({words})"
    exit_code = rc if rc is not None else code
    if said == "COMPLETED":
        if exit_code is None:
            return "job_lost", None, ("the scheduler says the job completed, but nothing "
                                      "says how the solve exited")  # fmt: skip
        return ("completed" if exit_code == 0 else "failed"), exit_code, ""
    if said == "FAILED" and exit_code:
        return "failed", exit_code, ""
    if said is not None:
        return "job_lost", rc, f"the scheduler ended the job ({words})"
    if psij_state == "CANCELED":
        return "job_lost", rc, "the job was cancelled outside Puzzle"
    if rc is not None:
        return ("completed" if rc == 0 else "failed"), rc, ""
    return "job_lost", None, words or "the job ended, but its outcome is unknown"
