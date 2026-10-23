"""The fatigue formulas (ADR 0026), checked by the same Shigley worked examples as
Tension-meter's `engineering_core/test/stress/stress_test.dart`, so both apps agree."""

import math

import numpy as np
import pytest

from puzzle.materials import fatigue as f

KSI = f.KSI


def factor(symbol, **kw):
    args = {"sut": 520e6, "surface": "machined"} | kw
    return f.endurance_limit(**args).factors[symbol]


def test_marin_factors_match_shigley_examples():
    assert factor("ka") == pytest.approx(0.860, abs=5e-4)  # Ex. 6-3
    assert factor("kb", size="rotating_round", d=0.032) == pytest.approx(0.858, abs=5e-4)  # 6-4
    assert factor("kb", size="non_rotating_round", d=0.032) == pytest.approx(0.954, abs=5e-4)
    assert factor("kd", temperature_c=(450 - 32) * 5 / 9) == pytest.approx(1.007, abs=5e-4)  # 6-5
    for r, _, ke in f.RELIABILITY_FACTORS:  # Table 6-5
        assert factor("ke", reliability=r) == pytest.approx(ke, abs=6e-4), r
    assert f.endurance_limit(1000e6, surface="polished").se_prime == 500e6  # Eq. 6-8
    assert f.endurance_limit(1600e6, surface="polished").se_prime == 700e6
    with pytest.raises(ValueError, match="steels only"):
        f.endurance_limit(300e6, steel=False)


def test_notch_sensitivity_and_fatigue_fraction():
    q = f.notch_sensitivity(690e6, 0.003)  # Ex. 6-6
    assert 1 + q * (1.65 - 1) == pytest.approx(1.55, abs=5e-3)
    assert f.fatigue_fraction(105 * KSI) == pytest.approx(0.837, abs=2e-3)  # Fig. 6-18
    assert f.fatigue_fraction(151 * KSI) == pytest.approx(0.795, abs=3e-3)
    assert f.fatigue_fraction(60 * KSI) == 0.9


def test_example_6_7_life_on_the_sn_line():
    """About 32 300 cycles at a reversed 403 MPa (Se = 280 MPa, Sut = 690 MPa); Shigley
    reads f = 0.845 off Fig. 6-18, the equations give 0.8435, hence within 3 %."""
    line = f.SnLine(se=280e6, sut=690e6)
    assert float(line.cycles(403e6)) / 32.3e3 == pytest.approx(1, abs=0.03)


def test_example_6_15_duty_cycle_by_gerber():
    """Sut = 151 kpsi, Se = 67.5 kpsi; (σa, σm) = (70, 10), (10, 50), (10, −30) kpsi:
    cycle 1 σrev = 70.3 kpsi and N = 619 000; the others infinite."""
    line = f.SnLine(se=67.5 * KSI, sut=151 * KSI)
    sa = np.array([70, 10, 10]) * KSI
    sm = np.array([10, 50, -30]) * KSI
    rev = f.reversed_stress("gerber", sa, sm, 151 * KSI, 120 * KSI)
    assert rev[0] / KSI == pytest.approx(70.3, abs=0.05)
    life = line.cycles(rev)
    assert life[0] / 619e3 == pytest.approx(1, abs=0.06)
    assert np.isinf(life[1:]).all()
    damage, repeats = f.miner([(1, life[0]), (1, life[1]), (1, life[2])])
    assert repeats / 619e3 == pytest.approx(1, abs=0.06)


def test_corrections_order_and_limits():
    def rev(c):
        return float(f.reversed_stress(c, 100e6, 200e6, 600e6, 400e6))

    assert rev("gerber") < rev("goodman") < rev("soderberg")
    assert rev("none") == 100e6
    assert np.isinf(f.reversed_stress("goodman", 100e6, 600e6, 600e6, 400e6))
    assert f.reversed_stress("goodman", 100e6, -200e6, 600e6, 400e6) == 100e6  # no benefit


def test_sn_line_below_a_thousand_cycles():
    line = f.SnLine(se=67.5 * KSI, sut=151 * KSI)
    assert line.cycles(151 * KSI) == 0
    assert float(line.cycles(line.f * 151 * KSI)) == pytest.approx(1000, abs=1)
    assert line.cycles(0.95 * 151 * KSI) < 1000


def test_measured_curve_and_miner():
    pts = [(1e3, 400e6), (1e6, 200e6)]
    assert f.cycles_on_curve(pts, 400e6) == pytest.approx(1e3)
    assert f.cycles_on_curve(pts, math.sqrt(400e6 * 200e6)) == pytest.approx(math.sqrt(1e9))
    assert np.isinf(f.cycles_on_curve(pts, 100e6))
    damage, repeats = f.miner([(1, 1000), (2, 4000), (5, np.inf)])  # Ex. 6-15's damage
    assert damage == pytest.approx(0.0015) and repeats == pytest.approx(666.67, abs=0.01)


def test_strain_life_models():
    """NUREG/CR-6909 eq. 29 for austenitic stainless at 1e6 cycles: εa ≈ 0.139 %."""
    langer = f.StrainLife("langer", langer_a=6.891, langer_b=1.920, langer_c=0.112)
    assert float(langer.strain_amplitude(1e6)) * 100 == pytest.approx(0.139, abs=0.002)
    assert float(langer.cycles(langer.strain_amplitude(1e5))) == pytest.approx(1e5, rel=1e-9)
    cmb = f.StrainLife(
        "coffin_manson_basquin",
        fatigue_strength=900e6,
        strength_exponent=-0.1,
        fatigue_ductility=0.5,
        ductility_exponent=-0.6,
        modulus=200e9,
    )
    n = np.array([1e2, 1e4, 1e6])
    assert cmb.cycles(cmb.strain_amplitude(n)) == pytest.approx(n, rel=1e-6)
    assert np.isinf(cmb.cycles(1e-5))  # below the curve at 1e12 cycles
