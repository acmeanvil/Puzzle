"""AISC 370-21 stainless member strengths (ADR 0079 step 5) against AISC Design Guide 27,
2nd edition, Chapter 12's design examples, each built from the example's given section
properties (Design Guide 27's own shape tables) and its rounded intermediate values
where the example rounds them (L_c/r, r_ts), to the example's printed digits:

- Example 1: round HSS 6.25×0.250, S30400, L_c 11 ft: P_n = 101 kips, φP_n 90.9 kips;
- Example 2: square HSS 6×6×0.180, S32101 (F_y 77 ksi), slender walls, b_e 5.18 in.,
  A_e 3.70 in.², P_n 186 kips;
- Example 4: a W-shape, S30400 (F_y 35 ksi), L 9 ft: φP_n 56.7 kips (curve A about y),
  L_p 2.42 ft, L_y 4.54 ft, L_r 34.2 ft, M_nx 22.3 kip-ft (F2-3, α_LT 0.540), M_ny 9.95
  kip-ft, and H1-1b;
- Example 6: the same W at F_y 30 ksi, L_c 12.5 ft about its major axis: φP_n 98.1 kips
  (curve B), φM_px 26.1 kip-ft.
"""

import math

import pytest

from puzzle.frames import aisc370 as a
from puzzle.frames.aisc360 import Props, Steel

KSI, IN, FT, KIP = 6.894757e6, 0.0254, 0.3048, 4448.2216
KIPFT = KIP * FT


def _steel(fy, e=28000, g=10800):
    return Steel(e * KSI, g * KSI, fy * KSI, 75 * KSI)


def test_example_1_round_hss():
    d, t, area, r = 6.25 * IN, 0.238 * IN, 4.50 * IN**2, 2.13 * IN
    i = r * r * area
    p = Props("CHS", area, i, i, 0, 0, 0, 0, 2 * i, d=d, t=t)
    c = a.compression(p, _steel(30), 11 * FT, 11 * FT, 11 * FT)
    assert c.clause == "E3 (curve B)"
    assert c.nominal / KIP == pytest.approx(101, abs=0.5)
    assert c.available("ASD") / KIP == pytest.approx(60.5, abs=0.3)


def test_example_2_slender_square_hss():
    area, i, t = 3.91 * IN**2, 21.9 * IN**4, 0.171 * IN
    r = 2.37 * IN
    p = Props("RHS", area, i, i, 0, 0, 0, 0, 0, d=6 * IN, bf=6 * IN, t=t,
              extra={"rx": r, "ry": r})  # fmt: skip
    s = _steel(77, e=29000, g=11200)
    c = a.compression(p, s, 11 * FT, 11 * FT, 11 * FT)
    assert "E7" in c.clause
    fcr = c.nominal / (KIP * (area / IN**2 - 4 * (5.49 - 5.18) * 0.171))
    assert fcr == pytest.approx(50.2, abs=0.1)  # E3-3, curve C
    assert c.nominal / KIP == pytest.approx(186, abs=0.6)
    assert c.available("LRFD") / KIP == pytest.approx(0.9 * 186, abs=0.6)


def _w(fy):
    """Example 4's W-shape, its r_ts rounded to 1.14 in. as the example has it."""
    iy, ho, sx = 4.48 * IN**4, 5.87 * IN, 10.2 * IN**3
    return Props("I", 4.67 * IN**2, 31.9 * IN**4, iy, 11.6 * IN**3, 3.41 * IN**3, sx,
                 2.22 * IN**3, 0.207 * IN**4, iy * ho**2 / 4, d=6.28 * IN, bf=4.03 * IN,
                 tf=0.410 * IN, tw=0.250 * IN, h=21.8 * 0.250 * IN, ho=ho,
                 extra={"rx": 2.61 * IN, "ry": 0.979 * IN, "rts": 1.14 * IN})  # fmt: skip


def test_example_4_w_shape_compression_and_biaxial_bending():
    p, s = _w(35), _steel(35)
    lc = 110 * 0.979 * IN  # the example's L_c/r of 110
    c = a.compression(p, s, lc, lc, lc)
    assert c.limit == "flexural buckling about y" and c.clause == "E3 (curve A)"
    assert c.available("LRFD") / KIP == pytest.approx(56.7, abs=0.15)
    lp, ly, lr = (x / FT for x in a.lengths(p, s, "austenitic"))
    assert (lp, ly, lr) == (pytest.approx(2.42, abs=0.01), pytest.approx(4.54, abs=0.01),
                            pytest.approx(34.2, abs=0.05))  # fmt: skip
    fx = a.flexure_x(p, s, 9 * FT, 1.0)
    assert fx.clause == "F2.2(c)"
    assert fx.nominal / KIPFT == pytest.approx(22.3, abs=0.05)
    assert fx.available("LRFD") / KIPFT == pytest.approx(0.9 * 22.3, abs=0.05)  # 20.1, rounded
    fy = a.flexure_y(p, s)
    assert fy.clause == "F6.1" and fy.nominal / KIPFT == pytest.approx(9.95, abs=0.01)
    ratio = a.interaction(7.00 * KIP, c.available("LRFD"), 3.00 * KIPFT, fx.available("LRFD"),
                          3.00 * KIPFT, fy.available("LRFD"))  # fmt: skip
    assert ratio == pytest.approx(7.00 / (2 * 56.7) + 3.00 / 20.1 + 3.00 / 8.96, abs=2e-3)


def test_example_6_major_axis_buckling_and_plastic_moment():
    p, s = _w(30), _steel(30)
    lc = 57.5 * 2.61 * IN  # the example's L_c/r_x of 57.5
    c = a.compression(p, s, lc, 0.01, 0.01)
    assert c.clause == "E3 (curve B)"
    assert c.available("LRFD") / KIP == pytest.approx(98.1, abs=0.15)
    assert a.flexure_x(p, s, 0.0, 1.0).available("LRFD") / KIPFT == pytest.approx(26.1, abs=0.05)


def test_the_curves_are_continuous():
    for curve, (_alpha, b0, _b1, b2) in a.CURVES.items():
        fy = 100.0
        at = fy / (b0 / math.pi) ** 2  # F_e where E3-2 hands over to E3-3
        assert a.fcr(fy, at, curve) == pytest.approx(fy, rel=2e-3), curve
        assert a.fcr(fy, fy / 3.2 * 1.0001, curve) == pytest.approx(b2 * fy / 3.2, rel=0.08), (
            curve
        )  # E3-3 to E3-4, close (the spec's own step)


def test_shear_strain_hardening_and_families():
    p, s = _w(35), _steel(35)
    v = a.shear_x(p, s, 3.0)
    assert v.clause == "G2.1"
    assert v.nominal == pytest.approx(0.6 * s.Fy * p.d * p.tw * 1.2)  # λ 21.8 < 0.59√(k E/F_y)
    from puzzle.model.schema import Material

    assert a.family(Material(name="Duplex stainless steel 2205")) == "duplex"
    assert a.family(Material(name="Stainless steel 304")) == "austenitic"
