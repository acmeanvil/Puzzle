import pytest
from conftest_geometry import split_bar_project

from puzzle.adapters.calculix.inp import write_inp
from puzzle.adapters.code_aster import CodeAsterAdapter
from puzzle.adapters.common import InputError
from puzzle.adapters.elmer import ElmerAdapter
from puzzle.adapters.fenicsx import FenicsxAdapter
from puzzle.mesh import generate_mesh
from puzzle.model import Component, Contact, Feature

FIXED = [Feature(type="fixed", selection="Left/Start")]


def _tie(meshing="nonconformal", type="bonded"):
    extra = {"meshing": meshing} if type == "bonded" else {}
    return Contact(name="Joint", type=type, source="Right/Start", target="Left/End", **extra)


def _setup(tmp_path, solver, contact, **kwargs):
    project, study = split_bar_project(tmp_path, solver, FIXED, contact, **kwargs)
    comp = next(iter(project.of_type(Component)))
    mesh = generate_mesh(project, comp.id, tmp_path, tmp_path / "m").mesh
    return project, study, mesh


def test_calculix_ties_nonconformal_bonds(tmp_path):
    project, study, mesh = _setup(tmp_path, "calculix", _tie())
    inp = write_inp(project, study, mesh)
    assert "*SURFACE, NAME=S1S_RIGHT_START, TYPE=ELEMENT" in inp
    assert "*SURFACE, NAME=S1M_LEFT_END, TYPE=ELEMENT" in inp
    assert "*TIE, NAME=T_JOINT\nS1S_RIGHT_START, S1M_LEFT_END" in inp


def test_code_aster_ties_with_liaison_mail(tmp_path):
    project, study, mesh = _setup(tmp_path, "code_aster", _tie())
    export = CodeAsterAdapter().write_input(project, study, mesh, tmp_path / "case")
    comm = (export.parent / "job.comm").read_text()
    assert "LIAISON_MAIL=(_F(GROUP_MA_MAIT=" in comm and "TYPE_RACCORD='MASSIF'" in comm


def test_code_aster_refuses_thermal_ties(tmp_path):
    project, study, mesh = _setup(tmp_path, "code_aster", _tie(), physics="heat_transfer")
    with pytest.raises(InputError) as err:
        CodeAsterAdapter().write_input(project, study, mesh, tmp_path / "case")
    assert err.value.code == "unsupported"


@pytest.mark.parametrize("adapter", [ElmerAdapter, FenicsxAdapter])
def test_unwritten_contacts_are_refused_not_ignored(tmp_path, adapter):
    """Ignoring a nonconformal bond would leave the halves unconnected."""
    project, study, mesh = _setup(tmp_path, adapter.name, _tie())
    with pytest.raises(InputError) as err:
        adapter().write_input(project, study, mesh, tmp_path / "case")
    assert err.value.code == "unsupported" and "bonded_nonconformal" in err.value.message


def test_conformal_bonds_need_nothing_from_the_solver(tmp_path):
    project, study, mesh = _setup(tmp_path, "elmer", _tie("conformal"))
    ElmerAdapter().write_input(project, study, mesh, tmp_path / "case")
