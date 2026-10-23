"""ADR 0032: extremes marked, values probed in the view, probes kept and read again.

On the cantilever's Tip load (CalculiX): the shown result marks its maximum and
minimum, and Properties gives both with their positions. Probing where the maximum is
reads the maximum (a probe at a node is that node's value). A kept probe is read again
for each run: doubling the tip load doubles it, since the model is linear. Agents and
the outline see kept probes too.
"""

import asyncio
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

from puzzle.commands import outline  # noqa: E402
from puzzle.commands.model import Edit, EditOp  # noqa: E402
from puzzle.gui.tree import ROLE  # noqa: E402
from puzzle.gui.window import MainWindow  # noqa: E402
from puzzle.model.schema import Component, ResultProbe, Study  # noqa: E402
from puzzle.project import load_project, save_project  # noqa: E402

pytestmark = [
    pytest.mark.usefixtures("engineering_units"),
    pytest.mark.skipif(not available("calculix"), reason="calculix not available"),
]
EXAMPLE = Path(__file__).parents[1] / "examples" / "cantilever"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def run(app, w):
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
    run(app, w)
    yield w
    w.close()


def rows(w):
    p = w.properties
    return {
        p.topLevelItem(i).text(0): p.topLevelItem(i).text(1) for i in range(p.topLevelItemCount())
    }


def test_extremes_probe_and_kept_probe(app, window):
    w = window
    bar = w.results_bar
    bar.field.setCurrentIndex(bar.field.findData("stress:von_mises"))
    shown = w.viewport.shown
    assert shown["marks"][0].startswith("Max ") and shown["marks"][1].startswith("Min ")
    r = rows(w)
    assert r["Maximum"] == f"{shown['max']:.4g} MPa" and r["Maximum at"].endswith(") mm")
    assert r["Study"] == "Tip load"

    # A probe where the maximum is reads the maximum (a node's own value).
    probe = w.viewport.probe_undeformed(shown["max_at"])
    # VTK samples in single precision: 1e-5 is that, not interpolation error.
    assert probe["shown"] == pytest.approx(shown["max"], rel=1e-5)
    assert probe["unit"] == "MPa" and probe["distance"] == 0
    assert w.probes_panel.table.rowCount() == 1
    w.viewport.probe_undeformed(shown["min_at"], add=True)
    assert len(w.viewport.probes) == 2 and len(shown["marks"]) == 4
    csv = w.probes_panel.csv().splitlines()
    assert csv[0] == "quantity,x (mm),y (mm),z (mm),value,unit" and len(csv) == 3
    w.viewport.clear_probes()
    assert not w.probes_panel.isVisibleTo(w)

    # Keep one, change the load, run again: the kept probe follows (linear: twice).
    probe = w.viewport.probe_undeformed(shown["max_at"])
    w.keep_probe(probe)
    (kept,) = w.session.project.of_type(ResultProbe)
    assert kept.quantity == "stress:von_mises" and np.allclose(kept.point, shown["max_at"])
    before = probe["value"]
    comp = w.session.project.of_type(Component)[0]
    force = comp.physics[0].features[1].values["y"].model_dump(mode="json")
    force["value"] *= 2
    op = EditOp(op="set", path="physics.0.features.1.values.y", value=force)
    w.submit(Edit(object=str(comp.id), ops=[op]))
    run(app, w)
    item = next(i for i in w.tree._items() if i.data(0, ROLE).kind == "probe")
    assert item.text(0) == "Probe 1 · Equivalent (von Mises) stress"
    w.tree.setCurrentItem(item)
    value = rows(w)["Value"]
    assert float(value.split()[0]) == pytest.approx(2 * before / 1e6, rel=1e-3)
    assert w.viewport.probes and w.viewport.probes[0]["value"] == pytest.approx(
        2 * before, rel=1e-3
    )

    # Agents read kept probes; the outline lists them.
    from puzzle.agent.mcp import build_server

    server = build_server(w.session.root, "test")
    result = asyncio.run(server.call_tool("probes", {}))
    (entry,) = result.structured_content["result"]
    assert entry["probe"] == "Probe 1" and entry["value"] == pytest.approx(2 * before, rel=1e-3)
    assert "Probe Probe 1: Equivalent (von Mises) stress of Tip load at (" in outline(
        w.session.project
    )
