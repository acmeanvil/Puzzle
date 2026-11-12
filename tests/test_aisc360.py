"""AISC 360-16 member strengths (ADR 0079 step 3) against the Manual's tabulated values
(15th ed., Fy = 50 ksi, from the specification's equations: Table 3-2's W18X50 and
W14X90, the latter's noncompact flange by F3) and hand calculations in kips and inches
for the HSS, slender-element and interaction branches."""

import math

import pytest

from puzzle.frames import aisc360 as a
from puzzle.frames.aisc360 import NotCovered

KSI, IN, FT, KIP = 6.894757e6, 0.0254, 0.3048, 4448.222
KIPFT = KIP * FT
STEEL = a.Steel(29000 * KSI, 11200 * KSI, 50 * KSI, 65 * KSI)


def _row(name):
    from puzzle.sections import _table

    return _table("aisc")[name]


def test_w18x50_matches_table_3_2():
    p = a._library("aisc", "W18X50")
    assert a.flexure_x(p, STEEL, 0.0, 1.0).available("LRFD") / KIPFT == pytest.approx(379, abs=0.5)
    assert a.flexure_x(p, STEEL, 0.0, 1.0).available("ASD") / KIPFT == pytest.approx(252, abs=0.5)
    notes = a.flexure_x(p, STEEL, 10 * FT, 1.0).notes[0]
    lp, lr = (float(x.split()[1]) for x in notes.split(", ")[:2])
    assert lp / FT == pytest.approx(5.83, abs=0.01)
    assert lr / FT == pytest.approx(16.9, abs=0.1)
    # at L_r, φM_rx = 0.9 (0.7 Fy Sx): 233 kip-ft
    at_lr = a.flexure_x(p, STEEL, lr, 1.0).available("LRFD") / KIPFT
    assert at_lr == pytest.approx(0.9 * 0.7 * 50 * _row("W18X50")["Sx"] / IN**3 / 12, rel=1e-4)
    assert round(at_lr) == 233
    assert a.shear_x(p, STEEL, 5.0).available("LRFD") / KIP == pytest.approx(192, abs=0.5)


def test_w14x90_flange_is_noncompact():
    """Table 3-2's W14X90: φM_px 574 kip-ft, F3-1 with b_f/2t_f 10.2 past λ_p 9.15."""
    p = a._library("aisc", "W14X90")
    f = a.flexure_x(p, STEEL, 0.0, 1.0)
    assert f.clause == "F3.2(a)"
    assert round(f.available("LRFD") / KIPFT) == 574


def test_cb_of_common_diagrams():
    parabola = [4 * x * (1 - x) for x in [k / 40 for k in range(41)]]
    assert a.cb(parabola) == pytest.approx(12.5 / 11, rel=1e-6)
    assert a.cb([1.0] * 9) == pytest.approx(1.0)
    linear = [1 - x for x in [k / 8 for k in range(9)]]  # one end moment, the other zero
    assert a.cb(linear) == pytest.approx(12.5 / (2.5 + 3 * 0.75 + 4 * 0.5 + 3 * 0.25))


def test_compression_e3_and_e4():
    p = a._library("aisc", "W14X90")
    r = _row("W14X90")
    area, ry = r["A"] / IN**2, r["ry"] / IN
    for lc_ft in (20.0, 40.0):  # E3-2 inelastic, then E3-3 elastic (Lc/r past 4.71√(E/Fy))
        fe = math.pi**2 * 29000 / (lc_ft * 12 / ry) ** 2
        fcr = 50 * 0.658 ** (50 / fe) if 50 / fe <= 2.25 else 0.877 * fe
        c = a.compression(p, STEEL, lc_ft * FT, lc_ft * FT, lc_ft * FT)
        assert c.nominal / KIP == pytest.approx(fcr * area, rel=1e-6)
    # a long torsional length with the lateral ones short: E4-2 governs
    c = a.compression(p, STEEL, 5 * FT, 5 * FT, 60 * FT)
    assert c.clause == "E4(a)"
    cw, j, ix, iy = (r[k] / IN**n for k, n in (("Cw", 6), ("J", 4), ("Ix", 4), ("Iy", 4)))
    fe = (math.pi**2 * 29000 * cw / (720**2) + 11200 * j) / (ix + iy)
    assert c.nominal / KIP == pytest.approx(50 * 0.658 ** (50 / fe) * area, rel=1e-6)


def test_slender_hss_wall_in_compression_e7():
    """HSS20X20X5/16: b/t = (20 - 3 t)/t = 62.9 past λ_r = 1.40√(E/Fy) = 33.7; at a short
    length, Fcr = Fy, b_e by E7-3 with c1 0.20, c2 1.38."""
    p = a._library("aisc", "HSS20X20X5/16")
    r = _row("HSS20X20X5/16")
    t, B, area = r["tdes"] / IN, r["B"] / IN, r["A"] / IN**2
    b = B - 3 * t
    lam, lam_r = b / t, 1.40 * math.sqrt(29000 / 50)
    fel = (1.38 * lam_r / lam) ** 2 * 50
    be = b * (1 - 0.20 * math.sqrt(fel / 50)) * math.sqrt(fel / 50)
    ae = area - 4 * (b - be) * t
    c = a.compression(p, STEEL, 0.01, 0.01, 0.01)
    assert "E7" in c.clause
    assert c.nominal / KIP == pytest.approx(50 * ae, rel=1e-4)


def test_hss_flange_local_buckling_f7():
    """HSS8X8X1/4: b/t (8 - 3t)/t = 31.3, between λ_p 27.0 and λ_r 33.7: F7-2."""
    p = a._library("aisc", "HSS8X8X1/4")
    r = _row("HSS8X8X1/4")
    t, z, s = r["tdes"] / IN, r["Zx"] / IN**3, r["Sx"] / IN**3
    lam = (8 - 3 * t) / t
    mp = 50 * z
    mn = min(mp - (mp - 50 * s) * (3.57 * lam * math.sqrt(50 / 29000) - 4.0), mp)
    f = a.flexure_x(p, STEEL, 0.0, 1.0)
    assert f.clause == "F7.2(b)"
    assert f.nominal / (KIP * IN) == pytest.approx(mn, rel=1e-6)


def test_round_hss_and_its_shear_and_torsion():
    p = a._library("aisc", "HSS10.000X0.250")
    r = _row("HSS10.000X0.250")
    d, t = r["OD"] / IN, r["tdes"] / IN
    dt = d / t
    assert 0.07 * 29000 / 50 < dt <= 0.31 * 29000 / 50
    f = a.flexure_x(p, STEEL, 0.0, 1.0)
    assert f.nominal / (KIP * IN) == pytest.approx(
        min((0.021 * 29000 / dt + 50) * r["Sx"] / IN**3, 50 * r["Zx"] / IN**3), rel=1e-6
    )
    assert a.shear_x(p, STEEL, 3.0).clause == "G5"
    assert a.torsion(p, STEEL, 3.0).clause == "H3.1(a)"


def test_interaction_both_branches():
    assert a.interaction(50, 100, 40, 100, 0, 100) == pytest.approx(0.5 + 8 / 9 * 0.4)
    assert a.interaction(10, 100, 40, 100, 20, 100) == pytest.approx(0.05 + 0.6)


def test_weak_axis_flexure_and_shear_of_an_i():
    p = a._library("aisc", "W18X50")
    r = _row("W18X50")
    zy, sy = r["Zy"] / IN**3, r["Sy"] / IN**3
    assert a.flexure_y(p, STEEL).nominal / (KIP * IN) == pytest.approx(min(50 * zy, 80 * sy))
    v = a.shear_y(p, STEEL, 5.0)
    assert v.nominal / KIP == pytest.approx(2 * 0.6 * 50 * r["bf"] * r["tf"] / IN**2)


def test_tension_yield_and_rupture():
    p = a._library("aisc", "W18X50")
    y, rupture = a.tension(p, STEEL, 0.85)
    assert y.available("LRFD") == pytest.approx(0.9 * STEEL.Fy * p.A)
    assert rupture.available("LRFD") == pytest.approx(0.75 * STEEL.Fu * 0.85 * p.A)


def test_unbuilt_shapes_are_refused():
    with pytest.raises(NotCovered, match="C sections"):
        a._library("aisc", "C10X20")
    with pytest.raises(NotCovered, match="L sections"):
        a._library("aisc", "L4X4X1/2")


def test_puzzle_shapes_give_props():
    from puzzle.model.schema import BeamSection, Quantity

    q = lambda v: Quantity(value=v, unit="mm")  # noqa: E731
    box = a.props(BeamSection(selection="F", shape="box", width=q(100), height=q(200),
                              thickness=q(10)))  # fmt: skip
    assert box.kind == "RHS" and box.Zx == pytest.approx((0.1 * 0.2**2 - 0.08 * 0.18**2) / 4)
    bar = a.props(BeamSection(selection="F", shape="rectangle", width=q(20), height=q(100)))
    assert bar.kind == "bar"
    assert a.flexure_x(bar, STEEL, 0.01, 1.0).clause == "F11.1"
