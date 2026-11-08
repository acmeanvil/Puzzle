"""The project schematic (ADR 0058): built from the model, laid out, exported, and the
user's view kept outside the project."""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from puzzle import schematic
from puzzle.model.schema import (
    OBJECT_TYPES,
    Amplitude,
    Component,
    CoordinateSystem,
    ImportedField,
    MeshRecipe,
    Project,
    ResultCase,
    ResultDefinition,
    SolveStep,
    Study,
    TreeGroup,
)
from puzzle.project.graph import run_hashes
from puzzle.schematic import view_state
from puzzle.schematic.layout import GAP_Y, layout
from puzzle.schematic.svg import to_svg


def _by_label(s, label):
    return next(n for n in s.nodes.values() if n.label == label)


def _study(p, name):
    return next(x for x in p.of_type(Study) if x.name == name)


def _record(p: Project, study: Study, run_id="r1", current=True):
    key = run_hashes(p)[study.id][study.solver][0] if current else "old"
    return SimpleNamespace(run_id=run_id, run_key=key, study_id=str(study.id),
                           study_name=study.name, solver=study.solver, status="completed",
                           point=None, recorded_at=datetime.now(UTC))  # fmt: skip


def test_every_kind_of_object_says_how_it_shows():
    kinds = {cls.kind for cls in OBJECT_TYPES}
    assert kinds <= set(schematic.SHOWN), kinds - set(schematic.SHOWN)
    for kind, how in schematic.SHOWN.items():
        assert how in ("node", "chip", "wire", "frame", "none"), kind
        if how == "chip":
            assert kind in schematic.CHIP_WORDS


def test_nodes_sit_in_their_columns(bracket):
    s = schematic.build(bracket)
    want = {"bracket.step": "Inputs", "Bracket": None, "Al 6061-T6": "Resources",
            "Tets": "Resources", "Globals": "Resources", "Thermal": "Studies"}  # fmt: skip
    for label, column in want.items():
        if column:
            assert schematic.COLUMNS[_by_label(s, label).column] == column, label
    kinds = {n.label + ":" + n.kind for n in s.nodes.values()}
    assert {"Bracket:geometry", "Bracket:component"} <= kinds


def test_transfers_are_labelled_data_wires_between_ports(bracket):
    s = schematic.build(bracket)
    data = [w for w in s.wires if w.kind == "data"]
    assert len(data) == 2
    heat = next(w for w in data if w.carries == "temperature")
    assert s.nodes[heat.source].label == "Thermal" and s.nodes[heat.target].label == "Static"
    assert heat.label == "temperature, last step, interpolate"
    assert heat.flow == "temperature"
    assert s.nodes[heat.source].port("temperature").side == "out"
    target = s.nodes[heat.target].port(heat.target_port)
    assert target.side == "in" and target.label == "Temperature (load)"
    assert {w.flow for w in data} == {"temperature", "stress"}


def test_a_study_not_run_shows_what_its_physics_produce(bracket):
    thermal = _study(bracket, "Thermal")
    thermal.steps = [SolveStep(type="stationary", physics=["heat_transfer"])]
    node = schematic.build(bracket).node_of(str(thermal.id))
    assert [p.key for p in node.outputs()] == ["temperature", "heat_flux"]
    assert node.state == "not_run"


def test_use_wires_show_sharing_with_a_count(bracket):
    s = schematic.build(bracket)
    comp = next(n for n in s.nodes.values() if n.kind == "component")
    users = {s.nodes[w.target].label for w in s.wires if w.kind == "use" and w.source == comp.id}
    assert users == {"Thermal", "Static", "Modal"}
    assert comp.used_by == 3
    into = [w for w in s.wires if w.kind == "use" and w.target == comp.id]
    assert {s.nodes[w.source].kind for w in into} == {"geometry", "mesh", "material"}
    study_in = next(w for w in s.wires if w.kind == "use" and s.nodes[w.target].kind == "study")
    assert study_in.target_port == "component"
    assert any("used by Modal, Static, Thermal" in line for line in s.flow_lines())


def test_upstream_and_downstream(bracket):
    s = schematic.build(bracket)
    thermal = s.node_of(str(_study(bracket, "Thermal").id))
    down = {s.nodes[k].label for k in s.downstream(thermal.id)}
    assert down == {"Static", "Modal"}
    mat = _by_label(s, "Al 6061-T6")
    assert {s.nodes[k].kind for k in s.downstream(mat.id)} == {"component", "study"}
    up = {s.nodes[k].label for k in s.upstream(s.node_of(str(_study(bracket, "Modal").id)).id)}
    assert {"Static", "Thermal", "bracket.step", "Al 6061-T6", "Tets"} <= up


def test_small_references_are_nodes_wired_to_their_users(bracket):
    """Coordinate systems, amplitudes and the like were chips; they are nodes now, so they
    can be wired (ADR 0071 decision 8), each with a socket to draw from."""
    amp = bracket.add(Amplitude(name="Ramp", points=[(0.0, 0.0), (1.0, 1.0)]))
    cs = bracket.add(CoordinateSystem(name="Flange"))
    comp = bracket.of_type(Component)[0]
    comp.physics[0].features[0].amplitude = amp.id
    comp.physics[0].features[0].axes = cs.id
    s = schematic.build(bracket)
    for obj in (amp, cs):
        node = s.nodes[str(obj.id)]
        assert node.kind in ("amplitude", "axes") and node.port("use") is not None
        assert any(w.kind == "use" and w.source == node.id and w.target == str(comp.id)
                   for w in s.wires)  # fmt: skip
    assert not s.node_of(str(comp.id)).chips


def test_files_missing_from_the_project_folder_are_marked(bracket, tmp_path):
    s = schematic.build(bracket, root=tmp_path)
    assert _by_label(s, "bracket.step").state == "missing"
    (tmp_path / "bracket.step").write_text("", encoding="utf-8")
    assert _by_label(schematic.build(bracket, root=tmp_path), "bracket.step").state == "complete"


def test_runs_and_derived_results_make_the_results_column(bracket):
    static = _study(bracket, "Static")
    case = ResultCase(study=static.id, coefficient=2.0)
    combo = bracket.add(ResultDefinition(name="Doubled", method="linear", cases=[case]))
    s = schematic.build(bracket, runs=[_record(bracket, static)])
    runs = s.nodes[f"runs:{static.id}"]
    assert schematic.COLUMNS[runs.column] == "Results" and runs.state == "up_to_date"
    assert runs.object == "r1"
    into = next(w for w in s.wires if w.target == str(combo.id))
    assert into.kind == "data" and into.source == runs.id
    old = schematic.build(bracket, runs=[_record(bracket, static, current=False)])
    assert old.nodes[f"runs:{static.id}"].state == "out_of_date"
    lay = layout(s)
    assert lay.positions[str(combo.id)][0] > lay.positions[runs.id][0]


def test_a_study_fed_by_another_sits_to_its_right(bracket):
    s = schematic.build(bracket)
    lay = layout(s)
    x = {n.label: lay.positions[k][0] for k, n in s.nodes.items() if n.kind == "study"}
    assert x["Thermal"] < x["Static"] < x["Modal"]
    assert [name for name, _ in lay.columns] == ["Inputs", "Resources", "Models", "Studies"]


def test_tree_groups_are_frames_and_fold_into_one_node(bracket):
    studies = [str(_study(bracket, n).id) for n in ("Static", "Modal")]
    group = bracket.add(TreeGroup(name="Structural", section="Studies", members=studies))
    s = schematic.build(bracket)
    assert s.groups[0].members == studies and s.nodes[studies[0]].group == str(group.id)
    folded = schematic.build(bracket, folded=frozenset({str(group.id)}))
    assert studies[0] not in folded.nodes
    node = folded.nodes[f"group:{group.id}"]
    assert node.label == "Structural"
    heat = next(w for w in folded.wires if w.kind == "data")
    assert heat.target == node.id and heat.target_port == "in"  # the transfer, rerouted
    assert not any(w.source == w.target for w in folded.wires)


def test_imported_fields_are_resources_wired_into_what_they_drive(bracket, tmp_path):
    from puzzle.model.schema import FieldColumn, FieldUse

    field = bracket.add(ImportedField(name="CFD pressure", file="fields/p.csv", columns=[
        FieldColumn(name="x", role="x"), FieldColumn(name="y", role="y"),
        FieldColumn(name="z", role="z"), FieldColumn(name="p", unit="Pa")]))  # fmt: skip
    comp = bracket.of_type(Component)[0]
    feature = comp.physics[1].features[0]
    feature.fields = {"p": FieldUse(field=field.id, column="p", scale=1.2)}
    s = schematic.build(bracket)
    node = s.node_of(str(field.id))
    assert schematic.COLUMNS[node.column] == "Resources"
    assert any(w.source == "file:fields/p.csv" and w.target == node.id for w in s.wires)
    wire = next(w for w in s.wires if w.kind == "data" and w.source == node.id)
    assert wire.target == str(comp.id) and wire.label == "p ×1.2"
    assert s.nodes[wire.target].port(wire.target_port).side == "in"
    hidden = schematic.build(bracket, fields=False)
    assert hidden.node_of(str(field.id)) is None and "file:fields/p.csv" not in hidden.nodes


def _overlaps(lay):
    boxes = [(x, y, x + lay.sizes[k][0], y + lay.sizes[k][1])
             for k, (x, y) in lay.positions.items()]  # fmt: skip
    return [(a, b) for i, a in enumerate(boxes) for b in boxes[i + 1:]
            if a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]]  # fmt: skip


def test_layout_is_stable_and_never_overlaps(bracket):
    s = schematic.build(bracket)
    first, second = layout(s), layout(schematic.build(bracket))
    assert first.positions == second.positions
    assert not _overlaps(first)


def test_pinned_nodes_stay_and_the_rest_move_out_of_their_way(bracket):
    s = schematic.build(bracket)
    free = layout(s)
    mat = _by_label(s, "Al 6061-T6").id
    tets = _by_label(s, "Tets").id
    pinned = {mat: free.positions[tets]}  # dropped right where Tets was
    lay = layout(s, pinned)
    assert lay.positions[mat] == free.positions[tets]
    assert not _overlaps(lay)
    assert lay.positions[tets][1] >= free.positions[tets][1] + GAP_Y


def test_a_large_project_lays_out_in_well_under_a_second(bracket):
    comp = bracket.of_type(Component)[0]
    mesh = bracket.of_type(MeshRecipe)[0]
    for i in range(60):
        copy = bracket.add(Component(name=f"Copy {i}", parts=comp.parts, mesh=mesh.id,
                                     materials=comp.materials, physics=comp.physics))  # fmt: skip
        for j in range(3):
            bracket.add(Study(name=f"Study {i}.{j}", component=copy.id, solver="calculix",
                              steps=[SolveStep(type="stationary")]))  # fmt: skip
    start = time.perf_counter()
    s = schematic.build(bracket, states={})
    lay = layout(s)
    took = time.perf_counter() - start
    assert len(s.nodes) > 240 and not _overlaps(lay)
    assert took < 1.0, took


def test_svg_export_is_a_picture_of_every_node_and_wire(bracket):
    s = schematic.build(bracket)
    svg = to_svg(s, layout(s), title="Bracket")
    root = ET.fromstring(svg)
    texts = {t.text for t in root.iter("{http://www.w3.org/2000/svg}text")}
    assert {"Thermal", "Static", "Modal", "Inputs", "Studies"} <= texts
    assert "temperature, last step, interpolate" in texts
    paths = list(root.iter("{http://www.w3.org/2000/svg}path"))
    assert len(paths) == len(s.wires)
    assert len(list(ET.fromstring(to_svg(s, layout(s), uses="none"))
                    .iter("{http://www.w3.org/2000/svg}path"))) == 2  # fmt: skip


def test_the_view_is_kept_per_user_outside_the_project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    state = view_state.ViewState({"a": (10.0, 20.0), "gone": (1.0, 1.0)}, {"a"}, {"g"})
    assert state.prune(["a"], groups=[])
    assert state.positions == {"a": (10.0, 20.0)} and state.folded == set()
    view_state.save(root, state)
    assert not list(root.iterdir())  # nothing written in the project
    back = view_state.load(root)
    assert back.positions == {"a": (10.0, 20.0)} and back.unfolded == {"a"}
    view_state.save(root, view_state.ViewState())  # Tidy layout forgets it
    assert view_state.load(root).positions == {}


@pytest.mark.parametrize(("field", "flow"), [("temperature", "temperature"),
                         ("heat_flux", "temperature"), ("displacement_amplitude", "displacement"),
                         ("stress", "stress"), ("strain", "stress"),
                         ("contact_pressure", "pressure"), ("velocity", "velocity"),
                         ("electric_potential", "other")])  # fmt: skip
def test_wires_are_coloured_by_what_flows(field, flow):
    assert schematic.flow_of(field) == flow


def test_the_cli_and_the_agent_read_the_same_schematic(bracket, tmp_path, capsys):
    import asyncio

    from puzzle.agent.mcp import build_server
    from puzzle.cli import main
    from puzzle.commands.outline import outline
    from puzzle.project import save_project

    root = tmp_path / "bracket"
    save_project(bracket, root)
    assert main(["schematic", str(root), "--svg", str(tmp_path / "s.svg")]) == 0
    assert ET.parse(tmp_path / "s.svg").getroot().tag.endswith("svg")
    assert main(["schematic", str(root)]) == 0
    out = capsys.readouterr().out
    assert "Studies:" in out and "Thermal -> Static (temperature, last step, interpolate)" in out
    assert "Flow:\n    Thermal -> Static" in outline(bracket)
    server = build_server(root, "test")
    data = asyncio.run(server.call_tool("schematic", {"node": "Thermal"})).structured_content
    assert data["node"]["downstream"] == ["Modal", "Static"]
    carried = {w["carries"] for w in data["wires"] if w["kind"] == "data"}
    assert "temperature, last step, interpolate" in carried
    assert {n["column"] for n in data["nodes"]} == {"Inputs", "Resources", "Models", "Studies"}
