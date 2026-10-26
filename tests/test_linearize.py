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


def test_the_line_frame_is_right_handed_and_follows_the_hoop():
    from puzzle.post.linearize import line_frame

    r = line_frame((0, 0, 0), (2, 0, 0), hoop=(0, 1, 0.3))
    n, t, h = r.T
    assert np.allclose(n, (1, 0, 0)) and np.allclose(h, np.array([0, 1, 0.3]) / np.hypot(1, 0.3))
    assert np.allclose(t, np.cross(h, n)) and np.allclose(r.T @ r, np.eye(3))
    assert np.allclose(line_frame((0, 0, 0), (0, 0, 1))[:, 2] @ (0, 0, 1), 0)  # default hoop


def test_rotating_a_uniaxial_stress_onto_the_line():
    from puzzle.post.linearize import line_frame, rotate

    sigma = np.array([[0, 0, 0, 0, 0, 0], [5.0, 0, 0, 0, 0, 0]])  # xx only
    local = rotate(sigma, line_frame((0, 0, 0), (1, 0, 0), hoop=(0, 0, 1)))
    assert local[1, 0] == pytest.approx(5.0) and np.allclose(local[1, 1:], 0)
    diagonal = rotate(sigma, line_frame((0, 0, 0), (1, 1, 0), hoop=(0, 0, 1)))
    assert diagonal[1, 0] == pytest.approx(2.5) and diagonal[1, 1] == pytest.approx(2.5)


def test_asme_bending_is_for_the_normal_stresses_across_the_line_only():
    # A linear stress along the line (nn) and the same in the hoop (hh): ASME's option
    # leaves nn's linear part in the peak and keeps hh's as bending.
    t = 0.02
    x = np.linspace(0, t, 47)
    field = np.zeros((len(x), 6))
    field[:, 0] = field[:, 2] = 30e6 * (1 - 2 * x / t)
    full = linearize(x, field)
    asme = linearize(x, field, normal_only=True)
    assert full["start"]["bending"][0] == pytest.approx(30e6)
    assert asme["start"]["bending"][0] == 0.0 and asme["start"]["peak"][0] == pytest.approx(30e6)
    assert asme["start"]["bending"][2] == pytest.approx(30e6)


def test_thick_cylinder_membrane_hoop_stress_is_p_ri_over_t():
    # Lamé, internal pressure p, closed ends: sigma_theta = A + B/r², sigma_r = A - B/r².
    from puzzle.post.linearize import line_frame

    p, ri, ro = 10e6, 0.1, 0.15
    a, b = p * ri**2 / (ro**2 - ri**2), p * ri**2 * ro**2 / (ro**2 - ri**2)
    r = np.linspace(ri, ro, 47)
    field = np.zeros((len(r), 6))
    field[:, 0], field[:, 1], field[:, 2] = a - b / r**2, a + b / r**2, a  # on the x axis
    out = report(r - ri, field, "tresca", frame=line_frame((ri, 0, 0), (ro, 0, 0), (0, 1, 0)))
    m = out["parts"]["start"]["membrane"]["components"]
    assert m["hh"] == pytest.approx(p * ri / (ro - ri), rel=1e-4)  # the hoop membrane
    assert m["nn"] == pytest.approx(-p * ri / (ri + ro), rel=1e-4)  # radial: ∫ sigma_r / t
    assert m["tt"] == pytest.approx(a)  # axial, uniform
    total_in = out["parts"]["start"]["total"]["components"]
    assert total_in["nn"] == pytest.approx(-p) and total_in["hh"] == pytest.approx(a + b / ri**2)
    s1, _s2, s3 = out["parts"]["start"]["total"]["principal"]
    assert out["parts"]["start"]["total"]["tresca"] == pytest.approx(s1 - s3)


def test_checks_against_an_allowable():
    from puzzle.post.linearize import checks

    t = 0.01
    x = np.linspace(0, t, 21)
    out = report(x, _field(x, 100e6 + 60e6 * (1 - 2 * x / t)))
    rows = checks(out, 120e6)
    assert [r["ok"] for r in rows] == [True, True, True]
    assert rows[0]["ratio"] == pytest.approx(100 / 120)
    assert rows[1]["value"] == pytest.approx(160e6) and rows[1]["limit"] == pytest.approx(180e6)
    local = checks(out, 60e6, membrane="local")
    assert local[0]["limit"] == pytest.approx(90e6) and not local[0]["ok"]
