"""Solver runs may use the machine's cores (Settings > Solvers > Cores per run):
OpenMP and CalculiX's counters are set for native and container runs, unless the user
set their own. Without them CalculiX factors on one core."""

import os

import pytest

from puzzle import settings
from puzzle.jobs.runtime import (
    THREAD_VARIABLES,
    ContainerRuntime,
    NativeRuntime,
    process_count,
    thread_environment,
    threads_per_process,
)
from puzzle.machine import default_cores, logical_cores, memory_bytes, physical_cores


def test_automatic_is_four_or_one_fewer_than_the_cores():
    """The maintainer's rule (2026-11-08): 4 on bigger computers, N-1 on smaller ones."""
    assert [default_cores(n) for n in (1, 2, 3, 4, 5, 8, 16, 64)] == [1, 1, 2, 3, 4, 4, 4, 4]


def test_automatic_cores_by_default_and_the_preference_otherwise(monkeypatch):
    for name in THREAD_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    env = thread_environment()
    assert {env[n] for n in THREAD_VARIABLES} == {str(default_cores())}
    assert 1 <= physical_cores() <= os.cpu_count()
    settings.current().set("solvers.threads", 2)
    assert thread_environment()["CCX_NPROC_EQUATION_SOLVER"] == "2"


def test_the_users_own_count_wins(monkeypatch):
    monkeypatch.setenv("OMP_NUM_THREADS", "3")
    env = thread_environment()
    assert env["OMP_NUM_THREADS"] == "3"


def test_native_and_container_runs_carry_them(monkeypatch, tmp_path):
    for name in THREAD_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    if logical_cores() < 2:
        pytest.skip("needs 2 logical cores")
    # 2, not 4: Cores per run offers at most this computer's logical cores (CI has 3).
    settings.current().set("solvers.threads", 2)
    env = NativeRuntime().environment([str(tmp_path / "ccx")])
    assert env["CCX_NPROC_EQUATION_SOLVER"] == "2"
    argv = ContainerRuntime(image="puzzle/elmer").wrap(["ElmerSolver"], tmp_path)
    assert "OMP_NUM_THREADS=2" in argv and "CCX_NPROC_STIFFNESS=2" in argv


def test_blas_stays_serial_under_threaded_solvers(monkeypatch, tmp_path):
    """BLIS reads OMP_NUM_THREADS too: under MUMPS's OpenMP threads that oversubscribed
    the cores until FEniCSx's LE1 never finished; a user's own value still wins."""
    monkeypatch.delenv("BLIS_NUM_THREADS", raising=False)
    assert thread_environment()["BLIS_NUM_THREADS"] == "1"
    argv = ContainerRuntime(image="puzzle/fenicsx").wrap(["python", "study.py"], tmp_path)
    assert "BLIS_NUM_THREADS=1" in argv
    monkeypatch.setenv("BLIS_NUM_THREADS", "4")
    assert thread_environment()["BLIS_NUM_THREADS"] == "4"


def test_choices_follow_this_computer():
    cores = dict((v, t) for t, v in settings.BY_KEY["solvers.threads"].choices)
    assert cores[0] == f"Automatic ({default_cores()})"
    assert max(cores) == logical_cores()  # any number up to the logical cores
    procs = [v for _, v in settings.BY_KEY["solvers.processes"].choices]
    assert procs[0] == 1 and max(procs) == physical_cores()
    assert memory_bytes() is None or memory_bytes() > 2**28


def test_processes_share_the_cores(monkeypatch):
    if physical_cores() < 2 or logical_cores() < 4:
        import pytest

        pytest.skip("needs 2 physical and 4 logical cores")
    for name in THREAD_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    settings.current().set("solvers.threads", 4)
    settings.current().set("solvers.processes", 2)
    assert process_count() == 2 and threads_per_process(2) == 2
    assert thread_environment(processes=2)["OMP_NUM_THREADS"] == "2"
    settings.current().set("solvers.processes", 1)
    assert thread_environment()["OMP_NUM_THREADS"] == "4"
