"""Local coordinate systems in the model and the GUI (ADR 0037); the solver benchmarks are
in tests/integration/test_local_axes.py.

Axes are made from the tree (one command), picked by name in a feature's Properties,
shown in the outline, drawn in the view along their directions, and part of the run
key. A solver that can't take a feature in given axes refuses it.
"""

import os
import shutil
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from puzzle.adapters.calculix import CalculixAdapter  # noqa: E402
from puzzle.adapters.common import globalize  # noqa: E402
from puzzle.adapters.elmer import ElmerAdapter  # noqa: E402
from puzzle.commands.outline import outline  # noqa: E402
from puzzle.gui import menus  # noqa: E402
from puzzle.gui.choices import choices  # noqa: E402
from puzzle.gui.window import MainWindow  # noqa: E402
from puzzle.model.schema import Component, CoordinateSystem, Feature, Quantity, Study  # noqa: E402
from puzzle.project.graph import run_hashes  # noqa: E402

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
    yield w
    w.close()


def test_cylindrical_frames():
    axes = CoordinateSystem(name="C", type="cylindrical", origin=(1.0, 0.0, 0.0))
    (f,) = axes.frames([[1.0, 2.0, 5.0]])
    assert np.allclose(f[:, 0], [0, 1, 0]) and np.allclose(f[:, 1], [-1, 0, 0])
    assert np.allclose(f[:, 2], [0, 0, 1])


def test_axes_made_picked_keyed_refused_and_drawn(window):
    menus.new_axes(window, "cartesian")
    project = window.session.project
    (axes,) = project.of_type(CoordinateSystem)
    assert window.session.history(limit=1)[0]["command"]["command"] == "create"

    comp = project.of_type(Component)[0]
    shown = choices(project, comp, "physics.0.features.1.axes")
    assert ("(none)", None) in shown and (axes.name, str(axes.id)) in shown

    tip = next(s for s in project.of_type(Study) if s.name == "Tip load")
    before = run_hashes(project)[tip.id]["calculix"]
    force = comp.physics[0].features[1]
    force.axes = axes.id
    axes.x_axis, axes.xy_direction = (0.0, 1.0, 0.0), (-1.0, 0.0, 0.0)  # turned 90° about z
    assert run_hashes(project)[tip.id]["calculix"] != before
    assert f"in axes {axes.name}" in outline(project)

    (moved,) = globalize(project, [force])
    local = np.array([force.values[a].to_si() if a in force.values else 0.0 for a in "xyz"])
    got = np.array([moved.values[a].to_si() for a in "xyz"])
    assert moved.axes is None and np.allclose(got, axes.rotation() @ local)

    cyl = project.add(CoordinateSystem(name="Polar", type="cylindrical"))
    comp.physics[0].features.append(
        Feature(
            type="displacement",
            selection=force.selection,
            values={"x": Quantity(value=1, unit="mm")},
            axes=cyl.id,
        )
    )
    assert not CalculixAdapter().check(project, tip)  # partial cylindrical: *TRANSFORM
    elmer = ElmerAdapter().check(project, tip)
    assert any("displacement in cylindrical (some components) axes" in p for p in elmer)


def test_symbols_follow_axes():
    from puzzle.post.symbols import _frames

    axes = CoordinateSystem(name="T", x_axis=(0.0, 1.0, 0.0), xy_direction=(-1.0, 0.0, 0.0))

    class P:
        objects = {axes.id: axes}

    f = Feature(type="displacement", selection="S", values={"x": Quantity(value=1, unit="mm")})
    f.axes = axes.id
    frames = _frames(P(), f, np.zeros((2, 3)))
    assert np.allclose(frames[:, :, 0], [[0, 1, 0]] * 2)


def test_feature_steps_typed_shown_and_refused(window):
    """A feature in some solve steps only (ADR 0037): typed in Properties as "2", shown
    in the outline, written by CalculiX, refused by a solver that can't."""
    from puzzle.gui.properties import parse_steps, steps_text

    assert parse_steps("All steps") == [] and parse_steps("3, 2") == [2, 3]
    assert steps_text([]) == "All steps" and steps_text([2]) == "2"
    with pytest.raises(ValueError):
        parse_steps("0")

    project = window.session.project
    comp = project.of_type(Component)[0]
    tip = next(s for s in project.of_type(Study) if s.name == "Tip load")
    comp.physics[0].features[1].steps = [2]
    assert "in steps 2" in outline(project)
    assert not CalculixAdapter().check(project, tip)
    assert any("in steps [2] only" in p for p in ElmerAdapter().check(project, tip))
