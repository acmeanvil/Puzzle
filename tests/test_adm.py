"""Aluminium member strengths by the Aluminum Design Manual 2015 (ADR 0079 step 5),
against its Part VII Illustrative Examples on 6061-T6 (F_ty = F_cy 35 ksi, F_tu 38 ksi,
E 10,100 ksi, G 3,800 ksi) and the Aluminum Association's standard I-beams, from the
shipped Part V table, as allowable (ASD, Ω 1.65 and 1.95) values:

- Example 9, I 8 × 6.18 column, L 8 ft: flexural kL/r 28.5 (F_c 31.3 ksi), torsional
  λ 35.3 governing (F_c/Ω 17.6 ksi, 92.6 k), the flange and web element stresses (21.1
  and 18.1 ksi) and the weighted average 104.3 k (E.3.1, with the example's areas);
- Example 15, I 5 × 3.70 beam, L_b 48 in., C_b 1, r_ye 1.2 r_y: M_np/Ω 130.2 in-k,
  M_nu/Ω 119.6 (Z 6.14), the weighted local buckling M_nlb/Ω 133.3, and lateral-torsional
  buckling 92.8 in-k governing;
- Example 26, I 8 × 6.18: M_np/Ω 350 in-k, M_nu/Ω 322 in-k.
"""

import math

import pytest

from puzzle.frames import adm
from puzzle.frames.aisc360 import Props, _library

KSI, IN, FT, KIP = 6.894757e6, 0.0254, 0.3048, 4448.2216
INK = KIP * IN
AL = adm.Aluminium(10100 * KSI, 3800 * KSI, 35 * KSI, 38 * KSI, 35 * KSI, 21 * KSI,
                   0.6 * 38 * KSI, True)  # fmt: skip


def asd(strength):
    return strength.available("ASD")


def test_buckling_constants_of_6061_t6():
    c = adm.constants(AL)
    assert c.Cc == pytest.approx(66, abs=0.5)  # the examples' λ2 = 66
    lam1 = (c.Bp - AL.Fcy) / (5.0 * c.Dp)
    assert lam1 == pytest.approx(6.7, abs=0.05)  # Example 9's flange λ1
    assert c.k1 * c.Bp / (5.0 * c.Dp) == pytest.approx(10.5, abs=0.05)  # and λ2


def test_example_9_i_beam_column():
    p = _library("aa", "I8X6.18")
    c = adm.constants(AL)
    assert adm.member_buckling(96 / 3.37, AL, c) / KSI == pytest.approx(31.3, abs=0.05)
    out = adm.compression(p, AL, 8 * FT, 0.01, 0.5 * 8 * FT)  # torsion fixed at the ends
    assert out.limit == "member torsional buckling"
    assert asd(out) / KIP == pytest.approx(92.6, rel=3e-3)
    flange = adm.one_edge((5.00 - 0.23) / (2 * 0.35), AL, c) / 1.65 / KSI
    web = adm.both_edges((8.00 - 2 * 0.35) / 0.23, AL, c) / 1.65 / KSI
    assert (flange, web) == (pytest.approx(21.1, abs=0.05), pytest.approx(18.1, abs=0.08))
    # E.3.1 with the example's areas: the flanges, the web, and the junctions at F_cy
    areas = Props("I", (3.339 + 1.679 + 2 * 0.23 * 0.35) * IN**2, p.Ix, p.Iy, p.Zx, p.Zy, p.Sx,
                  p.Sy, p.J, p.Cw, d=p.d, bf=p.bf, tf=p.tf, tw=p.tw, h=p.h, ho=p.ho)  # fmt: skip
    pnc, _fe, _words = adm._local(areas, AL, c)
    assert pnc / 1.65 / KIP == pytest.approx(104.3, rel=3e-3)


def test_example_15_i_beam_by_lateral_torsional_buckling():
    p = _library("aa", "I5X3.70")
    assert adm._yield_moment(p.Zx, p.Sx, AL) / 1.65 / INK == pytest.approx(130.2, abs=0.1)
    assert p.Zx * AL.Ftu / 1.95 / INK == pytest.approx(119.6, abs=0.1)
    braced = adm.flexure_x(p, AL, 0.0, 1.0, method="ASD")
    assert braced.limit == "rupture"  # 119.6 below yielding and local buckling
    c = adm.constants(AL)
    fc = adm.one_edge((3.5 - 0.19) / 2 / 0.32, AL, c)
    fb = adm.web_flexure((5.0 - 2 * 0.32) / 0.19, AL, c)
    iw = 0.19 * (5 - 2 * 0.32) ** 3 / 12
    by_hand = (fc / KSI * (13.9 - iw) / (2.5 - 0.16) + fb / KSI * iw / (2.5 - 0.32)) / 1.65
    assert by_hand == pytest.approx(133.3, abs=0.4)
    out = adm.flexure_x(p, AL, 48 * IN, 1.0, rye=1.2 * p.ry, method="ASD")
    assert out.limit == "lateral-torsional buckling"
    assert asd(out) / INK == pytest.approx(92.8, rel=4e-3)  # the example rounds C_c to 66


def test_example_26_yield_and_rupture():
    p = _library("aa", "I8X6.18")
    assert adm._yield_moment(p.Zx, p.Sx, AL) / 1.65 / INK == pytest.approx(350, abs=0.5)
    assert p.Zx * AL.Ftu / 1.95 / INK == pytest.approx(322, abs=0.5)


def test_the_method_picks_the_governing_limit_state():
    p = _library("aa", "I8X6.18")
    lrfd, allowable = adm.flexure_x(p, AL, 0.0, 1.0), adm.flexure_x(p, AL, 0.0, 1.0, method="ASD")
    assert lrfd.available("LRFD") <= min(
        s.available("LRFD") for s in (adm.Strength(p.Zx * AL.Ftu, 0.75, 1.95, "", ""),)
    )
    assert allowable.available("ASD") <= p.Zx * AL.Ftu / 1.95 + 1e-6


def test_temper_and_material():
    from puzzle.materials import library_material

    m = library_material("aluminium_6061_t6")
    a = adm.material(m)
    assert a.artificially_aged and a.Fcy == pytest.approx(241e6)
    assert a.Fsy == pytest.approx(0.6 * 241e6) and a.Fsu == pytest.approx(0.6 * 262e6)


def test_round_tube_constants_meet():
    c = adm.constants(AL)
    assert c.Ct == pytest.approx(141, abs=0.5)  # Part VI's C_t for 6061-T6
    at = c.Ct
    inelastic = c.Bt - c.Dt * math.sqrt(at)
    assert inelastic == pytest.approx(adm._round_elastic(at, AL.E), rel=1e-6)
