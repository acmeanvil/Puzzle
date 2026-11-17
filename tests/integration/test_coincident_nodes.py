"""Results on coincident nodes (ADR 0090 COMMON-1, the Elmer and FEniCSx report's).

Two halves of the bar end to end with no contact between them keep their own nodes,
eight of them at one place pairwise where the faces' meshes meet (corners and edges).
Matched by position, both nodes of a pair had taken one id: one node had no value and
the other part's value was read there.
"""

import numpy as np
import pytest
from conftest_geometry import HALF, split_bar_project
from solvers import over

from puzzle.model import Component, Contact, Feature, Quantity
from puzzle.solve import run_study

UNUSED = Contact(name="none", type="bonded", source="Right/Start", target="Left/End")


def _apart(tmp_path, solver, features, physics, settings=None):
    """The split bar with its contact taken away: two parts touching, each on its own."""
    project, study = split_bar_project(tmp_path, solver, features, UNUSED, physics, settings)
    project.get(study.component, Component).contacts = []
    return project, study


def _coincident(mesh, a: str, b: str) -> int:
    pa = {tuple(c) for c in np.round(mesh.node_coords(mesh.groups[a].node_ids), 12)}
    pb = {tuple(c) for c in np.round(mesh.node_coords(mesh.groups[b].node_ids), 12)}
    return len(pa & pb)


def _temperature(selection, celsius):
    return Feature(type="temperature", selection=selection,
                   values={"T": Quantity(value=celsius, unit="degC")})  # fmt: skip


@over("calculix", "elmer", "code_aster", "fenicsx")
def test_each_part_keeps_its_own_temperature_at_the_join(tmp_path, solver):
    """Left held at 100 and 50 degC, Right at 20 degC on both ends: on the touching faces
    Left reads 50 and Right 20 at every node, the coincident ones too."""
    features = [_temperature("Left/Start", 100), _temperature("Left/End", 50),
                _temperature("Right/Start", 20), _temperature("Right/End", 20)]  # fmt: skip
    project, study = _apart(tmp_path, solver, features, "heat_transfer",
                            {"temperature_dependent_materials": False})  # fmt: skip
    run = run_study(project, study.id, tmp_path, tmp_path / "run")
    t, mesh = run.results.last("temperature"), run.mesh
    assert _coincident(mesh, "Left/End", "Right/Start") > 0  # the case being tested
    assert len(np.unique(t.ids)) == len(t.ids) == len(mesh.node_ids)  # one row per node
    left = t.at(mesh.groups["Left/End"].node_ids)[:, 0] - 273.15
    right = t.at(mesh.groups["Right/Start"].node_ids)[:, 0] - 273.15
    print(f"{solver}: Left/End {left.min():.6g}..{left.max():.6g} degC, "
          f"Right/Start {right.min():.6g}..{right.max():.6g} degC")  # fmt: skip
    assert left == pytest.approx(50.0, abs=1e-6)
    assert right == pytest.approx(20.0, abs=1e-6)


@over("fenicsx")
def test_each_part_keeps_its_own_displacement_at_the_join(tmp_path, solver):
    """Left held at its start and pulled at its end by 10 MPa, Right held at its far end
    and unloaded: Right does not move, its coincident nodes included, and Left's end
    moves about σ L / E."""
    pull = Feature(type="pressure", selection="Left/End",
                   values={"p": Quantity(value=-10, unit="MPa")})  # fmt: skip
    features = [Feature(type="fixed", selection="Left/Start"), pull,
                Feature(type="fixed", selection="Right/End")]  # fmt: skip
    project, study = _apart(tmp_path, solver, features, "solid_mechanics")
    run = run_study(project, study.id, tmp_path, tmp_path / "run")
    u, mesh = run.results.last("displacement"), run.mesh
    assert _coincident(mesh, "Left/End", "Right/Start") > 0
    assert len(np.unique(u.ids)) == len(u.ids) == len(mesh.node_ids)
    right = np.abs(u.at(mesh.groups["Right/Start"].node_ids)).max()
    left = u.at(mesh.groups["Left/End"].node_ids)[:, 0].mean()
    stretch = 10e6 * HALF / 210e9  # σ L / E (structural steel); the held start stiffens it
    print(f"Right/Start |u| max {right:.3g} m; Left/End ux {left:.6g} m ({left / stretch:.4f})")
    assert right < 1e-12
    assert left == pytest.approx(stretch, rel=0.01)  # measured -0.33 %
