"""Amplitudes and body loads in the model and the GUI (ADR 0037); their solver
benchmarks are in tests/integration/test_body_loads.py.

An amplitude is made in the table editor (one command) and picked by name in a
feature's Properties; a study whose steps aren't transient refuses it, as does a
feature type no adapter varies yet. The amplitude is a dependency of the component, so
changing it changes the run key. Body loads show as one arrow.
"""

import os
import shutil
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from puzzle.adapters.calculix import CalculixAdapter  # noqa: E402
from puzzle.gui import menus  # noqa: E402
from puzzle.gui.choices import choices  # noqa: E402
from puzzle.gui.window import MainWindow  # noqa: E402
from puzzle.model.schema import Amplitude, Component, Feature, Quantity, Study  # noqa: E402
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


def test_amplitude_made_picked_checked_and_keyed(window):
    def table(dialog):
        dialog.result_rows = [(0.0, 0.0), (5.0, 1.0)]
        return True

    window.run_dialog = table
    menus.edit_amplitude(window)
    project = window.session.project
    (amp,) = project.of_type(Amplitude)
    assert amp.points == [(0.0, 0.0), (5.0, 1.0)] and amp.at(2.5) == 0.5 and amp.at(9) == 1.0
    assert window.session.history(limit=1)[0]["command"]["command"] == "create"

    comp = project.of_type(Component)[0]
    heat = next(i for i, p in enumerate(comp.physics) if p.type == "heat_transfer")
    shown = choices(project, comp, f"physics.{heat}.features.0.amplitude")
    assert ("(none)", None) in shown and (amp.name, str(amp.id)) in shown

    tip = next(s for s in project.of_type(Study) if s.name == "Conduction")
    comp.physics[heat].features.append(
        Feature(
            type="heat_flux",
            selection="Bar/Tip",
            values={"q": Quantity(value=1, unit="W/m^2")},
            amplitude=amp.id,
        )
    )
    before = run_hashes(project)[tip.id]["calculix"]
    problems = CalculixAdapter().check(project, tip)
    assert any("applies in transient steps" in p for p in problems)  # a steady study
    amp.points = [(0.0, 0.0), (5.0, 2.0)]
    assert run_hashes(project)[tip.id]["calculix"] != before  # the table is in the key

    # Forces follow amplitudes since ADR 0041; gravity doesn't yet.
    comp.physics[0].features.append(
        Feature(
            type="gravity",
            selection="Bar/Body",
            values={"gz": Quantity(value=-9.81, unit="m/s^2")},
            amplitude=amp.id,
        )
    )
    problems = CalculixAdapter().check(project, tip)
    assert any("does not vary gravity with an amplitude" in p for p in problems)


def test_body_load_symbol(window):
    import numpy as np

    from puzzle.post.symbols import symbols

    project = window.session.project
    comp = project.of_type(Component)[0]
    comp.physics[0].features.append(
        Feature(
            type="gravity", selection="Bar/Body", values={"gz": Quantity(value=-9.81, unit="m/s^2")}
        )
    )
    from puzzle.mesh import Mesh

    mesh = Mesh(np.array([1, 2]), np.array([[0.0, 0, 0], [0.2, 0.01, 0.01]]))
    (g,) = [s for s in symbols(comp, mesh, lambda _s: []) if s.feature.startswith("Gravity")]
    assert g.kind == "arrow" and np.allclose(g.directions[0], [0, 0, -1])
