"""ASCE 7-16 seismic procedures (ADR 0079 step 4), worked by hand from the equations:
the design spectrum (Figure 11.4-1), C_s and which equation governs (12.8-2 to 12.8-6),
C_u (Table 12.8-1), T_a (12.8-7), k (12.8.3), the vertical distribution, CQC, drift."""

import math

import pytest

from puzzle.frames import seismic
from puzzle.model.schema import SeismicDesign


def _design(**kw):
    values = dict(sds=1.0, sd1=0.6, s1=0.75, tl=8.0, r=8.0, cd=5.5, ie=1.0)
    values.update(kw)
    return SeismicDesign(**values)


def test_the_design_spectrum():
    sds, sd1, tl = 1.0, 0.6, 8.0
    ts, t0 = 0.6, 0.12
    assert seismic.spectrum(0.0, sds, sd1, tl) == pytest.approx(0.4)
    assert seismic.spectrum(t0 / 2, sds, sd1, tl) == pytest.approx(0.7)
    assert seismic.spectrum(0.3, sds, sd1, tl) == pytest.approx(1.0)
    assert seismic.spectrum(ts, sds, sd1, tl) == pytest.approx(1.0)
    assert seismic.spectrum(1.2, sds, sd1, tl) == pytest.approx(0.5)
    assert seismic.spectrum(10.0, sds, sd1, tl) == pytest.approx(0.6 * 8 / 100)


@pytest.mark.parametrize(("t", "cs", "clause"), [
    (0.4, 1.0 / 8, "12.8-2"),            # S_DS / (R/I_e)
    (1.0, 0.6 / 8, "12.8-3"),            # capped: S_D1 / (T R/I_e)
    (3.0, 0.5 * 0.75 / 8, "12.8-6"),     # 0.6/24 = 0.025 below 0.044 and 0.0469: 12.8-6
])  # fmt: skip
def test_the_seismic_coefficient(t, cs, clause):
    got, which = seismic.seismic_coefficient(_design(), t)
    assert got == pytest.approx(cs)
    assert which == clause


def test_the_minimum_without_large_s1():
    got, which = seismic.seismic_coefficient(_design(s1=0.3), 3.0)
    assert (got, which) == (pytest.approx(0.044), "12.8-5")


def test_period_coefficients():
    assert seismic.cu(0.4) == 1.4 and seismic.cu(0.1) == 1.7
    assert seismic.cu(0.25) == pytest.approx(1.45)
    assert seismic.approximate_period("steel_moment_frame", 10.0) == pytest.approx(0.0724 * 10**0.8)
    assert seismic.exponent(0.4) == 1.0 and seismic.exponent(3.0) == 2.0
    assert seismic.exponent(1.5) == pytest.approx(1.5)
    t, note = seismic.period_used(_design(structure="steel_moment_frame"), 10.0, 5.0)
    assert t == pytest.approx(1.4 * 0.0724 * 10**0.8) and "capped" in note


def test_the_vertical_distribution():
    """Two levels, 30 m and 60 m, 100 kN each, T = 1.4 s (under C_u T_a = 1.4 x 0.0488 x
    60^0.75 = 1.47 s), so k = 1 + (1.4 - 0.5) / 2 = 1.45: C_vx = w h^k / Σ w h^k."""
    d = _design(period=1.4)
    levels = [{"height": 30.0, "weight": 1e5}, {"height": 60.0, "weight": 1e5}]
    out = seismic.elf(d, levels, 60.0)
    assert out.period == pytest.approx(1.4)
    assert out.k == pytest.approx(1.45)
    cs, _ = seismic.seismic_coefficient(d, 1.4)
    v = cs * 2e5
    a, b = 30.0**1.45, 60.0**1.45
    assert out.base_shear == pytest.approx(v)
    assert [lv["force"] for lv in out.levels] == pytest.approx([v * a / (a + b), v * b / (a + b)])
    assert out.levels[0]["shear"] == pytest.approx(v)
    assert out.levels[1]["shear"] == pytest.approx(v * b / (a + b))


def test_cqc():
    assert seismic.correlation(10.0, 10.0) == pytest.approx(1.0)
    far = seismic.cqc([3.0, 4.0], [1.0, 10.0])
    assert far == pytest.approx(5.0, rel=1e-3)  # SRSS when the modes are apart
    near = seismic.cqc([3.0, 4.0], [10.0, 10.0])
    assert near == pytest.approx(7.0)  # one mode twice: added


def test_drift_at_the_centres_of_mass():
    d = _design(cd=5.5, ie=1.25, risk_category="III")
    levels = [{"height": 3.0, "nodes": ["a", "b"], "node_weights": {"a": 1.0, "b": 3.0}},
              {"height": 6.0, "nodes": ["c"], "node_weights": {"c": 1.0}}]  # fmt: skip
    out = seismic.drifts(levels, {"a": 0.002, "b": 0.004, "c": 0.010}, d)
    first = 5.5 * (0.25 * 0.002 + 0.75 * 0.004) / 1.25
    assert out[0]["drift_m"] == pytest.approx(first)
    assert out[1]["drift_m"] == pytest.approx(5.5 * 0.010 / 1.25 - first)
    assert out[0]["allowed_m"] == pytest.approx(0.015 * 3.0)  # Table 12.12-1, category III
    assert math.isclose(out[1]["drift_ratio"], out[1]["drift_m"] / 3.0)


def _two_storeys():
    """Two W14X90 bays' columns at x = 0 and 6 m, floors at 3 and 6 m, the roof's left
    node carrying a 10 kN dead load."""
    from uuid import uuid4

    from puzzle import frames
    from puzzle.model.schema import (
        BeamSection,
        Component,
        Feature,
        Material,
        MaterialAssignment,
        PartInstance,
        Physics,
        Project,
        Quantity,
        Study,
    )

    q = lambda v, u: Quantity(value=v, unit=u)  # noqa: E731
    nodes = {f"{c}{k}": [x, 0.0, 3.0 * k] for c, x in (("A", 0.0), ("B", 6.0)) for k in range(3)}
    members = [[f"{c}{k}{k + 1}", f"{c}{k}", f"{c}{k + 1}"] for c in "AB" for k in range(2)]
    members += [[f"F{k}", f"A{k}", f"B{k}"] for k in (1, 2)]
    p = Project(name="frame")
    geo = p.add(frames.geometry("Frame", nodes, members))
    steel = p.add(Material(name="A992", properties={"density": q(7850, "kg/m^3"),
                                                    "youngs_modulus": q(200, "GPa")}))  # fmt: skip
    features = [Feature(type="fixed", selection=f"F/{n}") for n in ("A0", "B0")]
    features.append(Feature(type="force", selection="F/A2", case="D",
                            values={"z": q(-10e3, "N")}))  # fmt: skip
    comp = p.add(Component(
        name="Frame", parts=[PartInstance(name="F", geometry=geo.id)], mesh=uuid4(),
        materials=[MaterialAssignment(material=steel.id, selection="F")],
        beam_sections=[BeamSection(selection="F", orientation=(0.0, 1.0, 0.0), shape="library",
                                   standard="aisc", designation="W14X90")],
        physics=[Physics(type="solid_mechanics", features=features)]))  # fmt: skip
    study = p.add(Study(name="S", component=comp.id, solver="calculix", steps=[],
                        seismic=_design(s1=0.4)))  # fmt: skip
    return p, study


def test_the_fe_spectrum_takes_the_frames_levels():
    """CalculiX's and Code_Aster's ELF scale (`spectra`) uses the frame's levels, as
    PyNite's: level 1 at 3 m weighs ρ A g (4 × 1.5 + 2 × 3) m, the roof at 6 m ρ A g
    (2 × 1.5 + 2 × 3) m + 10 kN, h_n = 6 m, whatever the mesh's extent; the modal
    base shear's ELF target is C_s W at C_u T_a for h_n = 6 m (a 2 s mode is capped)."""
    import numpy as np

    from puzzle import spectra
    from puzzle.frames.seismic_frame import levels
    from puzzle.mesh import Mesh
    from puzzle.model.schema import SolveStep
    from puzzle.results.model import Field, Results, StepResult
    from puzzle.sections import _table

    p, study = _two_storeys()
    rho_a_g = 7850 * _table("aisc")["W14X90"]["A"] * seismic.G
    mesh = Mesh(np.array([1, 2]), np.array([[0.0, 0, -1.0], [0, 0, 10.0]]))
    lv, height = spectra.elf_levels(p, study, mesh, 1.0)
    assert height == pytest.approx(6.0)
    assert [x["height"] for x in lv] == pytest.approx([3.0, 6.0])
    assert [x["weight"] for x in lv] == pytest.approx([12 * rho_a_g, 9 * rho_a_g + 10e3])
    frame, _, _ = levels(p, p.objects[study.component], study.seismic)
    assert [x["weight"] for x in lv] == pytest.approx([x["weight"] for x in frame])

    def g(name, v):
        return Field(name, "global", ["x", "y", "z"], np.array([0]), np.array([v]))

    disp = Field("displacement", "node", ["x", "y", "z"], np.array([1, 2]), np.ones((2, 3)))
    mode = StepResult(1, "modal", 0.5, mode=1, fields={
        "participation_factor": g("participation_factor", [1.0, 0, 0]),
        "effective_mass": g("effective_mass", [1000.0, 0, 0]),
        "mass_fraction": g("mass_fraction", [1.0, 0, 0]), "displacement": disp})  # fmt: skip
    study.steps = [SolveStep(type="modal", settings={"response_spectrum": True,
                                                     "spectrum_directions": "x"})]  # fmt: skip
    results = Results("calculix", [mode])
    spectra.applied(results, study, mesh, p)
    shear = results.steps[-1].fields["spectrum_base_shear"].values[0]
    t = seismic.cu(0.6) * seismic.approximate_period("other", 6.0)
    cs, _ = seismic.seismic_coefficient(study.seismic, t)
    assert shear[3] == pytest.approx(t)
    assert shear[1] == pytest.approx(cs * (21 * rho_a_g + 10e3))
