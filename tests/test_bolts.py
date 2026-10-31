"""Bolt types (ADR 0050): standard sizes, stress areas, preloads, and how a bolt takes
its type's values."""

from __future__ import annotations

import pytest

from puzzle import bolts
from puzzle.commands.outline import outline
from puzzle.materials import add_library_material
from puzzle.model.schema import (
    BeamFastener,
    BoltType,
    Component,
    Geometry,
    MeshRecipe,
    PartInstance,
    Project,
    Quantity,
)
from puzzle.project.graph import content_hashes


@pytest.mark.parametrize(
    ("standard", "name", "area"),
    [
        # Published tensile stress areas: ISO 898-1 Table 4 (mm²), ASME B1.1 (in²).
        ("iso_coarse", "M6", 20.1e-6),
        ("iso_coarse", "M10", 58.0e-6),
        ("iso_coarse", "M12", 84.3e-6),
        ("iso_coarse", "M20", 245e-6),
        ("iso_coarse", "M36", 817e-6),
        ("iso_fine", "M10x1.25", 61.2e-6),
        ("iso_fine", "M16x1.5", 167e-6),
        ("unc", "1/4-20 UNC", 0.0318 * 0.0254**2),
        ("unc", "1/2-13 UNC", 0.1419 * 0.0254**2),
        ("unc", "1-8 UNC", 0.606 * 0.0254**2),
        ("unf", "1/2-20 UNF", 0.1599 * 0.0254**2),
    ],
)
def test_stress_areas_match_the_standards(standard, name, area):
    size = bolts.size(standard, name)
    assert size is not None, name
    assert size.stress_area == pytest.approx(area, rel=0.005)


def test_every_series_is_listed():
    assert [s.name for s in bolts.sizes("iso_coarse")][:3] == ["M3", "M4", "M5"]
    assert len(bolts.sizes("iso_coarse")) == len(bolts.sizes("iso_fine")) == 17
    assert len(bolts.sizes("unc")) == len(bolts.sizes("unf")) == 18
    assert bolts.size("unc", "#10-24 UNC").diameter == pytest.approx(0.190 * 0.0254)


def test_preloads():
    # Torque: F = T / (K d); an M10 at 50 N·m with K = 0.2 is 25 kN.
    assert bolts.preload_from_torque(50.0, 0.2, 0.010) == pytest.approx(25e3)
    # 70 % of yield of an M10 8.8 (640 MPa, 58.0 mm²): 26.0 kN.
    assert bolts.preload_from_yield(70, 640e6, 58.0e-6) == pytest.approx(25.98e3, rel=1e-3)


def _project():
    p = Project(name="bolted")
    grade = add_library_material(p, "iso_898_1_class_8_8")
    steel = add_library_material(p, "structural_steel")
    m10 = bolts.size("iso_coarse", "M10")
    btype = p.add(
        BoltType(
            name="M10 8.8",
            standard="iso_coarse",
            size="M10",
            diameter=Quantity(value=10, unit="mm"),
            pitch=Quantity(value=m10.pitch * 1e3, unit="mm"),
            material=grade.id,
        )
    )
    geo = p.add(Geometry(name="G", source="g.step"))
    mesh = p.add(MeshRecipe(name="M"))
    comp = p.add(
        Component(
            name="Joint",
            parts=[PartInstance(name="A", geometry=geo.id)],
            mesh=mesh.id,
            connectors=[
                BeamFastener(name="Bolt 1", source="A/H1", target="A/H2", type=btype.id),
                BeamFastener(
                    name="Bolt 2",
                    source="A/H3",
                    target="A/H4",
                    type=btype.id,
                    diameter=Quantity(value=12, unit="mm"),
                    material=steel.id,
                    ends="rigid",
                    pretension=Quantity(value=1, unit="kN"),
                ),
            ],
        )
    )
    return p, btype, comp, grade, steel


def test_a_bolt_takes_its_types_values_unless_it_sets_its_own():
    p, btype, comp, grade, steel = _project()
    one, two = (bolts.resolve(p, c) for c in comp.connectors)
    assert one.diameter.to_si() == pytest.approx(0.010)
    assert one.material == grade.id
    assert (one.ends, one.formulation) == ("distributing", "beam")
    assert one.pretension.to_si() == pytest.approx(0.7 * 640e6 * 58.0e-6, rel=2e-3)
    assert two.diameter.to_si() == pytest.approx(0.012)  # its own values win
    assert two.material == steel.id and two.ends == "rigid"
    assert two.pretension.to_si() == pytest.approx(1000)
    solved = bolts.resolved(p).get(comp.id, Component)
    assert solved.connectors[0].diameter.to_si() == pytest.approx(0.010)


def test_torque_and_force_preloads_and_none():
    p, btype, comp, *_ = _project()
    torque = btype.model_copy(update={"preload": "torque",
                                      "torque": Quantity(value=50, unit="N*m")})  # fmt: skip
    p.objects[btype.id] = torque
    assert bolts.resolve(p, comp.connectors[0]).pretension.to_si() == pytest.approx(25e3)
    p.objects[btype.id] = btype.model_copy(update={"preload": "none"})
    assert bolts.resolve(p, comp.connectors[0]).pretension is None


def test_a_bolt_without_a_type_needs_its_own_size():
    with pytest.raises(ValueError, match="diameter and a material"):
        BeamFastener(name="B", source="A/x", target="A/y")
    with pytest.raises(ValueError, match="needs its force"):
        BoltType(name="T", diameter=Quantity(value=8, unit="mm"),
                 material="00000000-0000-0000-0000-000000000000", preload="force")  # fmt: skip


def test_changing_a_type_stales_its_bolts():
    p, btype, comp, *_ = _project()
    before = content_hashes(p)[comp.id]
    p.objects[btype.id] = btype.model_copy(update={"preload_percent": 60.0})
    assert content_hashes(p)[comp.id] != before


def test_the_outline_names_types():
    p, *_ = _project()
    text = outline(p)
    assert "Bolt type M10 8.8: M10 (iso_coarse), ISO 898-1 Class 8.8, preload 70 % of yield" in text
    assert "(type M10 8.8)" in text and "diameter 10 mm" in text
