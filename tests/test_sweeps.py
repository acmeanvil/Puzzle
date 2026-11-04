"""ADR 0065 phase 1's history operations against closed forms: a pipe's and a tube's
volume π (R² − r²) L, a sweep of a square as a prism, a loft between two squares as a
frustum, a mirrored half doubling a box, scaling by f³ and by fx fy fz, a spline through
its points, and the faces each names."""

from __future__ import annotations

import math

import numpy as np
import polars as pl
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


def test_thicken_a_sheet_and_patch_a_boundary():
    from OCP.BRepGProp import BRepGProp as G
    from OCP.GProp import GProp_GProps as P

    from puzzle.geometry.history import _bounds

    def area(shape):
        props = P()
        G.SurfaceProperties_s(shape, props)
        return props.Mass()

    sheet = [Op("sheet", "S", {"points": [[0, 0, 0], [0.1, 0, 0], [0.1, 0.05, 0], [0, 0.05, 0]]})]
    flat = regenerate(sheet + [Op("thicken", "T", {"t": 0.004})])
    assert volume(flat.shape) == pytest.approx(0.1 * 0.05 * 0.004, rel=1e-9)
    assert {"S/face", "T/offset", "T/side"} <= set(flat.names)
    both = regenerate(sheet + [Op("thicken", "T", {"t": 0.004, "both": True})])
    assert (_bounds(both.shape)[2], _bounds(both.shape)[5]) == pytest.approx((-0.002, 0.002))
    assert "S/face" in both.names
    # a cylindrical panel, r 0.1 m, 80° by 0.2 m, thickened 5 mm outward: the annular
    # sector's volume θ/2 ((r + t)² − r²) L
    panel = regenerate([Op("curved_sheet", "C", {"r": 0.1, "length": 0.2, "angle": 80.0}),
                        Op("thicken", "T", {"t": 0.005})])  # fmt: skip
    assert volume(panel.shape) == pytest.approx(
        math.radians(80) / 2 * (0.105**2 - 0.1**2) * 0.2, rel=1e-6)  # fmt: skip
    patch = regenerate([Op("patch", "P", {"points": [[0, 0, 0], [0.1, 0, 0], [0.1, 0.1, 0],
                                                     [0, 0.1, 0]]})])  # fmt: skip
    assert area(patch.shape) == pytest.approx(0.01, rel=1e-6) and "P/patch" in patch.names
    with pytest.raises(GeometryError, match="must be positive"):
        regenerate(sheet + [Op("thicken", "T", {"t": 0})])


def test_regions_on_faces_and_a_dxf_spline_kept_smooth(tmp_path):
    from OCP.BRepGProp import BRepGProp as G
    from OCP.GProp import GProp_GProps as P

    def area(shape):
        props = P()
        G.SurfaceProperties_s(shape, props)
        return props.Mass()

    a = Op("sheet", "A", {"points": [[0, 0, 0], [0.1, 0, 0], [0.1, 0.1, 0], [0, 0.1, 0]]})
    b = {"points": [[0.05, 0, 0], [0.15, 0, 0], [0.15, 0.1, 0], [0.05, 0.1, 0]]}
    for mode, want in (("add", 0.015), ("cut", 0.005), ("intersect", 0.005)):
        got = regenerate([a, Op("sheet", "B", {**b, "mode": mode})])
        assert area(got.shape) == pytest.approx(want, rel=1e-9), mode
    ezdxf = pytest.importorskip("ezdxf")
    doc = ezdxf.new()
    msp = doc.modelspace()
    # a half-disc outline: a straight base and a spline arch through points on the
    # circle r = 50 mm; the spline kept smooth gives the half disc's area π r² / 2
    arch = [(50 * math.cos(t), 50 * math.sin(t)) for t in [i * math.pi / 16 for i in range(17)]]
    msp.add_spline(fit_points=[(x, y, 0) for x, y in arch])
    msp.add_line((-50, 0), (50, 0))
    doc.header["$INSUNITS"] = 4  # mm
    doc.saveas(tmp_path / "arch.dxf")
    shape = regenerate([Op("extrude", "E", {"profile": {"dxf": "arch.dxf"}, "distance": 0.01})],
                       root=tmp_path).shape  # fmt: skip
    assert volume(shape) == pytest.approx(math.pi * 0.05**2 / 2 * 0.01, rel=2e-4)


def test_a_sketch_with_a_spline():
    r = 0.05
    ts = [i * math.pi / 8 for i in range(9)]
    points = {f"P{i}": [r * math.cos(t), r * math.sin(t)] for i, t in enumerate(ts)}
    sketch = {"points": points, "lines": {"base": ["P8", "P0"]},
              "splines": {"arch": [f"P{i}" for i in range(9)]}}  # fmt: skip
    got = regenerate([Op("extrude", "E", {"profile": {"sketch": sketch}, "distance": 0.01})])
    assert volume(got.shape) == pytest.approx(math.pi * r**2 / 2 * 0.01, rel=5e-4)
    assert "E/arch" in got.names and "E/base" in got.names


def test_the_sketch_editors_spline_tool():
    pytest.importorskip("PySide6")
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from puzzle.gui.sketcher import SketchModel, summary

    QApplication.instance() or QApplication([])
    m = SketchModel()
    a, b, c = m.add_point(0, 0), m.add_point(0.01, 0.01), m.add_point(0.02, 0)
    name = m.add_spline([a, b, c])
    assert m.entities()[name] == "spline" and summary(m.spec) == "1 spline"
    m.delete(b)  # a point of it: the spline goes with it
    assert not m.spec["splines"]


def test_intersection_curves_and_points(tmp_path):
    from optimise_beam import beam_project

    from puzzle.model.schema import Component, ConstructionGeometry, Operator, PartInstance
    from puzzle.operators import evaluate as ev
    from puzzle.project import save_project

    p, _ = beam_project()  # a box 200 x 20 x 6 mm
    comp = next(iter(p.of_type(Component)))
    comp.parts.append(PartInstance(name="B2", geometry=comp.parts[0].geometry,
                                   translation=(0.0, 0.0, 0.006)))  # stacked on top  # fmt: skip
    p.add(ConstructionGeometry(name="Mid", type="plane", origin=(0.1, 0, 0), normal=(1, 0, 0)))
    root = tmp_path / "beam"
    save_project(p, root)
    cut = p.add(Operator(name="Cut", type="intersection", target=comp.id, selection="B",
                         other="Mid"))  # fmt: skip
    got = ev.value(ev.Context(p, None, root=root), cut).frame
    assert np.allclose(got["x"].to_numpy(), 0.1)  # the section's outline at x = 0.1
    per = got.group_by("curve").agg(pl.col("s").max()).sort("curve")["s"].to_list()
    assert sorted(per) == pytest.approx(sorted([0.02, 0.006, 0.02, 0.006]))  # its four sides
    meet = p.add(Operator(name="Meet", type="intersection", target=comp.id,
                          selection="B", other="B2"))  # fmt: skip
    got = ev.value(ev.Context(p, None, root=root), meet).frame
    assert np.allclose(got["z"].to_numpy(), 0.006)  # where the stack's faces touch


def test_deform_twist_taper_bend_and_box():
    from puzzle.geometry.history import _bounds

    box = [Op("box", "B", {"dx": 0.02, "dy": 0.02, "dz": 0.2})]
    v0 = 0.02 * 0.02 * 0.2

    def run(kind, **extra):
        return regenerate(box + [Op("deform", "D", {"kind": kind, "axis": "z", **extra})])

    twisted = run("twist", angle=90.0)
    assert volume(twisted.shape) == pytest.approx(v0, rel=1e-4) and "B/+z" in twisted.names
    tapered = run("taper", factor=0.5)  # to half the section's size: a frustum's volume
    assert volume(tapered.shape) == pytest.approx(0.2 / 3 * (4e-4 + 1e-4 + 2e-4), rel=1e-4)
    bent = run("bend", angle=90.0, toward="y")  # round a radius L / θ = 0.1273 m
    r = 0.2 / (math.pi / 2)
    assert volume(bent.shape) == pytest.approx(v0, rel=2e-3)
    assert _bounds(bent.shape)[4] == pytest.approx(0.01 + r, rel=2e-3)  # every fibre at 90°
    sheared = run("box", corners=[[0, 0, 0]] * 4 + [[0.01, 0, 0]] * 4)  # the top moved 10 mm
    assert _bounds(sheared.shape)[3] == pytest.approx(0.03, rel=1e-4)
    assert volume(sheared.shape) == pytest.approx(v0, rel=1e-4)
    with pytest.raises(GeometryError, match="twist, taper, bend or box"):
        run("melt")
