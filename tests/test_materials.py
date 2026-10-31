import json

import pytest

from puzzle.materials import (
    add_library_material,
    library_keys,
    library_material,
    library_paths,
    search_library,
    user_library_path,
)
from puzzle.model import Project, Quantity

# Bolting steels (ASTM bolt and stud specifications, SAE J429, ISO 898-1)
BOLTING = {
    "astm_a193_b7", "astm_a193_b16", "astm_a193_b8_cl1", "astm_a193_b8m_cl2", "astm_a320_l7",
    "astm_a307_a", "astm_a449", "astm_a354_bd", "astm_f3125_a325", "astm_f3125_a490",
    "sae_j429_grade_2", "sae_j429_grade_5", "sae_j429_grade_8", "iso_898_1_class_4_6",
    "iso_898_1_class_8_8", "iso_898_1_class_10_9", "iso_898_1_class_12_9",
    # from Lacewing Lab (Shigley Tables 8-9 to 8-11)
    "sae_j429_grade_1", "sae_j429_grade_2_large", "sae_j429_grade_4", "sae_j429_grade_5_large",
    "sae_j429_grade_5_2", "sae_j429_grade_7", "sae_j429_grade_8_2", "astm_f3125_a325_large",
    "astm_a354_bc", "iso_898_1_class_4_8", "iso_898_1_class_5_8", "iso_898_1_class_8_8_large",
    "iso_898_1_class_9_8",
}  # fmt: skip

GENERIC = {
    "structural_steel",
    "stainless_steel_304",
    "aluminium_6061_t6",
    "titanium_ti6al4v",
    "copper",
    "electrical_steel",
    "ptfe",
    "air",
    "water",
} | BOLTING


def test_generic_set_is_shipped():
    assert set(library_keys()) == GENERIC  # the test setup excludes any user library


def test_every_value_converts_to_si():
    for key in library_keys():
        material = library_material(key)
        for prop, q in material.properties.items():
            assert isinstance(q.to_si(), float), (key, prop)
        for prop, table in material.tables.items():
            Quantity(value=1, unit=table.unit).to_si()  # unit parses
            assert all(p.temperature > 0 for p in table.points), (key, prop)


@pytest.mark.parametrize(
    ("key", "props"),
    [
        (
            "structural_steel",
            {
                "youngs_modulus",
                "poissons_ratio",
                "density",
                "thermal_conductivity",
                "specific_heat",
                "thermal_expansion",
            },
        ),
        ("copper", {"electrical_conductivity", "relative_permeability", "thermal_conductivity"}),
        ("electrical_steel", {"relative_permeability", "electrical_conductivity"}),
        ("ptfe", {"relative_permittivity", "thermal_conductivity"}),
        ("air", {"density", "dynamic_viscosity", "relative_permittivity", "relative_permeability"}),
        ("water", {"density", "dynamic_viscosity", "thermal_conductivity", "specific_heat"}),
    ],
)
def test_generics_cover_what_the_benchmarks_need(key, props):
    assert props <= set(library_material(key).properties)


def test_structural_steel_tables_follow_eurocode():
    steel = library_material("structural_steel")
    e = steel.tables["youngs_modulus"]
    assert e.at(293.15) == pytest.approx(210e9)
    assert e.at(873.15) == pytest.approx(0.31 * 210e9)  # k_E at 600 °C
    assert e.at(1.0) == e.curve()[0][1]  # held constant past the ends
    k = steel.tables["thermal_conductivity"]
    assert k.at(573.15) == pytest.approx(54 - 0.0333 * 300, rel=1e-4)
    cte = steel.tables["thermal_expansion"]
    assert cte.reference_temperature == pytest.approx(293.15)
    # Room-temperature values agree with the tables at 20 °C.
    for prop in ("youngs_modulus", "thermal_conductivity", "specific_heat", "thermal_expansion"):
        assert steel.properties[prop].to_si() == pytest.approx(
            steel.tables[prop].at(293.15), rel=1e-3
        )
    assert all(p.source == 0 for t in steel.tables.values() for p in t.points) and steel.sources


def test_user_library_adds_and_overrides(tmp_path, monkeypatch):
    user = {
        "materials": {
            "special_alloy": {
                "name": "Special alloy",
                "description": "",
                "info": {"group": "Test"},
                "properties": {"density": {"value": 8000, "unit": "kg/m^3"}},
                "tables": {
                    "youngs_modulus": {
                        "unit": "Pa",
                        "points": [[293.15, 2e11, 0], [773.15, 1.5e11, 0]],
                    }
                },
                "sources": ["A test source"],
            },
            "copper": {
                "name": "Copper (my data)",
                "description": "",
                "info": {},
                "properties": {"density": {"value": 8940, "unit": "kg/m^3"}},
                "tables": {},
                "sources": [],
            },
        }
    }
    path = tmp_path / "library.json"
    path.write_text(json.dumps(user))
    monkeypatch.setenv("PUZZLE_MATERIALS", str(path))
    assert library_paths() == [path]
    assert set(library_keys()) == GENERIC | {"special_alloy"}
    assert library_material("copper").name == "Copper (my data)"
    alloy = library_material("special_alloy")
    assert alloy.tables["youngs_modulus"].at(533.15) == pytest.approx(1.75e11)
    assert "special_alloy" in search_library("test")


def test_default_user_library_location(monkeypatch):
    monkeypatch.delenv("PUZZLE_MATERIALS")
    assert user_library_path().name == "library.json"
    assert user_library_path().parent.name == "materials"


def test_search():
    assert search_library("steel") == [
        "electrical_steel",
        "stainless_steel_304",
        "structural_steel",
    ]
    assert search_library("fastener") == sorted(BOLTING)  # by their group, "Fastener"


def test_adding_copies_into_the_project():
    project = Project(name="p")
    steel = add_library_material(project, "structural_steel", name="Frame steel")
    assert project.objects[steel.id].name == "Frame steel"
    assert steel.tables and steel.sources


def test_project_copy_is_independent_of_the_library():
    project = Project(name="p")
    steel = add_library_material(project, "structural_steel")
    steel.properties["youngs_modulus"] = Quantity(value=190, unit="GPa")
    steel.tables.clear()
    fresh = library_material("structural_steel")
    assert fresh.properties["youngs_modulus"].to_si() == pytest.approx(210e9)
    assert fresh.tables


def test_two_copies_are_separate_materials():
    project = Project(name="p")
    assert add_library_material(project, "air").id != add_library_material(project, "air").id


def test_unknown_material():
    with pytest.raises(KeyError, match="unobtainium"):
        library_material("unobtainium")
