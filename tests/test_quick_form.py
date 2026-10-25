"""One dialog per new object (no chains of popups): `gui.quick_form`.

New project asks its folder, name and units together; the name follows the folder
until typed in, and OK is blocked (with the reason on top) until both are given.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from forms import fill  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from puzzle.gui.quick_form import Ask, QuickForm  # noqa: E402
from puzzle.gui.window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_new_project_is_one_dialog(app, tmp_path):
    window = MainWindow()
    try:
        fill(window, folder=str(tmp_path / "Bracket"), units="engineering")
        window._new_dialog()
        form = window.last_form
        assert form.values()["name"] == "Bracket"  # followed the folder
        assert window.session.root == tmp_path / "Bracket"
        assert window.session.project.name == "Bracket"
        fill(window, folder="")
        window._new_dialog()
        assert window.last_form.problem() == "Choose a folder"
    finally:
        window.close()


def test_fields_follow_hide_and_rebuild(app):
    physics = {"Beam": ["solid"], "Plate": ["solid", "heat"]}
    form = QuickForm(
        "New study",
        [
            Ask("component", "Component", choices=[("Beam", "Beam"), ("Plate", "Plate")]),
            Ask(
                "physics",
                "Physics",
                choices=lambda v: [(p, p) for p in physics[v["component"]]],
                shown=lambda v: len(physics[v["component"]]) > 1,
            ),
            Ask("name", "Name", kind="text", follow=lambda v: f"{v['component']} study"),
        ],
    )
    assert "physics" not in form.values()  # Beam has one physics: not asked
    assert form.values()["name"] == "Beam study"
    form.set("component", "Plate")
    assert form.values() == {"component": "Plate", "physics": "solid", "name": "Plate study"}
    form.set("physics", "heat")
    form.set("name", "Mine")
    form.set("component", "Beam")
    assert form.values() == {"component": "Beam", "name": "Mine"}  # typed names stay
    with pytest.raises(ValueError, match="no choice 'Shell'"):
        form.set("component", "Shell")
