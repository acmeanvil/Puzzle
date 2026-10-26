"""Solver runs may use the machine's cores (Settings > Solvers > Threads per run):
OpenMP and CalculiX's counters are set for native and container runs, unless the user
set their own. Without them CalculiX factors on one core."""

import os

from puzzle import settings
from puzzle.jobs.runtime import (
    THREAD_VARIABLES,
    ContainerRuntime,
    NativeRuntime,
    thread_environment,
)


def test_every_core_by_default_and_the_preference_otherwise(monkeypatch):
    for name in THREAD_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    env = thread_environment()
    assert {env[n] for n in THREAD_VARIABLES} == {str(os.cpu_count())}
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
