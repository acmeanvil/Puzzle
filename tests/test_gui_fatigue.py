"""The fatigue tool in the GUI (ADR 0026), on the cantilever's Tip load (CalculiX).

The tip load, fully reversed and scaled so the root's von Mises stress is 1.2 × Se,
gives the root a finite life on Shigley's S-N line; its safety factor at the design life
of 10⁶ cycles is Se / σ (the line reaches Se at 10⁶ cycles). The steel has no tensile
strength at first, and the dialog says so.
"""

import os
import shutil
import time
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402
from solvers import available  # noqa: E402

from puzzle.commands.model import Edit, EditOp  # noqa: E402
from puzzle.gui.tree import ROLE  # noqa: E402
from puzzle.gui.window import MainWindow  # noqa: E402
from puzzle.materials import fatigue as fm  # noqa: E402
from puzzle.model.schema import Material, ResultDefinition, Study  # noqa: E402
from puzzle.project import load_project, save_project  # noqa: E402

pytestmark = [
    pytest.mark.usefixtures("engineering_units"),
    pytest.mark.skipif(not available("calculix"), reason="calculix not available"),
]

EXAMPLE = Path(__file__).parents[1] / "examples" / "cantilever"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path):
    root = tmp_path / "cantilever"
    shutil.copytree(
        EXAMPLE, root, ignore=shutil.ignore_patterns("results*", ".puzzle", "history.jsonl")
    )
    p = load_project(root)
    next(s for s in p.of_type(Study) if s.name == "Tip load").solver = "calculix"
    save_project(p, root)
    w = MainWindow(root)
    for item in w.tree._items():
        node = item.data(0, ROLE)
        if node.kind == "object" and node.label == "Tip load":
            w.tree.setCurrentItem(item)
    w.run_selected()
    end = time.monotonic() + 600
    while w.tasks:
        app.processEvents()
        assert time.monotonic() < end
        time.sleep(0.02)
    yield w
    w.close()


def test_fatigue_tool_on_the_cantilever(window):
    store = window._store()
    (record,) = store.runs()
    from puzzle.post.fatigue_tool import measure

    stress = store.read_results(record.run_id).last("stress").values
    vm_max = float(measure(np.asarray(stress, float), "signed_von_mises", signed=False).max())
    sut = 520e6
    se = fm.endurance_limit(sut, surface="machined").se
    scale = 1.2 * se / vm_max
    study = next(s for s in window.session.project.of_type(Study) if s.name == "Tip load")
    seen = {}

    def fill(dialog):
        dialog.table.cellWidget(0, 3).setText(f"{scale:.12g}")
        seen["before"] = dialog.status.text()
        return False  # cancelled: the steel lacks its tensile strength

    window.run_dialog = fill
    window.new_derived("fatigue", str(study.id))
    assert "needs its tensile strength" in seen["before"]

    steel = window.session.project.of_type(Material)[0]
    sut_q = {"value": 520, "unit": "MPa"}
    window.submit(
        Edit(
            object=str(steel.id),
            ops=[EditOp(op="set", path="properties.tensile_strength", value=sut_q)],
        )
    )

    def ok(dialog):
        assert dialog.table.cellWidget(0, 1).currentText() == "Tip load"
        dialog.table.cellWidget(0, 3).setText(f"{scale:.12g}")
        dialog.diameter.setText("10")  # mm: shown only for round sizes
        assert dialog.diameter.isHidden()
        seen["after"] = dialog.status.text()
        return True

    window.run_dialog = ok
    window.new_derived("fatigue", str(study.id))
    assert seen["after"].startswith("Shortest life ")
    (tool,) = window.session.project.of_type(ResultDefinition)
    assert tool.method == "fatigue" and tool.fatigue.blocks[0].scale == pytest.approx(scale)

    bar = window.results_bar
    assert bar.field.currentText() == "Life (cycles)"
    life = window.viewport.shown["min"]
    assert life == pytest.approx(float(fm.SnLine(se, sut).cycles(1.2 * se)), rel=1e-6)
    bar.field.setCurrentIndex(bar.field.findData("fatigue_safety_factor:value"))
    assert window.viewport.shown["min"] == pytest.approx(1 / 1.2, rel=1e-4)
    bar.field.setCurrentIndex(bar.field.findData("fatigue_alternating:value"))
    assert window.viewport.shown["unit"] == "MPa"
    assert window.viewport.shown["max"] == pytest.approx(1.2 * se / 1e6, rel=1e-9)
