"""The look (ADR 0045): Fusion everywhere, palettes from the tokens for each theme, roles
in place of inline colours, the 3D background setting, and every icon drawn."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtGui import QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from puzzle import settings  # noqa: E402
from puzzle.gui import icons, theme  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_each_theme_sets_fusion_and_its_palette(app):
    for name, tokens in theme.SETS.items():
        assert theme.apply(app, preference=name) is tokens
        sheet = app.styleSheet()
        app.setStyleSheet("")  # the sheet wraps the style; under it is Fusion
        assert app.style().name() == "fusion"
        app.setStyleSheet(sheet)
        p = app.palette()
        assert p.color(QPalette.ColorRole.Window).name() == tokens.surface
        assert p.color(QPalette.ColorRole.Highlight).name() == tokens.accent
        assert theme.current() is tokens
    theme.apply(app, preference="light")


def test_theme_and_background_are_preferences():
    keys = {p.key: p for p in settings.PREFERENCES}
    assert keys["interface.theme"].default == "system"
    assert {v for _, v in keys["interface.theme"].choices} == {
        "system",
        "light",
        "dark",
        "high_contrast",
    }
    assert keys["interface.view_background"].default == "gradient"


def test_roles_replace_inline_colours(app):
    label = theme.role(QLabel("hint"), "muted")
    assert label.property("role") == "muted"
    assert 'QLabel[role="muted"]' in theme.style_sheet(theme.LIGHT)


def test_background_gradient_or_plain():
    calls = []

    class Plotter:
        def set_background(self, colour, top=None):
            calls.append((colour, top))

    theme.view_background(Plotter(), theme.LIGHT, "gradient")
    theme.view_background(Plotter(), theme.LIGHT, "plain")
    assert calls == [(theme.LIGHT.view_bottom, theme.LIGHT.view_top),
                     (theme.LIGHT.view_plain, None)]  # fmt: skip


def test_every_icon_draws_in_the_theme_colour(app):
    names = list(icons.TABLER) + list(icons.DRAWINGS)
    for name in names:
        assert not icons.icon(name).isNull(), name
    assert (icons.TABLER_DIR / "LICENSE").read_text().startswith("MIT License")
    dark = icons._svg("run", "#e6e8eb").decode()
    assert "#e6e8eb" in dark and "currentColor" not in dark


def test_a_background_of_my_own(app, tmp_path):
    """A colour of the user's own behind the model, or a gradient of two; Window →
    3D view background… picks one and keeps it; the Settings dialog shows a colour
    button for it."""
    from puzzle.gui.settings_dialog import ColourButton, SettingsDialog

    calls = []

    class Plotter:
        def set_background(self, colour, top=None):
            calls.append((colour, top))

    prefs = settings.current()
    prefs.set("interface.view_colour", "#102030")
    prefs.set("interface.view_colour_top", "#a0b0c0")
    theme.view_background(Plotter(), theme.LIGHT, "colour")
    theme.view_background(Plotter(), theme.LIGHT, "colour_gradient")
    assert calls == [("#102030", None), ("#102030", "#a0b0c0")]

    from puzzle.gui.window import MainWindow

    window = MainWindow()
    try:
        assert window.pick_view_background("#336699") == "#336699"
        assert prefs.get("interface.view_background") == "colour"
        assert settings.reload().get("interface.view_colour") == "#336699"  # kept on disk
        assert window.viewport.shown["background"] == "#336699"
    finally:
        window.close()
    dialog = SettingsDialog(settings.current(), agent_path=tmp_path / "agent.toml")
    button = dialog.boxes["interface.view_colour"]
    assert isinstance(button, ColourButton) and button.currentData() == "#336699"
    dialog.close()
