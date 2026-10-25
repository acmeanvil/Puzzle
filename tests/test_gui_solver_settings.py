"""Solver settings… in the GUI (ADR 0023): one form, shared settings written on every
step, each solver's own in its block, the expert pass-through at the power-user level
only and checked before OK; one undo takes it all back."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("OCP")

from conftest_geometry import bar_project, write_bar_step  # noqa: E402
from forms import fill  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from puzzle import settings  # noqa: E402
from puzzle.gui.solver_settings import edit_solver_settings, parse_expert  # noqa: E402
from puzzle.gui.window import MainWindow  # noqa: E402
from puzzle.model.schema import Feature, Physics, Shootout, SolveStep, Study  # noqa: E402
from puzzle.project import save_project  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _window(tmp_path):
    root = tmp_path / "bar"
    root.mkdir()
    physics = Physics(
        type="solid_mechanics", features=[Feature(type="fixed", selection="Bar/Root")]
    )
    project, comp = bar_project(write_bar_step(root / "bar.step"), [physics], 10.0)
    steps = [SolveStep(type="stationary")]
    project.add(Study(name="S", component=comp.id, solver="calculix", steps=steps,
                      shootout=Shootout(solvers=["code_aster"])))  # fmt: skip
    save_project(project, root)
    return MainWindow(root)


def _study(window):
    return window.session.project.of_type(Study)[0]


def test_standard_level(app, tmp_path):
    window = _window(tmp_path)
    try:
        fill(window, **{"shared:linear_solver": "iterative", "shared:tolerance": "1e-8",
                        "calculix:solver": "SPOOLES"})  # fmt: skip
        edit_solver_settings(window, _study(window))
        form = window.last_form
        assert "expert:calculix" not in form.widgets  # power users only
        assert "code_aster:reac_iter" not in form.widgets  # a power setting
        study = _study(window)
        assert study.steps[0].settings["linear_solver"] == "iterative"
        assert study.steps[0].settings["tolerance"] == pytest.approx(1e-8)
        assert study.solver_settings == {"calculix": {"solver": "SPOOLES"}}
        fill(window, **{"shared:tolerance": "-1"})
        assert edit_solver_settings(window, _study(window)) is None
        assert "above zero" in window.last_form.problem()
        window.undo()
        assert _study(window).solver_settings == {} and not _study(window).steps[0].settings
    finally:
        window.close()


def test_power_level_and_pass_through(app, tmp_path):
    current = settings.current()
    current.set("solvers.level", "power")
    window = _window(tmp_path)
    try:
        cards = "[after_procedure]\n*CONTROLS, PARAMETERS=FIELD\n0.001, 0.002"
        fill(window, **{"expert:calculix": cards,
                        "expert:code_aster": "STAT_NON_LINE.NEWTON.REAC_ITER = 2",
                        "code_aster:reac_iter": "3"})  # fmt: skip
        edit_solver_settings(window, _study(window))
        study = _study(window)
        assert study.expert_settings["calculix"] == {
            "after_procedure": "*CONTROLS, PARAMETERS=FIELD\n0.001, 0.002"
        }
        assert study.expert_settings["code_aster"] == {"STAT_NON_LINE.NEWTON.REAC_ITER": "2"}
        assert study.solver_settings["code_aster"] == {"reac_iter": 3}
        fill(window, **{"expert:calculix": "[after_procedure]\n*END STEP"})
        assert edit_solver_settings(window, _study(window)) is None
        assert "Puzzle's to write" in window.last_form.problem()
    finally:
        current.set("solvers.level", "standard")
        window.close()


def test_pass_through_text():
    assert parse_expert("A.B = 1\nC.D.E = 'X'") == {"A.B": "1", "C.D.E": "'X'"}
    assert parse_expert("[model]\n*A\n[after_procedure:2]\n*B\n1, 2") == {
        "model": "*A",
        "after_procedure:2": "*B\n1, 2",
    }


def test_presets_save_and_apply(app, tmp_path):
    from puzzle.commands.outline import outline
    from puzzle.gui.solver_settings import apply_preset, save_preset
    from puzzle.model.schema import SettingsPreset

    window = _window(tmp_path)
    try:
        fill(window, **{"shared:tolerance": "1e-9", "calculix:solver": "SPOOLES"})
        edit_solver_settings(window, _study(window))
        window.ask_text = lambda *a, **k: "Tight"
        save_preset(window, _study(window))
        (preset,) = window.session.project.of_type(SettingsPreset)
        assert preset.shared == {"tolerance": 1e-9}
        assert preset.solver_settings == {"calculix": {"solver": "SPOOLES"}}
        assert "Preset Tight: tolerance=1e-09; calculix: solver=SPOOLES" in outline(
            window.session.project
        )
        # A second study, bare, takes the preset whole.
        study = _study(window)
        from puzzle.commands.model import Create

        window.submit(Create(kind="study", data={"name": "T", "component": str(study.component),
                                                  "solver": "calculix",
                                                  "steps": [{"type": "stationary"}]}))  # fmt: skip
        bare = next(s for s in window.session.project.of_type(Study) if s.name == "T")
        apply_preset(window, bare, preset)
        bare = next(s for s in window.session.project.of_type(Study) if s.name == "T")
        assert bare.steps[0].settings == {"tolerance": 1e-9}
        assert bare.solver_settings == {"calculix": {"solver": "SPOOLES"}}
        assert "calculix settings: solver=SPOOLES" in outline(window.session.project)
    finally:
        window.close()
