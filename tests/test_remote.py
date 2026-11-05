"""Remote runs (ADR 0021): hosts in the user's hosts.toml, a case staged by rsync, run
over ssh natively or under Slurm or PBS, streamed, fetched and cleaned up; cancels
reach the remote run; failures are coded. The fakes in remote_fakes.py stand in for
ssh, rsync and the schedulers on this machine, so the solver is the real CalculiX."""

import shutil
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
    text = path.read_text()
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
    exe, args = _remote_program(["/Users/me/puzzle/.pixi/envs/fenicsx/bin/python", "study.py"])
    assert exe == '"$ENVS/fenicsx/bin/python"' and args == ["study.py"]
    assert _remote_program(["ccx", "-i", "job"]) == ("ccx", ["-i", "job"])


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


def test_failures_are_coded():
    h = hosts.Host(name="hpc", address="hpc")
    assert failure(["ssh: Connection reset"], 255, h).code == "host_unreachable"
    assert failure(["[puzzle] queue rejected: invalid partition"], 97, h).code == "queue_rejected"
    assert failure(["[puzzle] walltime exceeded"], 124, h).code == "walltime_exceeded"
    assert failure(["*** ERROR in ccx"], 1, h) is None  # the solver's own: said as usual


# --- runs ------------------------------------------------------------------------------


def _case(tmp_path) -> Path:
    case = tmp_path / "project" / "run"
    case.mkdir(parents=True)
    (case / "input.txt").write_text("21\n")
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
    assert job.ok, (case / "solver.log").read_text()
    assert "threads 3" in lines and "done" in lines  # streamed as it ran
    assert (case / "answer.txt").read_text().strip() == "42"  # fetched back
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
    pid = int((home / rt.remote_dir(case) / "solver.pid").read_text())
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
    assert ok.ok, (case / "solver.log").read_text()
    assert any("submitted job" in x for x in lines) and "solving" in lines
    assert (case / "answer.txt").read_text().strip() == "7"
    assert (case / "puzzle-job.id").exists()  # kept with the run's files
    bad = run_job(["sh", "-c", "exit 3"], _case(tmp_path / "b"), RemoteRuntime(h, "calculix"))
    assert bad.returncode == 3
    short = remote_fakes.host(bin_dir, scheduler=scheduler, walltime="00:00:02")
    late_case = _case(tmp_path / "c")
    late = run_job(["sh", "-c", "sleep 20"], late_case, RemoteRuntime(short, "calculix"))
    tail = (late_case / "solver.log").read_text().splitlines()
    assert failure(tail, late.returncode, short).code == "walltime_exceeded"


@needs_rsync
def test_a_rejected_job_is_coded(tmp_path, fakes):
    bin_dir, _home = fakes
    case = _case(tmp_path)
    h = remote_fakes.host(bin_dir, scheduler="slurm", partition="rejected")
    job = run_job(["true"], case, RemoteRuntime(h, "calculix"))
    tail = (case / "solver.log").read_text().splitlines()
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
