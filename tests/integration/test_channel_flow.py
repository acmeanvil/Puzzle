"""Laminar channel flow through Code_Saturne: Poiseuille profile and pressure gradient."""

import importlib.util
from pathlib import Path

import numpy as np
import pytest

from puzzle.jobs import runtime_for
from puzzle.model import Material, Study
from puzzle.project import load_project
from puzzle.solve import run_study

BENCHMARK = Path(__file__).parents[2] / "benchmarks" / "channel_flow"

pytestmark = pytest.mark.skipif(
    not runtime_for("code_saturne").available("code_saturne"), reason="code_saturne not available"
)


def _constants():
    spec = importlib.util.spec_from_file_location("channel_build", BENCHMARK / "build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_poiseuille_profile_and_pressure_gradient(tmp_path):
    ch = _constants()
    project = load_project(BENCHMARK)
    study = next(s for s in project.of_type(Study) if s.name == "Channel flow")
    mu = project.of_type(Material)[0].properties["dynamic_viscosity"].to_si()
    run = run_study(project, study.id, BENCHMARK, tmp_path / "run", timeout_s=1800)

    velocity = run.results.last("velocity")
    pressure = run.results.last("pressure")
    assert velocity.location == pressure.location == "element"
    blocks = [b for b in run.mesh.blocks if b.dim == 3]
    ids = np.concatenate([b.ids for b in blocks])
    centres = np.vstack(
        [
            run.mesh.node_coords(b.connectivity.ravel())
            .reshape(*b.connectivity.shape, 3)
            .mean(axis=1)
            for b in blocks
        ]
    )
    centres = centres[np.argsort(ids)]
    x, y = centres[:, 0], centres[:, 1]

    at = np.abs(x - 0.07) < 0.3e-3
    u = velocity.at(np.sort(ids)[at])[:, 0]
    exact = 6 * ch.U * y[at] * (ch.H - y[at]) / ch.H**2
    assert u.max() / ch.U == pytest.approx(1.5, rel=0.01)
    assert np.linalg.norm(u - exact) / np.linalg.norm(exact) < 0.01

    def mean_pressure(x0):
        return pressure.at(np.sort(ids)[np.abs(x - x0) < 0.3e-3])[:, 0].mean()

    dpdx = (mean_pressure(0.08) - mean_pressure(0.05)) / 0.03
    assert dpdx == pytest.approx(-12 * mu * ch.U / ch.H**2, rel=0.02)
