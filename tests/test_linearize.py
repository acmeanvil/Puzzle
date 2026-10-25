"""Stress linearization (ASME VIII-2 Annex 5-A) on fields with known parts.

A stress linear through the wall is all membrane and bending (no peak); a quadratic one
leaves its known peak; the equivalents follow von Mises and Tresca by hand.
"""

import numpy as np
import pytest

from puzzle.post.linearize import equivalent, linearize, report, simpson


def _field(x, xx):
    out = np.zeros((len(x), 6))
    out[:, 0] = xx
    return out


def test_simpson_is_exact_for_cubics():
    x = np.linspace(0.0, 2.0, 7)
    assert simpson(x**3, x) == pytest.approx(4.0)
    assert simpson(x**2, np.linspace(0.0, 2.0, 7)) == pytest.approx(8 / 3)


def test_a_linear_stress_is_membrane_and_bending_only():
    t, m, b = 0.02, 50e6, 30e6  # wall, membrane, bending at the start
    x = np.linspace(0.0, t, 47)
    parts = linearize(x, _field(x, m + b * (1 - 2 * x / t)))
    assert parts["start"]["membrane"][0] == pytest.approx(m)
    assert parts["start"]["bending"][0] == pytest.approx(b)
    assert parts["end"]["bending"][0] == pytest.approx(-b)
    assert parts["start"]["membrane_bending"][0] == pytest.approx(m + b)
    assert np.abs(parts["start"]["peak"]).max() < 1e-6 * m
    assert np.abs(parts["end"]["peak"]).max() < 1e-6 * m


def test_a_quadratic_stress_leaves_its_peak():
    # s = (2x/t - 1)^2: mean 1/3, no bending (symmetric), peak 2/3 at either face.
    t = 1.0
    x = np.linspace(0.0, t, 47)
    parts = linearize(x, _field(x, (2 * x / t - 1) ** 2))
    assert parts["start"]["membrane"][0] == pytest.approx(1 / 3)
    assert parts["start"]["bending"][0] == pytest.approx(0, abs=1e-12)
    assert parts["start"]["peak"][0] == pytest.approx(2 / 3)
    assert parts["end"]["peak"][0] == pytest.approx(2 / 3)


def test_equivalents():
    shear = np.array([0, 0, 0, 10.0, 0, 0])
    assert equivalent(shear) == pytest.approx(10 * np.sqrt(3))
    assert equivalent(shear, "tresca") == pytest.approx(20.0)
    uniaxial = np.array([7.0, 0, 0, 0, 0, 0])
    assert equivalent(uniaxial) == pytest.approx(7.0)
    assert equivalent(uniaxial, "tresca") == pytest.approx(7.0)


def test_report_gives_the_governing_value_and_plot_series():
    t = 0.01
    x = np.linspace(0.0, t, 21)
    out = report(x, _field(x, 100 + 40 * (1 - 2 * x / t)), "tresca")
    assert out["value"] == pytest.approx(140)
    assert out["parts"]["end"]["membrane_bending"]["equivalent"] == pytest.approx(60)
    assert len(out["series"]["membrane_bending"]) == len(x)
    assert out["series"]["membrane_bending"][0] == pytest.approx(140)
