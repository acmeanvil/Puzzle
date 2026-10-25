"""The Advisor (ADR 0040): the next step from the project's state, the same for the
panel, the agent and the CLI; it never blocks and follows what is selected."""

import os
import shutil
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from puzzle.advisor import advise, next_step  # noqa: E402
from puzzle.model.schema import Feature, Project  # noqa: E402

EXAMPLE = Path(__file__).parents[1] / "examples" / "cantilever"


def _states(advice):
    return {a.step: a.state for a in advice}


def test_an_empty_project_starts_with_geometry():
    advice = advise(Project(name="new"))
    assert next_step(advice).step == "geometry"
    assert next_step(advice).action == "import_geometry"
    assert advice[-1].step == "component"  # nothing past it until there is one


def test_a_built_model_steps_along(tmp_path):
    pytest.importorskip("OCP")
    from conftest_geometry import bar_project, write_bar_step

    from puzzle.model.schema import Physics, SolveStep, Study

    physics = Physics(type="solid_mechanics", features=[])
    project, comp = bar_project(write_bar_step(tmp_path / "bar.step"), [physics])
    comp.materials = []
    advice = advise(project, component=comp)
    assert next_step(advice).step == "materials"
    assert next_step(advice).action == "assign_material"
    project, comp = bar_project(write_bar_step(tmp_path / "bar.step"), [physics])
    step = next_step(advise(project, component=comp))
    assert step.step == "conditions" and step.action == "add_feature:0:fixed"
    assert step.detail == "Needs a support (and 1 more)."
    physics.features += [Feature(type="fixed", selection="Bar/Root"),
                         Feature(type="force", selection="Bar/Tip")]  # fmt: skip
    step = next_step(advise(project, component=comp))
    assert step.step == "study" and step.action == "new_study"
    project.add(Study(name="Modes", component=comp.id, solver="calculix",
                      steps=[SolveStep(type="modal")]))  # fmt: skip
    physics.features = []  # a free modal study needs no support or load
    advice = advise(project, component=comp)
    assert _states(advice)["conditions"] == "done"
    assert next_step(advice).step == "run" and next_step(advice).action == "run"


def test_the_panel_follows_and_its_buttons_go(tmp_path):
    pytest.importorskip("PySide6")
    pytest.importorskip("OCP")
    from PySide6.QtWidgets import QApplication

    from puzzle import settings
    from puzzle.gui.window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        window.new_project(tmp_path / "p", "P")
        assert window.advisor_dock.isVisible() or not window.isVisible()
        window.advisor.refresh()
        assert window.advisor.heading.text() == "Next: Import geometry…"
        assert "import_geometry" in window.advisor.buttons
        assert window.advisor.example.isVisibleTo(window.advisor)  # an empty project
        # Closing it once keeps it closed for later new projects.
        window.advisor_dock.close()
        app.processEvents()
        assert settings.current().get("advisor.open_new") is False
    finally:
        settings.current().set("advisor.open_new", True)
        window.close()

    root = tmp_path / "c"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("results*", ".puzzle",
                                                                 "history.jsonl"))  # fmt: skip
    window = MainWindow(root)
    try:
        window.advisor.refresh()
        assert window.advisor.heading.text().startswith("Next: ")
        assert not window.advisor.example.isVisibleTo(window.advisor)
    finally:
        window.close()


def test_agents_and_the_cli_hear_the_same(tmp_path, capsys):
    import asyncio

    from puzzle.agent.mcp import build_server
    from puzzle.cli import main
    from puzzle.project import save_project

    root = tmp_path / "new"
    save_project(Project(name="new"), root)
    result = asyncio.run(build_server(root, "test").call_tool("next_steps", {}))
    data = result.structured_content
    assert data["next"]["step"] == "geometry" and data["steps"][0]["title"] == "Geometry"
    assert main(["advise", str(root)]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "Next: Import geometry…" and "● Geometry" in out
