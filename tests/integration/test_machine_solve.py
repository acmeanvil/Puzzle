"""Puzzle's Linux machine solving for real (ADR 0068 step 5): Elmer's image exported once
and imported as a WSL 2 distribution, then the cantilever's tip load solved in it
(`PUZZLE_ELMER_RUNTIME=wsl`, no Docker in the solve) agrees with the same solve in the
container to every printed digit. It takes minutes and gigabytes, so it runs only when
asked (`PUZZLE_MACHINE_TESTS=1`) on Windows with WSL and Docker holding Elmer's image;
the distribution is removed afterwards.
"""

import os
import shutil
from pathlib import Path

import numpy as np
import pytest

from puzzle.jobs import machine
from puzzle.jobs.runtime import DEFAULT_IMAGES, ContainerRuntime

EXAMPLE = Path(__file__).parents[2] / "examples" / "cantilever"

pytestmark = pytest.mark.skipif(
    os.environ.get("PUZZLE_MACHINE_TESTS") != "1" or machine.wsl() is None
    or not ContainerRuntime(image=DEFAULT_IMAGES["elmer"]).available("ElmerSolver"),
    reason="imports a WSL distribution (minutes): PUZZLE_MACHINE_TESTS=1, WSL and Elmer's image",
)  # fmt: skip


def _tip(root: Path, runtime: str, monkeypatch) -> float:
    from puzzle.model.schema import Study
    from puzzle.project import load_project
    from puzzle.solve import run_study

    monkeypatch.setenv("PUZZLE_ELMER_RUNTIME", runtime)
    p = load_project(root)
    study = next(s for s in p.of_type(Study) if s.name == "Tip load")
    study.solver = "elmer"
    run = run_study(p, study.id, root, root / f"run-{runtime}", timeout_s=1800)
    return float(np.abs(run.results.last("displacement").values).max())


def test_elmer_solves_in_the_machine_as_in_its_container(tmp_path, monkeypatch):
    root = tmp_path / "cantilever"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns(
        "results*", ".puzzle", "history.jsonl", "versions"))  # fmt: skip
    machine.remove("elmer")
    try:
        machine.install_from_image("elmer", say=lambda _: None)
        in_vm = _tip(root, "wsl", monkeypatch)
        in_docker = _tip(root, "docker", monkeypatch)
        print(f"tip: machine {in_vm:.9e} m, container {in_docker:.9e} m")
        assert in_vm == pytest.approx(in_docker, rel=1e-9)
    finally:
        machine.remove("elmer")
