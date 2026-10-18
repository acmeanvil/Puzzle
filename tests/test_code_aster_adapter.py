import pytest
from conftest_geometry import bar_project, write_bar_step

from puzzle.adapters.code_aster import CodeAsterAdapter
from puzzle.adapters.code_aster.tables import read_results
from puzzle.adapters.common import InputError
from puzzle.mesh import generate_mesh
from puzzle.model import Feature, Physics, Quantity, SolveStep, Study


def _case(tmp_path, features, step, physics="solid_mechanics"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    stepfile = write_bar_step(tmp_path / "bar.step")
    project, comp = bar_project(
        stepfile, [Physics(type=physics, features=features)], mesh_size_mm=5
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
    comm = (export.parent / "job.comm").read_text()
    assert (export.parent / "job.med").stat().st_size > 0
    assert "MECA_STATIQUE" in comm and "SIGM_NOEU" in comm
    assert "DDL_IMPO=(_F(GROUP_MA='G2', DX=0.0, DY=0.0, DZ=0.0),)" in comm
    traction = float(comm.split("FY=")[1].split(")")[0])
    assert traction == pytest.approx(-100 / (0.01 * 0.01), rel=1e-9)
    lines = export.read_text().splitlines()
    assert "F comm job.comm D 1" in lines and "F mmed job.med D 20" in lines
    assert "F libr displacement.csv R 81" in lines and "F libr stress.csv R 82" in lines


def test_modal_and_heat_cases(tmp_path):
    export = _case(
        tmp_path / "a",
        [Feature(type="fixed", selection="Bar/Root")],
        SolveStep(type="modal", settings={"modes": 3}),
    )
    comm = (export.parent / "job.comm").read_text()
    assert "CALC_MODES" in comm and "NMAX_FREQ=3" in comm and "NOM_PARA='FREQ'" in comm
    hot = Feature(
        type="temperature", selection="Bar/Root", values={"T": Quantity(value=100, unit="degC")}
    )
    export = _case(tmp_path / "b", [hot], SolveStep(type="stationary"), physics="heat_transfer")
    comm = (export.parent / "job.comm").read_text()
    assert "THER_LINEAIRE" in comm and "TEMP=373.15" in comm and "LAMBDA=" in comm


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
    (tmp_path / "displacement.csv").write_text(DISP.replace(" 2,7", " 1,9").replace(" 2,2", " 1,8"))
    (step,) = read_results(tmp_path).steps
    disp = step.fields["displacement"]
    assert list(disp.ids) == [2, 7, 8, 9]
    assert disp.at([7])[0].tolist() == [1e-3, 2e-3, 3e-3]


def test_table_reader_modes(tmp_path):
    (tmp_path / "displacement.csv").write_text(DISP)
    (tmp_path / "frequency.csv").write_text(FREQ)
    modes = read_results(tmp_path).modes()
    assert [(m.mode, m.value) for m in modes] == [(1, 203.9528), (2, 203.957)]
    assert modes[1].fields["displacement"].at([2])[0, 2] == 2.0


def test_transient_heat_case(tmp_path):
    hot = Feature(
        type="temperature", selection="Bar/Root", values={"T": Quantity(value=100, unit="degC")}
    )
    settings = {"time_step": Quantity(value=5, unit="s"), "end_time": Quantity(value=30, unit="s")}
    export = _case(
        tmp_path, [hot], SolveStep(type="transient", settings=settings), physics="heat_transfer"
    )
    comm = (export.parent / "job.comm").read_text()
    assert "TYPE_CALCUL='TRAN'" in comm and "ETAT_INIT=_F(VALE=293.15)" in comm
    assert "JUSQU_A=30, PAS=5" in comm


TRANSIENT = """#
RESULTAT,NOM_CHAM,INST,NUME_ORDRE,NOEUD,COOR_X,COOR_Y,COOR_Z,TEMP
res ,TEMP , 0.0, 0,1 , 0.0, 0.0, 0.0, 293.15
res ,TEMP , 5.0, 1,1 , 0.0, 0.0, 0.0, 300.0
res ,TEMP , 10.0, 2,1 , 0.0, 0.0, 0.0, 310.0
"""


def test_table_reader_time_series(tmp_path):
    (tmp_path / "temperature.csv").write_text(TRANSIENT)
    steps = read_results(tmp_path).steps
    assert [s.value for s in steps] == [0.0, 5.0, 10.0]
    assert steps[-1].fields["temperature"].values[0, 0] == 310.0
