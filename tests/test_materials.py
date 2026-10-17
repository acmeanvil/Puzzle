import pytest

from puzzle.materials import (
    add_library_material,
    library_keys,
    library_material,
    search_library,
)
from puzzle.model import Project, Quantity


def test_library_holds_the_workbook_and_the_fluids():
    keys = set(library_keys())
    assert len(keys) >= 160
    assert {
        "general_carbon_steel",
        "6061_t6_al",
        "304_304l_ss",
        "inconel_718_precipitation_hardened",
    } <= keys
    assert {"air", "water"} <= keys


def test_every_value_converts_to_si():
    for key in library_keys():
        material = library_material(key)
        for prop, q in material.properties.items():
            assert isinstance(q.to_si(), float), (key, prop)
        for prop, table in material.tables.items():
            Quantity(value=1, unit=table.unit).to_si()  # unit parses
            assert all(p.temperature > 0 for p in table.points), (key, prop)


def test_every_table_point_cites_a_source():
    for key in library_keys():
        material = library_material(key)
        for prop, table in material.tables.items():
            for point in table.points:
                assert point.source is not None, (key, prop)
                assert 0 <= point.source < len(material.sources)


def test_room_temperature_values_are_plausible():
    steel = library_material("general_carbon_steel")
    assert steel.properties["youngs_modulus"].to_si() == pytest.approx(200e9, rel=0.01)
    assert steel.properties["density"].to_si() == pytest.approx(7850, rel=0.001)
    al = library_material("6061_t6_al")
    assert al.properties["thermal_expansion"].to_si() == pytest.approx(23.6e-6, rel=0.02)


def test_temperature_tables_interpolate():
    inconel = library_material("inconel_718_precipitation_hardened")
    e = inconel.tables["youngs_modulus"]
    cold, hot = e.at(20.0), e.at(900.0)
    assert cold > e.at(293.15) > hot  # stiffness falls with temperature
    assert e.at(1.0) == e.curve()[0][1]  # held constant past the ends


def test_mean_cte_keeps_its_reference_temperature():
    table = library_material("6061_t6_al").tables["thermal_expansion"]
    assert table.unit == "1/K"
    assert table.reference_temperature == pytest.approx(293.15)


def test_search():
    assert "6061_t6_al" in search_library("6061")
    assert all("inconel" in k for k in search_library("inconel"))


def test_adding_copies_into_the_project():
    project = Project(name="p")
    steel = add_library_material(project, "astm_a36_cs", name="Frame steel")
    assert project.objects[steel.id].name == "Frame steel"
    assert steel.info["group"] == "Carbon Steel"
    assert steel.sources


def test_project_copy_is_independent_of_the_library():
    project = Project(name="p")
    steel = add_library_material(project, "astm_a36_cs")
    steel.properties["youngs_modulus"] = Quantity(value=210, unit="GPa")
    steel.tables.clear()
    fresh = library_material("astm_a36_cs")
    assert fresh.properties["youngs_modulus"].to_si() == pytest.approx(199.948e9, rel=1e-4)
    assert fresh.tables


def test_two_copies_are_separate_materials():
    project = Project(name="p")
    a = add_library_material(project, "air")
    b = add_library_material(project, "air")
    assert a.id != b.id


def test_unknown_material():
    with pytest.raises(KeyError, match="unobtainium"):
        library_material("unobtainium")
