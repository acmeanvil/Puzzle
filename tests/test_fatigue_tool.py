"""The fatigue tool (ADR 0026) on hand-made results, against `materials.fatigue` by hand.

A bar of steel (Sut 690 MPa, Sy 600 MPa) carries uniaxial stress: 400 MPa at node 1,
300 MPa at node 2, 100 MPa at node 3. Reversed, a polished, size-free part has
Se = 0.5 Sut = 345 MPa, so node 1 has a finite life, nodes 2 and 3 infinite life; their
safety factors are Se/σ (the stress that just reaches the design life of 10⁶ cycles).
"""

from datetime import datetime
from uuid import uuid4

import numpy as np
import pytest

from puzzle.errors import PuzzleError
from puzzle.materials import fatigue as fm
from puzzle.mesh import Mesh
from puzzle.mesh.mesh import Group
from puzzle.model.schema import (
    Component,
    FatigueBlock,
    FatigueSettings,
    Material,
    MaterialAssignment,
    PartInstance,
    Project,
    Quantity,
    ResultCase,
    ResultDefinition,
    SnCurve,
    Study,
)
from puzzle.post import derived
from puzzle.results.model import Field, Results, StepResult
from puzzle.results.store import RunRecord

IDS = np.array([1, 2, 3])
SXX = np.array([400e6, 300e6, 100e6])


def results(sxx, syy=None, exx=None, sxy=None):
    z = np.zeros(3)
    stress = np.stack([sxx, z if syy is None else syy, z, z if sxy is None else sxy, z, z], axis=1)
    fields = {"stress": Field("stress", "node", ["xx", "yy", "zz", "xy", "yz", "zx"], IDS, stress)}
    if exx is not None:
        strain = np.stack([exx, -0.3 * exx, -0.3 * exx, z, z, z], axis=1)
        fields["strain"] = Field(
            "strain", "node", ["xx", "yy", "zz", "xy", "yz", "zx"], IDS, strain
        )
    return Results("calculix", [StepResult(1, "stationary", 1.0, fields=fields)])


class Store:
    def __init__(self, study_ids, results_by_study):
        self.records = [
            RunRecord(
                f"run-{i}",
                "k",
                str(sid),
                "S",
                "calculix",
                "native",
                "m",
                "completed",
                datetime(2026, 10, 1 + i),
                1.0,
                1,
                datetime(2026, 10, 1 + i),
            )
            for i, sid in enumerate(study_ids)
        ]
        self.by_run = {f"run-{i}": r for i, r in enumerate(results_by_study)}

    def runs(self):
        return self.records

    def read_results(self, run_id):
        return self.by_run[run_id]

    def read_mesh(self, _):
        m = Mesh(IDS.copy(), np.array([[0.0, 0, 0], [1, 0, 0], [2, 0, 0]]))
        m.groups["Bar/Body"] = Group("Bar/Body", 3, [(3, 1)], IDS.copy())
        return m


def project(**props):
    p = Project("p")
    steel = Material(
        name="Steel",
        properties={
            "tensile_strength": Quantity(value=690, unit="MPa"),
            "yield_strength": Quantity(value=600, unit="MPa"),
            "youngs_modulus": Quantity(value=200, unit="GPa"),
        }
        | props,
    )
    p.add(steel)
    comp = p.add(
        Component(
            name="Bar",
            parts=[PartInstance(name="Bar", geometry=uuid4())],
            mesh=uuid4(),
            materials=[MaterialAssignment(material=steel.id, selection="Bar/Body")],
        )
    )
    studies = [
        p.add(Study(name=f"Case {i}", component=comp.id, solver="calculix", steps=[]))
        for i in (1, 2)
    ]
    return p, steel, studies


def tool(p, blocks, **settings):
    s = FatigueSettings(blocks=blocks, surface="polished", **settings)
    return p.add(ResultDefinition(name="Fatigue", method="fatigue", fatigue=s))


def field(d, name):
    return d.results.steps[0].fields[name].values[:, 0]


def test_fully_reversed_on_shigleys_line():
    p, _, (a, _) = project()
    store = Store([a.id], [results(SXX)])
    d = derived.evaluate(p, store, tool(p, [FatigueBlock(case=ResultCase(study=a.id))]))
    line = fm.SnLine(345e6, 690e6)
    life = field(d, "fatigue_life")
    assert life[0] == pytest.approx(float(line.cycles(400e6)), rel=1e-9)
    assert (life[1:] == 1e12).all()  # infinite, shown capped
    sf = field(d, "fatigue_safety_factor")
    assert sf[1:] == pytest.approx([345 / 300, 345 / 100], rel=1e-6)
    assert field(d, "fatigue_damage")[0] == pytest.approx(1e6 / life[0], rel=1e-9)
    assert [i.label for i in d.items][:3] == [
        "Life (cycles)",
        "Damage at the design life (1e+06)",
        "Safety factor",
    ]
    assert any("shown as 1e+12" in w for w in d.warnings)


def test_zero_based_takes_a_mean_and_goodman_corrects_it():
    p, _, (a, _) = project()
    store = Store([a.id], [results(np.array([600e6, 300e6, 100e6]))])
    d = derived.evaluate(
        p, store, tool(p, [FatigueBlock(loading="zero_based", case=ResultCase(study=a.id))])
    )
    sa, sm = 300e6, 300e6  # reversed 530.8 MPa, above Se = 345 MPa
    rev = sa / (1 - sm / 690e6)
    expect = float(fm.SnLine(345e6, 690e6).cycles(rev))
    assert field(d, "fatigue_alternating")[0] == pytest.approx(sa)
    assert field(d, "fatigue_mean")[0] == pytest.approx(sm)
    assert field(d, "fatigue_life")[0] == pytest.approx(expect, rel=1e-9)


def test_compression_counts_as_compression_with_signed_von_mises():
    p, _, (a, _) = project()
    store = Store([a.id], [results(-SXX)])
    d = derived.evaluate(
        p, store, tool(p, [FatigueBlock(loading="zero_based", case=ResultCase(study=a.id))])
    )
    assert (field(d, "fatigue_mean") < 0).all()
    # A compressive mean gets no benefit: the same life as fully reversed at σa.
    expect = float(fm.SnLine(345e6, 690e6).cycles(200e6))
    assert np.isinf(expect) and field(d, "fatigue_life")[0] == 1e12


def test_range_combination_and_a_duty_cycle_by_miner():
    """Case 1 − case 2 as the range; a second block repeats case 1 reversed 9 times."""
    p, _, (a, b) = project()
    store = Store([a.id, b.id], [results(SXX), results(-SXX)])
    rng = p.add(
        ResultDefinition(
            name="Swing",
            method="range",
            cases=[ResultCase(study=a.id), ResultCase(study=b.id)],
        )
    )
    blocks = [
        FatigueBlock(loading="range", combination=rng.id),
        FatigueBlock(case=ResultCase(study=a.id), scale=0.5, cycles=9),
    ]
    d = derived.evaluate(p, store, tool(p, blocks))
    line = fm.SnLine(345e6, 690e6)
    # The range of ±400 MPa is 800: amplitude 400, mean 0. Block 2: 200 MPa reversed.
    per_duty = 1 / float(line.cycles(400e6)) + 9 / float(line.cycles(200e6))
    assert field(d, "fatigue_life")[0] == pytest.approx(1 / per_duty, rel=1e-9)
    assert d.items[0].label == "Life (repetitions of the duty)"


def test_measured_curve_strain_life_and_missing_data():
    p, steel, (a, _) = project()
    steel.fatigue.sn_curves.append(SnCurve(name="R=-1", points=[(1e3, 500e6), (1e6, 250e6)]))
    store = Store([a.id], [results(SXX, exx=SXX / 200e9)])
    d = derived.evaluate(
        p, store, tool(p, [FatigueBlock(case=ResultCase(study=a.id))], life_model="sn_curve")
    )
    life = field(d, "fatigue_life")
    assert life[0] == pytest.approx(float(fm.cycles_on_curve([(1e3, 500e6), (1e6, 250e6)], 400e6)))

    from puzzle.model.schema import StrainLifeModel

    steel.fatigue.strain_life = StrainLifeModel(
        model="langer", langer_a=6.891, langer_b=1.920, langer_c=0.112
    )
    d = derived.evaluate(
        p, store, tool(p, [FatigueBlock(case=ResultCase(study=a.id))], life_model="strain_life")
    )
    strain = np.array([[400e6 / 200e9, -0.3 * 2e-3, -0.3 * 2e-3, 0, 0, 0]])
    from puzzle.post.fatigue_tool import _equivalent_strain

    ea = _equivalent_strain(strain)[0]
    langer = fm.StrainLife("langer", langer_a=6.891, langer_b=1.920, langer_c=0.112)
    assert field(d, "fatigue_life")[0] == pytest.approx(float(langer.cycles(ea)), rel=1e-9)

    bare, _, (c, _) = project()
    bare.objects[next(iter(bare.objects))].properties.pop("tensile_strength")
    with pytest.raises(PuzzleError, match="needs its tensile strength"):
        derived.evaluate(
            bare,
            Store([c.id], [results(SXX)]),
            tool(bare, [FatigueBlock(case=ResultCase(study=c.id))]),
        )


def test_non_proportional_loading_is_said():
    """Case 1 pulls along x, case 2 shears: the range's principal axis is 13° off x, the
    mean's 22.5° the other way."""
    p, _, (a, b) = project()
    z = np.zeros(3)
    store = Store([a.id, b.id], [results(SXX), results(z, sxy=SXX / 2)])
    rng = p.add(
        ResultDefinition(
            name="Swing", method="range", cases=[ResultCase(study=a.id), ResultCase(study=b.id)]
        )
    )
    d = derived.evaluate(p, store, tool(p, [FatigueBlock(loading="range", combination=rng.id)]))
    assert any("non-proportional" in w for w in d.warnings)


def test_a_notch_factor_raises_the_alternating_stress():
    """Kf = 1.5 on 300 MPa reversed is 450 MPa on the line (finite, where 300 was not)."""
    p, _, (a, _) = project()
    store = Store([a.id], [results(SXX)])
    d = derived.evaluate(
        p, store, tool(p, [FatigueBlock(case=ResultCase(study=a.id))], notch_factor=1.5)
    )
    line = fm.SnLine(345e6, 690e6)
    assert field(d, "fatigue_life")[1] == pytest.approx(float(line.cycles(450e6)), rel=1e-9)
    assert field(d, "fatigue_alternating")[1] == pytest.approx(450e6)
    assert field(d, "fatigue_safety_factor")[2] == pytest.approx(345 / 150, rel=1e-6)
    assert d.items[3].label == "Alternating stress × Kf"


def test_neuber_gives_kf_from_kt_and_the_radius():
    """Shigley Ex. 6-6: Sut 690 MPa, r = 3 mm, Kt = 1.65 → Kf = 1.55; zero-based, Kf on
    the alternating stress only unless asked for the mean too."""
    p, _, (a, _) = project()
    store = Store([a.id], [results(np.array([600e6, 300e6, 100e6]))])
    block = [FatigueBlock(loading="zero_based", case=ResultCase(study=a.id))]
    neuber = {"notch_from": "neuber", "notch_kt": 1.65, "notch_radius": 0.003}
    d = derived.evaluate(p, store, tool(p, block, **neuber))
    kf = fm.notch_factor(1.65, fm.notch_sensitivity(690e6, 0.003))
    assert kf == pytest.approx(1.55, abs=5e-3)
    assert field(d, "fatigue_alternating")[0] == pytest.approx(300e6 * kf)
    assert field(d, "fatigue_mean")[0] == pytest.approx(300e6)  # not on the mean
    assert any("Kf = 1.55" in w for w in d.warnings)
    rev = 300e6 * kf / (1 - 300e6 / 690e6)
    expect = float(fm.SnLine(345e6, 690e6).cycles(rev))
    assert field(d, "fatigue_life")[0] == pytest.approx(expect, rel=1e-9)
    d = derived.evaluate(p, store, tool(p, block, notch_on_mean=True, **neuber))
    assert field(d, "fatigue_mean")[0] == pytest.approx(300e6 * kf)
    with pytest.raises(ValueError, match="needs Kt and the notch radius"):
        FatigueSettings(blocks=block, notch_from="neuber", notch_kt=2.0)
