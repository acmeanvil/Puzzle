import pytest
from conftest_geometry import split_bar_project

from puzzle.adapters.calculix.inp import write_inp
from puzzle.adapters.code_aster import CodeAsterAdapter
from puzzle.adapters.common import InputError
from puzzle.adapters.elmer import ElmerAdapter
from puzzle.adapters.fenicsx import FenicsxAdapter
from puzzle.mesh import generate_mesh
from puzzle.model import Component, Contact, Feature, Quantity

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


def test_calculix_writes_contact_pairs(tmp_path):
    contact = Contact(
        name="Slide", type="frictional", friction=0.2, source="Right/Start", target="Left/End"
    )
    project, study, mesh = _setup(tmp_path, "calculix", contact)
    inp = write_inp(project, study, mesh)
    assert "*CONTACT PAIR, INTERACTION=I_SLIDE, TYPE=SURFACE TO SURFACE" in inp
    assert "*SURFACE BEHAVIOR, PRESSURE-OVERCLOSURE=LINEAR" in inp
    assert "*FRICTION\n0.2, " in inp and "*STATIC\n1, 1.0" in inp  # contact: full load first


def test_code_aster_contact(tmp_path):
    smooth = Contact(name="Slide", type="frictionless", source="Right/Start", target="Left/End")
    project, study, mesh = _setup(tmp_path, "code_aster", smooth)
    export = CodeAsterAdapter().write_input(project, study, mesh, tmp_path / "case")
    comm = (export.parent / "job.comm").read_text()
    assert "DEFI_CONTACT(MODELE=model, FORMULATION='CONTINUE', FROTTEMENT='SANS'" in comm
    assert "STAT_NON_LINE(" in comm and "ORIE_PEAU" in comm and "REAC_NODA" in comm
    # ADR 0053: the full load at once, cut down by Code_Aster only if an increment fails.
    assert "INTERVALLE=_F(JUSQU_A=1.0, NOMBRE=1)" in comm
    assert "ACTION='DECOUPE'" in comm and "INCREMENT=_F(LIST_INST=times)" in comm
    assert "P ncpus" in export.read_text()


def _rough(tmp_path, features=FIXED, order=2):
    from puzzle.model import MeshRecipe

    rough = Contact(
        name="Slide", type="frictional", friction=0.3, source="Right/Start", target="Left/End"
    )
    project, study = split_bar_project(tmp_path, "code_aster", features, rough)
    for recipe in project.of_type(MeshRecipe):
        recipe.element_order = order
    comp = next(iter(project.of_type(Component)))
    mesh = generate_mesh(project, comp.id, tmp_path, tmp_path / "m").mesh
    return project, study, mesh


def test_code_aster_friction_on_first_order_meshes(tmp_path):
    for sub in ("q", "l", "c"):
        (tmp_path / sub).mkdir()
    adapter = CodeAsterAdapter()
    with pytest.raises(InputError, match="first-order") as err:
        adapter.write_input(*_rough(tmp_path / "q"), tmp_path / "q" / "case")
    assert err.value.code == "unsupported"

    export = adapter.write_input(*_rough(tmp_path / "l", order=1), tmp_path / "l" / "case")
    comm = (export.parent / "job.comm").read_text()
    assert "FROTTEMENT='COULOMB'" in comm and "COULOMB=0.3" in comm

    # A displacement condition on the slave face's nodes is refused up front.
    held = [
        *FIXED,
        Feature(
            type="displacement", selection="Right/Start", values={"y": Quantity(value=0, unit="m")}
        ),
    ]
    with pytest.raises(InputError, match="shares nodes") as err:
        adapter.write_input(*_rough(tmp_path / "c", held, order=1), tmp_path / "c" / "case")
    assert err.value.details["conflicts"] == ["Right/Start"]


def test_code_aster_leaves_bolt_tied_nodes_out_of_contact():
    """A bolt hole's edge lies on the seat face: its nodes are tied to the bolt, so
    Code_Aster's contact must skip them (SANS_GROUP_NO) or its matrix is singular."""
    from types import SimpleNamespace as NS

    import numpy as np

    from puzzle.adapters.code_aster.comm import _contact_exclusions, _defi_contact

    groups = {
        "Bracket/Seat": NS(node_ids=np.array([1, 2, 3, 4])),
        "Bracket/Hole": NS(node_ids=np.array([4, 5])),
        "Base/Hole": NS(node_ids=np.array([7, 8])),
        "Base/Top": NS(node_ids=np.array([7, 9])),
    }
    names = {"Bracket/Seat": "G1", "Bracket/Hole": "G2", "Base/Hole": "G3", "Base/Top": "G4"}
    contact = NS(name="Seat", source="Bracket/Seat", target="Base/Top", type="frictional",
                 friction=0.2, extensions={})  # fmt: skip
    bolt = NS(source="Bracket/Hole", target="Base/Hole")
    excluded = _contact_exclusions([contact], [bolt], NS(groups=groups), names)
    assert excluded == {"Seat": ["G2"]}  # the base's hole touches only the master side
    assert "SANS_GROUP_NO=('G2',)" in _defi_contact([contact], names, excluded)
