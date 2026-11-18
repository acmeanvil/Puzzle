"""Puzzle's Linux machine on Windows (ADR 0068 step 5), without WSL: a distribution's
configuration kept beside it, the command a solve becomes (the run folder through
/mnt, the image's environment and entrypoint in front, the thread variables per run),
paths as WSL sees them, wsl.exe's UTF-16 listing read, and runtime_for choosing the
machine when a container can't run. The real import and solve is
`tests/integration/test_machine_solve.py`."""

import json
from pathlib import Path, PureWindowsPath

import pytest

from puzzle.jobs import machine


def test_paths_as_wsl_sees_them(monkeypatch):
    monkeypatch.setattr(Path, "resolve", lambda self: self)
    assert machine.linux_path(Path(PureWindowsPath(r"C:\Nate\run 1"))) == "/mnt/c/Nate/run 1"


def test_wsl_lists_in_utf16():
    listed = "Ubuntu\r\npuzzle-elmer\r\n".encode("utf-16-le")
    assert machine._text(listed).split() == ["Ubuntu", "puzzle-elmer"]


def test_a_solve_runs_as_in_its_container(tmp_path, monkeypatch):
    home = tmp_path / "machine"
    (home / "elmer").mkdir(parents=True)
    (home / "elmer" / "machine.json").write_text(json.dumps({
        "solver": "elmer", "distro": "puzzle-elmer", "entrypoint": ["/entry.sh"],
        "env": ["PATH=/opt/elmer/bin:/usr/bin"], "from": "image"}), encoding="utf-8")  # fmt: skip
    monkeypatch.setattr(machine, "_home", lambda: home)
    monkeypatch.setattr(machine, "distros", lambda: ["Ubuntu", "puzzle-elmer"])
    monkeypatch.setattr(machine, "wsl", lambda: "wsl.exe")
    monkeypatch.setattr(machine, "linux_path", lambda p: "/mnt/c/run")
    machine._SEEN["at"] = 0.0
    rt = machine.runtime("elmer")
    assert rt is not None and rt.available("ElmerSolver")
    argv = rt.wrap(["ElmerSolver", "case.sif"], tmp_path)
    assert argv[:7] == ["wsl.exe", "-d", "puzzle-elmer", "--cd", "/mnt/c/run", "--exec", "env"]
    assert "PATH=/opt/elmer/bin:/usr/bin" in argv
    assert any(a.startswith("OMP_NUM_THREADS=") for a in argv)
    assert argv[-3:] == ["/entry.sh", "ElmerSolver", "case.sif"]
    assert machine.runtime("openfoam") is None


def test_runtime_for_takes_the_machine_when_no_container_runs(monkeypatch):
    import sys

    from puzzle.jobs import runtime

    sentinel = machine.WslRuntime("elmer", "puzzle-elmer")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(machine, "runtime", lambda solver: sentinel if solver == "elmer" else None)
    monkeypatch.setattr(runtime.ContainerRuntime, "available", lambda self, exe: False)
    monkeypatch.setattr(runtime.NativeRuntime, "available", lambda self, exe: False)
    monkeypatch.delenv("PUZZLE_RUNTIME", raising=False)
    monkeypatch.delenv("PUZZLE_ELMER_RUNTIME", raising=False)
    assert runtime.runtime_for("elmer") is sentinel
    monkeypatch.setenv("PUZZLE_OPENFOAM_RUNTIME", "wsl")
    with pytest.raises(runtime.SolverNotFound, match="puzzle machine install openfoam"):
        runtime.runtime_for("openfoam")


def test_nothing_is_asked_of_wsl_without_a_puzzle_distribution(tmp_path, monkeypatch):
    monkeypatch.setattr(machine, "_home", lambda: tmp_path / "none")
    monkeypatch.setattr(machine, "distros", lambda: pytest.fail("asked WSL"))
    assert machine.installed() == {}


def test_a_staged_solve_runs_on_the_machines_own_disk(tmp_path, monkeypatch):
    """`solvers.wsl_stage` (ADR 0068 step 5): copied in before, back after, solved there."""
    calls = []
    monkeypatch.setattr(machine, "wsl", lambda: "wsl.exe")
    monkeypatch.setattr(machine, "linux_path", lambda p: "/mnt/c/run")
    monkeypatch.setattr(machine, "_run", lambda argv, timeout=600: calls.append(argv) or
                        type("Done", (), {"returncode": 0, "stderr": b""})())  # fmt: skip
    rt = machine.WslRuntime("elmer", "puzzle-elmer", stage=True)
    there = rt.staged(tmp_path)
    assert there.startswith("/tmp/puzzle-run/")
    rt.prepare(["ElmerSolver"], tmp_path)
    argv = rt.wrap(["ElmerSolver", "case.sif"], tmp_path)
    rt.finish(tmp_path)
    assert argv[argv.index("--cd") + 1] == there
    assert "cp -a '/mnt/c/run/.'" in calls[0][-1] and f"'{there}/'" in calls[0][-1]
    assert f"cp -a '{there}/.' '/mnt/c/run/'" in calls[1][-1]
    plain = machine.WslRuntime("elmer", "puzzle-elmer")
    assert (
        plain.wrap(["x"], tmp_path)[plain.wrap(["x"], tmp_path).index("--cd") + 1] == "/mnt/c/run"
    )


def test_a_failed_copy_back_fails_the_run(tmp_path, monkeypatch):
    """ADR-56: a staged case that didn't all come home was only said; a run would read
    what came back as its results. Now the copy-back's failure is the run's."""
    from puzzle.errors import PuzzleError

    monkeypatch.setattr(machine, "wsl", lambda: "wsl.exe")
    monkeypatch.setattr(machine, "linux_path", lambda p: "/mnt/c/run")
    monkeypatch.setattr(machine, "_run", lambda argv, timeout=600: type(
        "Done", (), {"returncode": 1, "stderr": b"cp: No space left on device"})())  # fmt: skip
    rt = machine.WslRuntime("elmer", "puzzle-elmer", stage=True)
    said = []
    with pytest.raises(PuzzleError) as err:
        rt.finish(tmp_path, said.append)
    assert err.value.code == "transfer_failed" and "No space left" in str(err.value)
    assert said and rt.staged(tmp_path) in str(err.value)
