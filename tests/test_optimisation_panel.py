"""The Optimisation panel's robust design view (ADR 0063 phase 4) on the closed-form
stand-in: the summary in words, the surface and distribution plots offered and drawn.
No solver: the points come from the stand-in study."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402
from test_optimise import Store, stand_in  # noqa: E402
from test_optimisers import robust_beam  # noqa: E402

from puzzle import optimise  # noqa: E402
from puzzle.gui.optimisation_panel import OptimisationPanel  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_the_robust_summary_and_plots(app, tmp_path):
    p, opt = robust_beam()
    opt.constraints[0].probability = 0.99
    store = Store()
    optimise.run(p, opt, tmp_path, tmp_path / "w", store=store, solve=stand_in(store))
    window = SimpleNamespace(session=SimpleNamespace(project=p), _store=lambda: store)
    panel = OptimisationPanel(window)
    panel.show(str(opt.id))
    text = panel.summary.text()
    assert "Robust design: t = 6.2" in text and "holds 99." in text and "(wanted 99 %)" in text
    heads = [panel.table.horizontalHeaderItem(c).text() for c in range(panel.table.columnCount())]
    assert heads[-1] == "Chosen by"
    items = [panel.plot_of.itemText(i) for i in range(panel.plot_of.count())]
    assert "Distribution: Tip" in items and "Surface: Tip against t" in items
    panel.plot_of.setCurrentIndex(items.index("Distribution: Tip"))
    assert panel.curve.series[0][0] == "Probability density" and len(panel.curve.series[0][1]) > 10
    panel.plot_of.setCurrentIndex(items.index("Surface: Tip against t"))
    (_, line), (_, dots) = panel.curve.series
    assert len(line) == 60 and dots and 1 in panel.curve.dots
