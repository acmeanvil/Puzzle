"""Coordinate systems that follow something else (ADR 0049 step 3): from a face or an
edge, attached to a part, relative to another system; re-derived after edits."""

import numpy as np
import pytest

pytest.importorskip("OCP")

from puzzle.axes import AxesError, absolute  # noqa: E402
from puzzle.model import Component, MeshRecipe, PartInstance, Project, Quantity  # noqa: E402
from puzzle.model.schema import CoordinateSystem, Geometry, HistoryStep  # noqa: E402


def _box(p, dz=0.01):
    return p.add(Geometry(name="Block", history=[
        HistoryStep(kind="box", name="B", args={"dx": 0.04, "dy": 0.02, "dz": dz}),
        HistoryStep(kind="cylinder", name="Pin", args={"r": 0.005, "h": 0.03, "x": 0.02,
                                                       "y": 0.01, "z": dz, "axis": "z"}),
        HistoryStep(kind="sphere", name="Ball", args={"r": 0.004, "x": -0.01, "y": 0.0, "z": 0.0}),
    ]))  # fmt: skip


def _axes(p, **kw):
    return p.add(CoordinateSystem(name=kw.pop("name", "A"), **kw))


def test_from_a_flat_face_its_centroid_and_outward_normal(tmp_path):
    p = Project(name="axes")
    geo = _box(p)
    cs = absolute(p, _axes(p, geometry=geo.id, entity="face", entity_name="B/-z"), tmp_path)
    assert np.allclose(cs.centre(), [0.02, 0.01, 0.0])
    r = cs.orientation()
    assert np.allclose(r[:, 2], [0, 0, -1])  # out of the block, downwards
    assert np.allclose(r[:, 0], [1, 0, 0])  # x from the system's own x_axis, in the face


def test_from_a_round_face_its_axis_and_from_a_ball_its_centre(tmp_path):
    p = Project(name="axes")
    geo = _box(p)
    pin = absolute(p, _axes(p, type="cylindrical", geometry=geo.id, entity="face",
                            entity_name="Pin/wall"), tmp_path)  # fmt: skip
    assert np.allclose(pin.orientation()[:, 2], [0, 0, 1]) or np.allclose(
        pin.orientation()[:, 2], [0, 0, -1])  # fmt: skip
    assert np.allclose(pin.centre(), [0.02, 0.01, 0.025])  # on the axis, level with the face
    ball = absolute(p, _axes(p, type="spherical", geometry=geo.id, entity="face",
                             entity_name="Ball/surface", name="S"), tmp_path)  # fmt: skip
    assert np.allclose(ball.centre(), [-0.01, 0.0, 0.0])


def test_from_an_edge_its_tangent_where_asked(tmp_path):
    p = Project(name="axes")
    geo = _box(p)
    cs = absolute(p, _axes(p, geometry=geo.id, entity="edge", entity_name="B/+x|B/-z",
                           along=0.25), tmp_path)  # fmt: skip
    x = cs.orientation()[:, 0]
    assert np.allclose(np.abs(x), [0, 1, 0])  # the edge runs along y at x = 40 mm, z = 0
    assert cs.centre()[0] == pytest.approx(0.04) and cs.centre()[2] == pytest.approx(0.0)
    assert cs.centre()[1] in (pytest.approx(0.005), pytest.approx(0.015))


def test_relative_to_another_system(tmp_path):
    p = Project(name="axes")
    parent = _axes(p, name="Turned", origin=(1.0, 0.0, 0.0), angles=(0.0, 0.0, 90.0))
    child = absolute(p, _axes(p, name="Child", origin=(1.0, 0.0, 0.0), relative_to=parent.id))
    assert np.allclose(child.centre(), [1.0, 1.0, 0.0])
    assert np.allclose(child.orientation()[:, 0], [0, 1, 0])
    loop = _axes(p, name="Loop", relative_to=child.id)
    p.get(child.id, CoordinateSystem).relative_to = loop.id
    with pytest.raises(AxesError, match="relative to themselves"):
        absolute(p, loop)


def test_attached_to_a_part_it_follows_the_placement(tmp_path):
    p = Project(name="axes")
    geo = _box(p)
    mesh = p.add(MeshRecipe(name="M", global_size=Quantity(value=5, unit="mm")))
    comp = p.add(Component(name="C", mesh=mesh.id, parts=[
        PartInstance(name="Block", geometry=geo.id, translation=(0.5, 0.0, 0.0),
                     rotation=(0.0, 0.0, 90.0))]))  # fmt: skip
    cs = _axes(p, component=comp.id, part="Block", entity="face", entity_name="B/-z")
    at = absolute(p, cs, tmp_path)
    # (0.02, 0.01, 0) turned 90° about z is (-0.01, 0.02, 0), then moved 0.5 in x.
    assert np.allclose(at.centre(), [0.49, 0.02, 0.0])
    assert np.allclose(at.orientation()[:, 0], [0, 1, 0])  # x turned with the part
    comp.parts[0].translation = (0.0, 0.0, 0.0)
    assert np.allclose(absolute(p, cs, tmp_path).centre(), [-0.01, 0.02, 0.0])


def test_it_re_derives_after_an_edit_and_names_a_lost_face(tmp_path):
    p = Project(name="axes")
    geo = _box(p)
    cs = _axes(p, geometry=geo.id, entity="face", entity_name="B/+z")
    assert absolute(p, cs, tmp_path).centre()[2] == pytest.approx(0.01)
    geo.history = _box(Project(name="x"), dz=0.03).history
    assert absolute(p, cs, tmp_path).centre()[2] == pytest.approx(0.03)
    cs.entity_name = "B/top"
    with pytest.raises(AxesError, match="face 'B/top' is gone"):
        absolute(p, cs, tmp_path)


def test_plain_systems_keep_their_hashes():
    from puzzle.project.graph import content_hashes

    p = Project(name="axes")
    cs = _axes(p, origin=(0.1, 0.0, 0.0))
    before = content_hashes(p)[cs.id]
    # The hash leaves step 3's defaults out, as before they existed.
    data = cs.model_dump(mode="json")
    assert "relative_to" in data and before == content_hashes(p)[cs.id]
    cs.relative_to = _axes(p, name="Other").id
    assert content_hashes(p)[cs.id] != before


def test_a_mesh_box_along_a_turned_system(tmp_path):
    """ADR 0049 step 5: a fine box turned 45° about z refines inside it, not beside it."""
    from puzzle.mesh import generate_mesh
    from puzzle.model.schema import BoxControl, NamedSelection

    p = Project(name="box")
    geo = p.add(Geometry(name="Plate", history=[
        HistoryStep(kind="box", name="P", args={"dx": 0.1, "dy": 0.1, "dz": 0.005})],
        selections=[NamedSelection(name="Body", entity="body", members=["Body"])]))  # fmt: skip
    turned = _axes(p, name="Diagonal", origin=(0.05, 0.05, 0.0), angles=(0.0, 0.0, 45.0))
    fine = BoxControl(name="Strip", low=(-0.04, -0.005, -0.01), high=(0.04, 0.005, 0.01),
                      size=Quantity(value=0.5, unit="mm"), axes=turned.id)  # fmt: skip
    mesh = p.add(MeshRecipe(name="M", global_size=Quantity(value=5, unit="mm"),
                            element_order=1, controls=[fine]))  # fmt: skip
    comp = p.add(Component(name="C", mesh=mesh.id, parts=[PartInstance(name="P", geometry=geo.id)]))
    xyz = generate_mesh(p, comp.id, tmp_path, tmp_path / "m").mesh.coords
    local = (xyz - [0.05, 0.05, 0.0]) @ turned.orientation()  # in the turned axes
    inside = (np.abs(local[:, 0]) < 0.03) & (np.abs(local[:, 1]) < 0.004)
    beside = (np.abs(local[:, 0]) < 0.03) & (np.abs(local[:, 1] - 0.02) < 0.004)
    assert inside.sum() > 10 * max(beside.sum(), 1)
