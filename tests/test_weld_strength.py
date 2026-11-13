"""AISC 360-16 J2 for light welds (ADR 0078 step 2), against the Manual's own closed
forms: a 1/16 in E70 fillet carries φR_n/l = 1.392 kip/in along its axis (Manual Eq.
8-2a, 0.75 × 0.60 × 70 × 0.707 / 16) and 1.5 times that across it (θ = 90°, Eq. J2-5)."""

import numpy as np
import pytest

from puzzle.model.schema import Quantity, Weld
from puzzle.weld_strength import OMEGA, PHI, check, directional_factor, throat

KIP_PER_IN = 4448.2216 / 0.0254  # N/m
KSI = 6.894757e6


def _weld(leg_in, leg2_in=None, **kw):
    q = lambda v: Quantity(value=v, unit="inch")  # noqa: E731
    return Weld(name="W", path="P/e", faces=["P/a", "Q/b"], leg=q(leg_in),
                leg2=q(leg2_in) if leg2_in else None, **kw)  # fmt: skip


def test_the_manuals_sixteenth_of_an_inch():
    w = _weld(1 / 16)
    got = check(w, 70 * KSI, [1.0], [0.0], [1.0])
    per = PHI * got["nominal_N_per_m"][0] / KIP_PER_IN
    assert per == pytest.approx(1.392, abs=5e-4)
    across = check(w, 70 * KSI, [0.0], [1.0], [1.0], directional=True)
    assert PHI * across["nominal_N_per_m"][0] / KIP_PER_IN == pytest.approx(1.5 * 1.392, abs=1e-3)


def test_throats_and_the_directional_factor():
    assert throat(_weld(0.25)) == pytest.approx(0.25 * 0.0254 / np.sqrt(2))
    assert throat(_weld(0.25, 0.5)) == pytest.approx(0.25 * 0.5 / np.hypot(0.25, 0.5) * 0.0254)
    assert directional_factor(np.array([1.0]), np.array([1.0]))[0] == pytest.approx(
        1 + 0.5 * np.sin(np.pi / 4) ** 1.5
    )


def test_utilisations_for_lrfd_and_asd():
    w = _weld(5 / 16)
    rn = 0.6 * 70 * KSI * throat(w)  # N/m
    got = check(w, 70 * KSI, [1.0, 1.0], [0.0, 0.0], [0.5 * PHI * rn, 0.25 * PHI * rn])
    assert got["governing"] == 0
    assert got["utilisation_lrfd"][0] == pytest.approx(0.5)
    assert got["utilisation_asd"][0] == pytest.approx(0.5 * PHI * OMEGA)


def test_cjp_is_its_base_metals():
    got = check(_weld(0.5, type="groove", groove="cjp"), 70 * KSI, [1.0], [0.0], [1.0])
    assert not got["checked"] and "base metal" in got["why"]


def test_the_filler_is_the_welds_or_its_base_metals_match():
    from uuid import uuid4

    from puzzle.materials import add_library_material
    from puzzle.model.schema import Component, MaterialAssignment, PartInstance, Project
    from puzzle.weld_strength import filler

    p = Project(name="w")

    def joint(material):
        m = add_library_material(p, material)
        parts = [PartInstance(name="P", geometry=uuid4())]
        assigned = [MaterialAssignment(material=m.id, selection="P/Body")]
        return Component(name="C", mesh=uuid4(), parts=parts, materials=assigned)

    assert filler(p, joint("structural_steel"), _weld(0.25))[:2] == ("E70XX", 482.6e6)
    # stainless: AISC 370-21 J2 with the base metal's matching filler (ADR 0078 step 3)
    assert filler(p, joint("stainless_steel_304"), _weld(0.25)) == (
        "E308L",
        517.1e6,
        "AISC 370-21 J2",
    )
    assert filler(p, joint("duplex_2205"), _weld(0.25))[0] == "E2209"
    e80 = add_library_material(p, "filler_e80xx")
    own = filler(p, joint("stainless_steel_304"), _weld(0.25, filler=e80.id))
    assert own[0] == "E80XX electrode" and own[1] == pytest.approx(551.6e6)


# --- the instantaneous centre of rotation (AISC 360-10 J2.4(b)) --------------------------

KSI_, IN_ = 6.894757e6, 0.0254


def _line(x, n=400, length=10 * IN_):
    ys = (np.arange(n) + 0.5) / n * length - length / 2
    return np.column_stack([np.full(n, x), ys]), np.tile([0.0, 1.0], (n, 1)), np.full(n, length / n)


def test_ic_concentric_two_lines_is_the_manuals_c():
    """Two vertical lines loaded along them through the centroid: C = 2 × 0.60 × 70 ×
    0.707 / 16 = 3.71 kips per sixteenth per inch (Manual Table 8-4's a = 0 entry)."""
    from puzzle import weld_strength as ws

    (p1, a1, l1), (p2, a2, l2) = _line(-3 * IN_), _line(3 * IN_)
    pts, axes, lens = np.vstack([p1, p2]), np.vstack([a1, a2]), np.concatenate([l1, l2])
    w = IN_ / 16
    out = ws.ic_capacity(pts, axes, lens, 70 * KSI_, 0.707 * w, w, [0, -1], [0, 0])
    assert out["Pn_N"] / 4448.2216 / 10 == pytest.approx(2 * 0.6 * 70 * 0.707 / 16, rel=2e-3)


def test_ic_pure_moment_on_a_line_against_its_integral():
    """A vertical line L in pure moment turns about its middle: every element loaded
    across its axis (θ = 90°), Δ_i = Δ_u r / (L/2), so M_n = 2 ∫ 0.6 F_EXX 1.5 f(p(r)) t_e r
    dr from 0 to L/2: integrated here independently of the code's elements."""
    from scipy.integrate import quad

    from puzzle import weld_strength as ws

    length, w = 10 * IN_, IN_ / 16
    te, fexx = 0.707 * w, 70 * KSI_
    dm = 0.209 * 92**-0.32 * w
    du = min(1.087 * 96**-0.65 * w, 0.17 * w)
    stress = lambda r: 0.6 * fexx * 1.5 * ws._fp(du * r / (length / 2) / dm)  # noqa: E731
    exact = 2 * quad(lambda r: float(stress(r)) * te * r, 0, length / 2)[0]
    pts, axes, lens = _line(0.0, n=2000)
    got = ws.ic_moment_capacity(pts, axes, lens, fexx, te, w)
    assert got == pytest.approx(exact, rel=1e-3)


def test_ic_far_eccentricity_tends_to_the_moment_capacity():
    """P_n e approaches M_n as the load goes far out: the IC goes to the centroid."""
    from puzzle import weld_strength as ws

    pts, axes, lens = _line(0.0)
    w = IN_ / 16
    mn = ws.ic_moment_capacity(pts, axes, lens, 70 * KSI_, 0.707 * w, w)
    e = 500 * IN_
    out = ws.ic_capacity(pts, axes, lens, 70 * KSI_, 0.707 * w, w, [0, -1], [e, 0])
    assert out["Pn_N"] * e == pytest.approx(mn, rel=0.01)


def test_ic_eccentricity_lowers_the_strength_monotonically():
    from puzzle import weld_strength as ws

    (p1, a1, l1), (p2, a2, l2) = _line(-3 * IN_), _line(3 * IN_)
    pts, axes, lens = np.vstack([p1, p2]), np.vstack([a1, a2]), np.concatenate([l1, l2])
    w = IN_ / 16
    c = [ws.ic_capacity(pts, axes, lens, 70 * KSI_, 0.707 * w, w, [0, -1], [a * 10 * IN_, 0])
         ["Pn_N"] for a in (0.0, 0.1, 0.5, 1.0, 2.0)]  # fmt: skip
    assert all(x > y for x, y in zip(c, c[1:], strict=False))


def test_ic_balances_an_unsymmetric_group():
    """An L-shaped group (a vertical line and a horizontal one): the IC found in 2D, its
    elements' forces balancing the load (residual nil)."""
    from puzzle import weld_strength as ws

    pv, av, lv = _line(0.0)
    n = 300
    xs = (np.arange(n) + 0.5) / n * 6 * IN_
    ph = np.column_stack([xs, np.full(n, -5 * IN_)])
    pts = np.vstack([pv, ph])
    axes = np.vstack([av, np.tile([1.0, 0.0], (n, 1))])
    lens = np.concatenate([lv, np.full(n, 6 * IN_ / n)])
    w = IN_ / 16
    out = ws.ic_capacity(pts, axes, lens, 70 * KSI_, 0.707 * w, w, [0, -1], [12 * IN_, 0])
    assert out["method"] == "instantaneous centre" and out["residual"] < 1e-6
    assert 0 < out["Pn_N"] < ws.ic_capacity(pts, axes, lens, 70 * KSI_, 0.707 * w, w, [0, -1],
                                            [1 * IN_, 0])["Pn_N"]  # fmt: skip


def test_a_stainless_weld_by_aisc_370_design_guide_27_example_9():
    """Design Guide 27 (2nd ed.) Example 9: S30400 end plate, quarter-inch fillets, Group B
    filler F_EXX 75 ksi (our E308L), two lines of 11.5 in.: F_nw 45.0 ksi, A_we 4.07 in²,
    R_n 183 kips, φR_n 137, R_n/Ω 91.5."""
    from puzzle import weld_strength as ws

    weld = _weld(0.25)
    fexx = ws.STAINLESS_FEXX["E308L"]
    assert fexx / KSI_ == pytest.approx(75, abs=0.01)
    got = ws.check(weld, fexx, [1.0], [0.0], [1.0])
    per_length = got["nominal_N_per_m"][0] * IN_ / 4448.2216  # kips per inch, one line
    assert 0.6 * fexx / KSI_ == pytest.approx(45.0, abs=0.01)
    rn = per_length * 2 * 11.5
    assert rn == pytest.approx(183, abs=0.5)
    assert 0.75 * rn == pytest.approx(137, abs=0.5) and rn / 2.0 == pytest.approx(91.5, abs=0.3)


def test_fatigue_allowable_ranges_by_appendix_3():
    """Eq. A-3-1 at 2 million cycles: category B 1000 (12/2e6)^0.333 = 18.2 ksi, C 13.1,
    E 8.2; A at its threshold 24 ksi; F (A-3-2) 100 (1.5/2e6)^0.167 = 9.5 ksi; the
    thresholds past them."""
    from puzzle.weld_strength import allowable_range

    ksi = 6.894757e6
    assert allowable_range("B", 2e6) / ksi == pytest.approx(18.2, abs=0.1)
    assert allowable_range("F", 2e6) / ksi == pytest.approx(9.5, abs=0.05)
    for cat, cf in (("B", 12.0), ("C", 4.4), ("E", 1.1)):
        assert allowable_range(cat, 2e6) / 1e6 == pytest.approx(6900 * (cf / 2e6) ** 0.333)
    assert allowable_range("A", 2e6) == pytest.approx(165e6)  # the threshold governs
    assert allowable_range("F", 2e6) / 1e6 == pytest.approx(690 * (1.5 / 2e6) ** 0.167)
    assert allowable_range("E'", 1e10) == pytest.approx(18e6)
    with pytest.raises(ValueError, match="category"):
        allowable_range("Z", 1e6)


def test_an_aluminium_weld_by_the_adm_example_27():
    """ADM 2015 Part VII Example 27: 3/8 in. fillets on 6061-T6 with 4043, 16 in. of weld:
    the base metal 0.6 F_tuw S_w L = 86.4 k, the weld metal 0.6 (0.85 F_tuw) S_we L = 51.7 k
    (the example rounds F_nw to 12.2 ksi), the lesser governing, P = R_n/Ω = 26.5 k."""
    from puzzle import weld_strength as ws

    weld = _weld(0.375)
    ksi, kip, inch = 6.894757e6, 4448.2216, 0.0254
    got = ws.check_aluminium(weld, 24 * ksi, 24 * ksi, [1.0], [0.0], [1.0])  # 4043, in ksi
    assert got["governed_by"] == "weld metal"
    rn = got["nominal_N_per_m"][0] * inch / kip * 16
    assert rn == pytest.approx(0.6 * 0.85 * 24 * 0.707 * 0.375 * 16, rel=2e-3)
    assert rn == pytest.approx(51.7, rel=5e-3)  # the example rounds F_nw to 12.2 ksi
    assert rn / ws.ADM_OMEGA == pytest.approx(26.5, abs=0.15)
    base = 0.6 * 24 * 0.375 * 16
    assert base == pytest.approx(86.4)
    transverse = ws.check_aluminium(weld, 24 * ksi, 24 * ksi, [0.0], [1.0],
                                    [1.0], directional=True)  # fmt: skip
    assert transverse["nominal_N_per_m"][0] == pytest.approx(
        min(1.36 * got["nominal_N_per_m"][0], base * kip / 16 / inch), rel=1e-6)  # fmt: skip


def test_an_aluminium_joint_takes_4043_by_the_adm():
    from uuid import uuid4

    from puzzle.materials import add_library_material
    from puzzle.model.schema import Component, MaterialAssignment, PartInstance, Project
    from puzzle.weld_strength import ADM, filler, welded_base_strength

    p = Project(name="w")
    m = add_library_material(p, "aluminium_6061_t6")
    comp = Component(name="C", mesh=uuid4(), parts=[PartInstance(name="P", geometry=uuid4())],
                     materials=[MaterialAssignment(material=m.id, selection="P/Body")])  # fmt: skip
    w = _weld(0.25)
    w = w.model_copy(update={"faces": ["P/a", "P/b"]})
    assert filler(p, comp, w) == ("ER4043", 165e6, ADM)
    assert welded_base_strength(p, comp, w) == pytest.approx(165e6)


# AISC Steel Construction Manual, 14th ed., Table 8-4 (angle 0°): C for two vertical lines
# l long, kl apart, a vertical load at a l from the centroid (E70, per sixteenth per inch).
TABLE_8_4 = [  # (k, a, C)
    (0.0, 0.10, 3.72), (0.0, 0.50, 2.30), (0.0, 1.00, 1.28), (0.0, 2.00, 0.655),
    (0.6, 0.10, 3.67), (0.6, 0.50, 2.48), (0.6, 1.00, 1.56), (0.6, 2.00, 0.867),
    (1.0, 0.50, 2.60), (1.0, 1.00, 1.77), (1.6, 1.00, 2.00), (2.0, 1.00, 2.10), (2.0, 3.00, 1.05),
]  # fmt: skip


@pytest.mark.parametrize(("k", "a", "c"), TABLE_8_4)
def test_ic_against_the_manuals_table_8_4(k, a, c):
    """The instantaneous centre against the Manual's own tabulation of it (14th ed.
    Table 8-4), to its printed digits; k = 0 is one line taken twice."""
    from puzzle import weld_strength as ws

    length = 10 * IN_
    (p1, a1, l1), (p2, a2, l2) = _line(-k * length / 2), _line(k * length / 2)
    pts, axes, lens = np.vstack([p1, p2]), np.vstack([a1, a2]), np.concatenate([l1, l2])
    w = IN_ / 16
    out = ws.ic_capacity(pts, axes, lens, 70 * KSI_, 0.707 * w, w, [0, -1], [a * length, 0])
    got = out["Pn_N"] / 4448.2216 / 10
    assert got == pytest.approx(c, abs=0.006 if c < 1 else 0.012), (k, a, got)


def test_asme_structural_stress_by_hand():
    """ASME VIII-2 5.5.5 worked by hand: Δσm 100 MPa, Δσb 50 MPa, t 20 mm, steel, -3σ.
    R_b = 1/3; I^(1/m) = (1.23 - 0.364/3 - 0.17/9) / (1.007 - 0.306/3 - 0.178/9)
    = 1.08978 / 0.88522 = 1.23109; Eq. (5.58) 150 / (1 - 0.09) = 164.835 MPa;
    t_ess^((2 - 3.6)/7.2) = 20^-0.22222 = 0.51390; ΔS_ess = 164.835 / (0.51390 × 1.23109)
    = 260.55; N = (11577.9 / 260.55)^(1/0.3195) = 1.4365e5."""
    from puzzle.weld_strength import asme_cycles, t_ess

    got = asme_cycles(100e6, 50e6, 0.020)
    assert got["I_factor"] == pytest.approx(1.23109, rel=2e-5)
    assert got["structural_stress_range_Pa"] == pytest.approx(164.835e6, rel=1e-5)
    assert got["equivalent_range"] == pytest.approx(260.55, rel=1e-4)
    assert got["cycles"] == pytest.approx(1.4365e5, rel=1e-3)
    assert (t_ess(10), t_ess(40), t_ess(200)) == (16.0, 40.0, 150.0)
    # pure membrane, thin plate: I = 1.23/1.007, t_ess 16 mm
    thin = asme_cycles(100e6, 0.0, 0.008)
    expected = 100 / 0.91 / (16 ** (-1.6 / 7.2) * 1.23 / 1.007)
    assert thin["equivalent_range"] == pytest.approx(expected, rel=1e-9)
    # a stiff hysteresis curve (no plasticity) leaves Neuber's rule elastic
    stiff = asme_cycles(100e6, 50e6, 0.020, k_css=1e15, n_css=0.1)
    assert stiff["cycles"] == pytest.approx(got["cycles"], rel=1e-9)
    # a mean stress over half the yield with R > 0: f_M = (1 - R)^(1/3.6)
    mean = asme_cycles(100e6, 0.0, 0.020, smax=300e6, smin=200e6, sy=250e6)
    assert mean["f_M"] == pytest.approx((1 - 2 / 3) ** (1 / 3.6))
    # the mean curve gives more cycles than the design (-3σ) one, by (19930.2/11577.9)^(1/h)
    ratio = asme_cycles(100e6, 50e6, 0.020, basis="mean")["cycles"] / got["cycles"]
    assert ratio == pytest.approx((19930.2 / 11577.9) ** (1 / 0.3195), rel=1e-9)
