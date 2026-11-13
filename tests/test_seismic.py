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
