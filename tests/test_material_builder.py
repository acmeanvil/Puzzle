"""The material builder's foundations (ADR 0020): one property registry, completeness
before a run, your own materials stored apart (variants, undo), and material models
that solvers write or refuse, never silently drop."""

import re
import tomllib
from importlib.resources import files
from pathlib import Path

import pytest

from puzzle.materials import families, library_keys, library_material
from puzzle.materials.registry import BY_KEY, completeness, needs_for
from puzzle.materials.store import LibraryStore, entry_of
from puzzle.model.schema import Material, MaterialModels, Quantity

ADAPTERS = Path(__file__).parents[1] / "src" / "puzzle" / "adapters"


def test_every_property_the_adapters_read_is_registered():
    generic = tomllib.loads(files("puzzle.materials").joinpath("generic.toml").read_text())
    shipped = {
        k
        for m in generic.values()
        for k in m
        if k not in ("name", "description", "sources", "tables")
    }
    assert shipped <= set(BY_KEY)
    pattern = re.compile(
        r"(?:props|properties)\[\"(\w+)\"\]|need\(([^)]*)\)|piecewise\(\"(\w+)\"\)|"
        r"needed = \[([^\]]*)\]"
    )
    read = set()
    for path in ADAPTERS.rglob("*.py"):
        for match in pattern.finditer(path.read_text()):
            for group in match.groups():
                read |= set(
                    re.findall(
                        r"\"(\w+)\"", f'"{group}"' if group and '"' not in group else group or ""
                    )
                )
    material_words = {w for w in read if w in shipped or w.endswith(("_modulus", "_ratio"))}
    assert material_words and material_words <= set(BY_KEY), material_words - set(BY_KEY)


def test_needs_follow_the_analysis():
    assert needs_for("solid_mechanics", {"stationary"}) == {"youngs_modulus", "poissons_ratio"}
    assert "density" in needs_for("solid_mechanics", {"modal"})
    assert "thermal_expansion" in needs_for("solid_mechanics", {"stationary"}, thermal_strain=True)
    assert needs_for("heat_transfer", {"transient"}) == {
        "thermal_conductivity",
        "density",
        "specific_heat",
    }
    assert needs_for("heat_transfer", {"stationary"}, {"fluid_flow"}) >= {"specific_heat"}


def test_an_incomplete_material_is_reported_before_a_run(bracket):
    from puzzle.model.schema import Study

    al = next(o for o in bracket.objects.values() if isinstance(o, Material))
    del al.properties["thermal_conductivity"]
    problems = [p for p in bracket.problems() if p.code == "incomplete_material"]
    assert problems and problems[0].details["missing"] == ["thermal_conductivity"]
    assert "has no thermal conductivity, needed for heat transfer" in problems[0].message
    assert {p.object for p in problems} <= {s.name for s in bracket.of_type(Study)}


def test_completeness_per_solver_and_analysis():
    only_elastic = Material(
        name="Elastic only",
        properties={
            "youngs_modulus": Quantity(value=200, unit="GPa"),
            "poissons_ratio": Quantity(value=0.3),
        },
    )
    report = {(r["solver"], r["analysis"]): r for r in completeness(only_elastic)}
    assert report[("calculix", "Static structural")]["missing"] == []
    assert report[("calculix", "Modal")]["missing"] == ["density"]
    assert report[("calculix", "Steady heat")]["missing"] == ["thermal_conductivity"]
    assert ("code_saturne", "Static structural") not in report  # it doesn't run them


def _plastic(name="Bilinear steel", yield_stress=250e6):
    m = library_material("structural_steel")
    m.name = name
    m.models = MaterialModels(
        plasticity={"curves": [{"points": [(0.0, yield_stress), (0.1, 300e6)]}]}
    )
    return m


def test_your_materials_are_stored_apart_with_variants_and_undo():
    store = LibraryStore()
    store.save("my_steel", entry_of(_plastic()))
    assert "my_steel" in library_keys()
    mine = library_material("my_steel")
    assert mine.models.plasticity.curves[0].points[0] == (0.0, 250e6)

    # A variant inherits what it doesn't set: here only the yield stress differs.
    hard = _plastic("Hardened", 400e6)
    variant = {"name": "Hardened", "parent": "my_steel", "models": entry_of(hard)["models"]}
    store.save("my_steel_hardened", variant)
    h = library_material("my_steel_hardened")
    assert h.properties["youngs_modulus"] == mine.properties["youngs_modulus"]
    assert h.models.plasticity.curves[0].points[0][1] == 400e6
    assert families()["my_steel"] == ["my_steel_hardened"]

    # Editing a shipped material saves your copy; removing it brings the library's back.
    generic = library_material("structural_steel").properties["youngs_modulus"]
    edited = entry_of(library_material("structural_steel"))
    edited["properties"]["youngs_modulus"] = {"value": 205, "unit": "GPa"}
    store.save("structural_steel", edited)
    assert library_material("structural_steel").properties["youngs_modulus"].value == 205
    store.remove("structural_steel")
    assert library_material("structural_steel").properties["youngs_modulus"] == generic

    assert store.undo() == "structural_steel"  # the removal taken back
    assert library_material("structural_steel").properties["youngs_modulus"].value == 205


def test_bad_entries_are_refused_and_change_nothing():
    from puzzle.errors import PuzzleError

    store = LibraryStore()
    bad = entry_of(_plastic())
    bad["models"]["plasticity"]["curves"][0]["points"] = [[0.01, 250e6]]
    with pytest.raises(PuzzleError, match="starts at zero plastic strain"):
        store.save("bad_steel", bad)
    with pytest.raises(PuzzleError, match="no family 'nothing'"):
        store.save("orphan", {"name": "Orphan", "parent": "nothing"})
    assert "bad_steel" not in library_keys() and not store.entries()


def test_solvers_refuse_material_models_they_dont_write(bracket):
    from puzzle.adapters import check_study
    from puzzle.model.schema import Study

    al = next(o for o in bracket.objects.values() if isinstance(o, Material))
    al.models = MaterialModels(hyperelasticity={"c10": 1e6})  # no solver writes it yet
    al.solver_keywords = {"calculix": "*CREEP, LAW=NORTON"}
    static = next(s for s in bracket.of_type(Study) if s.name == "Static")
    problems = check_study(bracket, static)["calculix"]
    assert any("does not use hyperelasticity yet" in p for p in problems)
    assert any("cannot place material keywords yet" in p for p in problems)


def test_a_model_is_refused_only_where_its_physics_is_solved():
    rows = {(r["solver"], r["analysis"]): r for r in completeness(_plastic())}
    assert rows[("elmer", "Static structural")]["refused"] == ["plasticity"]
    assert rows[("elmer", "Steady heat")]["refused"] == []  # plastic steel conducts heat
    assert rows[("calculix", "Static structural")]["refused"] == []  # CalculiX yields


def test_an_agent_reads_a_materials_completeness(tmp_path):
    pytest.importorskip("mcp")
    import asyncio

    from puzzle.agent.mcp import build_server
    from puzzle.materials import add_library_material
    from puzzle.model.schema import Project
    from puzzle.project import save_project

    p = Project(name="m")
    add_library_material(p, "air")
    save_project(p, tmp_path)
    server = build_server(tmp_path, "test")
    out = asyncio.run(server.call_tool("material_check", {"material": "Air"})).structured_content
    rows = {(r["solver"], r["analysis"]): r for r in out["analyses"]}
    assert rows[("calculix", "Static structural")]["missing"] == [
        "poissons_ratio",
        "youngs_modulus",
    ]
    assert rows[("code_saturne", "Steady flow")]["missing"] == []
