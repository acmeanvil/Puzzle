"""Per-user preferences (ADR 0024): stored outside projects, shown in one dialog,
applied to what the GUI shows, never to results."""

import os

import pytest

from puzzle.model.units import pretty, shown, system_units
from puzzle.settings import Settings, settings_path


def test_preferences_round_trip_and_keep_unknown_keys():
    s = Settings()
    assert s.get("units.system") == "si" and s.get("geometry.preview") == "live"
    s.set("units.system", "imperial")
    s.data["future.option"] = 3  # written by a newer Puzzle
    s.save()
    again = Settings()
    assert again.get("units.system") == "imperial" and again.data["future.option"] == 3
    with pytest.raises(ValueError, match="Display units is one of"):
        again.set("units.system", "cubits")
    assert settings_path().parent == __import__("pathlib").Path(os.environ["PUZZLE_USER_DIR"])


def test_display_units_convert_what_is_shown():
    si, eng, imp = (system_units(n) for n in ("si", "engineering", "imperial"))
    assert shown(2.5, "mm", si) == (pytest.approx(0.0025), "m")
    assert shown(210, "GPa", eng) == (pytest.approx(210e3), "MPa")
    assert shown(20, "degC", imp) == (pytest.approx(68.0), "degF")
    assert shown(25.4, "mm", imp) == (pytest.approx(1.0), "in")
    mine = system_units("custom", {"pressure": "ksi", "length": "cm"})
    assert shown(1, "MPa", mine)[1] == "ksi" and shown(10, "mm", mine) == (pytest.approx(1), "cm")
    assert pretty("W/(m^2*K)") == "W/(m²·K)" and pretty("degC") == "°C"


def test_the_cli_lists_and_sets_preferences(capsys):
    from puzzle.cli import main

    assert main(["settings"]) == 0
    assert "units.system" in capsys.readouterr().out
    assert main(["settings", "units.system", "engineering"]) == 0
    assert Settings().get("units.system") == "engineering"
    assert main(["settings", "units.system", "cubits"]) == 1


# --- the GUI ----------------------------------------------------------------------------

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path):
    import shutil
    from pathlib import Path

    from puzzle.gui.window import MainWindow

    example = Path(__file__).parents[1] / "examples" / "cantilever"
    root = tmp_path / "cantilever"
    shutil.copytree(
        example, root, ignore=shutil.ignore_patterns("results*", ".puzzle", "history.jsonl")
    )
    w = MainWindow(root)
    yield w
    while w.tasks:
        app.processEvents()
    w.close()


def _size_item(window):
    from puzzle.gui.tree import Node

    mesh = next(o for o in window.session.project.objects.values() if o.name == "Tets 2.5 mm")
    window.tree.select(Node("object", str(mesh.id)))
    stack = [window.properties.invisibleRootItem()]
    while stack:
        item = stack.pop()
        for i in range(item.childCount()):
            child = item.child(i)
            data = child.data(1, 256)
            if data and data[0] == "global_size":
                return child
            stack.append(child)
    raise LookupError


def test_the_dialog_saves_and_the_window_shows_the_new_units(window):
    from puzzle.agent.settings import load_settings
    from puzzle.gui.settings_dialog import SettingsDialog

    assert _size_item(window).text(1) == "0.0025 m"  # SI by default
    dialog = SettingsDialog(parent=window)
    units = dialog.boxes["units.system"]
    assert not dialog.boxes["units.custom.length"].isEnabled()  # only for "my own"
    units.setCurrentIndex(units.findData("imperial"))
    preview = dialog.boxes["geometry.preview"]
    preview.setCurrentIndex(preview.findData("on_demand"))
    assert dialog.boxes["meshing.preview"].isEnabled()  # ADR 0019 built
    assert dialog.boxes["meshing.element_types"].isEnabled()
    dialog.model.setText("claude-haiku-5-5")
    dialog.save()
    window.settings_dialog = lambda: _Accepted()
    window.open_settings()
    assert _size_item(window).text(1) == "0.0984252 in"
    assert load_settings().model == "claude-haiku-5-5"

    # A bare number is in the unit shown; the stored value keeps its unit.
    _size_item(window).setText(1, "0.2")
    mesh = next(o for o in window.session.project.objects.values() if o.name == "Tets 2.5 mm")
    assert mesh.global_size.unit == "in" and mesh.global_size.to_si() == pytest.approx(0.00508)


class _Accepted:
    def exec(self):
        return True


def test_tools_preview_on_demand_when_asked(app, tmp_path):
    from puzzle.gui.window import MainWindow
    from puzzle.settings import Settings, reload

    s = Settings()
    s.set("geometry.preview", "on_demand")
    s.set("units.system", "engineering")
    s.save()
    reload()
    w = MainWindow()
    try:
        w.new_project(tmp_path / "p", "P")
        w.ask_text = lambda *a, **k: "Part 1"
        from puzzle.gui import menus

        menus.new_part(w)
        w.left_tabs.setCurrentWidget(w.workbench)
        tool = w.workbench.start_tool("fillet")
        assert w.pending is None  # nothing previewed until asked
        buttons = [
            b.text()
            for b in tool.findChildren(__import__("PySide6.QtWidgets").QtWidgets.QPushButton)
        ]
        assert "Preview" in buttons
        assert tool.form.widgets["r"].text() == "2"  # mm, the display length unit
        tool.preview()
        assert w.pending is not None
    finally:
        while w.tasks:
            app.processEvents()
        w.close()
