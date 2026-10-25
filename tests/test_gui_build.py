"""Building a model from nothing with the GUI's menus, then solving it.

The menu functions are called as the menu entries call them; `choose` and
`ask_text` stand in for the dialogs. Face selections are added the way a pick in the
view adds them (an Edit on the geometry), since picking needs a real OpenGL view.
"""

import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from conftest_geometry import write_bar_step  # noqa: E402
from forms import fill  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from solvers import available  # noqa: E402

from puzzle.commands.model import Edit, EditOp  # noqa: E402
from puzzle.geometry import import_step  # noqa: E402
from puzzle.gui import menus  # noqa: E402
from puzzle.gui.tree import Node  # noqa: E402
from puzzle.gui.window import MainWindow  # noqa: E402
from puzzle.model.catalog import spec  # noqa: E402
from puzzle.model.schema import Component, Geometry, Study  # noqa: E402

# These tests type lengths in mm (Settings > Units: engineering).
pytestmark = pytest.mark.usefixtures("engineering_units")

EXAMPLE = Path(__file__).parents[1] / "examples" / "cantilever"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class Answers:
    """Scripted replies to the window's questions, in order."""

    def __init__(self, window, *replies):
        self.replies = list(replies)
        window.choose = self.next
        window.ask_text = self.next

    def next(self, *args, **kwargs):
        return self.replies.pop(0)


def _form(window, action):
    """The dialog an action shows, filled as `fill` was told, without accepting it."""
    shown = []
    fill_run = window.run_dialog

    def keep(dialog):
        fill_run(dialog)
        shown.append(dialog)
        return False

    window.run_dialog = keep
    action(window)
    window.run_dialog = fill_run
    return shown[0]


def _only(window, cls):
    (obj,) = window.session.project.of_type(cls)
    return obj


@pytest.mark.skipif(not available("calculix"), reason="calculix not available")
def test_build_and_solve_a_cantilever_from_menus(app, tmp_path):
    window = MainWindow()
    try:
        window.new_project(tmp_path / "beam", "Beam")
        step = write_bar_step(tmp_path / "bar.step")
        menus.import_step_file(window, step)
        geo = _only(window, Geometry)
        assert (tmp_path / "beam" / "bar.step").exists()  # copied into the project
        assert [s.name for s in geo.selections] == ["Body"]

        # What a pick in the view does: name faces as selections on the geometry.
        model = import_step(tmp_path / "beam" / "bar.step")
        for name, (axis, at) in {"Root": ("x", 0.0), "Tip": ("x", 0.2)}.items():
            value = {"name": name, "entity": "face", "members": model.faces_on_plane(axis, at)}
            window.submit(
                Edit(object="bar", ops=[EditOp(op="append", path="selections", value=value)])
            )

        menus.new_mesh(window)
        fill(window, geometry="bar", mesh="Mesh 1", name="Beam")
        menus.new_component(window)
        comp = _only(window, Component)
        assert comp.parts[0].name == "bar"

        window.add_material("structural_steel")
        fill(window, material="Structural steel", selection="bar/Body")
        menus.assign_material(window, _only(window, Component))
        menus.add_physics(window, _only(window, Component), "solid_mechanics")
        Answers(window, "bar/Root")
        menus.add_feature(window, _only(window, Component), 0, spec("solid_mechanics", "fixed"))
        Answers(window, "bar/Tip")
        menus.add_feature(window, _only(window, Component), 0, spec("solid_mechanics", "force"))
        force = _only(window, Component).physics[0].features[1]
        window.submit(
            Edit(
                object="Beam",
                ops=[
                    EditOp(
                        op="set",
                        path="physics.0.features.1.values.y",
                        value={"value": -100, "unit": "N"},
                    )
                ],
            )
        )
        assert force.type == "force" and force.selection == "bar/Tip"

        fill(window, component="Beam", solver="calculix", analysis="stationary", name="Bend")
        menus.new_study(window)
        study = _only(window, Study)
        assert window.session.project.problems() == []

        window.tree.select(Node("object", str(study.id)))
        window.run_selected()
        end = time.monotonic() + 600
        while window.tasks and time.monotonic() < end:
            app.processEvents()
            time.sleep(0.02)
        shown = window.viewport.shown
        assert shown.get("kind") == "result", window.log.toPlainText()[-2000:]
        # Euler-Bernoulli tip deflection of the 200 x 10 x 10 mm steel bar: F L^3 / 3 E I.
        E, L, I = 210e9, 0.2, 0.01**4 / 12  # noqa: E741
        assert shown["unit"] == "mm"  # Settings > Units: engineering in these tests
        assert shown["max"] == pytest.approx(1e3 * 100 * L**3 / (3 * E * I), rel=0.05)

        # Everything above went through the command log, attributed to the user.
        kinds = [
            e["command"]["command"]
            for e in window.session.history_entries()
            if e["type"] == "command"
        ]
        assert kinds.count("create") == 4 and "run_study" in kinds
    finally:
        window.close()


def test_menus_match_the_item(app, tmp_path):
    import shutil
    from pathlib import Path

    root = tmp_path / "c"
    shutil.copytree(
        Path(__file__).parents[1] / "examples" / "cantilever",
        root,
        ignore=shutil.ignore_patterns("results*", ".puzzle", "history.jsonl"),
    )
    window = MainWindow(root)
    try:
        comp = _only(window, Component)
        texts = lambda node: [a.text() for a in menus.build_menu(window, node).actions()]  # noqa: E731
        assert "Add physics" in texts(Node("object", str(comp.id)))
        physics = menus.build_menu(window, Node("physics", str(comp.id), "physics.0"))
        from PySide6.QtWidgets import QMenu

        (sub,) = [m for m in physics.findChildren(QMenu) if m.title() == "Add feature"]
        assert [a.text() for a in sub.actions()] == [
            s.label for s in menus.specs("solid_mechanics")
        ]
        assert texts(Node("feature", str(comp.id), "physics.0.features.0")) == [
            "Change selection…",
            "Reaction force…",  # a support (ADR 0042)
            "Remove",
        ]
        assert texts(Node("feature", str(comp.id), "physics.0.features.1")) == [
            "Change selection…",
            "Remove",
        ]

        # Removing a feature from its menu is one undoable edit.
        menus.remove(window, comp, "physics.0.features.0")
        assert [f.type for f in _only(window, Component).physics[0].features] == ["force"]
        window.undo()
        assert [f.type for f in _only(window, Component).physics[0].features] == ["fixed", "force"]
        assert menus.selections_of(window.session.project, comp, "face") == ["Bar/Root", "Bar/Tip"]
    finally:
        window.close()


def test_a_study_on_a_multiphysics_component_asks_which_physics(app, tmp_path):
    """The cantilever has heat transfer and solid mechanics; a new Code_Aster study
    solves one of them (an empty list would mean both, which Code_Aster refuses)."""
    import shutil

    from puzzle.gui import menus
    from puzzle.gui.window import MainWindow

    root = tmp_path / "cantilever"
    shutil.copytree(
        EXAMPLE, root, ignore=shutil.ignore_patterns("results*", ".puzzle", "history.jsonl")
    )
    window = MainWindow(root)
    try:
        before = {s.id for s in window.session.project.of_type(Study)}
        # One dialog asks everything; the physics row shows because there are two.
        from puzzle.gui.choices import label

        fill(window, component="Cantilever", solver="code_aster", analysis="stationary")
        form = _form(window, menus.new_study)
        assert form.form.isRowVisible(form.widgets["physics"])
        form.set("physics", "heat_transfer")  # the name follows the choices
        assert form.values()["name"] == f"Cantilever {label('heat_transfer').lower()}"
        fill(
            window,
            component="Cantilever",
            solver="code_aster",
            analysis="stationary",
            physics="solid_mechanics",
            name="Static",
        )
        menus.new_study(window)
        (study,) = [s for s in window.session.project.of_type(Study) if s.id not in before]
        assert study.steps[0].physics == ["solid_mechanics"] and study.name == "Static"
        # Names already taken block OK, with the reason on top.
        fill(window, name="Static")
        assert menus.new_study(window) is None
        assert window.last_form.problem() == "There is already a study 'Static'"
    finally:
        while window.tasks:
            app.processEvents()
        window.close()
