"""The bolt generator (ADR 0050): rules, selection sets and picks on a flange with
holes of three sizes; bolt types matched to the holes; a rule run again after the
geometry changes."""

from __future__ import annotations

import pytest

pytest.importorskip("OCP")

from puzzle import bolt_generator as gen  # noqa: E402
from puzzle.commands import Session  # noqa: E402
from puzzle.materials import add_library_material  # noqa: E402
from puzzle.model.schema import (  # noqa: E402
    BoltRule,
    BoltType,
    Component,
    Geometry,
    HistoryStep,
    MaterialAssignment,
    MeshRecipe,
    NamedSelection,
    PartInstance,
    Project,
    Quantity,
)
from puzzle.project import save_project  # noqa: E402

q = lambda v, u="mm": Quantity(value=v, unit=u)  # noqa: E731
# Six holes: two each of 6.6, 10.5 and 17.5 mm (M6, M10 and M16 clearance holes).
HOLES = [(0.015, 0.015, 3.3), (0.085, 0.015, 3.3), (0.015, 0.085, 5.25),
         (0.085, 0.085, 5.25), (0.05, 0.03, 8.75), (0.05, 0.07, 8.75)]  # fmt: skip


def _flange(tmp_path, holes=HOLES):
    p = Project(name="flange")
    steel = add_library_material(p, "structural_steel")
    grade = add_library_material(p, "iso_898_1_class_8_8")
    history = [HistoryStep(kind="box", name="P", args={"dx": 0.1, "dy": 0.1, "dz": 0.01})]
    history += [
        HistoryStep(kind="hole", name=f"H{i}", args={"r": r * 1e-3, "x": x, "y": y})
        for i, (x, y, r) in enumerate(holes, start=1)
    ]
    plate = p.add(
        Geometry(
            name="Plate",
            history=history,
            selections=[
                NamedSelection(name="Body", entity="body", members=["Body"]),
                NamedSelection(name="Big holes", entity="face", members=["H5/wall", "H6/wall"]),
            ],
        )
    )
    mesh = p.add(MeshRecipe(name="M", global_size=q(5)))
    p.add(
        Component(
            name="Flange",
            parts=[
                PartInstance(name="Lower", geometry=plate.id),
                PartInstance(name="Upper", geometry=plate.id, translation=(0.0, 0.0, 0.01)),
            ],
            mesh=mesh.id,
            materials=[
                MaterialAssignment(material=steel.id, selection=f"{n}/Body")
                for n in ("Lower", "Upper")
            ],  # fmt: skip
        )
    )
    for size in ("M6", "M10", "M16"):
        p.add(BoltType(name=f"{size} 8.8", size=size, material=grade.id))
    root = tmp_path / "flange"
    save_project(p, root)
    return root


def _comp(session):
    return session.project.of_type(Component)[0]


def test_rules_pick_holes_by_size_axis_and_length(tmp_path):
    root = _flange(tmp_path)
    s = Session(root)
    comp = _comp(s)
    every = gen.by_rule(s.project, comp, root, BoltRule(name="All"))
    assert len(every) == 3 * 2  # each hole of the lower plate with the one above it
    m10 = gen.by_rule(s.project, comp, root, BoltRule(name="M10s", min_diameter=q(10),
                                                      max_diameter=q(11)))  # fmt: skip
    assert len(m10) == 2 and all(p.diameter == pytest.approx(0.0105) for p in m10)
    assert len(gen.by_rule(s.project, comp, root, BoltRule(name="Z", axis=(0, 0, 1)))) == 6
    assert gen.by_rule(s.project, comp, root, BoltRule(name="X", axis=(1, 0, 0))) == []
    assert gen.by_rule(s.project, comp, root, BoltRule(name="Long", min_length=q(20))) == []
    assert len(gen.by_rule(s.project, comp, root, BoltRule(name="One part",
                                                           parts=["Lower"]))) == 0  # fmt: skip


def test_types_are_matched_to_holes_and_bolts_made_in_one_batch(tmp_path):
    root = _flange(tmp_path)
    s = Session(root)
    comp = _comp(s)
    rule = BoltRule(name="Flange bolts", names="Flange bolt {n}")
    planned = gen.plan(s.project, comp, gen.by_rule(s.project, comp, root, rule), rule)
    kinds = sorted(p.type_name for p in planned.add)
    assert kinds == ["M10 8.8", "M10 8.8", "M16 8.8", "M16 8.8", "M6 8.8", "M6 8.8"]
    s.submit_batch(gen.commands(s.project, comp, planned, rule=rule))
    comp = _comp(s)
    names = sorted(c.name for c in comp.connectors)
    assert names == [f"Flange bolt {n}" for n in range(1, 7)]
    assert {c.group for c in comp.connectors} == {"Flange bolts"}
    assert all(c.type is not None and c.diameter is None for c in comp.connectors)
    assert [r.name for r in comp.bolt_rules] == ["Flange bolts"]
    assert s.history(limit=1)[0]["command"]["command"] == "batch"
    # Run again: nothing new, all kept.
    again = gen.plan(s.project, comp, gen.by_rule(s.project, comp, root, rule), rule)
    assert again.add == [] and len(again.kept) == 6 and again.stale == []


def test_selection_sets_and_picks(tmp_path):
    root = _flange(tmp_path)
    s = Session(root)
    comp = _comp(s)
    sets = gen.by_sets(s.project, comp, root, "Lower/Big holes", "Upper/Big holes")
    assert len(sets) == 2 and all(p.diameter == pytest.approx(0.0175) for p in sets)
    picked = gen.by_picks(s.project, comp, root, [("Lower", "H1/wall"), ("Upper", "H1/wall"),
                                                  ("Lower", "H3/wall")])  # fmt: skip
    assert [(p.source_face, p.target_face) for p in picked] == [("H1/wall", "H1/wall")]


def test_a_rule_run_again_after_the_geometry_changed(tmp_path):
    root = _flange(tmp_path)
    s = Session(root)
    comp = _comp(s)
    rule = BoltRule(name="Small", max_diameter=q(7), names="Small {n}")
    planned = gen.plan(s.project, comp, gen.by_rule(s.project, comp, root, rule), rule)
    s.submit_batch(gen.commands(s.project, comp, planned, rule=rule))
    # One 6.6 mm hole goes (the plate is shared, so from both parts): its bolt is stale.
    from puzzle.commands.model import Edit, EditOp

    plate = s.project.of_type(Geometry)[0]
    s.submit(Edit(object=str(plate.id), ops=[EditOp(op="set", path="history.1.suppressed",
                                                    value=True)]))  # fmt: skip
    comp = _comp(s)
    again = gen.plan(s.project, comp, gen.by_rule(s.project, comp, root, rule), rule)
    assert again.add == [] and len(again.stale) == 1 and len(again.kept) == 1
    s.submit_batch(gen.commands(s.project, comp, again, rule=rule, remove_stale=True))
    assert [c.name for c in _comp(s).connectors] == again.kept


def test_the_agent_proposes_and_submits_bolts(tmp_path):
    """MCP `bolts_generate`: "M10s through every 10.5 mm hole" as a rule, previewed as
    proposals; submitting the batch makes them."""
    pytest.importorskip("mcp")
    import asyncio

    from puzzle.agent.mcp import build_server

    root = _flange(tmp_path)
    Session(root).submit({"command": "set_autonomy", "level": "act_in_budget"})
    server = build_server(root, "test")

    def call(tool, **arguments):
        result = asyncio.run(server.call_tool(tool, arguments))
        return result.structured_content.get("result", result.structured_content)

    found = call("bolts_generate", component="Flange", rule="M10 flange", min_diameter=0.010,
                 max_diameter=0.011, bolt_type="M10 8.8", names="M10 bolt {n}")  # fmt: skip
    assert [p["name"] for p in found["proposals"]] == ["M10 bolt 1", "M10 bolt 2"]
    assert {p["bolt_type"] for p in found["proposals"]} == {"M10 8.8"}
    out = call("submit", command=found["make_bolts"], rationale="bolt the flange")
    assert out["status"] == "applied"
    comp = _comp(Session(root))
    assert [c.name for c in comp.connectors] == ["M10 bolt 1", "M10 bolt 2"]
    assert comp.bolt_rules[0].name == "M10 flange"


def test_the_panel_finds_previews_and_makes(tmp_path):
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from puzzle.gui.bolt_generator_panel import generate_bolts
    from puzzle.gui.window import MainWindow

    app = QApplication.instance() or QApplication([])  # noqa: F841
    window = MainWindow(_flange(tmp_path))
    comp = _comp(window.session)
    panel = generate_bolts(window, comp)
    panel.max_d.setText("0.007")  # the display unit is metres: the 6.6 mm holes
    panel.find()
    assert panel.table.rowCount() == 2 and panel.table.item(0, 4).text() == "M6 8.8"
    assert any(g.feature.startswith("Proposed") for g in window.viewport.glyphs)
    panel.table.item(1, 0).setCheckState(Qt.CheckState.Unchecked)
    panel.make()
    comp = _comp(window.session)
    assert len(comp.connectors) == 1 and comp.connectors[0].rule == "Bolt rule 1"
    assert panel.summary.text().startswith("1 new bolt; 1 already there")
    window.close()


def test_a_stack_of_three_plates_gets_one_bolt_through_all(tmp_path):
    """Three plates on one axis: one bolt from the first plate's hole to the last's, the
    middle plate's hole listed as passed through (ADR 0050 stacks)."""
    from puzzle.project import load_project

    root = _flange(tmp_path, holes=[(0.05, 0.05, 5.25)])
    p = load_project(root)
    comp = p.of_type(Component)[0]
    comp.parts.append(PartInstance(name="Middle", geometry=comp.parts[0].geometry,
                                   translation=(0.0, 0.0, 0.01)))  # fmt: skip
    comp.parts[1].translation = (0.0, 0.0, 0.02)  # Upper on top of Middle
    comp.materials.append(MaterialAssignment(material=comp.materials[0].material,
                                             selection="Middle/Body"))  # fmt: skip
    save_project(p, root)
    s = Session(root)
    comp = _comp(s)
    rule = BoltRule(name="Stack")
    pairs = gen.by_rule(s.project, comp, root, rule)
    (one,) = pairs
    assert (one.source_part, one.target_part) == ("Lower", "Upper")
    assert one.through == [("Middle", "H1/wall")]
    assert one.centre_distance == pytest.approx(0.02)
    planned = gen.plan(s.project, comp, pairs, rule)
    s.submit_batch(gen.commands(s.project, comp, planned, rule=rule))
    (bolt,) = _comp(s).connectors
    assert bolt.through == ["Middle/Stack hole 1"] and bolt.type is not None


def test_unpaired_holes_get_bolts_to_ground(tmp_path):
    """A rule over one plate's holes with `ground_unpaired`: each hole alone on its axis
    gets an anchor; made, the bolts are grounded with one end each."""
    root = _flange(tmp_path)
    s = Session(root)
    comp = _comp(s)
    rule = BoltRule(name="Anchors", parts=["Lower"], ground_unpaired=True, names="Anchor {n}",
                    ground_length=q(30))  # fmt: skip
    pairs = gen.by_rule(s.project, comp, root, rule)
    assert len(pairs) == 6 and all(p.target_part is None for p in pairs)
    planned = gen.plan(s.project, comp, pairs, rule)
    s.submit_batch(gen.commands(s.project, comp, planned, rule=rule))
    bolts = _comp(s).connectors
    assert len(bolts) == 6 and all(b.ground and b.target is None for b in bolts)
    assert bolts[0].ground_length.to_si() == pytest.approx(0.03)
    assert gen.plan(s.project, _comp(s), gen.by_rule(s.project, _comp(s), root, rule),
                    rule).kept == [b.name for b in bolts]  # fmt: skip


def test_two_holes_of_one_part_join_only_when_asked():
    """A split clamp: two coaxial holes of one part."""
    import numpy as np

    up = np.array([0.0, 0.0, 1.0])
    holes = [gen.Hole("Clamp", f"H{i}/wall", z * up, up, 0.0105, z * up)
             for i, z in ((1, 0.0), (2, 0.03))]  # fmt: skip
    assert gen.pair(holes, 1e-6) == []
    (one,) = gen.pair(holes, 1e-6, same_part=True)
    assert (one.source_face, one.target_face) == ("H1/wall", "H2/wall")
