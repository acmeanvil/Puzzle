"""The baseline GUI on Qt's offscreen platform: tree, properties, commands, mesh, run.

VTK cannot draw offscreen (no OpenGL context), so the viewport records what it would
show and the tests check that; tests/integration/test_gui_render.py draws for real.
"""

import os
import shutil
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("pyvistaqt")

from PySide6.QtWidgets import QApplication  # noqa: E402
from solvers import available  # noqa: E402

from puzzle.commands import Session  # noqa: E402
from puzzle.commands.model import Edit, EditOp  # noqa: E402
from puzzle.gui.tree import ROLE  # noqa: E402
from puzzle.gui.window import MainWindow  # noqa: E402
from puzzle.model.schema import Material, Study  # noqa: E402

EXAMPLE = Path(__file__).parents[1] / "examples" / "cantilever"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path):
    root = tmp_path / "cantilever"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("results", ".puzzle"))
    w = MainWindow(root)
    yield w
    w.close()


def wait(app, condition, timeout=600.0):
    end = time.monotonic() + timeout
    while not condition():
        app.processEvents()
        if time.monotonic() > end:
            raise TimeoutError("the GUI did not finish in time")
        time.sleep(0.02)


def labels(window):
    return [i.text(0) for i in window.tree._items()]


def select(window, kind, label=None):
    for item in window.tree._items():
        node = item.data(0, ROLE)
        if node.kind == kind and (label is None or node.label == label):
            window.tree.setCurrentItem(item)
            return node
    raise LookupError((kind, label))


def property_item(window, path):
    for item in _walk(window.properties.invisibleRootItem()):
        data = item.data(1, 256)  # Qt.UserRole
        if data is not None and data[0] == path:
            return item
    raise LookupError(path)


def _walk(parent):
    for i in range(parent.childCount()):
        yield parent.child(i)
        yield from _walk(parent.child(i))


def test_tree_lists_the_model(window):
    names = labels(window)
    for expected in ("Geometry", "Bar", "Materials", "Structural steel", "Components"):
        assert expected in names
    assert "Tip load" in names and "fixed on Bar/Root" in names
    assert any(n.startswith("Root") for n in names)  # selections under the geometry


def test_an_edit_is_a_command_and_undoes(window):
    select(window, "object", "Tets 2.5 mm")
    item = property_item(window, "global_size")
    assert item.text(1) == "2.5 mm"
    item.setText(1, "4 mm")  # as if typed into the cell
    recipe = next(o for o in window.session.project.objects.values() if o.name == "Tets 2.5 mm")
    assert recipe.global_size.value == 4 and recipe.global_size.unit == "mm"
    entry = window.session.history(limit=1)[0]
    assert entry["type"] == "command" and entry["actor"]["kind"] == "user"
    assert entry["command"]["ops"][0]["path"] == "global_size"
    assert window.history.item(0).text().startswith(f"#{entry['seq']}  user  command edit")

    window.undo()
    recipe = next(o for o in window.session.project.objects.values() if o.name == "Tets 2.5 mm")
    assert recipe.global_size.value == 2.5


def test_a_bad_edit_is_refused_and_reported(window):
    select(window, "object", "Tets 2.5 mm")
    before = len(window.session.history_entries())
    property_item(window, "element_order").setText(1, "7")
    assert len(window.session.history_entries()) == before
    assert "error:" in window.log.toPlainText()
    assert property_item(window, "element_order").text(1) == "2"  # the panel shows the truth


def test_materials_add_and_delete(window):
    window.add_material("air")
    names = [m.name for m in window.session.project.of_type(Material)]
    assert len(names) == 2
    added = next(n for n in names if n != "Structural steel")
    select(window, "object", added)
    window.delete_selected()
    assert [m.name for m in window.session.project.of_type(Material)] == ["Structural steel"]


def test_changes_from_another_client_show_up(app, window):
    other = Session(window.session.root)  # e.g. an agent over MCP, or the CLI
    other.submit(Edit(object="Tip load", ops=[EditOp(op="set", path="name", value="Tip force")]))
    wait(app, lambda: "Tip force" in labels(window), timeout=10)


@pytest.mark.skipif(not available("calculix"), reason="calculix not available")
def test_mesh_highlight_and_run(app, window):
    select(window, "object", "Cantilever")
    window.mesh_selected()
    wait(app, lambda: not window.tasks)
    assert window.meshes, window.log.toPlainText()
    select(window, "selection", "Tip")
    assert window.viewport.shown == {
        "kind": "mesh",
        "cells": window.viewport.shown["cells"],
        "highlight": "Bar/Tip",
    }
    select(window, "feature", "Bar/Root")
    assert window.viewport.shown["highlight"] == "Bar/Root"

    select(window, "object", "Tip load")
    window.run_selected()
    wait(app, lambda: not window.tasks)
    assert any(n.startswith("Tip load · calculix") for n in labels(window)), (
        window.log.toPlainText()
    )
    assert window.results_bar.isVisible() or window.results_bar.field.count()
    shown = window.viewport.shown
    assert shown["kind"] == "result" and shown["scalar"] == "displacement_magnitude"
    assert shown["max"] > 0 and shown["scale"] > 0

    window.results_bar.field.setCurrentText("stress")
    assert window.viewport.shown["scalar"] == "von_mises"
    study = next(s for s in window.session.project.of_type(Study) if s.name == "Tip load")
    assert window.session.history(limit=1)[0]["command"]["study"] == str(study.id)


SET_TIP = {
    "command": "edit",
    "object": "Cantilever",
    "ops": [
        {
            "op": "set",
            "path": "physics.0.features.1.values.y",
            "value": {"value": -250, "unit": "N"},
        }
    ],
}


def _scripted_window(app, tmp_path, replies):
    from puzzle.agent.providers import ScriptedProvider

    root = tmp_path / "cantilever"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("results", ".puzzle"))
    provider = ScriptedProvider(replies)
    return MainWindow(root, provider_factory=lambda: provider), provider


def _tip(window):
    comp = next(o for o in window.session.project.objects.values() if o.name == "Cantilever")
    return comp.physics[0].features[1].values["y"].value


def test_agent_proposes_and_the_user_accepts(app, tmp_path):
    from puzzle.agent.providers import Reply, ToolCall

    replies = [
        Reply(tool_calls=[ToolCall("1", "submit", {"command": SET_TIP, "rationale": "asked"})]),
        Reply(text="Proposed a 250 N tip load."),
    ]
    window, _ = _scripted_window(app, tmp_path, replies)
    try:
        window.agent.send("make the tip load 250 N")
        wait(app, lambda: not window.tasks, timeout=60)
        assert "Proposed a 250 N tip load." in window.agent.transcript.toPlainText()
        assert window.agent.proposals.count() == 1
        assert "edit Cantilever — asked" in window.agent.proposals.item(0).text()
        assert _tip(window) == -100

        window.agent.accept_selected()
        wait(app, lambda: not window.tasks, timeout=60)
        assert _tip(window) == -250
        assert window.agent.proposals.count() == 0
        assert "decision" in window.history.item(0).text()
        # The change itself is the agent's, accepted by the user:
        assert "agent built-in (accepted by user)  command edit" in window.history.item(1).text()
    finally:
        window.close()


def test_agent_rejects_and_levels(app, tmp_path, monkeypatch):
    from puzzle.agent.providers import Reply, ToolCall

    replies = [Reply(tool_calls=[ToolCall("1", "submit", {"command": SET_TIP})]), Reply(text="ok")]
    window, _ = _scripted_window(app, tmp_path, replies)
    try:
        window.agent.send("change it")
        wait(app, lambda: not window.tasks, timeout=60)
        window.agent.reject_selected()
        assert window.agent.proposals.count() == 0 and _tip(window) == -100

        window.agent._set_level("act_in_budget")
        assert window.session.project.agent.level == "act_in_budget"
        assert window.agent.level.currentText() == "act_in_budget"
        monkeypatch.setattr(window.agent, "_confirm_act", lambda: False)
        window.agent._set_level("act")  # declined at the second confirmation
        assert window.session.project.agent.level == "act_in_budget"
        monkeypatch.setattr(window.agent, "_confirm_act", lambda: True)
        window.agent._set_level("act")
        assert window.session.project.agent.level == "act"
        kinds = [e["type"] for e in window.session.history_entries()]
        assert "confirmation_request" in kinds  # both sign-offs are in the record
    finally:
        window.close()


def test_agent_without_a_provider_says_how_to_set_one(app, window, monkeypatch):
    monkeypatch.setenv("PUZZLE_AGENT_PROVIDER", "nonsense")
    window.agent.send("hello")
    assert "unknown agent provider" in window.agent.transcript.toPlainText()
    assert not window.tasks
