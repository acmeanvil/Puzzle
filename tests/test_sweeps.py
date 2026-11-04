"""ADR 0065 phase 1's history operations against closed forms: a pipe's and a tube's
volume π (R² − r²) L, a sweep of a square as a prism, a loft between two squares as a
frustum, a mirrored half doubling a box, scaling by f³ and by fx fy fz, a spline through
its points, and the faces each names."""

from __future__ import annotations

import math

import pytest

pytest.importorskip("OCP")

from OCP.BRepGProp import BRepGProp  # noqa: E402
from OCP.GProp import GProp_GProps  # noqa: E402

from puzzle.geometry.history import GeometryError, Op, regenerate  # noqa: E402


def volume(shape) -> float:
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return props.Mass()


def square(side, cx=0.0, cy=0.0):
    h = side / 2
    return {"points": [[cx - h, cy - h], [cx + h, cy - h], [cx + h, cy + h], [cx - h, cy + h]]}


def test_a_pipe_and_a_tube_along_a_straight_path():
    pipe = regenerate([Op("pipe", "P", {"r": 0.01, "path": [[0, 0, 0], [0.2, 0, 0]]})])
    assert volume(pipe.shape) == pytest.approx(math.pi * 0.01**2 * 0.2, rel=1e-6)
    assert {"P/wall", "P/start", "P/end"} <= set(pipe.names)
    tube = regenerate([Op("pipe", "T", {"r": 0.01, "inner": 0.006,
                                        "path": [[0, 0, 0], [0.2, 0, 0]]})])  # fmt: skip
    assert volume(tube.shape) == pytest.approx(math.pi * (0.01**2 - 0.006**2) * 0.2, rel=1e-6)
    assert "T/bore" in tube.names
    with pytest.raises(GeometryError, match="smaller than its outer"):
        regenerate([Op("pipe", "X", {"r": 0.01, "inner": 0.02, "path": [[0, 0, 0], [1, 0, 0]]})])


def test_a_pipe_along_a_curve_keeps_its_section():
    # a quarter circle of radius 0.1 m, the pipe's volume π r² × the arc length (Pappus)
    pts = [[0.1 * math.cos(t), 0.1 * math.sin(t), 0.0] for t in
           [i * math.pi / 2 / 8 for i in range(9)]]  # fmt: skip
    pipe = regenerate([Op("pipe", "P", {"r": 0.005, "path": pts})])
    assert volume(pipe.shape) == pytest.approx(math.pi * 0.005**2 * (math.pi / 2 * 0.1),
                                               rel=2e-3)  # fmt: skip


def test_a_sweep_of_a_square_is_a_prism():
    sweep = regenerate(
        [
            Op(
                "sweep",
                "S",
                {
                    "profile": square(0.02),
                    "plane": "yz",
                    "path": [[0, 0, 0], [0.1, 0, 0]],
                    "smooth": False,
                },
            )
        ]
    )
    assert volume(sweep.shape) == pytest.approx(0.02**2 * 0.1, rel=1e-6)
    assert {"S/start", "S/end", "S/side-1", "S/side-4"} <= set(sweep.names)


def test_a_loft_between_two_squares_is_a_frustum():
    loft = regenerate([Op("loft", "L", {"ruled": True, "sections": [
        {"plane": "xy", "offset": 0.0, "profile": square(0.04)},
        {"plane": "xy", "offset": 0.03, "profile": square(0.02)}]})])  # fmt: skip
    a1, a2, h = 0.04**2, 0.02**2, 0.03
    assert volume(loft.shape) == pytest.approx(h / 3 * (a1 + a2 + math.sqrt(a1 * a2)), rel=1e-6)
    assert {"L/start", "L/end"} <= set(loft.names)


def test_mirror_and_scale():
    half = [Op("box", "B", {"dx": 0.05, "dy": 0.02, "dz": 0.01})]
    whole = regenerate(half + [Op("mirror", "M", {"axis": "x", "x": 0.0})])
    assert volume(whole.shape) == pytest.approx(2 * 0.05 * 0.02 * 0.01, rel=1e-9)
    assert "B/+x~m" in whole.names and "B/+x" in whole.names
    assert "B/-x" not in whole.names  # the mirror plane's faces are inside now
    other = regenerate(half + [Op("mirror", "M", {"axis": "x", "keep": False})])
    assert volume(other.shape) == pytest.approx(0.05 * 0.02 * 0.01, rel=1e-9)
    assert "B/+x" in other.names  # the other hand keeps its names
    big = regenerate(half + [Op("scale", "S", {"factor": 2.0})])
    assert volume(big.shape) == pytest.approx(8 * 0.05 * 0.02 * 0.01, rel=1e-9)
    odd = regenerate(half + [Op("scale", "S", {"fx": 2.0, "fy": 1.0, "fz": 3.0})])
    assert volume(odd.shape) == pytest.approx(6 * 0.05 * 0.02 * 0.01, rel=1e-6)
    assert "B/+z" in odd.names


def test_a_spline_goes_through_its_points():
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.gp import gp_Pnt

    pts = [[0, 0, 0], [0.05, 0.02, 0.0], [0.1, 0.0, 0.01]]
    curve = regenerate([Op("spline", "C", {"points": pts})])
    for p in pts:
        v = BRepBuilderAPI_MakeVertex(gp_Pnt(*p)).Vertex()
        assert BRepExtrema_DistShapeShape(curve.shape, v).Value() < 1e-9
    swept = regenerate([Op("spline", "C", {"points": pts}),
                        Op("pipe", "P", {"r": 0.002, "along": "C", "mode": "new"})])  # fmt: skip
    assert volume(swept.shape) > 0


def test_measures_curvature_topology_sharp_edges_and_the_oriented_box():
    from puzzle.geometry.history import _faces
    from puzzle.geometry.measure import least_radius, oriented_box, sharp_edges, topology

    box = regenerate([Op("box", "B", {"dx": 0.05, "dy": 0.02, "dz": 0.01})])
    assert topology(box.shape) == {"solids": 1, "shells": 1, "faces": 6, "edges": 12,
                                   "free_edges": 0}  # fmt: skip
    assert sharp_edges(box.shape) == 12
    assert oriented_box(box.shape) == pytest.approx((0.05, 0.02, 0.01), abs=1e-6)
    assert least_radius(_faces(box.shape)) is None  # all flat
    round_ = regenerate([Op("box", "B", {"dx": 0.05, "dy": 0.02, "dz": 0.01}),
                         Op("fillet", "F", {"r": 0.003, "rule": {"parallel": "z"}})])  # fmt: skip
    assert least_radius(round_.names["F/edge-1"]) == pytest.approx(0.003, rel=1e-6)
    # top and bottom: 4 straight edges and 4 arcs each, all at right angles (16); the 8
    # edges where the fillets meet the sides are tangent and not counted
    assert sharp_edges(round_.shape) == 16 and topology(round_.shape)["edges"] == 24
    tilted = regenerate([Op("box", "B", {"dx": 0.05, "dy": 0.02, "dz": 0.01}),
                         Op("rotate", "R", {"angle": 30.0, "axis": "z"})])  # fmt: skip
    assert oriented_box(tilted.shape) == pytest.approx((0.05, 0.02, 0.01), abs=1e-5)


def test_loft_sections_typed_in_the_step_dialog():
    from puzzle.geometry.steps import format_sections, parse_sections

    got = parse_sections("xy 0: 0, 0; 50, 0; 50, 50 | xy 40: 10, 10; 40, 10; 40, 40")
    assert got[1]["offset"] == pytest.approx(0.04)
    assert got[1]["profile"]["points"][0] == pytest.approx([0.01, 0.01])
    assert parse_sections(format_sections(got)) == got
    with pytest.raises(ValueError, match="plane offset"):
        parse_sections("0, 0; 1, 1")
