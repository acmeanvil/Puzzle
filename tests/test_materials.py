import pytest

from puzzle.materials import add_library_material, library_keys, library_material
from puzzle.model import Project, Quantity


def test_every_library_value_converts_to_si():
    for key in library_keys():
        for prop, q in library_material(key).properties.items():
            assert isinstance(q.to_si(), float), (key, prop)


def test_library_has_the_core_materials():
    assert {"structural_steel", "aluminium_6061_t6", "copper", "air"} <= set(library_keys())


def test_adding_copies_into_the_project():
    project = Project(name="p")
    steel = add_library_material(project, "structural_steel", name="Frame steel")
    assert project.objects[steel.id].name == "Frame steel"
    assert steel.properties["youngs_modulus"].to_si() == pytest.approx(200e9)


def test_project_copy_is_independent_of_the_library():
    project = Project(name="p")
    steel = add_library_material(project, "structural_steel")
    steel.properties["youngs_modulus"] = Quantity(value=210, unit="GPa")
    fresh = library_material("structural_steel")
    assert fresh.properties["youngs_modulus"].value == 200


def test_two_copies_are_separate_materials():
    project = Project(name="p")
    a = add_library_material(project, "copper")
    b = add_library_material(project, "copper")
    assert a.id != b.id


def test_unknown_material():
    with pytest.raises(KeyError, match="unobtainium"):
        library_material("unobtainium")
