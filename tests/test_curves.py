"""Stress-strain curves (materials.curves), checked as Tension-meter checks its own
(cicada_core/test/profiles/stress_strain_test.dart), so both apps agree."""

import math

import pytest

from puzzle.materials.curves import (
    Asme3DClass,
    Asme3DCurve,
    RambergOsgood,
    bilinear,
    engineering_to_true,
    post_yield,
    thin,
    true_to_engineering,
)

# SA-240 304: E 195 GPa, Sy 205 MPa, Su 515 MPa.
CURVE = Asme3DCurve(195e9, 205e6, 515e6, Asme3DClass.STAINLESS)


def test_annex_3d_constants():
    r = 205 / 515
    assert CURVE.r == pytest.approx(r, abs=1e-12)
    assert CURVE.m2 == pytest.approx(0.75 * (1 - r), abs=1e-12)
    assert CURVE.k == pytest.approx(1.5 * r**1.5 - 0.5 * r**2.5 - r**3.5, abs=1e-12)
    assert CURVE.true_ultimate_stress == pytest.approx(515e6 * math.exp(CURVE.m2), abs=1)


def test_the_engineering_curve_peaks_near_the_ultimate():
    eng = true_to_engineering(CURVE.true_curve(400))
    assert max(s for _, s in eng) / 515e6 == pytest.approx(1, abs=0.03)
    assert math.log(1 + eng[-1][0]) == pytest.approx(CURVE.m2, abs=0.03)


def test_the_curve_passes_close_to_the_offset_yield():
    assert CURVE.true_strain(205e6) - 205e6 / 195e9 == pytest.approx(0.002, abs=0.0006)


def test_true_and_engineering_invert_each_other():
    pts = [(0.1, 400e6), (0.25, 600e6)]
    for (e, s), (e2, s2) in zip(engineering_to_true(true_to_engineering(pts)), pts, strict=True):
        assert e == pytest.approx(e2, abs=1e-12) and s == pytest.approx(s2, abs=1e-3)


def test_ramberg_osgood_two_point_fit():
    last = CURVE.true_curve()[-1]
    ro = RambergOsgood.fit_through(195e9, 205e6, stress=last[1], strain=last[0])
    assert ro.strain(last[1]) == pytest.approx(last[0], abs=1e-9)
    assert ro.plastic_strain(205e6) == pytest.approx(0.002, abs=1e-12)
    assert ro.alpha == pytest.approx(0.002 * 195e9 / 205e6, abs=1e-9)
    assert ro.tangent_modulus(1e3) == pytest.approx(195e9, abs=1e6)
    assert ro.tangent_modulus(400e6) < ro.tangent_modulus(250e6)
    assert RambergOsgood.fit_through(195e9, 205e6, stress=150e6, strain=0.01) is None


def test_post_yield_starts_at_zero_and_rises():
    plastic = post_yield(CURVE.true_curve(), 195e9, start_stress=150e6)
    assert plastic[0] == (0.0, 150e6)
    assert all(b[0] > a[0] and b[1] > a[1] for a, b in zip(plastic, plastic[1:], strict=False))
    assert len(thin(plastic, 25)) == 25 and thin(plastic, 25)[-1] == plastic[-1]


def test_bilinear_and_invalid_classes():
    sy, et = bilinear([(0.0, 0.0), (0.2, 405e6)], 195e9, 205e6)
    assert sy == 205e6 and et == pytest.approx(200e6 / (0.2 - 205e6 / 195e9 - 0.002))
    assert not Asme3DCurve(115e9, 99e6, 100e6, Asme3DClass.ALUMINUM).is_valid
