"""ADR 0035: supports and loads drawn as symbols, from the model.

On the cantilever: the component shows every feature's symbols (cones on the fixed
root, one arrow for the tip force, the temperature faces tinted); a selected feature
shows only its own; the force arrow points along the force (−y), and pressure arrows
point into the face; the view toolbar hides them.
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

from puzzle.gui.tree import ROLE  # noqa: E402
from puzzle.gui.window import MainWindow  # noqa: E402
from puzzle.model.schema import Component, Feature, Quantity  # noqa: E402
from puzzle.post.symbols import symbols  # noqa: E402

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
    w = MainWindow(root)
    select(w, "object", "Cantilever")
    w.mesh_selected()
    end = time.monotonic() + 300
    while w.tasks:
        app.processEvents()
        assert time.monotonic() < end
        time.sleep(0.02)
    yield w
    w.close()


def select(w, kind, label):
    for item in w.tree._items():
        node = item.data(0, ROLE)
        if node.kind == kind and node.label == label:
            w.tree.setCurrentItem(item)
            return


def test_component_feature_and_toggle(window):
    w = window
    select(w, "object", "Cantilever")
    shown = {name: (kind, n) for name, kind, n in w.viewport.shown["symbols"]}
    assert shown["Fixed on Bar/Root"][0] == "cone" and shown["Fixed on Bar/Root"][1] > 0
    assert shown["Force on Bar/Tip"] == ("arrow", 1)
    assert shown["Temperature on Bar/Root"][0] == "tint"

    select(w, "feature", "Bar/Tip")  # the first feature on Bar/Tip: the force
    names = [name for name, _, _ in w.viewport.shown["symbols"]]
    assert len(names) == 1 and names[0].endswith("on Bar/Tip")

    w.viewport.view_bar.actions_by_name["symbols"].trigger()
    assert w.viewport.shown["symbols"] == []


def test_directions(window):
    w = window
    comp = w.session.project.of_type(Component)[0]
    mesh = w.meshes[str(comp.id)]
    comp.physics[0].features.append(
        Feature(type="pressure", selection="Bar/Tip", values={"p": Quantity(value=1, unit="MPa")})
    )
    glyphs = {g.feature: g for g in symbols(comp, mesh, lambda s: w._entities_of(comp, s))}
    force = glyphs["Force on Bar/Tip"]
    assert np.allclose(force.directions[0], [0, -1, 0])
    tip = glyphs["Pressure on Bar/Tip"]
    centre = mesh.coords.mean(axis=0)
    # Into the face: from the tip end towards the bar's middle.
    assert np.all(np.einsum("ij,ij->i", tip.directions, centre - tip.points) > 0)
