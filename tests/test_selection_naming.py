"""Robust naming (G3, ADR 0014): edges and points named by their faces, anchors that
re-find members of a re-exported file, rules, and reattachment in bulk.

Each test builds the case the user meets: a parameter edit, a CAD file replaced by a
new export with its faces in another order, a feature removed. Members are checked by
where they are (planes, lengths, radii), not by their names alone.
"""

import math

import pytest

pytest.importorskip("OCP")

from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform  # noqa: E402
from OCP.gp import gp_Trsf  # noqa: E402

from puzzle.geometry.history import Op, regenerate  # noqa: E402
from puzzle.geometry.naming import (  # noqa: E402
    accept_all,
    anchor,
    by_rule,
    name_all,
    named_geometry,
    reattach_ops,
    resolve,
    suggest_rule,
)
from puzzle.model.schema import Geometry, HistoryStep, NamedSelection, Project  # noqa: E402


def _plate(r=0.005, fillet=False):
    ops = [
        Op("box", "Plate", {"dx": 0.08, "dy": 0.04, "dz": 0.01}),
        Op("hole", "Bore", {"x": 0.02, "y": 0.02, "r": r}),
    ]
    if fillet:
        ops.append(Op("fillet", "Round", {"r": 0.004, "rule": {"parallel": "z", "outer": True}}))
    return name_all(regenerate(ops))


def _length(named, name):
    return anchor("edge", named.of["edge"][name])[1]


def test_edges_and_points_are_named_by_their_faces():
    named = _plate()
    assert _length(named, "Plate/+x|Plate/+z") == pytest.approx(0.04)
    assert _length(named, "Bore/wall|Plate/+z") == pytest.approx(2 * math.pi * 0.005)
    corner = anchor("vertex", named.of["vertex"]["Plate/+x|Plate/+y|Plate/+z"])
    assert corner[2:] == pytest.approx([0.08, 0.04, 0.01])
    # A hole's wall has a seam: an edge with the wall on both sides.
    assert "Bore/wall" in named.of["edge"]
    assert len(named.of["edge"]) == 12 + 3  # the box's, two circles and the seam


def test_edge_names_survive_a_resize_where_edge_numbers_do_not():
    sel = NamedSelection(
        name="Rim",
        entity="edge",
        members=["Bore/wall|Plate/+z", "Plate/+x|Plate/+z"],
    )
    (report,) = resolve([sel], _plate(r=0.007))
    assert [m.status for m in report.members] == ["kept", "kept"]
    bigger = _plate(r=0.007)
    assert _length(bigger, "Bore/wall|Plate/+z") == pytest.approx(2 * math.pi * 0.007)
    # A fillet on the vertical corners leaves the top edges, which now end at the
    # rounds, under the same names; the corner edge it rounds away is detached.
    corner = NamedSelection(name="Corner", entity="edge", members=["Plate/+x|Plate/+y"])
    rounded = _plate(fillet=True)
    (report, kept) = resolve([corner, sel], rounded)
    assert report.members[0].status == "detached"
    assert [m.status for m in kept.members] == ["kept", "kept"]
    assert _length(rounded, "Plate/+x|Plate/+z") == pytest.approx(0.04 - 2 * 0.004)


# --- re-imported files: names count positions; anchors catch a changed file ----------


def _write_step(named_shape, path):
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    from puzzle.geometry.prep import _set_unit

    trsf = gp_Trsf()
    trsf.SetScaleFactor(1000.0)  # CAD files are in mm
    mm = BRepBuilderAPI_Transform(named_shape.shape, trsf, True).Shape()
    writer = STEPControl_Writer()
    _set_unit("MM")
    writer.Transfer(mm, STEPControl_AsIs)
    writer.Write(str(path))


def _export_v1(path):
    _write_step(
        regenerate(
            [
                Op("box", "Plate", {"dx": 0.08, "dy": 0.04, "dz": 0.01}),
                Op("hole", "Bore", {"x": 0.02, "y": 0.02, "r": 0.005}),
            ]
        ),
        path,
    )


def _export_v2(path, bore_x=0.02):
    """The part as another CAD tool builds it (a profile drawn from another corner) with
    a slot added: its faces come out of the file in another order (the x = 0 end is
    face-1 in the first export, face-3 here), so face-N points elsewhere."""
    corners = [[0.08, 0.04], [0.0, 0.04], [0.0, 0.0], [0.08, 0.0]]
    _write_step(
        regenerate(
            [
                Op("extrude", "Plate", {"profile": {"points": corners}, "distance": 0.01}),
                Op("hole", "Slot", {"x": 0.065, "y": 0.02, "r": 0.003}),
                Op("hole", "Bore", {"x": bore_x, "y": 0.02, "r": 0.005}),
            ]
        ),
        path,
    )


def _file_project(tmp_path):
    _export_v1(tmp_path / "plate.step")
    p = Project(name="re-import")
    geo = p.add(Geometry(name="Plate", source="plate.step"))
    named = named_geometry(p, geo, tmp_path)
    wall = next(n for n, fs in named.of["face"].items() if anchor("face", fs)[0] == "Cylinder")
    end = next(
        n for n, fs in named.of["face"].items() if anchor("face", fs)[2] == pytest.approx(0.0)
    )
    rim = next(n for n in named.of["edge"] if wall in n.split("|"))
    sels = [
        NamedSelection(
            name=name,
            entity=entity,
            members=[m],
            anchors={m: named.signature(entity, m)},
        )
        for name, entity, m in (("Bolt", "face", wall), ("End", "face", end), ("Rim", "edge", rim))
    ]
    return p, geo.model_copy(update={"selections": sels}), (wall, end, rim)


def test_a_reexported_file_is_refound_by_anchors(tmp_path):
    p, geo, (wall, end, rim) = _file_project(tmp_path)
    _export_v2(tmp_path / "plate.step")  # the user replaces the file
    named = named_geometry(p, geo, tmp_path)
    reports = {r.selection: r for r in resolve(geo.selections, named)}
    for name in ("End", "Rim"):  # renumbered in the new file
        (m,) = reports[name].members
        assert m.status == "refound" and m.now != m.name, (name, m)
    (bolt,) = reports["Bolt"].members  # still face-7, and the anchor confirms it
    assert bolt.status == "kept"
    assert anchor("face", named.of["face"][bolt.now])[2:] == pytest.approx([0.02, 0.02, 0.005])
    end_now = named.of["face"][reports["End"].members[0].now]
    assert anchor("face", end_now)[2] == pytest.approx(0.0)
    rim_now = anchor("edge", named.of["edge"][reports["Rim"].members[0].now])
    assert rim_now[1] == pytest.approx(2 * math.pi * 0.005)
    # Without the anchor, "End" would now be the y = 0.04 side: that is the danger.
    assert anchor("face", named.of["face"][end])[2] != pytest.approx(0.0)


def test_a_member_that_moved_is_detached_with_ranked_candidates(tmp_path):
    p, geo, _ = _file_project(tmp_path)
    _export_v2(tmp_path / "plate.step", bore_x=0.03)  # the bore moved 10 mm
    named = named_geometry(p, geo, tmp_path)
    (bolt,) = [r for r in resolve(geo.selections, named) if r.selection == "Bolt"]
    (m,) = bolt.members
    assert m.status == "detached"  # never guessed
    best = named.of["face"][m.candidates[0][0]]
    assert anchor("face", best)[2:] == pytest.approx([0.03, 0.02, 0.005])  # the moved bore
    # Accepting the best candidates writes new names and fresh anchors.
    choices = accept_all([bolt])
    ops = reattach_ops(geo, choices, named)
    members = next(o.value for o in ops if o.path.endswith(".members"))
    assert members == [m.candidates[0][0]]
    anchors = next(o.value for o in ops if o.path.endswith(".anchors"))
    assert anchors[members[0]][2] == pytest.approx(0.03)


# --- rules ----------------------------------------------------------------------------------


def test_a_rule_picks_the_same_faces_after_any_edit():
    rule = {"plane": {"axis": "z", "at": 0.01}}
    sel = NamedSelection(name="Top", entity="face", rule=rule)
    for named in (_plate(), _plate(r=0.007), _plate(fillet=True)):
        (report,) = resolve([sel], named)
        assert [m.status for m in report.members] == ["rule"]
        (face,) = report.resolved
        assert anchor("face", named.of["face"][face])[2:][2] == pytest.approx(0.01)


def test_suggested_rules_pick_exactly_the_members():
    named = _plate()
    assert suggest_rule(named, "face", ["Plate/-x"]) == {"plane": {"axis": "x", "at": 0.0}}
    assert suggest_rule(named, "edge", ["Bore/wall|Plate/+z", "Bore/wall|Plate/-z"]) == {
        "radius": 0.005
    }
    assert by_rule(named, "face", {"radius": 0.005}) == ["Bore/wall"]
    # Two unrelated faces: no simple rule.
    assert suggest_rule(named, "face", ["Plate/-x", "Plate/+z"]) is None


def test_the_mesher_reports_detached_selections(tmp_path):
    """End to end: a history whose fillet removes a selected corner edge meshes with a
    warning that names it; the kept selections land on the right entities."""
    import numpy as np

    from puzzle.materials import add_library_material
    from puzzle.mesh import generate_mesh
    from puzzle.model import Component, MaterialAssignment, MeshRecipe, PartInstance, Quantity

    p = Project(name="g3")
    geo = p.add(
        Geometry(
            name="Plate",
            history=[
                HistoryStep(kind="box", name="Plate", args={"dx": 0.08, "dy": 0.04, "dz": 0.01}),
                HistoryStep(kind="hole", name="Bore", args={"x": 0.02, "y": 0.02, "r": 0.005}),
                HistoryStep(
                    kind="fillet",
                    name="Round",
                    args={"r": 0.004, "rule": {"parallel": "z", "outer": True}},
                ),
            ],
            selections=[
                NamedSelection(name="Solid", entity="body", members=["Body"]),
                NamedSelection(name="Rim", entity="edge", members=["Bore/wall|Plate/+z"]),
                NamedSelection(name="Corner", entity="edge", members=["Plate/+x|Plate/+y"]),
                NamedSelection(name="End", entity="face", rule={"plane": {"axis": "x", "at": 0.0}}),
            ],
        )
    )
    steel = add_library_material(p, "structural_steel")
    mesh = p.add(MeshRecipe(name="m", global_size=Quantity(value=4, unit="mm"), element_order=1))
    comp = p.add(
        Component(
            name="C",
            parts=[PartInstance(name="P", geometry=geo.id)],
            mesh=mesh.id,
            materials=[MaterialAssignment(material=steel.id, selection="P/Solid")],
        )
    )
    meshed = generate_mesh(p, comp.id, tmp_path, tmp_path / "m")
    assert meshed.warnings == ["selection 'Corner': 'Plate/+x|Plate/+y' is detached"]
    rim = meshed.mesh.node_coords(meshed.mesh.groups["P/Rim"].node_ids)
    radii = np.hypot(rim[:, 0] - 0.02, rim[:, 1] - 0.02)
    assert np.allclose(radii, 0.005, atol=1e-6) and np.allclose(rim[:, 2], 0.01)
    xs = meshed.mesh.node_coords(meshed.mesh.groups["P/End"].node_ids)[:, 0]
    assert np.allclose(xs, 0.0)
    report = {r["selection"]: r for r in meshed.selections["P"]}
    assert report["End"]["members"][0]["status"] == "rule"
    # Picks name edges persistently, so a new selection stores the face-pair name.
    assert ("P", "edge", "Bore/wall|Plate/+z") in meshed.anchors


def _saved_plate(tmp_path):
    """A saved project whose plate geometry has anchored selections, then a new export."""
    from puzzle.commands import Session
    from puzzle.project import save_project

    root = tmp_path / "plate"
    root.mkdir()
    save_project(Project(name="plate"), root)
    p, geo, _ = _file_project(root)
    data = geo.model_dump(mode="json")
    Session(root).submit({"command": "create", "kind": "geometry", "data": data})
    _export_v2(root / "plate.step", bore_x=0.03)
    return root


def test_an_agent_gets_the_review_and_a_ready_command(tmp_path):
    pytest.importorskip("mcp")
    import asyncio

    from puzzle.agent.mcp import build_server

    root = _saved_plate(tmp_path)
    server = build_server(root, "test")
    result = asyncio.run(server.call_tool("selections_check", {"geometry": "Plate"}))
    out = result.structured_content
    states = {r["selection"]: [m["status"] for m in r["members"]] for r in out["selections"]}
    # The rim is the bore's top circle: it moved with the bore.
    assert states == {"Bolt": ["detached"], "End": ["refound"], "Rim": ["detached"]}
    command = out["accept_all"]
    assert command["command"] == "edit" and command["object"] == "Plate"
    paths = {o["path"] for o in command["ops"]}
    assert "selections.1.rule" in paths  # the end face: the plane x = 0


def test_the_cli_reattaches_all(tmp_path, capsys):
    from puzzle.cli import main
    from puzzle.project import load_project

    root = _saved_plate(tmp_path)
    assert main(["selections", str(root), "Plate"]) == 1  # the bore is detached
    assert "Bolt (face): detached" in capsys.readouterr().out
    assert main(["selections", str(root), "Plate", "--accept-all"]) == 0
    (geo,) = load_project(root).of_type(Geometry)
    sels = {s.name: s for s in geo.selections}
    assert sels["End"].rule == {"plane": {"axis": "x", "at": 0.0}}
    assert main(["selections", str(root), "Plate"]) == 0  # nothing left to decide
