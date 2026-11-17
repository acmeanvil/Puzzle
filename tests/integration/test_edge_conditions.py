"""Conditions held on an edge or a point of a 3D model (ADR 0090 FENICS-1).

A held value on an edge or a point is well defined: its nodes take it. FEniCSx once
looked every condition up among the mesh's facets, so one on an edge held nothing and
said nothing (20 degC on a tip edge left the bar at a uniform 100 degC). The reference is
CalculiX on the same mesh (the same quadratic tetrahedra, so the same discrete problem):
there is no closed form for a temperature held along one edge, and the field is singular
there, so the check is the two solvers' agreement node by node, and the held nodes'
values exactly. Measured 2026-11-17: FEniCSx and Code_Aster both 5.1e-4 K off CalculiX
(an edge at 20 degC, a point at 60 degC, the root at 100 degC) and 1.9e-6 of the largest
displacement off it (fixed on an edge, a point moved 0.01 mm in z, the root fixed), the
same on each: the five digits of CalculiX's .frd. Before the fix FEniCSx returned a
uniform 373.15 K.
"""

import numpy as np
import pytest
from conftest_geometry import BAR, bar_project, write_bar_step
from solvers import available, over

from puzzle.geometry import import_step
from puzzle.model import Feature, Physics, Quantity, SolveStep, Study
from puzzle.model.schema import Geometry, NamedSelection
from puzzle.solve import run_study


def _bar(tmp_path, physics):
    """The bar with an edge selection along the tip's y = 0 side (TipEdge) and a point
    selection at the tip's corner (TipCorner)."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    step = write_bar_step(tmp_path / "bar.step")
    model = import_step(step)
    L = BAR[0]
    edges = [e.name for e in model.of_dim(1)
             if abs(e.bbox[0] - L) < 1e-6 and abs(e.bbox[3] - L) < 1e-6
             and abs(e.bbox[1]) < 1e-6 and abs(e.bbox[4]) < 1e-6]  # fmt: skip
    corner = [p.name for p in model.of_dim(0)
              if np.allclose(np.asarray(p.bbox[:3]), [L, BAR[1], BAR[2]], atol=1e-6)]  # fmt: skip
    assert len(edges) == 1 and len(corner) == 1
    project, comp = bar_project(step, physics, mesh_size_mm=4)
    geo = project.get(comp.parts[0].geometry, Geometry)
    geo.selections += [
        NamedSelection(name="TipEdge", entity="edge", members=edges),
        NamedSelection(name="TipCorner", entity="vertex", members=corner),
    ]
    return project, comp


def _solve(tmp_path, solver, physics):
    project, comp = _bar(tmp_path / solver, physics)
    study = project.add(
        Study(name="s", component=comp.id, solver=solver, steps=[SolveStep(type="stationary")])
    )
    return run_study(project, study.id, tmp_path / solver, tmp_path / solver / "run",
                     timeout_s=900)  # fmt: skip


def _heat():
    def held(selection, celsius):
        return Feature(type="temperature", selection=selection,
                       values={"T": Quantity(value=celsius, unit="degC")})  # fmt: skip

    return [Physics(type="heat_transfer", settings={"temperature_dependent_materials": False},
                    features=[held("Bar/Root", 100), held("Bar/TipEdge", 20),
                              held("Bar/TipCorner", 60)])]  # fmt: skip


def _structure():
    return [Physics(type="solid_mechanics", features=[
        Feature(type="fixed", selection="Bar/Root"),
        Feature(type="fixed", selection="Bar/TipEdge"),
        Feature(type="displacement", selection="Bar/TipCorner",
                values={"z": Quantity(value=0.01, unit="mm")}),
        Feature(type="gravity", selection="Bar/Body",
                values={"gz": Quantity(value=-9.81e3, unit="m/s^2")}),
    ])]  # fmt: skip


def _nodes(run, name):
    return run.mesh.groups[name].node_ids


@pytest.fixture(scope="module")
def calculix(tmp_path_factory):
    if not available("calculix"):
        pytest.skip("calculix not available")
    tmp = tmp_path_factory.mktemp("ccx")
    return _solve(tmp, "calculix", _heat()), _solve(tmp / "s", "calculix", _structure())


@over("calculix", "fenicsx", "code_aster")
def test_a_temperature_held_on_an_edge_and_a_point(tmp_path, solver, calculix):
    run = _solve(tmp_path, solver, _heat())
    T = run.results.last("temperature")
    assert np.allclose(T.at(_nodes(run, "Bar/TipEdge"))[:, 0], 293.15, atol=1e-9)
    assert np.allclose(T.at(_nodes(run, "Bar/TipCorner"))[:, 0], 333.15, atol=1e-9)
    reference = calculix[0].results.last("temperature")
    worst = float(np.abs(T.at(reference.ids) - reference.values).max())
    print(f"{solver}: T {T.values.min():.3f}..{T.values.max():.3f} K, off CalculiX {worst:.2g} K")
    assert worst < 2e-3  # 2.5e-5 of the 80 K range: CalculiX's .frd holds five digits


@over("calculix", "fenicsx", "code_aster")
def test_a_support_on_an_edge_and_a_point(tmp_path, solver, calculix):
    run = _solve(tmp_path, solver, _structure())
    u = run.results.last("displacement")
    assert np.abs(u.at(_nodes(run, "Bar/TipEdge"))).max() < 1e-15
    assert np.allclose(u.at(_nodes(run, "Bar/TipCorner"))[:, 2], 1e-5, rtol=1e-9)
    reference = calculix[1].results.last("displacement")
    scale = float(np.abs(reference.values).max())
    worst = float(np.abs(u.at(reference.ids) - reference.values).max())
    print(f"{solver}: |u| max {scale:.4g} m, off CalculiX {worst / scale:.2g} of it")
    assert worst < 1e-5 * scale
