"""ADR 0048 rounds 2 and 3: 2D shapes and operations with named sides, and planar
imports. Measured against exact lengths and areas."""

import math

import pytest

pytest.importorskip("OCP")

from OCP.BRepGProp import BRepGProp  # noqa: E402
from OCP.GProp import GProp_GProps  # noqa: E402

from puzzle.geometry.history import (  # noqa: E402
    GeometryError,
    Op,
    TopAbs_EDGE,
    _bounds,
    _faces,
    _subshapes,
    regenerate,
)


def _area(shape) -> float:
    g = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, g)
    return g.Mass()


def _length(edges) -> float:
    total = 0.0
    for e in edges:
        g = GProp_GProps()
        BRepGProp.LinearProperties_s(e, g)
        total += g.Mass()
    return total


def _lengths(r) -> dict[str, float]:
    return {k: _length(v) for k, v in r.edges.items()}


def _normal_z(face) -> float:
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.TopAbs import TopAbs_REVERSED

    n = BRepAdaptor_Surface(face).Plane().Axis().Direction().Z()
    return -n if face.Orientation() == TopAbs_REVERSED else n


RECT = Op("rectangle", "P", {"dx": 0.1, "dy": 0.05})


def test_shapes_name_every_side():
    r = regenerate([RECT])
    assert _lengths(r) == pytest.approx({"P/-x": 0.05, "P/+x": 0.05, "P/-y": 0.1, "P/+y": 0.1})
    assert _area(r.shape) == pytest.approx(0.005) and list(r.names) == ["P/face"]
    assert _normal_z(_faces(r.shape)[0]) == pytest.approx(1.0)
    r = regenerate([Op("circle", "C", {"r": 0.01, "x": 0.02})])
    assert _lengths(r) == pytest.approx({"C/outer": 2 * math.pi * 0.01})
    r = regenerate([Op("annulus", "A", {"r_inner": 0.01, "r_outer": 0.02})])
    assert _lengths(r) == pytest.approx({"A/inner": 0.02 * math.pi, "A/outer": 0.04 * math.pi})
    assert _area(r.shape) == pytest.approx(math.pi * (0.02**2 - 0.01**2))
    r = regenerate([Op("polygon", "H", {"sides": 6, "r": 0.01, "angle": 15})])
    assert _lengths(r) == pytest.approx({f"H/side-{k}": 0.01 for k in range(1, 7)})
    assert _area(r.shape) == pytest.approx(1.5 * math.sqrt(3) * 0.01**2)
    r = regenerate([Op("slot", "S", {"length": 0.05, "width": 0.01, "angle": 30})])
    half_turn = math.pi * 0.005
    assert _lengths(r) == pytest.approx({"S/side-1": 0.04, "S/side-2": 0.04,
                                         "S/end-1": half_turn, "S/end-2": half_turn})  # fmt: skip
    assert _area(r.shape) == pytest.approx(0.04 * 0.01 + math.pi * 0.005**2)
    # On another plane, at an offset, sized by a parameter.
    r = regenerate([Op("rectangle", "P", {"dx": "W", "dy": 0.02, "plane": "xz", "offset": 0.3})],
                   {"W": 0.07})  # fmt: skip
    assert _bounds(r.shape) == pytest.approx((0, 0.3, 0, 0.07, 0.3, 0.02), abs=1e-9)


@pytest.mark.parametrize("bad", [{"dx": 0, "dy": 1}, {"dx": -1, "dy": 1}])
def test_bad_sizes_are_refused(bad):
    with pytest.raises(GeometryError, match="positive"):
        regenerate([Op("rectangle", "P", bad)])
    with pytest.raises(GeometryError, match="inner radius"):
        regenerate([Op("annulus", "A", {"r_inner": 0.02, "r_outer": 0.01})])
    with pytest.raises(GeometryError, match="length must exceed"):
        regenerate([Op("slot", "S", {"length": 0.01, "width": 0.02})])


def test_booleans_keep_the_sides_names():
    hole = Op("circle", "C", {"r": 0.01, "x": 0.05, "y": 0.025, "mode": "cut"})
    r = regenerate([RECT, hole])
    assert _area(r.shape) == pytest.approx(0.005 - math.pi * 1e-4)
    assert _lengths(r)["C/outer"] == pytest.approx(0.02 * math.pi) and "P/-x" in r.edges
    more = {"dx": 0.05, "dy": 0.05, "x0": 0.08}
    union = regenerate([RECT, Op("rectangle", "Q", {**more, "mode": "add"})])
    assert len(_faces(union.shape)) == 1 and _area(union.shape) == pytest.approx(0.0065)
    assert _lengths(union)["Q/+x"] == pytest.approx(0.05) and "P/+x" not in union.edges
    common = regenerate([RECT, Op("rectangle", "Q", {**more, "mode": "intersect"})])
    assert _area(common.shape) == pytest.approx(0.001)
    assert _lengths(common)["P/+x"] == pytest.approx(0.05)  # the overlap's right side
    # Separate: two regions sharing the edge between them (4 + 4 - 1 edges).
    side = regenerate([RECT, Op("rectangle", "Q", {**more, "x0": 0.1, "mode": "separate"})])
    assert len(_faces(side.shape)) == 2 and len(_subshapes(side.shape, TopAbs_EDGE)) == 7
    assert side.edges["P/+x"][0].IsSame(side.edges["Q/-x"][0])


def test_corners_rounded_and_cut():
    r = regenerate([RECT, Op("fillet", "R", {"r": 0.01, "rule": {"all": True}})])
    assert _area(r.shape) == pytest.approx(0.005 - 4 * (1 - math.pi / 4) * 1e-4)
    lengths = _lengths(r)
    assert [lengths[f"R/edge-{k}"] for k in range(1, 5)] == pytest.approx([0.005 * math.pi] * 4)
    assert lengths["P/-y"] == pytest.approx(0.08)  # trimmed by both corners, name kept
    # The 3D default rule (outer edges along z) means a flat part's outer corners.
    default = regenerate(
        [RECT, Op("fillet", "R", {"r": 0.01, "rule": {"parallel": "z", "outer": True}})]
    )
    assert _area(default.shape) == pytest.approx(_area(r.shape))
    one = regenerate([RECT, Op("fillet", "R", {"r": 0.01, "rule": {"between": ["P/-x", "P/+y"]}})])
    assert _lengths(one)["P/-x"] == pytest.approx(0.04) and "R/edge-2" not in one.edges
    cut = regenerate([RECT, Op("chamfer", "K", {"d": 0.01, "rule": {"near": [0.1, 0]}})])
    assert _area(cut.shape) == pytest.approx(0.005 - 0.5e-4)
    assert _lengths(cut)["K/edge-1"] == pytest.approx(0.01 * math.sqrt(2))
    # Holes' corners stay sharp with "outer".
    square = Op("rectangle", "H", {"dx": 0.02, "dy": 0.02, "x0": 0.04, "y0": 0.01, "mode": "cut"})
    outer = regenerate([RECT, square, Op("fillet", "R", {"r": 0.005, "rule": {"outer": True}})])
    assert "R/edge-5" not in outer.edges and _lengths(outer)["H/-x"] == pytest.approx(0.02)


def test_a_corner_two_regions_share_is_refused():
    right = Op("rectangle", "Q", {"dx": 0.05, "dy": 0.05, "x0": 0.1, "mode": "separate"})
    with pytest.raises(GeometryError, match="shared by two regions"):
        regenerate([RECT, right, Op("fillet", "R", {"r": 0.01, "rule": {"all": True}})])
    r = regenerate([RECT, right, Op("fillet", "R", {"r": 0.01, "rule": {"near": [0, 0]}})])
    assert len(_faces(r.shape)) == 2 and len(_subshapes(r.shape, TopAbs_EDGE)) == 8
    assert r.edges["P/+x"][0].IsSame(r.edges["Q/-x"][0])  # still sharing their edge


def _wall(points, t=0.005, **more):
    return regenerate([Op("offset", "W", {"line": {"points": points}, "t": t, **more})])


def test_a_wall_along_a_line():
    straight = _wall([[0, 0], [0.1, 0]])
    assert _area(straight.shape) == pytest.approx(0.0005)
    assert _lengths(straight) == pytest.approx({"W/left": 0.1, "W/right": 0.1,
                                                "W/start": 0.005, "W/end": 0.005})  # fmt: skip
    assert _bounds(straight.shape)[4] == pytest.approx(0.005)  # left of +x is +y
    bend, t = [[0, 0], [0.1, 0], [0.1, 0.08]], 0.005
    # Mitred at a left turn: the inside loses t², the outside gains it.
    assert _area(_wall(bend).shape) == pytest.approx(t * 0.18 - t**2)
    assert _area(_wall(bend, side="right").shape) == pytest.approx(t * 0.18 + t**2)
    both = _wall(bend, side="both")
    assert _area(both.shape) == pytest.approx(t * 0.18)
    assert _lengths(both)["W/left"] == pytest.approx(0.175)
    ring = _wall([[0, 0], [0.1, 0], [0.1, 0.1], [0, 0.1]], closed=True)
    assert _area(ring.shape) == pytest.approx(0.1**2 - 0.09**2)
    assert set(ring.edges) == {"W/left", "W/right"}
    with pytest.raises(GeometryError, match="thicker"):
        _wall([[0, 0], [0.1, 0], [0.1, 0.003]])
    with pytest.raises(GeometryError, match="turns back"):
        _wall([[0, 0], [0.1, 0], [0, 0]])
    with pytest.raises(GeometryError, match="side must be"):
        _wall([[0, 0], [0.1, 0]], side="up")


def test_mirror_and_pattern_on_faces():
    half = Op("rectangle", "P", {"dx": 0.05, "dy": 0.05})
    r = regenerate([half, Op("mirror", "M", {"axis": "x", "x": 0.0})])
    assert len(_faces(r.shape)) == 1 and _area(r.shape) == pytest.approx(0.005)
    assert _normal_z(_faces(r.shape)[0]) == pytest.approx(1.0)
    assert _lengths(r)["P/+x~m"] == pytest.approx(0.05)
    hole = Op("circle", "C", {"r": 0.005, "x": 0.01, "y": 0.01, "mode": "cut"})
    r = regenerate([RECT, hole, Op("pattern", "N", {"feature": "C", "count": 3, "dx": 0.02})])
    assert _area(r.shape) == pytest.approx(0.005 - 3 * math.pi * 0.005**2)
    assert {"C/outer", "C#2/outer", "C#3/outer"} <= set(r.edges)


def _faces_step(path, faces_mm):
    """A STEP file of flat faces (written in mm, as OCCT's writer assumes)."""
    from OCP.BRep import BRep_Builder
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
    from OCP.gp import gp_Pnt
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
    from OCP.TopoDS import TopoDS_Compound

    compound, builder = TopoDS_Compound(), BRep_Builder()
    builder.MakeCompound(compound)
    for pts in faces_mm:
        poly = BRepBuilderAPI_MakePolygon()
        for q in pts:
            poly.Add(gp_Pnt(*q))
        poly.Close()
        builder.Add(compound, BRepBuilderAPI_MakeFace(poly.Wire(), True).Face())
    from puzzle.geometry.prep import _set_unit

    writer = STEPControl_Writer()
    _set_unit("MM")  # an earlier test may leave OCCT's unit at metres
    writer.Transfer(compound, STEPControl_AsIs)
    writer.Write(str(path))
    return path


def test_flat_faces_import_and_lie_on_xy(tmp_path):
    raised = _faces_step(
        tmp_path / "raised.step", [[(0, 0, 5), (40, 0, 5), (40, 20, 5), (0, 20, 5)]]
    )
    kept = regenerate([Op("import", "I", {"file": str(raised)})])
    assert _bounds(kept.shape) == pytest.approx((0, 0, 0.005, 0.04, 0.02, 0.005), abs=1e-9)
    flat = regenerate([Op("import", "I", {"file": str(raised), "flat": True})])
    assert _bounds(flat.shape) == pytest.approx((0, 0, 0, 0.04, 0.02, 0), abs=1e-9)
    assert sorted(flat.edges) == [f"I/edge-{k}" for k in range(1, 5)]
    xz = _faces_step(tmp_path / "xz.step", [[(0, 0, 0), (40, 0, 0), (40, 0, 20), (0, 0, 20)]])
    laid = regenerate([Op("import", "I", {"file": str(xz), "flat": True})])  # (x, z) -> (x, y)
    assert _bounds(laid.shape) == pytest.approx((0, 0, 0, 0.04, 0.02, 0), abs=1e-9)
    two = _faces_step(
        tmp_path / "two.step",
        [
            [(0, 0, 0), (40, 0, 0), (40, 20, 0), (0, 20, 0)],
            [(40, 0, 0), (60, 0, 0), (60, 20, 0), (40, 20, 0)],
        ],
    )
    apart = regenerate([Op("import", "I", {"file": str(two)})])
    joined = regenerate([Op("import", "I", {"file": str(two)}), Op("fragment", "F", {})])
    assert len(_subshapes(apart.shape, TopAbs_EDGE)) == 8
    assert len(_subshapes(joined.shape, TopAbs_EDGE)) == 7  # the shared edge, once
    assert sorted(joined.names) == ["I/face-1", "I/face-2"]
    skew = _faces_step(
        tmp_path / "skew.step",
        [[(0, 0, 0), (40, 0, 0), (0, 20, 5)], [(0, 0, 0), (40, 0, 0), (0, -20, 5)]],
    )
    with pytest.raises(GeometryError, match="one plane"):
        regenerate([Op("import", "I", {"file": str(skew), "flat": True})])


def test_dxf_regions(tmp_path):
    ezdxf = pytest.importorskip("ezdxf")
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 4
    m = doc.modelspace()
    m.add_lwpolyline([(0, 0), (40, 0), (40, 20), (0, 20)], close=True)
    m.add_lwpolyline([(40, 0), (60, 0), (60, 20), (40, 20)], close=True)
    m.add_circle((10, 10), 3)  # a hole in the first
    doc.saveas(tmp_path / "closed.dxf")
    r = regenerate([Op("face", "D", {"profile": {"dxf": "closed.dxf"}})], root=tmp_path)
    areas = sorted(_area(f) for f in _faces(r.shape))
    assert areas == pytest.approx([0.0004, 0.0008 - math.pi * 0.003**2])
    assert len(_subshapes(r.shape, TopAbs_EDGE)) == 8  # 5 + 4, one shared
    # Lines drawn once across both regions, and a stray construction line.
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 4
    m = doc.modelspace()
    for a, b in [((0, 0), (60, 0)), ((60, 0), (60, 20)), ((60, 20), (0, 20)), ((0, 20), (0, 0)),
                 ((40, 0), (40, 20)), ((10, 5), (20, 5))]:  # fmt: skip
        m.add_line(a, b)
    doc.saveas(tmp_path / "lines.dxf")
    r = regenerate([Op("face", "D", {"profile": {"dxf": "lines.dxf"}})], root=tmp_path)
    assert sorted(_area(f) for f in _faces(r.shape)) == pytest.approx([0.0004, 0.0008])
    assert len(_subshapes(r.shape, TopAbs_EDGE)) == 7
    assert _length(r.edges["D/side-1"]) == pytest.approx(0.06)  # the bottom, in two pieces
    assert len(r.edges["D/side-5"]) == 1  # the shared line, once


def test_every_2d_tool_builds_from_its_defaults():
    """The ribbon's 2D group: each tool has fields, a readable name, and its defaults
    make a region."""
    from puzzle.geometry.steps import STEPS, parse_profile
    from puzzle.gui.workbench import GROUPS, tool_name

    kinds = dict(GROUPS)["2D"]
    assert {"rectangle", "circle", "annulus", "polygon", "slot", "offset", "face"} <= set(kinds)
    for kind in kinds:
        assert "_" not in tool_name(kind) and tool_name(kind)[0].isupper(), kind
        if kind == "section":
            continue
        args = {f.name: parse_profile(f.default) if f.kind == "profile" else f.default
                for f in STEPS[kind]}  # fmt: skip
        r = regenerate([Op(kind, "T", args)])
        assert _area(r.shape) > 0 and r.edges, kind


def test_every_operation_can_be_saved_in_a_project():
    """The schema's history kinds are exactly the operations regenerate knows (ADR 0065's
    sweeps and mirror were missing, so a project could not hold them)."""
    import typing

    from puzzle.geometry.history import MODIFIERS, TOOLS
    from puzzle.model.schema import HistoryStep

    kinds = set(typing.get_args(HistoryStep.model_fields["kind"].annotation))
    assert kinds == set(TOOLS) | set(MODIFIERS) | {"sketch"}


@pytest.mark.parametrize("steps", [
    [Op("face", "W", {"profile": {"points": [[0, 0], [0, 0.01], [0.01, 0.01], [0.01, 0]]}})],
    [Op("rectangle", "W", {"dx": 0.01, "dy": 0.01, "x0": -0.02}),
     Op("mirror", "M", {"axis": "x", "x": 0.0, "keep": False})],
], ids=["clockwise outline", "mirrored hand"])  # fmt: skip
def test_2d_elements_run_counter_clockwise(tmp_path, steps):
    """However a region faces, its 2D elements are meshed counter-clockwise seen from
    +z (CalculiX refuses the others: a nonpositive Jacobian)."""
    import numpy as np

    from puzzle.materials import add_library_material
    from puzzle.mesh import generate_mesh
    from puzzle.model import (
        Component,
        Geometry,
        MaterialAssignment,
        MeshRecipe,
        NamedSelection,  # fmt: skip
        PartInstance,
        Project,
        Quantity,
    )
    from puzzle.model.schema import HistoryStep

    p = Project(name="o")
    history = [HistoryStep(kind=s.kind, name=s.name, args=s.args) for s in steps]
    geo = p.add(Geometry(name="G", history=history,
                         selections=[NamedSelection(name="R", entity="face",
                                                    members=["W/face"])]))  # fmt: skip
    steel = add_library_material(p, "structural_steel")
    recipe = p.add(MeshRecipe(name="M", global_size=Quantity(value=2, unit="mm")))
    c = p.add(
        Component(
            name="C",
            dimension="2d_planar",
            mesh=recipe.id,
            parts=[PartInstance(name="G", geometry=geo.id)],
            materials=[MaterialAssignment(material=steel.id, selection="G/R")],
        )
    )
    mesh = generate_mesh(p, c.id, tmp_path, tmp_path / "m").mesh
    for block in mesh.region_blocks():
        xyz = mesh.node_coords(block.connectivity[:, :3].ravel()).reshape(-1, 3, 3)
        area = np.cross(xyz[:, 1] - xyz[:, 0], xyz[:, 2] - xyz[:, 0])[:, 2]
        assert (area > 0).all()


def test_2d_models_refuse_unwritten_contacts():
    """A frictional contact in a 2D model has no benchmark: CalculiX, Code_Aster and
    Elmer refuse it by name (conformal bonds share nodes and need nothing)."""
    from puzzle.adapters.calculix import inp
    from puzzle.adapters.code_aster import comm
    from puzzle.adapters.elmer import sif
    from puzzle.errors import PuzzleError
    from puzzle.model.schema import Component, Contact

    touch = Contact(name="Touch", type="frictional", friction=0.2, source="A/e", target="B/e")
    glue = Contact(name="Glue", type="bonded", source="A/e", target="B/e")

    def comp(*contacts):
        empty = {"connectors": [], "beam_sections": [], "physics": []}
        return Component.model_construct(dimension="2d_planar", contacts=list(contacts), **empty)

    for refuse in (inp._refuse_in_2d, comm._refuse_in_2d, sif._refuse_in_2d):
        with pytest.raises(PuzzleError, match="Touch"):
            refuse(comp(touch))
    for refuse in (inp._refuse_in_2d, comm._refuse_in_2d, sif._refuse_in_2d):
        refuse(comp(glue))
