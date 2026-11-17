import pytest
from conftest_geometry import bar_project, write_bar_step

from puzzle.adapters.code_aster import CodeAsterAdapter
from puzzle.adapters.code_aster.tables import read_results
from puzzle.adapters.common import InputError
from puzzle.mesh import generate_mesh
from puzzle.model import Feature, Physics, Quantity, SolveStep, Study


def _case(tmp_path, features, step, physics="solid_mechanics", settings=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    stepfile = write_bar_step(tmp_path / "bar.step")
    project, comp = bar_project(
        stepfile,
        [Physics(type=physics, features=features, settings=settings or {})],
        mesh_size_mm=5,
    )
    study = project.add(Study(name="s", component=comp.id, solver="code_aster", steps=[step]))
    mesh = generate_mesh(project, comp.id, tmp_path, tmp_path / "m").mesh
    work = tmp_path / "case"
    work.mkdir()
    return CodeAsterAdapter().write_input(project, study, mesh, work)


def test_static_case_files(tmp_path):
    fixed = Feature(type="fixed", selection="Bar/Root")
    force = Feature(type="force", selection="Bar/Tip", values={"y": Quantity(value=-100, unit="N")})
    export = _case(tmp_path, [fixed, force], SolveStep(type="stationary"))
    comm = (export.parent / "job.comm").read_text(encoding="utf-8")
    assert (export.parent / "job.med").stat().st_size > 0
    assert "MECA_STATIQUE" in comm and "SIGM_NOEU" in comm
    assert "DDL_IMPO=(_F(GROUP_MA='G2', DX=0.0, DY=0.0, DZ=0.0),)" in comm
    traction = float(comm.split("FY=")[1].split(")")[0])
    assert traction == pytest.approx(-100 / (0.01 * 0.01), rel=1e-9)
    lines = export.read_text(encoding="utf-8").splitlines()
    assert "F comm job.comm D 1" in lines and "F mmed job.med D 20" in lines
    assert "F libr displacement.csv R 81" in lines and "F libr stress.csv R 82" in lines


def test_modal_and_heat_cases(tmp_path):
    export = _case(
        tmp_path / "a",
        [Feature(type="fixed", selection="Bar/Root")],
        SolveStep(type="modal", settings={"modes": 3}),
    )
    comm = (export.parent / "job.comm").read_text(encoding="utf-8")
    assert "CALC_MODES" in comm and "'FREQ', 'FACT_PARTICI_DX'" in comm
    # 3 asked, 3 more to find where a pair ends, then the checked pass to there
    assert "NMAX_FREQ=6), VERI_MODE=_F(STOP_ERREUR='NON')" in comm and "range(3, " in comm
    assert "NMAX_FREQ=_n" in comm
    hot = Feature(
        type="temperature", selection="Bar/Root", values={"T": Quantity(value=100, unit="degC")}
    )
    constant = {"temperature_dependent_materials": False}
    export = _case(tmp_path / "b", [hot], SolveStep(type="stationary"), "heat_transfer", constant)
    comm = (export.parent / "job.comm").read_text(encoding="utf-8")
    assert "THER_LINEAIRE" in comm and "TEMP=373.15" in comm and "LAMBDA=" in comm

    # Structural steel carries EN 1993-1-2 tables: by default they make it nonlinear.
    export = _case(tmp_path / "c", [hot], SolveStep(type="stationary"), "heat_transfer")
    comm = (export.parent / "job.comm").read_text(encoding="utf-8")
    assert "THER_NON_LINE" in comm and "THER_NL=_F(LAMBDA=lam1, BETA=beta1)" in comm
    assert "ETAT_INIT=_F(STAT='OUI')" in comm and "573.15, 44.01" in comm


def test_unsupported_steps_are_refused(tmp_path):
    with pytest.raises(InputError, match="transient"):
        _case(tmp_path, [Feature(type="fixed", selection="Bar/Root")], SolveStep(type="transient"))


DISP = """#
#--------------------------------------------------------------------------------
RESULTAT,NOM_CHAM,NUME_ORDRE,NOEUD,COOR_X,COOR_Y,COOR_Z,DX,DY,DZ
res ,DEPL , 1,7 , 0.0, 0.0, 0.0, 1.0E-03, 2.0E-03, 3.0E-03
res ,DEPL , 1,2 , 1.0, 0.0, 0.0, 4.0E-03, 5.0E-03, 6.0E-03
res ,DEPL , 2,7 , 0.0, 0.0, 0.0, 0.0, 0.0, 1.0
res ,DEPL , 2,2 , 1.0, 0.0, 0.0, 0.0, 0.0, 2.0
"""
FREQ = """#
NUME_ORDRE,FREQ
1, 2.039528E+02
2, 2.039570E+02
"""


def test_table_reader_static(tmp_path):
    (tmp_path / "displacement.csv").write_text(
        DISP.replace(" 2,7", " 1,9").replace(" 2,2", " 1,8"), encoding="utf-8"
    )
    (step,) = read_results(tmp_path).steps
    disp = step.fields["displacement"]
    assert list(disp.ids) == [2, 7, 8, 9]
    assert disp.at([7])[0].tolist() == [1e-3, 2e-3, 3e-3]


def test_table_reader_modes(tmp_path):
    (tmp_path / "displacement.csv").write_text(DISP, encoding="utf-8")
    (tmp_path / "frequency.csv").write_text(FREQ, encoding="utf-8")
    modes = read_results(tmp_path).modes()
    assert [(m.mode, m.value) for m in modes] == [(1, 203.9528), (2, 203.957)]
    assert modes[1].fields["displacement"].at([2])[0, 2] == 2.0


def test_transient_heat_case(tmp_path):
    hot = Feature(
        type="temperature", selection="Bar/Root", values={"T": Quantity(value=100, unit="degC")}
    )
    settings = {"time_step": Quantity(value=5, unit="s"), "end_time": Quantity(value=30, unit="s")}
    export = _case(
        tmp_path,
        [hot],
        SolveStep(type="transient", settings=settings),
        "heat_transfer",
        {"temperature_dependent_materials": False},
    )
    comm = (export.parent / "job.comm").read_text(encoding="utf-8")
    assert "TYPE_CALCUL='TRAN'" in comm and "ETAT_INIT=_F(VALE=293.15)" in comm
    assert "JUSQU_A=30, PAS=5" in comm
    assert "PARM_THETA=1.0" in comm  # backward Euler, like every other solver

    export = _case(
        tmp_path / "cn",
        [hot],
        SolveStep(type="transient", settings=settings | {"time_scheme": "crank_nicolson"}),
        "heat_transfer",
        {"temperature_dependent_materials": False},
    )
    assert "PARM_THETA=0.5" in (export.parent / "job.comm").read_text(encoding="utf-8")
    with pytest.raises(InputError) as err:
        _case(
            tmp_path / "bdf",
            [hot],
            SolveStep(type="transient", settings=settings | {"time_scheme": "bdf2"}),
            "heat_transfer",
        )
    assert err.value.code == "unsupported"


TRANSIENT = """#
RESULTAT,NOM_CHAM,INST,NUME_ORDRE,NOEUD,COOR_X,COOR_Y,COOR_Z,TEMP
res ,TEMP , 0.0, 0,1 , 0.0, 0.0, 0.0, 293.15
res ,TEMP , 5.0, 1,1 , 0.0, 0.0, 0.0, 300.0
res ,TEMP , 10.0, 2,1 , 0.0, 0.0, 0.0, 310.0
"""


def test_table_reader_time_series(tmp_path):
    (tmp_path / "temperature.csv").write_text(TRANSIENT, encoding="utf-8")
    steps = read_results(tmp_path).steps
    assert [s.value for s in steps] == [0.0, 5.0, 10.0]
    assert steps[-1].fields["temperature"].values[0, 0] == 310.0


def test_plasticity_keeps_the_discretes_behaviour(tmp_path):
    """ADR 0090 package 6: with a plastic material the behaviour list was rebuilt from
    the materials alone, so an elastic support's discretes (and, under large deflection,
    shells' GROT_GDEP and the discretes' PETIT) had no entry."""
    from puzzle.model.schema import Material, MaterialModels

    stepfile = write_bar_step(tmp_path / "bar.step")
    support = Feature(type="elastic_support", selection="Bar/Tip",
                      values={"kn": Quantity(value=1e12, unit="N/m^3")})  # fmt: skip
    features = [Feature(type="fixed", selection="Bar/Root"), support]
    for large in (False, True):
        project, comp = bar_project(stepfile, [Physics(type="solid_mechanics", features=features)],
                                    mesh_size_mm=5)  # fmt: skip
        steel = project.get(comp.materials[0].material, Material)
        steel.models = MaterialModels(
            plasticity={"curves": [{"points": [(0.0, 250e6), (0.5, 500e6)]}]}
        )
        settings = {"geometric_nonlinearity": True} if large else {}
        study = project.add(
            Study(
                name="s",
                component=comp.id,
                solver="code_aster",
                steps=[SolveStep(type="stationary", settings=settings)],
            )
        )
        mesh = generate_mesh(project, comp.id, tmp_path, tmp_path / f"m{large}").mesh
        work = tmp_path / f"case{large}"
        work.mkdir()
        CodeAsterAdapter().write_input(project, study, mesh, work)
        comm = (work / "job.comm").read_text(encoding="utf-8")
        behaviour = comm.split("COMPORTEMENT=(")[1].split("), NEWTON")[0]
        assert "RELATION='VMIS_ISOT_TRAC'" in behaviour
        law = "RELATION='ELAS', DEFORMATION='PETIT'" if large else "RELATION='ELAS')"
        assert "'SPRINGS'" in behaviour and law in behaviour.split("'SPRINGS'")[1]
