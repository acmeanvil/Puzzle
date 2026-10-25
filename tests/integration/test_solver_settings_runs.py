"""Solver settings change how a solver works, not the answer (ADR 0023): the cantilever's
tip deflection is the same with each linear solver, and the pass-through reaches the
solver (Code_Aster's own log reports the keyword it was given)."""

import pytest
from conftest_geometry import bar_project, write_bar_step
from solvers import over

from puzzle.model.schema import Feature, Physics, Quantity, SolveStep, Study
from puzzle.solve import run_study

CASES = {
    "calculix": [{}, {"solver": "SPOOLES"}, {"solver": "ITERATIVE CHOLESKY"}],
    "code_aster": [{}, {"method": "MULT_FRONT"}, {"method": "GCPC"}],
    "elmer": [{}, {"linear_method": "direct"}, {"linear_method": "CG", "preconditioner": "ILU1"}],
    "fenicsx": [{}, {"linear_method": "lu"}, {"linear_method": "cg_gamg"}],
}


def _tip(tmp_path, solver, own, expert=None, shared=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    force = Feature(type="force", selection="Bar/Tip", values={"y": Quantity(value=-100, unit="N")})
    physics = Physics(
        type="solid_mechanics",
        features=[Feature(type="fixed", selection="Bar/Root"), force],
        settings={"temperature_dependent_materials": False},
    )
    project, comp = bar_project(write_bar_step(tmp_path / "bar.step"), [physics], 5.0)
    step = SolveStep(type="stationary", settings=shared or {})
    study = project.add(Study(name="S", component=comp.id, solver=solver, steps=[step],
                              solver_settings={solver: own},
                              expert_settings={solver: expert or {}}))  # fmt: skip
    run = run_study(project, study.id, tmp_path, tmp_path / "w", timeout_s=600)
    tip = run.mesh.groups["Bar/Tip"].node_ids
    return run.results.last("displacement").at(tip)[:, 1].mean(), tmp_path / "w"


@over("calculix", "code_aster", "elmer", "fenicsx")
def test_every_linear_solver_gives_the_same_answer(tmp_path, solver):
    pytest.importorskip("OCP")
    tips = [_tip(tmp_path / str(i), solver, own)[0] for i, own in enumerate(CASES[solver])]
    print(f"{solver}: tip deflection {', '.join(f'{t:.8g}' for t in tips)}")
    assert tips[1] == pytest.approx(tips[0], rel=1e-8)
    assert tips[2] == pytest.approx(tips[0], rel=1e-5)  # iterative: to its tolerance


@over("code_aster")
def test_code_aster_pass_through_reaches_the_solver(tmp_path, solver):
    pytest.importorskip("OCP")
    tip, work = _tip(tmp_path, solver, {}, {"MECA_STATIQUE.SOLVEUR.RENUM": "'METIS'"},
                     {"linear_solver": "direct"})  # fmt: skip
    comm = next(work.rglob("job.comm")).read_text()
    assert "SOLVEUR=_F(RENUM='METIS')" in comm and tip < 0


@over("elmer")
def test_elmer_pass_through_and_shared_settings(tmp_path, solver):
    pytest.importorskip("OCP")
    tip, work = _tip(tmp_path, solver, {}, {"Solver 1/Linear System Residual Output": "10",
                                            "Simulation/Max Output Level": "3"},
                     {"linear_solver": "iterative"})  # fmt: skip
    sif = next(work.rglob("case.sif")).read_text()
    assert "Linear System Iterative Method = BiCGStabL" in sif
    assert "Max Output Level = 3" in sif and "Max Output Level = 5" not in sif
    assert "Linear System Residual Output = 10" in sif and tip < 0


@over("fenicsx")
def test_fenicsx_petsc_options_reach_the_solve(tmp_path, solver):
    import json

    pytest.importorskip("OCP")
    expert = {"ksp_type": "gmres", "pc_type": "gamg", "ksp_rtol": "1e-11"}
    tip, work = _tip(tmp_path, solver, {}, expert)
    case = json.loads(next(work.rglob("case.json")).read_text())
    assert case["settings"]["petsc_options"] == expert and tip < 0


CFD = {
    "code_saturne": (
        {"pressure_solver": "conjugate_gradient", "velocity_scheme": "solu"},
        {"time_parameters/iterations": "400"},
        "setup.xml",
        ['<solver_choice choice="conjugate_gradient"', "<iterations>400</iterations>"],
    ),
    "openfoam": (
        {"p_solver": "PCG", "relaxation_U": 0.8},
        {"system/fvSolution/solvers/p/maxIter": "1000"},
        "fvSolution",
        ["PCG", "maxIter 1000", "U 0.8"],
    ),
}


@over("code_saturne", "openfoam")
def test_cfd_settings_keep_poiseuille(tmp_path, solver):
    """The laminar channel with other linear solvers and a pass-through entry: the peak
    velocity stays 1.5 times the mean (Poiseuille), and the settings reach the case."""
    import importlib.util
    from pathlib import Path

    from puzzle.project import load_project

    bench = Path(__file__).parents[2] / "benchmarks" / "channel_flow"
    spec = importlib.util.spec_from_file_location("channel_build", bench / "build.py")
    channel = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(channel)
    project = load_project(bench)
    study = next(s for s in project.of_type(Study) if s.name == "Channel flow")
    own, expert, name, expected = CFD[solver]
    study.solver, study.shootout = solver, None
    study.solver_settings = {solver: own}
    study.expert_settings = {solver: expert}
    run = run_study(project, study.id, bench, tmp_path / "run", timeout_s=1800)
    written = next((tmp_path / "run").rglob(name)).read_text()
    for text in expected:
        assert text in " ".join(written.split()), text
    u = run.results.last("velocity").values[:, 0]
    print(f"{solver}: peak velocity {u.max() / channel.U:.4f} of the mean (Poiseuille 1.5)")
    assert u.max() / channel.U == pytest.approx(1.5, rel=0.01)
