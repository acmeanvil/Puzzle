"""Solver settings (ADR 0023): the typed schema, run keys per solver, and Code_Aster's
keyword overrides placed in the command file (or refused, never dropped)."""

import pytest

from puzzle.adapters.code_aster.expert import apply
from puzzle.errors import PuzzleError

COMM = (
    "res = MECA_STATIQUE(MODELE=model, CHAM_MATER=mater, EXCIT=(_F(CHARGE=load),))\n"
    "res2 = STAT_NON_LINE(MODELE=model, EXCIT=(_F(CHARGE=load, FONC_MULT=ramp),), "
    "NEWTON=_F(REAC_ITER=1), CONVERGENCE=_F(ITER_GLOB_MAXI=200, RESI_GLOB_RELA=1e-6), "
    "INCREMENT=_F(LIST_INST=DEFI_LIST_REEL(DEBUT=0.0, INTERVALLE=_F(JUSQU_A=1.0, NOMBRE=10))))\n"
)


def test_overrides_are_placed():
    out = apply(COMM, {
        "MECA_STATIQUE.SOLVEUR.METHODE": "'MULT_FRONT'",
        "STAT_NON_LINE.CONVERGENCE.RESI_GLOB_RELA": "1e-8",
        "STAT_NON_LINE.NEWTON.REAC_ITER": "2",
        "STAT_NON_LINE.INFO": "2",
    })  # fmt: skip
    assert "EXCIT=(_F(CHARGE=load),), SOLVEUR=_F(METHODE='MULT_FRONT'))" in out
    assert "RESI_GLOB_RELA=1e-8)" in out and "ITER_GLOB_MAXI=200" in out
    assert "NEWTON=_F(REAC_ITER=2)" in out and out.count("INFO=2") == 1
    # The list of instants inside INCREMENT is untouched.
    assert "INTERVALLE=_F(JUSQU_A=1.0, NOMBRE=10)" in out
    # Inside a tuple of factors, every one.
    two = apply("x = AFFE_CHAR_MECA(DDL_IMPO=(_F(GROUP_MA='A', DX=0), _F(GROUP_MA='B', DX=0)))",
                {"AFFE_CHAR_MECA.DDL_IMPO.DY": "0"})  # fmt: skip
    assert two.count("DY=0") == 2


@pytest.mark.parametrize(
    "path, value, why",
    [
        ("meca_statique.SOLVEUR", "1", "capitals"),
        ("MECA_STATIQUE.SOLVEUR.METHODE", "MUMPS", "not a Code_Aster value"),
        ("DYNA_VIBRA.SOLVEUR.METHODE", "'MUMPS'", "has no DYNA_VIBRA"),
    ],
)
def test_bad_overrides_are_refused(path, value, why):
    with pytest.raises(PuzzleError, match=why):
        apply(COMM, {path: value})


def _project(tmp_path):
    from conftest_geometry import bar_project, write_bar_step

    from puzzle.model.schema import Feature, Physics, Quantity, SolveStep, Study

    force = Feature(type="force", selection="Bar/Tip", values={"y": Quantity(value=-100, unit="N")})
    physics = Physics(
        type="solid_mechanics", features=[Feature(type="fixed", selection="Bar/Root"), force]
    )
    project, comp = bar_project(write_bar_step(tmp_path / "bar.step"), [physics], 10.0)
    study = project.add(
        Study(name="S", component=comp.id, solver="calculix", steps=[SolveStep(type="stationary")])
    )
    return project, study


def test_run_keys_follow_each_solvers_own_settings(tmp_path):
    pytest.importorskip("OCP")
    from puzzle.model.schema import Shootout
    from puzzle.project.graph import run_hashes

    project, study = _project(tmp_path)
    study.shootout = Shootout(solvers=["code_aster"])
    before = run_hashes(project)[study.id]
    study.solver_settings = {"calculix": {"solver": "SPOOLES"}}
    after = run_hashes(project)[study.id]
    assert after["calculix"] != before["calculix"]
    assert after["code_aster"] == before["code_aster"]  # its runs stay current


def test_settings_reach_the_calculix_deck(tmp_path):
    pytest.importorskip("OCP")
    from puzzle.adapters.calculix import CalculixAdapter
    from puzzle.adapters.calculix.inp import write_inp
    from puzzle.mesh import generate_mesh

    project, study = _project(tmp_path)
    mesh = generate_mesh(project, study.component, tmp_path, tmp_path / "m").mesh
    plain = write_inp(project, study, mesh)
    assert "SOLVER=" not in plain and "FREQUENCY=" not in plain  # defaults: as before
    study.steps[0].settings.update({"linear_solver": "iterative", "output_every": 2,
                                    "max_iterations": 30})  # fmt: skip
    study.expert_settings = {
        "calculix": {"after_procedure": "*CONTROLS, PARAMETERS=FIELD\n0.001, 0.002"}
    }
    deck = write_inp(project, study, mesh)
    assert "*STATIC, SOLVER=ITERATIVE CHOLESKY" in deck
    assert "*NODE FILE, FREQUENCY=2" in deck
    assert "4, 8, 9, 30, 10, 4, 0, 5, 0, 0" in deck
    assert "*CONTROLS, PARAMETERS=FIELD\n0.001, 0.002" in deck
    study.solver_settings = {"calculix": {"solver": "SPOOLES", "nope": 1}}
    problems = CalculixAdapter().check(project, study)
    assert any("unknown solver settings: nope" in p for p in problems)
    study.solver_settings = {}
    study.expert_settings = {"calculix": {"after_procedure": "*END STEP", "where": "*X"}}
    problems = CalculixAdapter().check(project, study)
    assert any("Puzzle's to write" in p for p in problems)
    assert any("'where' is not a place for cards" in p for p in problems)


def test_a_shootout_lists_each_solvers_settings():
    from puzzle.model.schema import SolveStep, Study
    from puzzle.shootout import ShootoutRun, settings_in_use

    study = Study(
        name="S",
        component="00000000-0000-0000-0000-000000000000",
        solver="calculix",
        steps=[SolveStep(type="stationary", settings={"tolerance": 1e-8})],
        solver_settings={"calculix": {"solver": "SPOOLES"}},
        expert_settings={"code_aster": {"MECA_STATIQUE.INFO": "2"}},
    )
    assert settings_in_use(study, "calculix") == ["tolerance = 1e-08", "solver = SPOOLES"]
    assert settings_in_use(study, "code_aster") == [
        "tolerance = 1e-08",
        "expert MECA_STATIQUE.INFO",
    ]
    run = ShootoutRun("calculix", {}, settings={"calculix": ["solver = SPOOLES"], "code_aster": []})
    assert (
        "| calculix | solver = SPOOLES |" in run.table()
        and "| code_aster | defaults |" in run.table()
    )


def test_agents_read_the_settings(tmp_path):
    pytest.importorskip("OCP")
    import asyncio

    from puzzle.agent.mcp import build_server
    from puzzle.project import save_project

    project, study = _project(tmp_path)
    study.solver_settings = {"calculix": {"solver": "SPOOLES"}}
    root = tmp_path / "p"
    save_project(project, root)
    (root / "bar.step").write_bytes((tmp_path / "bar.step").read_bytes())
    result = asyncio.run(build_server(root, "test").call_tool("solver_settings", {"study": "S"}))
    data = result.structured_content
    assert [r["key"] for r in data["shared"]][:2] == ["linear_solver", "tolerance"]
    solver = data["solvers"]["calculix"]["settings"][0]
    assert solver["native"] == "SOLVER=" and solver["current"] == "SPOOLES"
    assert "after_procedure" in data["solvers"]["calculix"]["expert_help"]


def test_shared_settings_a_solver_cant_translate_are_refused(tmp_path):
    pytest.importorskip("OCP")
    from puzzle.adapters.code_saturne import CodeSaturneAdapter
    from puzzle.adapters.openfoam import OpenFoamAdapter

    project, study = _project(tmp_path)
    study.steps[0].settings.update({"linear_solver": "direct", "output_every": 5})
    saturne = CodeSaturneAdapter().check(project, study)
    assert "code_saturne does not translate the setting linear_solver = direct" in saturne
    assert "code_saturne does not translate the setting output_every" in saturne
    study.steps[0].settings = {"linear_solver": "iterative", "tolerance": 1e-7}
    foam = [p for p in OpenFoamAdapter().check(project, study) if "translate" in p]
    assert foam == []  # both are OpenFOAM's to take
    study.expert_settings = {"openfoam": {"fvSolution/p": "1"}}
    assert any("not a dictionary entry" in p for p in OpenFoamAdapter().check(project, study))


def test_mumps_options_are_placed_and_checked():
    """ADR 0053: MUMPS's acceleration, low-rank tolerance, ordering and memory go on
    every SOLVEUR; only with MUMPS, and a tolerance only with low rank."""
    from puzzle.adapters.code_aster.comm import with_settings
    from puzzle.model.schema import SolveStep, Study

    step = SolveStep(type="stationary")

    def study(**own):
        nobody = "00000000-0000-0000-0000-000000000000"
        return Study(name="S", component=nobody, solver="code_aster", steps=[step],
                     solver_settings={"code_aster": own})  # fmt: skip

    out = with_settings(COMM, study(acceleration="LR", low_rank_threshold=1e-9, ordering="METIS",
                                    factor_memory="IN_CORE"), step)  # fmt: skip
    assert out.count("ACCELERATION='LR'") == 2 and out.count("LOW_RANK_SEUIL=1e-09") == 2
    assert "RENUM='METIS'" in out and "GESTION_MEMOIRE='IN_CORE'" in out
    # Nothing set: only MUMPS's iterative refinement, which Puzzle turns on (REFINEMENTS:
    # a large model failed FACTOR_57 without it), on every solve that takes a SOLVEUR.
    plain = with_settings(COMM, study(), step)
    assert plain.count("POSTTRAITEMENTS='FORCE'") == 2
    assert plain.replace(", SOLVEUR=_F(POSTTRAITEMENTS='FORCE')", "") == COMM
    assert "POSTTRAITEMENTS='AUTO'" in with_settings(COMM, study(refinement="AUTO"), step)
    assert "POSTTRAITEMENTS" not in with_settings(COMM, study(method="MULT_FRONT"), step)
    with pytest.raises(PuzzleError, match="MUMPS options"):
        with_settings(COMM, study(method="MULT_FRONT", acceleration="FR+"), step)
    with pytest.raises(PuzzleError, match="needs a low-rank"):
        with_settings(COMM, study(acceleration="FR", low_rank_threshold=1e-9), step)


def test_code_aster_export_takes_the_cores_and_memory(monkeypatch):
    """ADR 0053: run_aster's ncpus (its threads) and memory per process from Settings >
    Solvers and the runtime, and no one-hour limit."""
    from puzzle import settings
    from puzzle.adapters.code_aster import comm
    from puzzle.jobs import runtime

    settings.current().set("solvers.threads", 2)
    monkeypatch.setattr(runtime.NativeRuntime, "memory", lambda self: 8 * 2**30)
    monkeypatch.setattr(runtime.ContainerRuntime, "memory", lambda self: 8 * 2**30)
    lines = comm.resources()
    assert "P ncpus 2" in lines and "P mpi_nbcpu 1" in lines
    assert "P memory_limit 6144" in lines and "P time_limit 604800" in lines
    assert "P memory_limit 3072" in comm.resources(processes=2)


def test_code_aster_low_rank_runs_on_one_thread():
    """ADR 0053: MUMPS's low-rank factoring hangs with several OpenMP threads in this
    Code_Aster build, so a .comm asking for it (setting or expert line) gets ncpus 1."""
    from puzzle import settings
    from puzzle.adapters.code_aster import comm
    from puzzle.machine import logical_cores

    if logical_cores() < 2:
        pytest.skip("needs 2 logical cores")
    settings.current().set("solvers.threads", 2)  # at most the logical cores (CI has 3)
    assert comm._low_rank("SOLVEUR=_F(ACCELERATION='LR+', LOW_RANK_SEUIL=1e-09)")
    assert not comm._low_rank("SOLVEUR=_F(ACCELERATION='FR+')")
    assert "P ncpus 1" in comm.resources(low_rank=True)
    assert "P ncpus 2" in comm.resources()


def test_load_stepping_and_cutbacks_reach_the_calculix_deck(tmp_path):
    """Convergence controls (ADR 0023): the step's load stepping on *STATIC's data line,
    even for a linear step, and CalculiX's cut-backs in TIME INCREMENTATION."""
    pytest.importorskip("OCP")
    from puzzle.adapters.calculix.inp import write_inp
    from puzzle.mesh import generate_mesh

    project, study = _project(tmp_path)
    mesh = generate_mesh(project, study.component, tmp_path, tmp_path / "m").mesh
    assert "TIME INCREMENTATION" not in write_inp(project, study, mesh)
    study.steps[0].settings.update({"increments": 20, "initial_increment": 0.02,
                                    "min_increment": 1e-6, "fixed_increments": True})  # fmt: skip
    study.solver_settings = {"calculix": {"cutbacks": 8, "cutback_factor": 0.5,
                                          "divergence_check_after": 6}}  # fmt: skip
    deck = write_inp(project, study, mesh)
    assert "*STATIC, DIRECT\n0.02, 1.0, 1e-06, 0.05\n" in deck
    assert "6, 8, 9, 16, 10, 4, 0, 8, 0, 0\n0.5, 0.5, 0.75, 0.85, 0, 0, 1.5, 0" in deck


def _nonlinear_comm():
    return (
        "times = DEFI_LIST_INST(DEFI_LIST=_F(LIST_INST=DEFI_LIST_REEL(DEBUT=0.0, "
        "INTERVALLE=_F(JUSQU_A=1.0, NOMBRE=1))), ECHEC=_F(EVENEMENT='ERREUR', "
        "ACTION='DECOUPE', SUBD_METHODE='MANUEL', SUBD_PAS=4, SUBD_NIVEAU=5))\n"
        "res = STAT_NON_LINE(MODELE=model, NEWTON=_F(REAC_ITER=1), "
        "CONVERGENCE=_F(ITER_GLOB_MAXI=200), INCREMENT=_F(LIST_INST=times))\n"
    )


def test_convergence_settings_reach_the_code_aster_command_file():
    from puzzle.adapters.code_aster.comm import _failure, with_settings
    from puzzle.model.schema import SolveStep, Study

    study = Study(name="S", component="00000000-0000-0000-0000-000000000000",
                  solver="code_aster", steps=[SolveStep(type="stationary")])  # fmt: skip
    step = study.steps[0]
    assert "RECH_LINEAIRE" not in with_settings(_nonlinear_comm(), study, step)  # as before
    study.solver_settings = {"code_aster": {
        "line_search": "MIXTE", "line_search_iterations": 5, "prediction": "ELASTIQUE",
        "matrix": "TANGENTE", "cutback_parts": 10, "cutback_levels": 3,
        "absolute_tolerance": 1e-4}}  # fmt: skip
    out = with_settings(_nonlinear_comm(), study, step)
    assert "RECH_LINEAIRE=_F(METHODE='MIXTE', ITER_LINE_MAXI=5)" in out
    assert "PREDICTION='ELASTIQUE'" in out and "MATRICE='TANGENTE'" in out
    assert "SUBD_PAS=10, SUBD_NIVEAU=3" in out and "RESI_GLOB_MAXI=0.0001" in out
    assert "SUBD_PAS_MINI" not in _failure(step)
    step.settings["min_increment"] = 1e-5
    assert "SUBD_PAS_MINI=1e-05" in _failure(step)
    step.settings["fixed_increments"] = True
    assert _failure(step) == "ECHEC=_F(EVENEMENT='ERREUR', ACTION='ARRET')"
    with pytest.raises(PuzzleError, match="never cut back"):
        with_settings(_nonlinear_comm(), study, step)
    study.solver_settings = {"code_aster": {"line_search_iterations": 5}}
    with pytest.raises(PuzzleError, match="need a line search"):
        with_settings(_nonlinear_comm(), study, SolveStep(type="stationary"))


def test_relaxation_and_newton_reach_the_elmer_input():
    from puzzle.adapters.elmer.sif import with_settings
    from puzzle.model.schema import SolveStep, Study

    sif = "Simulation\n  Max Output Level = 3\nEnd\n\nSolver 1\n  Equation = Heat\nEnd\n"
    study = Study(name="S", component="00000000-0000-0000-0000-000000000000", solver="elmer",
                  steps=[SolveStep(type="stationary")],
                  solver_settings={"elmer": {"relaxation": 0.7,
                                             "newton_after_iterations": 3}})  # fmt: skip
    out = with_settings(sif, study, study.steps[0])
    assert "Nonlinear System Relaxation Factor = 0.7" in out
    assert "Nonlinear System Newton After Iterations = 3" in out
    study.solver_settings = {"elmer": {"relaxation": 1.5}}
    with pytest.raises(PuzzleError, match="at most 1"):
        with_settings(sif, study, study.steps[0])


def test_load_stepping_is_refused_where_a_solver_does_not_take_it(tmp_path):
    pytest.importorskip("OCP")
    from puzzle.adapters.elmer import ElmerAdapter

    project, study = _project(tmp_path)
    study.solver = "elmer"
    study.steps[0].settings["increments"] = 10
    assert "elmer does not translate the setting increments" in ElmerAdapter().check(project, study)


def test_agents_change_settings_in_one_edit(tmp_path):
    pytest.importorskip("OCP")
    import asyncio

    from puzzle.agent.mcp import build_server
    from puzzle.commands import Session
    from puzzle.project import load_project, save_project

    project, study = _project(tmp_path)
    study.solver_settings = {"calculix": {"solver": "SPOOLES"}}
    study.steps[0].settings["tolerance"] = 1e-8
    root = tmp_path / "p"
    save_project(project, root)
    (root / "bar.step").write_bytes((tmp_path / "bar.step").read_bytes())
    Session(root).submit({"command": "set_autonomy", "level": "act_in_budget"})
    server = build_server(root, "test")
    result = asyncio.run(server.call_tool("solver_settings_set", {
        "study": "S", "shared": {"increments": 20, "tolerance": None},
        "solvers": {"calculix": {"cutbacks": 8, "solver": None}}}))  # fmt: skip
    assert not result.is_error, result.structured_content
    saved = next(o for o in load_project(root).objects.values() if o.name == "S")
    assert saved.steps[0].settings == {"increments": 20}
    assert saved.solver_settings == {"calculix": {"cutbacks": 8}}
    with pytest.raises(Exception, match="above zero"):  # refused, nothing changed
        asyncio.run(server.call_tool("solver_settings_set", {
            "study": "S", "solvers": {"calculix": {"cutbacks": -1}}}))  # fmt: skip
