"""Symmetry planes (ADR 0081 step 1): a plane fitted to a face's nodes, and symmetry and
antisymmetry made displacement supports on its normal before an adapter writes them."""

import numpy as np
import pytest

from puzzle.adapters.common import InputError
from puzzle.mesh.mesh import Group, Mesh
from puzzle.model.schema import Component, CoordinateSystem, Feature, Physics, Project
from puzzle.symmetry import applied, held, plane, plane_features, plane_ops


def _comp(**kw):
    from uuid import uuid4

    from puzzle.model.schema import PartInstance

    return Component(name="C", parts=[PartInstance(name="P", geometry=uuid4())], mesh=uuid4(), **kw)


def _square(normal=(1.0, 0.0, 0.0), bent=0.0):
    """A 3 x 3 grid of points on the plane through the origin with this normal."""
    n = np.asarray(normal, float) / np.linalg.norm(normal)
    a = np.cross(n, [0.0, 0.0, 1.0] if abs(n[2]) < 0.9 else [1.0, 0.0, 0.0])
    a /= np.linalg.norm(a)
    b = np.cross(n, a)
    pts = np.array([i * a + j * b for i in (-1, 0, 1) for j in (-1, 0, 1)]) * 0.01
    pts[4] += bent * n  # the middle point off the plane
    return pts


def test_a_plane_through_a_faces_nodes():
    normal, centre, flat = plane(_square((0.0, 0.0, 1.0)))
    assert flat and abs(normal[2]) == pytest.approx(1.0) and np.allclose(centre, 0.0)
    assert not plane(_square(bent=1e-4))[2]  # bowed by 1 % of its size: not a plane
    # 2D: the normal of the straight edge, in the xy plane.
    edge = np.array([[0.0, y, 0.0] for y in np.linspace(0.0, 0.02, 5)])
    normal, _, flat = plane(edge, planar=True)
    assert flat and abs(normal[0]) == pytest.approx(1.0) and normal[2] == 0.0


def test_what_a_plane_across_an_axis_holds():
    assert held("symmetry", 0, False) == ["x"]
    assert held("antisymmetry", 0, False) == ["y", "z"]
    assert held("antisymmetry", 1, True) == ["x"]  # 2D: the in-plane one only


def _model(points, *features, physics="solid_mechanics", dimension="3d"):
    project = Project(name="p")
    physics = [Physics(type=physics, features=list(features))]
    comp = project.add(_comp(dimension=dimension, physics=physics))
    ids = np.arange(1, len(points) + 1)
    mesh = Mesh(node_ids=ids, coords=np.asarray(points, float),
                groups={"P/Cut": Group("P/Cut", 2, [(2, 1)], ids)})  # fmt: skip
    return project, comp, mesh


def test_symmetry_becomes_a_support_on_the_normal():
    project, comp, mesh = _model(_square((0.0, 1.0, 0.0)),
                                 Feature(type="symmetry", selection="P/Cut", steps=[2]),
                                 Feature(type="antisymmetry", selection="P/Cut"))  # fmt: skip
    out = applied(project, comp.id, mesh).objects[comp.id].physics[0].features
    assert [(f.type, sorted(f.values), f.steps) for f in out] == [
        ("displacement", ["y"], [2]),
        ("displacement", ["x", "z"], []),
    ]
    assert all(q.to_si() == 0.0 for f in out for q in f.values.values())
    assert project.objects[comp.id].physics[0].features[0].type == "symmetry"  # untouched


def test_a_plane_across_no_axis_takes_local_axes_or_is_refused():
    from types import SimpleNamespace

    n = (1.0, 1.0, 0.0)
    project, comp, mesh = _model(_square(n), Feature(type="symmetry", selection="P/Cut"))
    calculix = SimpleNamespace(axes_features={"displacement:cartesian:partial"})
    done = applied(project, comp.id, mesh, calculix)
    (f,) = done.objects[comp.id].physics[0].features
    cs = done.objects[f.axes]
    assert isinstance(cs, CoordinateSystem) and list(f.values) == ["x"]
    assert abs(np.dot(cs.x_axis, n)) == pytest.approx(np.sqrt(2.0))
    with pytest.raises(InputError, match="across no global axis"):
        applied(project, comp.id, mesh, SimpleNamespace(axes_features=set()))


def test_curved_faces_are_refused():
    project, comp, mesh = _model(_square(bent=1e-4), Feature(type="symmetry", selection="P/Cut"))
    with pytest.raises(InputError, match="needs a flat face"):
        applied(project, comp.id, mesh)


def test_heat_symmetry_is_the_natural_condition_and_flow_keeps_its_own():
    heat, comp, mesh = _model(_square(), Feature(type="symmetry", selection="P/Cut"),
                              Feature(type="temperature", selection="P/Cut"),
                              physics="heat_transfer")  # fmt: skip
    kept = applied(heat, comp.id, mesh).objects[comp.id].physics[0].features
    assert [f.type for f in kept] == ["temperature"]  # nothing written for the plane
    project = Project(name="p")
    both = project.add(_comp(physics=[
        Physics(type="heat_transfer", features=[Feature(type="symmetry", selection="P/Cut")]),
        Physics(type="fluid_flow", features=[Feature(type="symmetry", selection="P/Cut")]),
        Physics(type="solid_mechanics", features=[Feature(type="symmetry", selection="P/Cut")]),
    ]))  # fmt: skip
    out = applied(project, both.id, mesh).objects[both.id].physics
    assert [[f.type for f in p.features] for p in out] == [[], ["symmetry"], ["displacement"]]


def test_a_plane_goes_on_every_physics_that_takes_one():
    comp = _comp(physics=[Physics(type="solid_mechanics"),
                                        Physics(type="heat_transfer"),
                                        Physics(type="magnetic_fields")])  # fmt: skip
    assert plane_features(comp, "symmetry") == [
        (0, "symmetry"), (1, "symmetry"), (2, "magnetic_symmetry")]  # fmt: skip
    assert plane_features(comp, "antisymmetry") == [(0, "antisymmetry")]
    ops = plane_ops(comp, "symmetry", "P/Cut")
    assert [o.path for o in ops] == [f"physics.{i}.features" for i in range(3)]


def test_agents_add_a_symmetry_plane(tmp_path):
    pytest.importorskip("OCP")
    import asyncio

    from conftest_geometry import bar_project, write_bar_step

    from puzzle.agent.mcp import build_server
    from puzzle.commands import Session
    from puzzle.project import load_project, save_project

    physics = [Physics(type="solid_mechanics"), Physics(type="heat_transfer")]
    project, comp = bar_project(write_bar_step(tmp_path / "bar.step"), physics, 10.0)
    root = tmp_path / "p"
    save_project(project, root)
    (root / "bar.step").write_bytes((tmp_path / "bar.step").read_bytes())
    Session(root).submit({"command": "set_autonomy", "level": "act_in_budget"})
    server = build_server(root, "test")
    args = {"component": "Bar", "selection": "Bar/Root"}
    result = asyncio.run(server.call_tool("symmetry_plane", args))
    assert not result.is_error
    saved = load_project(root).objects[comp.id]
    assert [[f.type for f in p.features] for p in saved.physics] == [["symmetry"], ["symmetry"]]
