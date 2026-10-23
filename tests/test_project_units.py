"""ADR 0030: a project's own unit system, switched on per project, over the user's.

A new project asked for engineering units shows mm and MPa to everyone; switching the
project's units off gives each user their own (SI by default here); undo brings the
project's back. Values never change: the stored force is the same through all of it.
"""

import os
import shutil
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from puzzle import settings  # noqa: E402
from puzzle.commands import Session  # noqa: E402
from puzzle.commands.model import Create  # noqa: E402
from puzzle.errors import PuzzleError  # noqa: E402
from puzzle.gui.viewport import bar_format  # noqa: E402
from puzzle.gui.window import MainWindow  # noqa: E402
from puzzle.model.schema import ProjectUnits  # noqa: E402

EXAMPLE = Path(__file__).parents[1] / "examples" / "cantilever"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_a_new_project_with_units_shows_them_to_everyone(app, tmp_path):
    w = MainWindow()
    try:
        w.new_project(tmp_path / "p", "P", units="engineering")
        (units,) = w.session.project.of_type(ProjectUnits)
        assert units.use and units.system == "engineering"
        assert settings.current().display_units()["pressure"] == "MPa"
        assert w.units_button.text() == "mm · N · MPa · °C  (project)"
        # Someone else opening it sees the same, whatever their own preference is.
        assert settings.current().get("units.system") == "si"
    finally:
        w.close()


def test_the_switch_off_gives_each_user_theirs_and_undo_restores(app, tmp_path):
    root = tmp_path / "cantilever"
    shutil.copytree(
        EXAMPLE, root, ignore=shutil.ignore_patterns("results*", ".puzzle", "history.jsonl")
    )
    w = MainWindow(root)
    try:
        assert w.units_button.text().endswith("(yours)") and "Pa" in w.units_button.text()

        def on(dialog):
            dialog.use.setChecked(True)
            dialog.system.setCurrentIndex(dialog.system.findData("engineering"))
            return True

        w.run_dialog = on
        w.edit_units()
        assert settings.project_system() == "engineering"
        assert w.units_button.text().startswith("mm")
        from puzzle.commands import outline

        assert "Units shown: SI with mm, MPa and °C" in outline(Session(root).project)

        def off(dialog):
            dialog.use.setChecked(False)
            return True

        w.run_dialog = off
        w.edit_units()
        assert settings.project_system() is None and w.units_button.text().endswith("(yours)")
        w.undo()
        assert settings.project_system() == "engineering"
        with pytest.raises(PuzzleError, match="has its units already"):
            Session(root).submit(Create(kind="units", data={"system": "si"}))
    finally:
        w.close()


def test_colour_bar_labels_read_as_numbers():
    assert bar_format(0.99, 121.0) % 121.0 == "121"
    assert bar_format(0, 0.0123) % 0.0123 == "0.0123"
    assert bar_format(0, 45000) % 45000 == "45000"
    assert bar_format(0, 1.21e8) % 1.21e8 == "1.210e+08"
