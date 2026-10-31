"""Solver runs may use the machine's cores (Settings > Solvers > Cores per run):
OpenMP and CalculiX's counters are set for native and container runs, unless the user
set their own. Without them CalculiX factors on one core."""

import os

from puzzle import settings
from puzzle.jobs.runtime import (
    THREAD_VARIABLES,
    ContainerRuntime,
    NativeRuntime,
    process_count,
    thread_environment,
    threads_per_process,
)
from puzzle.machine import logical_cores, memory_bytes, physical_cores


def test_physical_cores_by_default_and_the_preference_otherwise(monkeypatch):
    for name in THREAD_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    env = thread_environment()
    assert {env[n] for n in THREAD_VARIABLES} == {str(physical_cores())}
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
    settings.current().set("solvers.threads", 4)
    env = NativeRuntime().environment([str(tmp_path / "ccx")])
    assert env["CCX_NPROC_EQUATION_SOLVER"] == "4"
    argv = ContainerRuntime(image="puzzle/elmer").wrap(["ElmerSolver"], tmp_path)
    assert "OMP_NUM_THREADS=4" in argv and "CCX_NPROC_STIFFNESS=4" in argv


def test_choices_follow_this_computer():
    cores = dict((v, t) for t, v in settings.BY_KEY["solvers.threads"].choices)
    assert cores[0] == f"Physical cores ({physical_cores()})"
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
