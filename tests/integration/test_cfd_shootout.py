"""Code_Saturne and OpenFOAM as a shoot-out pair (ADR 0011) on the laminar channel.

The benchmark study (benchmarks/channel_flow) lists OpenFOAM as its shoot-out solver:
both run on one hexahedral mesh and their cell values are compared directly, with the
tolerances the study carries. Each solver's own accuracy against plane Poiseuille flow
is tested in test_channel_flow.py.
"""

from pathlib import Path

import pytest
from solvers import available

from puzzle.model import Study
from puzzle.project import load_project
from puzzle.shootout import run_shootout

BENCHMARK = Path(__file__).parents[2] / "benchmarks" / "channel_flow"

pytestmark = pytest.mark.skipif(
    not (available("code_saturne") and available("openfoam")),
    reason="needs code_saturne and openfoam",
)


def test_channel_flow_shootout(tmp_path):
    project = load_project(BENCHMARK)
    study = next(s for s in project.of_type(Study) if s.name == "Channel flow")
    out = run_shootout(project, study.id, BENCHMARK, tmp_path, timeout_s=3600)
    print("\n" + out.table())
    assert set(out.runs) == {"code_saturne", "openfoam"}
    assert out.runs["openfoam"].mesh is out.runs["code_saturne"].mesh  # one mesh
    assert out.passed


@pytest.mark.parametrize("level", [100.0, -50.0])
def test_both_solvers_keep_the_outlet_pressure(tmp_path, level):
    """ADR 0090 CS-1: Code_Saturne holds the pressure at a reference outlet face at zero
    and ignores the value set there, so an outlet at 100 Pa came back at 0 to 0.003 Pa
    where OpenFOAM gave 100.000 to 100.004, and −50 Pa stopped its case model. Puzzle
    adds the level when reading. The laminar channel in 2D (water at 1 mm/s, a 10 mm gap,
    a pressure drop of about 6 mPa over its 50 mm): the outlet column's mean pressure is
    the outlet's within 1 mPa, half a cell's drop, on both."""
    import sys

    import numpy as np

    sys.path.insert(0, str(Path(__file__).parents[1]))
    import test_mesh_layer as base

    from puzzle.model import Quantity, SolveStep
    from puzzle.solve import run_study

    means = {}
    for solver in ("code_saturne", "openfoam"):
        p, comp = base.cfd_2d("2d_planar", "quadrilaterals", 1.0)
        outlet = next(f for f in comp.physics[0].features if f.type == "pressure_outlet")
        outlet.values["p"] = Quantity(value=level, unit="Pa")
        study = p.add(
            Study(
                name="s",
                component=comp.id,
                solver=solver,
                steps=[SolveStep(type="stationary", settings={"iterations": 500})],
            )
        )
        run = run_study(p, study.id, tmp_path / solver, tmp_path / solver / "run",
                        timeout_s=1800)  # fmt: skip
        f = run.results.last("pressure")
        blocks = run.mesh.region_blocks()
        ids = np.concatenate([b.ids for b in blocks])
        x = np.concatenate([run.mesh.node_coords(b.connectivity.ravel()).reshape(
            *b.connectivity.shape, 3).mean(axis=1)[:, 0] for b in blocks])  # fmt: skip
        last = x > x.max() - 1e-4
        means[solver] = float(f.at(ids[last])[:, 0].mean())
        print(f"{solver}: outlet at {level:g} Pa, its cells' mean {means[solver]:.6g} Pa")
    for solver, mean in means.items():
        assert mean == pytest.approx(level, abs=1e-3), solver
    assert means["code_saturne"] == pytest.approx(means["openfoam"], abs=1e-3)
