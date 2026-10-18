"""Contact between separately meshed parts (CalculiX and Code_Aster).

The 200 x 10 x 10 mm steel bar is two halves joined at x = 100 mm. Each half is meshed
on its own with a different element size, so the interface nodes do not coincide and
the solver must connect them.

- Uniaxial patch test: symmetry planes and an axial load. A bond must pass the
  uniform stress F/A across the interface; the end displacement is F L / (E A).
- Split cantilever: fixed root, tip load. A bond must reproduce the beam deflection.
"""

import numpy as np
import pytest
from conftest_geometry import split_bar_project
from solvers import over

from puzzle.materials import library_material
from puzzle.model import (
    Contact,
    Feature,
    Quantity,
)
from puzzle.solve import run_study

HALF, B, H = 0.1, 0.01, 0.01
A, INERTIA = B * H, B * H**3 / 12
STEEL = library_material("structural_steel").properties
E, NU = STEEL["youngs_modulus"].to_si(), STEEL["poissons_ratio"].to_si()


def _bond(meshing="nonconformal"):
    # The finer half is the source (slave) side.
    return Contact(
        name="Joint", type="bonded", source="Right/Start", target="Left/End", meshing=meshing
    )


def _force(selection, **xyz):
    return Feature(
        type="force",
        selection=selection,
        values={k: Quantity(value=v, unit="N") for k, v in xyz.items()},
    )


def _symmetry():
    # Symmetry planes on the left half only: the right half is held through the bond,
    # and its interface nodes must not carry constraints of their own (CalculiX ties
    # dependent nodes, which an SPC on the same node contradicts).
    planes = [
        Feature(
            type="displacement",
            selection=f"Left/{face}",
            values={axis: Quantity(value=0, unit="m")},
        )
        for face, axis in (("Side", "y"), ("Bottom", "z"))
    ]
    start = Feature(
        type="displacement", selection="Left/Start", values={"x": Quantity(value=0, unit="m")}
    )
    return [*planes, start]


@over("calculix", "code_aster")
def test_tie_passes_uniform_stress(tmp_path, solver):
    force = 2000.0  # N
    project, study = split_bar_project(
        tmp_path, solver, [*_symmetry(), _force("Right/End", x=force)], _bond()
    )
    run = run_study(project, study.id, tmp_path, tmp_path / "run", timeout_s=900)
    u = run.results.last("displacement")
    x = run.mesh.node_coords(u.ids)[:, 0]
    exact = force / (E * A) * x
    print(
        f"{solver}: end displacement {u.values[x > 2 * HALF - 1e-9, 0].mean():.6e} m "
        f"(exact {force * 2 * HALF / (E * A):.6e})"
    )
    assert np.abs(u.values[:, 0] - exact).max() < 1e-3 * exact.max()
    stress = run.results.last("stress")
    error = np.abs(stress.values[:, 0] - force / A) / (force / A)
    near = np.abs(run.mesh.node_coords(stress.ids)[:, 0] - HALF) < 5e-3
    print(
        f"{solver}: stress error {error[~near].max():.1e} away from the joint, "
        f"{error[near].mean():.1e} mean / {error[near].max():.1e} max within 5 mm"
    )
    # Exact away from the joint; a node-to-surface tie disturbs stress locally.
    assert error[~near].max() < 2e-3
    assert error[near].mean() < 0.02 and error[near].max() < 0.15


@over("calculix", "code_aster")
def test_tie_carries_bending(tmp_path, solver):
    load = -100.0  # N at the tip
    fixed = Feature(type="fixed", selection="Left/Start")
    deflections = {}
    for meshing in ("nonconformal", "conformal"):
        work = tmp_path / meshing
        work.mkdir()
        project, study = split_bar_project(
            work, solver, [fixed, _force("Right/End", y=load)], _bond(meshing)
        )
        run = run_study(project, study.id, work, work / "run", timeout_s=900)
        u = run.results.last("displacement")
        x = run.mesh.node_coords(u.ids)[:, 0]
        deflections[meshing] = u.values[x > 2 * HALF - 1e-9, 1].mean()
    length = 2 * HALF
    beam = load * length**3 / (3 * E * INERTIA) + load * length / ((5 / 6) * E / (2 * (1 + NU)) * A)
    print(
        f"{solver}: tied {deflections['nonconformal']:.6e}, conformal "
        f"{deflections['conformal']:.6e}, beam {beam:.6e} m"
    )
    assert deflections["nonconformal"] == pytest.approx(beam, rel=0.02)
    assert deflections["nonconformal"] == pytest.approx(deflections["conformal"], rel=5e-3)


@over("calculix")
def test_tie_conducts_heat(tmp_path, solver):
    """Steady conduction 100 -> 20 C across the tie: the profile must stay linear."""
    hot = Feature(
        type="temperature", selection="Left/Start", values={"T": Quantity(value=100, unit="degC")}
    )
    cold = Feature(
        type="temperature", selection="Right/End", values={"T": Quantity(value=20, unit="degC")}
    )
    project, study = split_bar_project(
        tmp_path,
        solver,
        [hot, cold],
        _bond(),
        physics="heat_transfer",
        settings={"temperature_dependent_materials": False},
    )
    run = run_study(project, study.id, tmp_path, tmp_path / "run", timeout_s=900)
    T = run.results.last("temperature")
    x = run.mesh.node_coords(T.ids)[:, 0]
    exact = 373.15 - 80.0 * x / (2 * HALF)
    # The tie disturbs the profile locally by a few hundredths of a kelvin (of 80 K).
    assert np.abs(T.values[:, 0] - exact).max() < 0.05  # K
