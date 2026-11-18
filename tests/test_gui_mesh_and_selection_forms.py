"""Forms for what only agents reached (ADR 0072 section 10, items 6-10): New mesh
study…, Mesh shoot-out…, Selection by rule…, Who may connect and New part…. Each form,
filled, submits what the MCP tool's (or the CLI's) path does for the same arguments, or
shows what its function returns; Ask the agent… hands the values to the agent window;
each has a help key that opens a page."""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from forms import fill  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from puzzle.commands.session import _dump  # noqa: E402
from puzzle.gui.window import MainWindow  # noqa: E402
from puzzle.project import save_project  # noqa: E402

EXAMPLE = Path(__file__).parents[1] / "examples" / "cantilever"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def wait(app, window, timeout=60.0):
    end = time.monotonic() + timeout
    while window.tasks:
        app.processEvents()
        if time.monotonic() > end:
            raise TimeoutError
        time.sleep(0.02)
    app.processEvents()


def last_batch(window) -> list[dict]:
    entry = [e for e in window.session.history_entries() if e["type"] == "command"][-1]
    assert entry["command"]["command"] == "batch"
    return entry["command"]["commands"]


def _call(server, tool, **arguments):
    result = asyncio.run(server.call_tool(tool, arguments))
    if result.structured_content is not None:
        return result.structured_content.get("result", result.structured_content)
    return result.content[0].text


@pytest.fixture
def stand_in(app, tmp_path):
    """The convergence tests' stand-in project (a study, a recipe), opened."""
    from test_convergence import project

    p, _ = project()
    save_project(p, tmp_path / "p")
    w = MainWindow(tmp_path / "p")
    yield w
    w.close()


def test_new_mesh_study_submits_the_create_the_tool_reads(stand_in):
    from puzzle import convergence as conv
    from puzzle.gui import convergence_panel
    from puzzle.model.schema import MeshStudy, Study

    w = stand_in
    study = w.session.project.of_type(Study)[0]
    fill(w, study=str(study.id), name="Fine check", levels=4, ratio=2.0, target="1")
    assert convergence_panel.new_mesh_study(w) == "Fine check"
    assert w.last_form.property("help") == "form:new_mesh_study"
    want = conv.mesh_study_command(study, name="Fine check", levels=4, ratio=2.0, target=0.01)
    assert last_batch(w) == [_dump(want)]
    (made,) = w.session.project.of_type(MeshStudy)
    assert made.study == study.id and made.levels == 4
    # MCP mesh_convergence reads it by name, as it reads any mesh study.
    found, check = conv.settings(w.session.project, "Fine check")
    assert found.id == study.id and (check.levels, check.ratio, check.target) == (4, 2.0, 0.01)
    # The Convergence panel shows it, its meshes not run yet.
    assert w.convergence.check_name == "Fine check"
    assert "Fine check: mesh convergence" in w.convergence.title.text()
    assert w.convergence.run_button.isEnabled()
    fill(w, study=str(study.id), name="Fine check")
    assert convergence_panel.new_mesh_study(w) is None  # the name is taken: OK blocked
    assert "already a mesh study" in w.last_form.problem()


def test_ask_the_agent_hands_the_form_over(stand_in):
    """Where the agent is set up, the button closes the form, makes nothing, and puts a
    request built from the values in the agent's prompt box, unsent."""
    from puzzle.gui import convergence_panel
    from puzzle.model.schema import MeshStudy

    w = stand_in
    w.agent.provider_factory = lambda *a, **k: None  # a configured agent

    def run(dialog):
        dialog.set("target", "3")
        w.last_form = dialog
        dialog.ask_button.click()
        return False

    w.run_dialog = run
    assert convergence_panel.new_mesh_study(w) is None
    assert not w.session.project.of_type(MeshStudy)
    text = w.agent.input.text()
    assert "mesh study 'S mesh study'" in text and "within 3 %" in text
    assert not w.agent_dock.isHidden()


def test_no_ask_button_without_an_agent(stand_in, monkeypatch):
    from puzzle.gui import agent_prefill, convergence_panel

    w = stand_in
    monkeypatch.setattr(agent_prefill, "agent_ready", lambda window: False)
    fill(w, name="Plain")
    convergence_panel.new_mesh_study(w)
    assert not hasattr(w.last_form, "ask_button")


def test_mesh_shootout_shows_what_the_tool_returns(app, stand_in, monkeypatch):
    """Both solve through `shootout.mesh_shootout_report`; with the solves stood in, the
    form's table is the tool's comparisons, row for row."""
    pytest.importorskip("mcp")
    from puzzle import shootout
    from puzzle.agent.mcp import build_server
    from puzzle.gui import mesh_shootout as form
    from puzzle.model.schema import Study

    asked = []

    def fake(project, study_id, root, workdir, metrics, **_):
        asked.append([(m.quantity, m.norm, m.tolerance) for m in metrics])
        out = shootout.ShootoutRun(primary="gmsh", runs={})
        for i, m in enumerate(metrics):
            out.comparisons.append(shootout.Comparison("netgen", m, 0.01 + 0.05 * i))
        return out

    monkeypatch.setattr(shootout, "run_mesh_shootout", fake)
    w = stand_in
    study = w.session.project.of_type(Study)[0]
    box = None
    fill(w, study=str(study.id), quantity="displacement", also="temperature", tolerance="4")
    form.mesh_shootout(w)
    box = w.last_form.widgets["quantity"]
    assert [box.itemData(i) for i in range(box.count())] == [v for _, v in form.DEFAULTS]
    wait(app, w)
    table = w.mesh_shootout_table
    shown = [[table.item(r, c).text() for c in range(table.columnCount())]
             for r in range(table.rowCount())]  # fmt: skip
    # A run for an agent (ADR 0090): at "propose" it would be a proposal
    w.session.submit({"command": "set_autonomy", "level": "act_in_budget"})
    server = build_server(w.session.root, "test")
    report = _call(server, "mesh_shootout", study="S",
                   quantities=["displacement", "temperature"], tolerance=0.04)  # fmt: skip
    assert asked[0] == asked[1] == [("displacement", "relative_value", 0.04),
                                    ("temperature", "relative_value", 0.04)]  # fmt: skip
    assert shown == form.rows(report, 0.04)
    assert shown[0] == ["Displacement", "1 %", "4 %", "✓ agree"]
    assert shown[1][3] == "✗ differ"
    assert w.output_tabs.currentWidget() is table


@pytest.fixture
def plate(app, tmp_path):
    """A 50 x 30 x 10 mm box part with a second face selection."""
    pytest.importorskip("OCP")
    from puzzle.model.schema import Geometry, HistoryStep, NamedSelection, Project

    p = Project("p")
    p.add(Geometry(name="Plate", history=[HistoryStep(
        kind="box", name="Base", args={"dx": 0.05, "dy": 0.03, "dz": 0.01})],
        selections=[NamedSelection(name="Body", entity="body", members=["Body"]),
                    NamedSelection(name="Ends", entity="face",
                                   members=["Base/-x", "Base/+x"])]))  # fmt: skip
    save_project(p, tmp_path / "plate")
    w = MainWindow(tmp_path / "plate")
    yield w
    w.close()


def test_selection_by_rule_counts_lights_and_makes_it(si_units, app, plate):
    pytest.importorskip("mcp")
    from puzzle.agent.mcp import build_server
    from puzzle.gui import selection_rule as form
    from puzzle.model.schema import Geometry

    w = plate
    geo = w.session.project.of_type(Geometry)[0]
    fill(w, geometry=str(geo.id), entity="face", rule="normal", direction="0, 0, 1",
         within="1", name="Top")  # fmt: skip
    w.selection_by_rule(geo)
    assert w.last_form.property("help") == "form:selection_rule"
    assert w.last_form.matches.text() == "Picks 1 face: Base/+z"
    rule = {"normal": {"direction": [0.0, 0.0, 1.0], "within": 1.0}}
    assert last_batch(w) == [_dump(form.command(geo, "Top", "face", rule))]
    # The tool an agent checks selections with resolves it to the same face.
    server = build_server(w.session.root, "test")
    reports = _call(server, "selections_check", geometry="Plate")["selections"]
    top = next(r for r in reports if r["selection"] == "Top")
    assert [m["now"] for m in top["members"]] == ["Base/+z"]

    # A box in the units shown, and a set over selections; the members follow.
    geo = w.session.project.of_type(Geometry)[0]
    fill(w, geometry=str(geo.id), entity="face", rule="box", low="-1, -1, -1",
         high="1, 1, 0.005", name="Low")  # fmt: skip
    w.selection_by_rule(geo)
    assert w.last_form.matches.text().startswith("Picks 1 face: Base/-z")
    geo = w.session.project.of_type(Geometry)[0]
    fill(w, geometry=str(geo.id), entity="face", rule="union", first="Ends", second="Top",
         name="Three")  # fmt: skip
    w.selection_by_rule(geo)
    assert w.last_form.matches.text().startswith("Picks 3 faces")
    assert last_batch(w) == [_dump(form.command(geo, "Three", "face",
                                                {"union": ["Ends", "Top"]}))]  # fmt: skip
    wait(app, w)  # the preview the highlight asked for


def test_the_rule_is_built_in_si_from_the_units_shown(engineering_units):
    from puzzle.gui.selection_rule import rule_of

    got = rule_of({"rule": "near", "point": "10, 0, 5", "distance": "2"})
    assert got == {"near": {"point": [0.01, 0.0, 0.005], "distance": 0.002}}
    assert rule_of({"rule": "area", "min_area": "100"}) == {"min_area": pytest.approx(1e-4)}
    with pytest.raises(ValueError, match="two selections"):
        rule_of({"rule": "difference", "first": "A"})


@pytest.fixture
def cantilever(app, tmp_path):
    root = tmp_path / "c"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("results*", ".puzzle",
                                                                 "history.jsonl"))  # fmt: skip
    w = MainWindow(root)
    yield w
    w.close()


def test_who_may_connect_sends_what_the_cli_does(cantilever, tmp_path):
    from puzzle.cli import main
    from puzzle.gui.settings_dialog import SettingsDialog

    w = cantilever
    dialog = SettingsDialog(agent_path=tmp_path / "agent.toml", parent=w)
    assert dialog.who.currentData() == "local" and dialog.may.currentData() == "propose"
    dialog.who.setCurrentIndex(dialog.who.findData("network"))
    dialog.save()
    assert w.session.project.agent.mcp == "network"
    ours = [e for e in w.session.history_entries() if e["type"] == "command"][-1]["command"]

    other = tmp_path / "other"
    shutil.copytree(EXAMPLE, other, ignore=shutil.ignore_patterns("results*", ".puzzle",
                                                                  "history.jsonl"))  # fmt: skip
    assert main(["autonomy", str(other), "--mcp", "network"]) == 0
    from puzzle.commands import Session

    theirs = [e for e in Session(other).history_entries() if e["type"] == "command"][-1]
    assert ours == theirs["command"] == {"command": "set_autonomy", "mcp": "network"}


def test_act_freely_needs_the_double_sign_off(cantilever, tmp_path):
    from puzzle.gui.settings_dialog import SettingsDialog

    w = cantilever
    asked = []
    w.agent._confirm_act = lambda: asked.append(1) or False
    dialog = SettingsDialog(agent_path=tmp_path / "agent.toml", parent=w)
    dialog.may.setCurrentIndex(dialog.may.findData("act"))
    dialog.save()
    assert asked and w.session.project.agent.level == "propose"  # declined: unchanged
    assert dialog.result() == 0  # still open
    w.agent._confirm_act = lambda: True
    dialog.save()
    assert w.session.project.agent.level == "act"
    kinds = [e["type"] for e in w.session.history_entries()][-2:]
    assert kinds == ["confirmation_request", "command"]


def test_who_may_connect_is_in_the_project_menu(cantilever):
    w = cantilever
    seen = []

    def run(dialog):
        seen.append(dialog.groups.currentItem().text())
        return False

    w.run_dialog = run
    project = w.menus["Project"]
    next(a for a in project.actions() if a.text() == "Who may connect…").trigger()
    assert seen == ["Agent"]


def test_new_part_is_one_chooser_from_every_menu(app, cantilever):
    from puzzle.gui import menus
    from puzzle.gui.tree import Node
    from puzzle.model.schema import Geometry

    w = cantilever
    offered = []

    def run(dialog):
        box = dialog.widgets["start"]
        offered.append([box.itemData(i) for i in range(box.count())])
        return False

    w.run_dialog = run
    geometry = w.menus["Geometry"]
    next(a for a in geometry.actions() if a.text() == "New part…").trigger()
    tree_menu = menus.build_menu(w, Node("section", label="Geometry"))
    next(a for a in tree_menu.actions() if a.text() == "New part…").trigger()
    w.workbench.new_part()
    assert offered == [["sketch", "box", "cylinder", "import"]] * 3  # the sketch first

    before = len(w.session.project.of_type(Geometry))
    fill(w, start="cylinder", name="Pin")
    menus.new_part(w)
    pin = next(g for g in w.session.project.of_type(Geometry) if g.name == "Pin")
    assert pin.history[0].kind == "cylinder" and pin.selections[0].name == "Body"
    assert w.last_form.property("help") == "form:new_part"
    fill(w, start="sketch", name="Plate", plane="xz")
    menus.new_part(w)
    assert w.left_tabs.currentWidget() is w.workbench
    assert w.workbench.sketching["new_part"] == "Plate"
    assert w.workbench.sketching["plane"] == "xz"
    w.workbench.cancel_sketch()
    assert len(w.session.project.of_type(Geometry)) == before + 1  # a sketch makes it later


def test_the_new_items_are_in_the_menus(cantilever):
    from test_main_menu import texts

    from puzzle.gui import menus
    from puzzle.gui.tree import Node
    from puzzle.model.schema import Study

    w = cantilever
    have = texts(w)
    for item in ("New mesh study…", "Mesh shoot-out…", "Selection by rule…",
                 "Who may connect…", "New part…"):  # fmt: skip
        assert item in have, item
    study = w.session.project.of_type(Study)[0]
    shown = [a.text() for a in menus.build_menu(w, Node("object", str(study.id))).actions()]
    assert "New mesh study…" in shown and "Mesh shoot-out…" in shown
    assert "Selection by rule…" in [a.text() for a in w.pick_bar.tool_actions.values()]


def test_every_form_opens_a_help_heading():
    from puzzle import help as manual

    for key, heading in (("form:new_mesh_study", "How to make a mesh study"),
                         ("form:mesh_shootout", "How to run a mesh shoot-out"),
                         ("form:selection_rule", "How to make a selection by rule"),
                         ("form:who_may_connect", "Outside agents over MCP"),
                         ("form:new_part", "How to build a part from steps")):  # fmt: skip
        found = manual.lookup(key)
        assert found is not None and found[1] == heading, key
