"""Exact measuring (ADR 0042) against closed-form values: a 30 x 20 x 10 mm block with a
4 mm hole through it, measured from its history (OCCT), not from a mesh."""

import math

import pytest

pytest.importorskip("OCP")

from puzzle.geometry.history import Op, regenerate  # noqa: E402
from puzzle.geometry.measure import MeasureError, measure, rows, text  # noqa: E402
from puzzle.geometry.naming import name_all  # noqa: E402

L, W, T, R = 0.03, 0.02, 0.01, 0.002


@pytest.fixture(scope="module")
def block():
    ops = [
        Op("box", "B", {"dx": L, "dy": W, "dz": T}),
        Op("hole", "H", {"r": R, "x": 0.01, "y": 0.01}),
    ]
    return name_all(regenerate(ops))


def test_body(block):
    (e,) = measure(block, [("body", "Body")]).entities
    assert e.volume == pytest.approx(L * W * T - math.pi * R**2 * T, rel=1e-9)
    hole_wall = 2 * math.pi * R * T
    faces = 2 * (L * W - math.pi * R**2) + 2 * (L * T) + 2 * (W * T) + hole_wall
    assert e.area == pytest.approx(faces, rel=1e-9)
    assert [hi - lo for lo, hi in zip(*e.bounds, strict=True)] == pytest.approx([L, W, T], abs=1e-9)


def test_face_edge_point(block):
    top = measure(block, [("face", "B/+z")]).entities[0]
    assert top.kind == "Plane" and top.area == pytest.approx(L * W - math.pi * R**2, rel=1e-9)
    assert top.normal == pytest.approx((0, 0, 1))
    wall = measure(block, [("face", "H/wall")]).entities[0]
    assert wall.kind == "Cylinder" and wall.radius == pytest.approx(R)
    assert abs(wall.axis[2]) == pytest.approx(1.0)
    edge = measure(block, [("edge", "B/+x|B/+z")]).entities[0]
    assert edge.kind == "Line" and edge.length == pytest.approx(W)
    rim = next(n for n in block.of["edge"] if "H/wall" in n and "B/+z" in n)
    circle = measure(block, [("edge", rim)]).entities[0]
    assert circle.radius == pytest.approx(R) and circle.length == pytest.approx(2 * math.pi * R)
    assert circle.centre == pytest.approx((0.01, 0.01, T))
    corner = measure(block, [("vertex", "B/+x|B/+y|B/+z")]).entities[0]
    assert corner.centre == pytest.approx((L, W, T))


def test_two_picks(block):
    m = measure(block, [("face", "B/-x"), ("face", "B/+x")])
    assert m.pair["minimum_distance"] == pytest.approx(L)
    assert m.pair["centre_distance"] == pytest.approx(L)
    assert m.pair["dx"] == pytest.approx(L) and m.pair["angle"] == pytest.approx(180.0)
    assert m.totals["area"] == pytest.approx(2 * W * T)
    side = measure(block, [("face", "B/+x"), ("face", "B/+z")])
    assert side.pair["angle"] == pytest.approx(90.0)
    assert side.pair["minimum_distance"] == pytest.approx(0.0, abs=1e-12)  # they touch
    # From the hole's wall to the -x side: 10 mm to its axis, less the radius.
    gap = measure(block, [("face", "H/wall"), ("face", "B/-x")])
    assert gap.pair["minimum_distance"] == pytest.approx(0.01 - R)
    corners = measure(block, [("vertex", "B/-x|B/-y|B/-z"), ("vertex", "B/+x|B/+y|B/+z")])
    assert corners.pair["minimum_distance"] == pytest.approx(math.sqrt(L**2 + W**2 + T**2))


def test_rows_read_in_display_units(block):
    m = measure(block, [("edge", "B/+x|B/+z")])
    shown = {label: text(v, u, {"length": "mm"}) for label, v, u in rows(m)}
    assert shown["Length"] == "20 mm"
    assert shown["Direction"] in ("(0, 1, 0)", "(0, -1, 0)")
    assert shown["Centroid"] == "(30, 10, 10) mm"


def test_unknown_names_are_refused(block):
    with pytest.raises(MeasureError, match="no face 'nope'"):
        measure(block, [("face", "nope")])


def test_placed_parts_measure_where_they_sit(tmp_path):
    """Two blocks in a component, the second moved 50 mm along x and turned 90° about z:
    the gap between them and the turned face's normal are measured as placed."""
    from puzzle.geometry.measure import measure_placed
    from puzzle.model.schema import (
        Component,
        Geometry,
        HistoryStep,
        MeshRecipe,
        PartInstance,
        Project,
    )

    p = Project(name="two")
    geo = p.add(
        Geometry(
            name="Block",
            history=[HistoryStep(kind="box", name="B", args={"dx": 0.01, "dy": 0.01, "dz": 0.01})],
        )
    )
    mesh = p.add(MeshRecipe(name="M"))
    comp = p.add(
        Component(
            name="Pair",
            mesh=mesh.id,
            parts=[
                PartInstance(name="A", geometry=geo.id),
                PartInstance(
                    name="C", geometry=geo.id, translation=(0.05, 0, 0), rotation=(0, 0, 90)
                ),
            ],
        )
    )
    m = measure_placed(p, comp, tmp_path, [("A", "body", "Body"), ("C", "body", "Body")])
    # C turned about z spans x from 0.04 to 0.05: 30 mm from A's +x side at 0.01.
    assert m.pair["minimum_distance"] == pytest.approx(0.03)
    assert [e.name for e in m.entities] == ["A/Body", "C/Body"]
    turned = measure_placed(p, comp, tmp_path, [("C", "face", "B/+x")]).entities[0]
    assert turned.normal == pytest.approx((0, 1, 0), abs=1e-12)


def test_cli_and_agents_measure_too(tmp_path, capsys):
    """`puzzle measure` and MCP `geometry_measure` give the same exact values."""
    import asyncio

    from puzzle.agent.mcp import build_server
    from puzzle.cli import main
    from puzzle.model.schema import Geometry, HistoryStep, Project
    from puzzle.project import save_project

    p = Project(name="m")
    args = {"dx": L, "dy": W, "dz": T}
    p.add(Geometry(name="Block", history=[HistoryStep(kind="box", name="B", args=args)]))
    root = tmp_path / "m"
    save_project(p, root)
    assert main(["measure", str(root), "Block", "face:B/-x", "face:B/+x"]) == 0
    out = capsys.readouterr().out
    assert "Minimum distance: 0.03 m" in out and "Angle: 180°" in out
    assert main(["measure", str(root), "Block", "face:nope"]) == 1
    server = build_server(root, "test")
    picks = [{"entity": "edge", "name": "B/+x|B/+z"}]
    result = asyncio.run(
        server.call_tool("geometry_measure", {"picks": picks, "geometry": "Block"})
    )
    (edge,) = result.structured_content["entities"]
    assert edge["length"] == pytest.approx(W) and edge["kind"] == "Line"
