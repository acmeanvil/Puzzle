"""Remote runs (ADR 0021): hosts in the user's hosts.toml, a case staged by rsync, run
over ssh natively or under Slurm or PBS, streamed, fetched and cleaned up; cancels
reach the remote run; failures are coded. The fakes in remote_fakes.py stand in for
ssh, rsync and the schedulers on this machine, so the solver is the real CalculiX."""

import shutil
import sys
import threading
import time
from pathlib import Path

import pytest
import remote_fakes

from puzzle import hosts
from puzzle.errors import PuzzleError
from puzzle.jobs.remote import RemoteRuntime, Resources, _remote_program, failure
from puzzle.jobs.runner import run_job

ROOT = Path(__file__).parents[1]
CCX = shutil.which("ccx", path=str(ROOT / ".pixi" / "envs" / "calculix" / "bin"))
needs_rsync = pytest.mark.skipif(not Path("/usr/bin/rsync").exists(), reason="no rsync")


@pytest.fixture
def fakes(tmp_path, monkeypatch):
    return remote_fakes.install(tmp_path, monkeypatch)


# --- hosts -------------------------------------------------------------------------------


def test_hosts_are_the_users_and_round_trip(tmp_path):
    h = hosts.Host(name="cluster", address="login.hpc.example", user="me", scheduler="slurm",
                   partition="short", cores=32, images={"elmer": "/scratch/elmer.sif"})  # fmt: skip
    path = hosts.add(h)
    assert path.name == "hosts.toml" and "projects" not in str(path)
    text = path.read_text(encoding="utf-8")
    assert "partition" in text and "envs_dir" not in text  # defaults left out
    back = hosts.get("cluster")
    assert back == h
    assert h.ssh_argv("true")[-2:] == ["me@login.hpc.example", "true"]
    with pytest.raises(PuzzleError) as unknown:
        hosts.get("nowhere")
    assert unknown.value.code == "unknown_host"
    hosts.remove("cluster")
    assert hosts.load() == {}
    with pytest.raises(PuzzleError):
        hosts.Host(name="x", address="y", runtime="kubernetes")


@pytest.mark.skipif(sys.platform == "win32", reason="the fakes are shell scripts")

def test_check_says_what_a_host_has(fakes):
    bin_dir, _home = fakes
    found = hosts.check(remote_fakes.host(bin_dir, scheduler="slurm"))
    assert found.reachable and found.cores and found.cores > 0
    assert found.solvers["calculix"] is bool(CCX)
    assert found.scheduler is True  # the fake sbatch is on its PATH
    gone = hosts.check(remote_fakes.host(bin_dir, address="unreachable"))
    assert not gone.reachable and "No route" in gone.message


# --- scripts -----------------------------------------------------------------------------


def test_programs_from_local_pixi_envs_become_the_hosts():
    exe, name, env, args = _remote_program(["/Users/me/puzzle/.pixi/envs/fenicsx/bin/python",
                                            "study.py"])  # fmt: skip
    assert (exe, name, env, args) == ('"$ENVS"/fenicsx/bin/python', "python", "fenicsx",
                                      ["study.py"])  # fmt: skip
    assert _remote_program(["ccx", "-i", "job"]) == ("ccx", "ccx", "", ["-i", "job"])
    # From Windows: the same environment's program on the (Unix) host, without .exe.
    win = r"C:\Users\me\puzzle\.pixi\envs\fenicsx\python.exe"
    assert _remote_program([win, "study.py"]) == ('"$ENVS"/fenicsx/bin/python', "python",
                                                  "fenicsx", ["study.py"])  # fmt: skip
    ccx = r"C:\Puzzle\envs\calculix\Library\bin\ccx.exe"
    assert _remote_program([ccx, "-i", "job"])[:3] == ('"$ENVS"/calculix/bin/ccx', "ccx",
                                                       "calculix")  # fmt: skip



@needs_rsync
def test_a_pixi_program_runs_from_an_envs_dir_with_a_space(tmp_path, fakes):
    """FEniCSx's command is the local pixi Python: there it is the same environment's,
    found and run even when the host's envs_dir has a space in it."""
    bin_dir, _home = fakes
    envs = tmp_path / "pixi envs"
    for env in ("calculix", "fenicsx"):  # another env's python must not be the one run
        (envs / env / "bin").mkdir(parents=True)
        python = envs / env / "bin" / "python"
        python.write_text(
            f'#!/bin/sh\necho "{env} python $* prefix=$CONDA_PREFIX"\n', encoding="utf-8"
        )
        python.chmod(0o755)
    case = _case(tmp_path)
    rt = RemoteRuntime(remote_fakes.host(bin_dir, envs_dir=str(envs)), "fenicsx")
    lines = []
    job = run_job(["/Users/me/puzzle/.pixi/envs/fenicsx/bin/python", "study.py", "a b"], case,
                  rt, on_output=lines.append)  # fmt: skip
    assert job.ok, (case / "solver.log").read_text(encoding="utf-8")
    assert f"fenicsx python study.py a b prefix={envs / 'fenicsx'}" in lines


def test_driver_and_job_scripts(tmp_path):
    h = hosts.Host(name="hpc", address="hpc", scheduler="slurm", partition="long", account="p1",
                   walltime="02:00:00", memory_gb=8, cores=16)  # fmt: skip
    rt = RemoteRuntime(h, "calculix", Resources(processes=4))
    job = rt.job_script(["ccx", "-i", "job"])
    for line in ("#SBATCH --ntasks=4", "#SBATCH --cpus-per-task=4", "#SBATCH --time=02:00:00",
                 "#SBATCH --partition=long", "#SBATCH --account=p1", "#SBATCH --mem=8192M",
                 "export OMP_NUM_THREADS=$T", "ccx -i job"):  # fmt: skip
        assert line in job
    driver = rt.driver(["ccx", "-i", "job"])
    assert "sbatch --parsable puzzle-job.sh" in driver and "puzzle-job.id" in driver
    pbs = RemoteRuntime(hosts.Host(name="p", address="p", scheduler="pbs", cores=8), "elmer",
                        Resources(processes=2))  # fmt: skip
    assert "#PBS -l select=1:ncpus=8:mpiprocs=2" in pbs.job_script(["ElmerSolver", "case.sif"])
    box = RemoteRuntime(hosts.Host(name="a", address="a", runtime="apptainer"), "elmer")
    line = box.solve_lines(["ElmerSolver", "case.sif"])[-1]
    assert line.startswith("apptainer exec") and "docker://ghcr.io/acmeanvil/puzzle-elmer" in line
    # Two projects' cases with one name never share a remote folder.
    a, b = tmp_path / "a" / "run", tmp_path / "b" / "run"
    assert rt.remote_dir(a) != rt.remote_dir(b) and rt.remote_dir(a).startswith("puzzle-runs/")


def test_queue_values_are_checked_before_they_reach_a_job_script():
    """Partition, account and walltime go into #SBATCH/#PBS lines as they are, so a
    newline or a ; there is refused where it is typed and where it is used."""
    from pydantic import ValidationError

    from puzzle.model import RemoteRun

    for bad in ({"partition": "short\n#SBATCH --uid=0"}, {"account": "p1; rm -rf ~"},
                {"walltime": "1h"}, {"walltime": "01:00:00 --x"}, {"cores": "8"}):  # fmt: skip
        with pytest.raises(PuzzleError) as refused:
            hosts.Host(name="hpc", address="hpc", scheduler="slurm", **bad)
        assert refused.value.code == "invalid" and next(iter(bad)) in refused.value.message
    with pytest.raises(PuzzleError, match="PBS takes HH:MM:SS"):
        hosts.Host(name="p", address="p", scheduler="pbs", walltime="1-00:00:00")
    for good in ("90", "30:00", "02:00:00", "2-12", "1-12:00:00"):
        hosts.Host(name="hpc", address="hpc", scheduler="slurm", walltime=good)
    hosts.Host(name="hpc", address="hpc", partition="short,long", account="proj-1.a")
    with pytest.raises(ValidationError, match="partition"):
        RemoteRun(host="hpc", partition="a b")
    assert RemoteRun(host="hpc", walltime="00:30:00").walltime == "00:30:00"
    # A Resources made in code is checked where the job script is written.
    rt = RemoteRuntime(hosts.Host(name="hpc", address="hpc", scheduler="slurm"), "calculix",
                       Resources(account="x\n#SBATCH --x"))  # fmt: skip
    with pytest.raises(PuzzleError) as refused:
        rt.job_script(["ccx", "-i", "job"])
    assert refused.value.code == "invalid" and refused.value.details["field"] == "account"


def test_failures_are_coded():
    h = hosts.Host(name="hpc", address="hpc")
    assert failure(["ssh: Connection reset"], 255, h).code == "host_unreachable"
    assert failure(["[puzzle] queue rejected: invalid partition"], 97, h).code == "queue_rejected"
    assert failure(["[puzzle] walltime exceeded"], 124, h).code == "walltime_exceeded"
    assert failure(["[puzzle] out of memory: job 7 used more"], 98, h).code == "out_of_memory"
    assert failure(["[puzzle] the scheduler ended job 7: NODE_FAIL"], 99, h).code == "job_lost"
    assert failure(["[puzzle] job 7 outcome unknown: no record"], 99, h).code == "job_lost"
    assert failure(["*** ERROR in ccx"], 1, h) is None  # the solver's own: said as usual


# --- runs ------------------------------------------------------------------------------


def _case(tmp_path) -> Path:
    case = tmp_path / "project" / "run"
    case.mkdir(parents=True)
    (case / "input.txt").write_text("21\n", encoding="utf-8")
    return case


@needs_rsync
def test_a_job_runs_there_and_comes_back(tmp_path, fakes):
    bin_dir, home = fakes
    case = _case(tmp_path)
    rt = RemoteRuntime(remote_fakes.host(bin_dir, cores=3), "calculix")
    lines = []
    job = run_job(["sh", "-c", 'echo "threads $OMP_NUM_THREADS"; '
                   'echo $(( $(cat input.txt) * 2 )) > answer.txt; echo done'],
                  case, rt, on_output=lines.append)  # fmt: skip
    assert job.ok, (case / "solver.log").read_text(encoding="utf-8")
    assert "threads 3" in lines and "done" in lines  # streamed as it ran
    assert (case / "answer.txt").read_text(encoding="utf-8").strip() == "42"  # fetched back
    assert any("sent" in x for x in lines) and any("fetched" in x for x in lines)
    assert not (home / rt.remote_dir(case)).exists()  # cleaned up there
    assert job.runtime == "lab (native)"


@needs_rsync
def test_a_cancel_stops_the_remote_run(tmp_path, fakes):
    bin_dir, home = fakes
    case = _case(tmp_path)
    rt = RemoteRuntime(remote_fakes.host(bin_dir, keep_case=True), "calculix")
    stop = threading.Event()
    threading.Timer(1.5, stop.set).start()
    job = run_job(["sh", "-c", "echo $$ > solver.pid; sleep 60"], case, rt, cancel=stop)
    assert job.cancelled
    pid = int((home / rt.remote_dir(case) / "solver.pid").read_text(encoding="utf-8"))
    time.sleep(0.5)
    import os

    with pytest.raises(ProcessLookupError):  # the remote solver is gone, not orphaned
        os.kill(pid, 0)


@needs_rsync
@pytest.mark.parametrize("scheduler", ["slurm", "pbs"])
def test_a_scheduler_job_is_submitted_followed_and_coded(tmp_path, fakes, scheduler):
    bin_dir, _home = fakes
    case = _case(tmp_path)
    h = remote_fakes.host(bin_dir, scheduler=scheduler, walltime="00:00:30")
    lines = []
    ok = run_job(["sh", "-c", "echo solving; echo 7 > answer.txt"], case,
                 RemoteRuntime(h, "calculix"), on_output=lines.append)  # fmt: skip
    assert ok.ok, (case / "solver.log").read_text(encoding="utf-8")
    assert any("submitted job" in x for x in lines) and "solving" in lines
    assert (case / "answer.txt").read_text(encoding="utf-8").strip() == "7"
    assert (case / "puzzle-job.id").exists()  # kept with the run's files
    bad = run_job(["sh", "-c", "exit 3"], _case(tmp_path / "b"), RemoteRuntime(h, "calculix"))
    assert bad.returncode == 3
    short = remote_fakes.host(bin_dir, scheduler=scheduler, walltime="00:00:02")
    late_case = _case(tmp_path / "c")
    late = run_job(["sh", "-c", "sleep 20"], late_case, RemoteRuntime(short, "calculix"))
    tail = (late_case / "solver.log").read_text(encoding="utf-8").splitlines()
    assert failure(tail, late.returncode, short).code == "walltime_exceeded"


def _scheduler_run(tmp_path, bin_dir, scheduler, command, **host):
    case = _case(tmp_path)
    h = remote_fakes.host(bin_dir, scheduler=scheduler, walltime="00:00:30", **host)
    job = run_job(command, case, RemoteRuntime(h, "calculix"))
    tail = (case / "solver.log").read_text(encoding="utf-8").splitlines()
    return job, failure(tail, job.returncode, h), tail


@needs_rsync
@pytest.mark.parametrize(
    ("ended", "code"),
    [("OUT_OF_MEMORY|0:125", "out_of_memory"), ("NODE_FAIL|0:0", "job_lost"),
     ("BOOT_FAIL|0:0", "job_lost"), ("PREEMPTED|0:0", "job_lost"),
     ("CANCELLED by 123|0:15", "job_lost"), ("FAILED|0:9", "job_lost"),
     ("DEADLINE|0:0", "walltime_exceeded")],
)  # fmt: skip
def test_how_a_slurm_job_ended_decides_its_outcome(tmp_path, fakes, monkeypatch, ended, code):
    """A job the scheduler ended (memory, a node, preemption, someone's scancel) is a
    coded failure, never exit 0 = success, whatever its ExitCode says."""
    bin_dir, _home = fakes
    monkeypatch.setenv("FAKE_SACCT_STATE", ended)
    job, said, tail = _scheduler_run(tmp_path, bin_dir, "slurm", ["true"])
    assert job.returncode != 0 and said is not None, tail
    assert said.code == code


@needs_rsync
def test_without_accounting_the_job_scripts_exit_status_counts(tmp_path, fakes, monkeypatch):
    bin_dir, _home = fakes
    monkeypatch.setenv("FAKE_NO_ACCOUNTING", "1")
    ok, said, tail = _scheduler_run(tmp_path / "a", bin_dir, "slurm", ["true"])
    assert ok.ok and said is None, tail
    assert any("no scheduler record" in x for x in tail)
    bad, said, _ = _scheduler_run(tmp_path / "b", bin_dir, "slurm", ["sh", "-c", "exit 3"])
    assert bad.returncode == 3 and said is None  # the solver's own failure
    monkeypatch.setenv("FAKE_NO_HISTORY", "1")  # PBS without job history: the same
    pbs, said, _ = _scheduler_run(tmp_path / "c", bin_dir, "pbs", ["sh", "-c", "exit 3"])
    assert pbs.returncode == 3 and said is None


@needs_rsync
def test_pbs_memory_and_signal_endings_are_coded(tmp_path, fakes, monkeypatch):
    bin_dir, _home = fakes
    monkeypatch.setenv("FAKE_PBS_EXIT", "-27")
    _job, said, tail = _scheduler_run(tmp_path / "a", bin_dir, "pbs", ["true"])
    assert said is not None and said.code == "out_of_memory", tail
    monkeypatch.setenv("FAKE_PBS_EXIT", "271")  # killed by SIGTERM (256 + 15)
    _job, said, tail = _scheduler_run(tmp_path / "b", bin_dir, "pbs", ["true"])
    assert said is not None and said.code == "job_lost" and "signal 15" in said.message, tail


@needs_rsync
def test_a_failed_queue_query_is_not_the_jobs_end(tmp_path, fakes, monkeypatch):
    """squeue timing out a few times, or forgetting a finished job, doesn't end the
    run early or wrongly; only when the scheduler never answers is the outcome unknown."""
    bin_dir, _home = fakes
    monkeypatch.setenv("FAKE_SQUEUE_FAILS", "3")
    monkeypatch.setenv("FAKE_NO_ACCOUNTING", "1")  # no sacct to fall back on either
    job, said, tail = _scheduler_run(tmp_path / "a", bin_dir, "slurm",
                                     ["sh", "-c", "sleep 2; echo 7 > answer.txt"])  # fmt: skip
    assert job.ok and said is None, tail
    assert (tmp_path / "a" / "project" / "run" / "answer.txt").read_text(
        encoding="utf-8"
    ).strip() == "7"
    monkeypatch.delenv("FAKE_SQUEUE_FAILS")
    monkeypatch.delenv("FAKE_NO_ACCOUNTING")
    monkeypatch.setenv("FAKE_SQUEUE_FORGETS", "1")
    job, said, tail = _scheduler_run(tmp_path / "b", bin_dir, "slurm", ["sh", "-c", "exit 3"])
    assert job.returncode == 3 and said is None, tail
    monkeypatch.setenv("FAKE_SQUEUE_FAILS", "1000")
    monkeypatch.setenv("FAKE_NO_ACCOUNTING", "1")
    monkeypatch.setenv("PUZZLE_REMOTE_RETRIES", "2")
    job, said, tail = _scheduler_run(tmp_path / "c", bin_dir, "slurm", ["sh", "-c", "sleep 8"])
    assert said is not None and said.code == "job_lost" and "outcome unknown" in said.message


@needs_rsync
def test_a_cancel_while_the_submit_runs_stops_the_job(tmp_path, fakes, monkeypatch):
    """The cancel comes before sbatch has said the job's id: the driver stops the job
    as soon as the id comes back, so it is not left running in the queue."""
    bin_dir, home = fakes
    monkeypatch.setenv("FAKE_SBATCH_DELAY", "3")
    case = _case(tmp_path)
    h = remote_fakes.host(bin_dir, scheduler="slurm", walltime="00:01:00", keep_case=True)
    stop = threading.Event()
    threading.Timer(1.0, stop.set).start()
    job = run_job(["sh", "-c", "sleep 60"], case, RemoteRuntime(h, "calculix"), cancel=stop)
    assert job.cancelled
    jobs = home / ".fake-jobs"
    for _ in range(50):  # the driver cancels once sbatch returns
        if (jobs / "1000.cancelled").exists():
            break
        time.sleep(0.1)
    assert (jobs / "1000.cancelled").exists()
    assert (home / RemoteRuntime(h, "calculix").remote_dir(case) / "puzzle-job.id").exists()


@needs_rsync
def test_a_rejected_job_is_coded(tmp_path, fakes):
    bin_dir, _home = fakes
    case = _case(tmp_path)
    h = remote_fakes.host(bin_dir, scheduler="slurm", partition="rejected")
    job = run_job(["true"], case, RemoteRuntime(h, "calculix"))
    tail = (case / "solver.log").read_text(encoding="utf-8").splitlines()
    err = failure(tail, job.returncode, h)
    assert err.code == "queue_rejected" and "invalid partition" in err.message


@needs_rsync
@pytest.mark.skipif(CCX is None, reason="no CalculiX in .pixi/envs/calculix")
def test_a_study_runs_on_a_host_with_the_same_answer_and_run_key(tmp_path, fakes, monkeypatch):
    """The cantilever on 'lab' (CalculiX from the host's pixi envs) matches a local run,
    and the run key is the same: a result is the same wherever it was computed."""
    from conftest_geometry import bar_project, write_bar_step

    from puzzle.model import Feature, Physics, Quantity, RemoteRun, SolveStep, Study
    from puzzle.project.graph import run_hashes
    from puzzle.results import ResultStore
    from puzzle.solve import run_study

    bin_dir, _home = fakes
    hosts.add(remote_fakes.host(bin_dir))
    monkeypatch.setenv("PUZZLE_SOLVER_PATH", str(Path(CCX).parent))
    step = write_bar_step(tmp_path / "bar.step")
    physics = [Physics(type="solid_mechanics", features=[
        Feature(type="fixed", selection="Bar/Root"),
        Feature(type="force", selection="Bar/Tip", values={"y": Quantity(value=-100, unit="N")}),
    ])]  # fmt: skip
    project, comp = bar_project(step, physics, mesh_size_mm=5)
    study = project.add(Study(name="S", component=comp.id, solver="calculix",
                              steps=[SolveStep(type="stationary")]))  # fmt: skip
    here = run_study(project, study.id, tmp_path, tmp_path / "local")
    key = run_hashes(project)[study.id]
    study.remote = RemoteRun(host="lab")
    assert run_hashes(project)[study.id] == key
    store = ResultStore(tmp_path / "results")
    lines = []
    there = run_study(project, study.id, tmp_path, tmp_path / "remote", store=store,
                      on_output=lines.append)  # fmt: skip
    assert there.job.runtime == "lab (native)"
    a, b = (r.results.last("displacement").values for r in (here, there))
    assert abs(b).max() == pytest.approx(abs(a).max(), rel=1e-9)
    assert any("fetched" in x for x in lines)


def test_every_sweep_point_runs_on_the_studys_host(tmp_path, fakes, monkeypatch):
    """A swept study sent to a host (by Study.remote or run_study's host=) runs each
    point there, not here: the points see the host's runtime."""
    from conftest_geometry import bar_project, write_bar_step

    from puzzle import solve
    from puzzle.jobs.runtime import runtime_for
    from puzzle.model import (
        ParameterSet,
        Physics,
        Quantity,
        RemoteRun,
        SolveStep,
        Study,
        SweepParameter,
        SweepStep,
    )

    bin_dir, _home = fakes
    hosts.add(remote_fakes.host(bin_dir))
    step = write_bar_step(tmp_path / "bar.step")
    project, comp = bar_project(step, [Physics(type="solid_mechanics")], mesh_size_mm=5)
    project.add(ParameterSet(name="P", values={"load": Quantity(value=1, unit="N")}))
    study = project.add(Study(name="S", component=comp.id, solver="calculix", steps=[
        SweepStep(parameters=[SweepParameter(name="load", values=[
            Quantity(value=v, unit="N") for v in (1, 2)])]),
        SolveStep(type="stationary")]))  # fmt: skip
    seen = []
    real = solve._run_study

    def point(project, study_id, root, workdir, **kwargs):
        if kwargs.get("_point") is None:
            return real(project, study_id, root, workdir, **kwargs)
        rt = runtime_for("calculix")
        seen.append(rt.host.name if isinstance(rt, RemoteRuntime) else "here")
        return solve.StudyRun(mesh=None, results=None, run_key=kwargs["_point"][0])

    monkeypatch.setattr(solve, "_run_study", point)
    solve.run_study(project, study.id, tmp_path, tmp_path / "w", host="lab")
    assert seen == ["lab", "lab"]
    study.remote = RemoteRun(host="lab")
    solve.run_study(project, study.id, tmp_path, tmp_path / "w2")
    assert seen == ["lab"] * 4


# --- GUI -------------------------------------------------------------------------------


def test_the_hosts_dialog_and_run_on(tmp_path, monkeypatch):
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from conftest_geometry import bar_project, write_bar_step
    from forms import fill
    from PySide6.QtWidgets import QApplication

    from puzzle.gui.hosts_dialog import HostsDialog
    from puzzle.gui.window import MainWindow
    from puzzle.model import Physics, SolveStep, Study
    from puzzle.project import save_project

    app = QApplication.instance() or QApplication([])
    dialog = HostsDialog()
    dialog.fields["name"].setText("cluster")
    dialog.fields["address"].setText("login.example")
    dialog.fields["scheduler"].setCurrentIndex(dialog.fields["scheduler"].findData("slurm"))
    dialog.fields["partition"].setText("short")
    assert dialog.form.isRowVisible(dialog.fields["walltime"])  # queue fields with a scheduler
    assert dialog.save().name == "cluster"
    assert hosts.get("cluster").partition == "short"
    dialog.close()

    step = write_bar_step(tmp_path / "bar.step")
    project, comp = bar_project(step, [Physics(type="solid_mechanics")], mesh_size_mm=5)
    study = project.add(Study(name="S", component=comp.id, solver="calculix",
                              steps=[SolveStep(type="stationary")]))  # fmt: skip
    save_project(project, tmp_path / "p")
    shutil.copy(step, tmp_path / "p" / "bar.step")
    w = MainWindow(tmp_path / "p")
    try:
        from puzzle.gui.hosts_dialog import run_on

        fill(w, host="cluster", processes="4", walltime="00:30:00")
        assert run_on(w, w.session.project.objects[study.id])
        remote = w.session.project.objects[study.id].remote
        assert (remote.host, remote.processes, remote.walltime) == ("cluster", 4, "00:30:00")
        offered = [label for label, _v in w.last_form.asks["host"].choices]
        assert offered[0] == "This computer" and "cluster (login.example)" in offered
        fill(w, host="")
        run_on(w, w.session.project.objects[study.id])
        assert w.session.project.objects[study.id].remote is None
    finally:
        w.close()
        app.processEvents()


def test_an_agents_remote_runs_count_core_hours(tmp_path):
    from puzzle.commands import Session
    from puzzle.commands.session import Actor
    from puzzle.model import RemoteRun, Study
    from puzzle.model.schema import Component, Geometry, MeshRecipe, PartInstance, Project
    from puzzle.project import save_project

    p = Project("p")
    mesh = p.add(MeshRecipe(name="M"))
    geo = p.add(Geometry(name="G", source="g.step"))
    comp = p.add(Component(name="C", mesh=mesh.id, parts=[PartInstance(name="G", geometry=geo.id)]))
    p.add(Study(name="S", component=comp.id, solver="calculix", remote=RemoteRun(host="lab")))
    save_project(p, tmp_path)
    s = Session(tmp_path)
    s.submit({"command": "set_autonomy", "level": "act_in_budget",
              "budget": {"runs": 5, "solver_seconds": 600, "core_hours": 0}})  # fmt: skip
    with pytest.raises(PuzzleError) as spent:
        s.submit({"command": "run_study", "study": "S"}, Actor("agent", "test"))
    assert spent.value.code == "budget_exceeded" and "core-hours" in spent.value.message
    assert s.budget_used()["core_hours"] == 0.0
