"""Splitting faces by a projected sketch (`split_face`, ADR 0014's "split face by
sketch"): only faces are split, never the solid, and the pieces inside the outline
are named "<op>/region". Checked by areas and volumes against closed forms."""

import math

import pytest

pytest.importorskip("OCP")

from puzzle.geometry.history import (  # noqa: E402
    GeometryError,
    Op,
    body_signature,
    regenerate,
    signature,
)

SQUARE = {"points": [[0.04, 0.04], [0.06, 0.04], [0.06, 0.06], [0.04, 0.06]]}
CIRCLE = {"points": {"O": [0.05, 0.0]}, "circles": {"c": ["O", 0.01]}, "constraints": []}


def _area(faces):
    """Exact areas: `signature`'s quick integration is ~5e-4 off on trimmed curved faces."""
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    total = 0.0
    for f in faces:
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(f, props, 1e-9)
        total += props.Mass()
    return total


def _plate():
    return Op("box", "Plate", {"dx": 0.1, "dy": 0.1, "dz": 0.02})


def test_a_square_on_the_top_splits_it_and_keeps_the_solid():
    shape = regenerate([_plate(), Op("split_face", "Pad", {"profile": SQUARE, "offset": 0.02})])
    region = shape.names["Pad/region"]
    assert _area(region) == pytest.approx(0.02**2, rel=1e-9)
    # The top keeps its name on both pieces: the whole top still adds up.
    assert _area(shape.names["Plate/+z"]) == pytest.approx(0.1**2, rel=1e-9)
    assert len(shape.names["Plate/+z"]) == 2
    [body] = shape.solids()
    assert body_signature(body)[0] == pytest.approx(0.1 * 0.1 * 0.02, rel=1e-9)
    assert len(shape.faces()) == 7


def test_a_sketch_step_projected_onto_a_curved_wall():
    """A circle of r = 10 mm sketched on z = 0, projected up onto a bar's round side
    (r = 50 mm along x): the patch is the cylinder's area over the disc."""
    ops = [
        Op("cylinder", "Bar", {"r": 0.05, "h": 0.1, "axis": "x"}),
        Op("sketch", "Spot", {"sketch": CIRCLE, "plane": "xy"}),
        Op("split_face", "Patch", {"profile": {"sketch": "Spot"}, "faces": ["Bar/wall"],
                                   "direction": "forward"}),
    ]  # fmt: skip
    shape = regenerate(ops)
    r, big = 0.01, 0.05
    # Area of z = sqrt(R^2 - y^2) over the disc x^2 + y^2 <= r^2: the integral of
    # R / sqrt(R^2 - y^2), as the series pi r^2 (1 + r^2 / (8 R^2) + 3 r^4 / (64 R^4)).
    exact = math.pi * r**2 * (1 + r**2 / (8 * big**2) + 3 * r**4 / (64 * big**4))
    assert _area(shape.names["Patch/region"]) == pytest.approx(exact, rel=1e-5)
    for f in shape.names["Patch/region"]:
        assert signature(f)[2][2] > 0  # forward only: the top, not the bottom too
    [body] = shape.solids()  # the solid isn't cut (body_signature's quick volume: 1e-3)
    assert body_signature(body)[0] == pytest.approx(math.pi * big**2 * 0.1, rel=1e-3)


def test_both_ways_reaches_both_sides():
    ops = [
        Op("cylinder", "Bar", {"r": 0.05, "h": 0.1, "axis": "x"}),
        Op("split_face", "Patch", {"profile": {"sketch": CIRCLE}, "faces": ["Bar/wall"]}),
    ]
    region = regenerate(ops).names["Patch/region"]
    assert {signature(f)[2][2] > 0 for f in region} == {True, False}


def test_a_sketch_off_every_face_is_refused():
    with pytest.raises(GeometryError, match="no face lies on the sketch's plane"):
        regenerate([_plate(), Op("split_face", "Pad", {"profile": SQUARE, "offset": 0.01})])
    with pytest.raises(GeometryError, match="no face 'Plate/top'"):
        regenerate([_plate(), Op("split_face", "Pad", {"profile": SQUARE, "faces": ["Plate/top"]})])


def test_selections_follow_the_region_through_an_edit():
    """The region is a name like any other: a later fillet leaves it in place."""
    ops = [
        _plate(),
        Op("split_face", "Pad", {"profile": SQUARE, "offset": 0.02}),
        Op("fillet", "Round", {"rule": {"parallel": "z", "outer": True}, "r": 0.002}),
    ]
    shape = regenerate(ops)
    assert _area(shape.names["Pad/region"]) == pytest.approx(0.02**2, rel=1e-9)


def test_the_region_is_meshed_as_a_selection(tmp_path):
    """End to end: a selection on the region becomes a mesh group on the top only."""
    import numpy as np

    from puzzle.mesh import generate_mesh
    from puzzle.model import (
        Component,
        Geometry,
        MeshRecipe,
        NamedSelection,
        PartInstance,
        Project,
        Quantity,
    )
    from puzzle.model.schema import HistoryStep

    p = Project(name="pad")
    geo = p.add(Geometry(name="Plate", history=[
        HistoryStep(kind="box", name="Plate", args={"dx": 0.1, "dy": 0.1, "dz": 0.02}),
        HistoryStep(kind="split_face", name="Pad", args={"profile": SQUARE, "offset": 0.02}),
    ], selections=[NamedSelection(name="Pad", entity="face", members=["Pad/region"])]))  # fmt: skip
    mesh = p.add(MeshRecipe(name="Mesh", global_size=Quantity(value=5, unit="mm")))
    comp = p.add(Component(name="Plate", parts=[PartInstance(name="Plate", geometry=geo.id)],
                           mesh=mesh.id))  # fmt: skip
    result = generate_mesh(p, comp.id, tmp_path, tmp_path / "m")
    assert result.warnings == []
    xyz = result.mesh.node_coords(result.mesh.groups["Plate/Pad"].node_ids)
    assert np.allclose(xyz[:, 2], 0.02)
    assert xyz[:, :2].min() == pytest.approx(0.04) and xyz[:, :2].max() == pytest.approx(0.06)
