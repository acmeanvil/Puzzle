"""Legend… (the result colour bar): a custom range per result, the look kept as
preferences, and one colour outside the range; checked on a solved bar."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("OCP")

from conftest_geometry import bar_project, write_bar_step  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from solvers import available  # noqa: E402

from puzzle import settings  # noqa: E402
from puzzle.gui.tree import ROLE  # noqa: E402
from puzzle.gui.window import MainWindow  # noqa: E402
from puzzle.model.schema import Feature, Physics, Quantity, SolveStep, Study  # noqa: E402
from puzzle.post.legend import Legend  # noqa: E402
from puzzle.project import save_project  # noqa: E402
from puzzle.results.store import ResultStore  # noqa: E402
from puzzle.solve import run_study  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_legend_bar_args_place_and_size():
    left = Legend(position="left").bar_args("MPa", "%.1f")
    right = Legend(position="right", size=1.5).bar_args("MPa", "%.1f")
    bottom = Legend(position="bottom", bands=9).bar_args("MPa", "%.1f")
    assert left["vertical"] and left["position_x"] < 0.1
    assert right["position_x"] > 0.7 and right["label_font_size"] == 18
    assert not bottom["vertical"] and bottom["position_y"] < 0.2 and bottom["n_labels"] == 5
    assert Legend(bands=9).bar_args("", "%g")["n_labels"] == 10  # a label per band edge
    colours = Legend(colormap="viridis", invert=True, bands=9, outside="grey").mesh_args(True)
    assert colours == {"cmap": "viridis_r", "n_colors": 9, "above_color": "#a6a6a6",
                       "below_color": "#a6a6a6"}  # fmt: skip
    assert "above_color" not in Legend(outside="grey").mesh_args(False)  # auto: no outside


@pytest.mark.skipif(not available("calculix"), reason="calculix not available")
def test_the_panel_sets_a_range_per_result_and_keeps_the_look(app, tmp_path):
    root = tmp_path / "bar"
    root.mkdir()
    force = Feature(type="force", selection="Bar/Tip", values={"y": Quantity(value=-100, unit="N")})
    held = Feature(type="fixed", selection="Bar/Root")
    project, comp = bar_project(
        write_bar_step(root / "bar.step"),
        [Physics(type="solid_mechanics", features=[held, force])],
        10.0,
    )
    steps = [SolveStep(type="stationary")]
    study = project.add(Study(name="Bend", component=comp.id, solver="calculix", steps=steps))
    save_project(project, root)
    run_study(project, study.id, root, tmp_path / "w", store=ResultStore(root / "results"))
    window = MainWindow(root)
    try:
        for item in window.tree._items():
            if item.data(0, ROLE).kind == "run":
                window.tree.setCurrentItem(item)
        app.processEvents()
        assert window.viewport.shown.get("kind") == "result"
        assert "legend" in window.results_ribbon.buttons
        window.edit_legend()
        panel = window.legend_panel
        assert window.side.currentWidget() is panel
        key = window.viewport.shown["key"]
        panel.fill()  # the result's own extremes, as a custom range
        low, high = window.viewport.ranges[key]
        assert window.viewport.shown["custom_range"] and high > low
        panel.bottom.setText(f"{low}")
        panel.top.setText(f"{(low + high) / 2}")
        panel.outside.setCurrentIndex(panel.outside.findData("grey"))
        panel.bands.setCurrentIndex(panel.bands.findData(9))
        panel.colormap.setCurrentIndex(panel.colormap.findData("viridis"))
        panel.invert.setChecked(True)
        panel.position.setCurrentIndex(panel.position.findData("right"))
        panel.apply()
        assert window.viewport.ranges[key] == pytest.approx((low, (low + high) / 2))
        assert window.viewport.shown["clim"] == pytest.approx((low, (low + high) / 2))
        saved = Legend.from_settings()
        assert (saved.colormap, saved.invert, saved.bands, saved.outside, saved.position) == (
            "viridis", True, 9, "grey", "right")  # fmt: skip
        panel.top.setText(f"{low - 1}")
        panel.apply()
        assert panel.message.text() == "The top must be above the bottom"
        # Another result keeps its own (automatic) range.
        bar = window.results_bar
        other = next(k for k in bar.items if k != key and k.startswith("stress"))
        bar.field.setCurrentIndex(bar.field.findData(other))
        app.processEvents()
        assert not window.viewport.shown["custom_range"]
        panel.mode.setCurrentIndex(panel.mode.findData("auto"))
        bar.field.setCurrentIndex(bar.field.findData(key))
        app.processEvents()
        panel.load()
        assert panel.mode.currentData() == "custom"  # its own range came back with it
    finally:
        for key in ("results.colormap", "results.invert_colors", "results.bands",
                    "results.outside_color", "results.legend_position"):  # fmt: skip
            settings.current().set(key, settings.BY_KEY[key].default)
        window.close()
