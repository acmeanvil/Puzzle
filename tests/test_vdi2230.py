"""VDI 2230 Part 1:2015 against the guideline's worked Examples B1, B2 and B5 and Table A1,
as Lacewing Lab's tests read them (engineering_core/test/fastening/vdi2230_test.dart and
bolt_torque_test.dart): every printed value, to the book's rounding."""

from __future__ import annotations

import math

import pytest

from puzzle import threads, vdi2230
from puzzle.vdi2230 import GRADES, Cylinder

MM, MPA, ES = 1e-3, 1e6, 205e9


def r(m_per_n: float) -> float:
    """Resiliences in the guideline's 10⁻⁶ mm/N."""
    return m_per_n * 1e9


def test_table_a1_preloads_and_torques():
    m12, m16 = vdi2230.thread(12 * MM, 1.75 * MM), vdi2230.thread(16 * MM, 2 * MM)
    fm = vdi2230.assembly_preload
    assert fm(m12, GRADES["12.9"].rp02(m12.d), 0.10).force / 1e3 == pytest.approx(75.9, abs=0.1)
    assert fm(m12, GRADES["10.9"].rp02(m12.d), 0.10).force / 1e3 == pytest.approx(64.8, abs=0.15)
    assert fm(m16, GRADES["10.9"].rp02(m16.d), 0.12).force / 1e3 == pytest.approx(118.8, abs=0.15)
    # Table A1's torques use DKm = (dW + dh)/2: 108 N·m and 302 N·m.
    dkm = vdi2230.friction_diameter(17.23 * MM, 13.5 * MM)
    assert vdi2230.tightening_torque(m12, 64.8e3, 0.10, 0.10, dkm) == pytest.approx(108, abs=1.5)
    dkm16 = vdi2230.friction_diameter(22.5 * MM, 17.5 * MM)
    assert vdi2230.tightening_torque(m16, 118.8e3, 0.12, 0.12, dkm16) == pytest.approx(302, abs=4)
    # A hollow bolt reduces to the solid one at no bore and loses capacity with one.
    assert fm(m12, 940e6, 0.1, bore=0).force == fm(m12, 940e6, 0.1).force
    assert fm(m12, 940e6, 0.1, bore=4 * MM).force < fm(m12, 940e6, 0.1).force


@pytest.mark.parametrize(
    ("d", "p", "dw", "mu", "f_m", "m_a"),
    [
        (4, 0.7, 5.88, 0.10, 4.5, 2.6),
        (6, 1.0, 8.88, 0.12, 10.2, 10.1),
        (8, 1.25, 11.63, 0.14, 26.6, 40.1),
        (10, 1.5, 14.63, 0.12, 29.6, 48.0),
        (12, 1.75, 16.63, 0.10, 64.8, 108.0),
        (16, 2.0, 22.49, 0.12, 118.8, 302.0),
        (20, 2.5, 28.19, 0.10, 190.0, 517.0),
        (24, 3.0, 33.61, 0.14, 305.0, 1329.0),
        (30, 3.5, 42.75, 0.08, 446.0, 1500.0),
        (36, 4.0, 51.11, 0.20, 558.0, 5043.0),
    ],
)
def test_table_a1_torques_on_iso_273_holes(d, p, dw, mu, f_m, m_a):
    """Table A1's torques: ISO 4014 heads (dW min) on ISO 273 medium holes, μK = μG; the
    table rounds (±2.5 %, Section 5.5.1), so each is matched to 2 %. Its dW are exactly
    `bolts.SIZES`' bearing diameters."""
    from puzzle import bolts, fasteners

    th = vdi2230.thread(d * MM, p * MM)
    dkm = vdi2230.friction_diameter(dw * MM, fasteners.clearance_hole(d * MM))
    assert vdi2230.tightening_torque(th, f_m * 1e3, mu, mu, dkm) == pytest.approx(m_a, rel=0.02)
    assert bolts.size("iso_coarse", f"M{d}").bearing == pytest.approx(dw * MM)
    # and back: the preload the torque gives
    back = vdi2230.preload_from_torque(th, m_a, mu, mu, dkm)
    assert back == pytest.approx(f_m * 1e3, rel=0.02)


def test_example_b1_piston_rod_m12_socket_head_into_c45():
    """DIN EN ISO 4762 M12 × 60 10.9 into C45, concentric."""
    th = vdi2230.thread(12 * MM, 1.75 * MM)
    assert th.d2 / MM == pytest.approx(10.863, abs=0.001)
    assert th.minor_area / MM**2 == pytest.approx(76.25, abs=0.05)
    assert th.stress_area / MM**2 == pytest.approx(84.3, abs=0.05)
    b = vdi2230.bolt_resilience(
        th, ES, [Cylinder(24 * MM, 12 * MM)], 18 * MM, "socket", tapped=True
    )
    assert r(b.delta_s) == pytest.approx(2.95, abs=0.005)
    assert r(b.nut) == pytest.approx(0.171, abs=0.001)
    # Treated as a bolted joint for δP (dWm = 21.11 mm, D'A = 80 mm), tapped for δPZu.
    p = vdi2230.plate_resilience(21.11 * MM, 13.5 * MM, 80 * MM, 42 * MM, ES, tapped=False)
    assert p.tan_phi == pytest.approx(0.566, abs=0.0005)
    assert p.limit_diameter / MM == pytest.approx(44.9, abs=0.05)
    assert r(p.delta_p) == pytest.approx(0.363, abs=0.0005)
    assert p.shape == "cones"
    lf = vdi2230.load_factor(b.delta_s, p.delta_p, b.nut, w=2, n=1)
    assert lf.phi_k == pytest.approx(0.161, abs=0.0005)
    # ak/h = 0.093, lA/h = 0, SV 4: n = 0.35 (Table 2 interpolates to 0.346).
    assert vdi2230.load_introduction_factor(4, 0.093, 0) == pytest.approx(0.35, abs=0.005)
    phi = 0.35 * lf.phi_k
    assert phi == pytest.approx(0.056, abs=0.0005)
    # R4: Rz 16, axial: 3 + 3 + 2 µm (thread, one head, one interface).
    fz = vdi2230.embedding(16e-6, shear=False, interfaces=1, tapped=True)
    assert fz / 1e-6 == pytest.approx(8)
    assert vdi2230.preload_loss(fz, b.delta_s, p.delta_p) == pytest.approx(2415, abs=5)
    # R8: FSmax 66 194 N, σz 785.2, τ 269.3, σred 819 MPa.
    ws = vdi2230.working_stress(th, 64800 + 0.056 * 24900, 64800, 0.1)
    assert ws.axial / MPA == pytest.approx(785.2, abs=0.5)
    assert ws.torsion / MPA == pytest.approx(269.3, abs=0.5)
    assert ws.equivalent / MPA == pytest.approx(819, abs=0.5)
    assert vdi2230.endurance_limit(th.d) / MPA == pytest.approx(48.9, abs=0.05)


def test_example_b2_flange_coupling_m16_hex_with_nut():
    """DIN EN ISO 4014 M16 × 80 10.9 with nut, transverse load."""
    th = vdi2230.thread(16 * MM, 2 * MM)
    b = vdi2230.bolt_resilience(th, ES, [Cylinder(42 * MM, 16 * MM)], 18 * MM, "hex")
    # The book takes Ad3 as 144 mm² and rounds each term: 2.249.
    assert r(b.delta_s) == pytest.approx(2.249, abs=0.003)
    p = vdi2230.plate_resilience(22.5 * MM, 17 * MM, 99 * MM, 60 * MM, 1.1e11)
    assert p.tan_phi == pytest.approx(0.598, abs=0.0005)
    assert p.limit_diameter / MM == pytest.approx(58.4, abs=0.05)
    assert r(p.delta_p) == pytest.approx(0.781, abs=0.0005)
    fz = vdi2230.embedding(5e-6, shear=True, interfaces=1)
    assert fz / 1e-6 == pytest.approx(11)
    assert vdi2230.preload_loss(fz, b.delta_s, p.delta_p) == pytest.approx(3630, abs=5)
    # R12: FKRmin = FMzul/αA − FZ = 70 620 N, SG 1.26; τB = 0.62 Rm = 620 MPa.
    slip = (118800 / 1.6 - 3630) / 56000
    assert slip == pytest.approx(1.26, abs=0.005)
    assert 8400 / th.nominal_area / MPA == pytest.approx(41.8, abs=0.05)
    assert GRADES["10.9"].shear_ratio * GRADES["10.9"].rm(th.d) / MPA == pytest.approx(620)


def test_example_b5_cylinder_cap_m20_eccentric():
    """DIN EN ISO 4762 M20 × 60 10.9 into E295, eccentric clamping and loading."""
    th = vdi2230.thread(20 * MM, 2.5 * MM)
    assert th.stress_area / MM**2 == pytest.approx(245, abs=0.3)
    b = vdi2230.bolt_resilience(
        th, ES, [Cylinder(7.5 * MM, 20 * MM)], 27.5 * MM, "socket", tapped=True
    )
    # The book prints δM 0.104; 0.33·20/(205 000·314.16) is 0.1025.
    assert r(b.nut) == pytest.approx(0.1025, abs=0.0001)
    assert r(b.delta_s) == pytest.approx(1.157, abs=0.002)
    # The book prints 48.7 mm; its own βS 5.93·10⁻⁸ × ES·I3 is 49.0.
    assert b.bending_length / MM == pytest.approx(48.7, abs=0.3)
    # Tapped: D'A 87.1 mm for y, DA 39.1 mm at the interface: cone and sleeve.
    p = vdi2230.plate_resilience(
        28.87 * MM, 22 * MM, 39.1 * MM, 35 * MM, ES, tapped=True, da_sub=87.1 * MM
    )
    assert p.tan_phi == pytest.approx(0.564, abs=0.0005)
    assert p.limit_diameter / MM == pytest.approx(68.3, abs=0.05)
    assert r(p.delta_p) == pytest.approx(0.2458, abs=0.001)  # the book's tan φ is rounded
    assert p.shape == "cone_and_sleeve"
    i_bers = vdi2230.moment_of_gyration(
        28.87 * MM,
        39.1 * MM,
        p.limit_diameter,
        0.564,
        2,
        35 * MM,
        s_sym=1.7 * MM,
        b=51.4 * MM,
        c_t=33 * MM,
    )
    assert i_bers / MM**4 == pytest.approx(112869, abs=30)
    n = vdi2230.load_introduction_factor(2, 0.321, 0)
    assert n == pytest.approx(0.28, abs=0.003)
    lf = vdi2230.load_factor(
        b.delta_s, p.delta_p, b.nut, 2, 0.28, 35 * MM, ES, -1.7 * MM, 24 * MM, i_bers
    )
    assert r(lf.delta_p_star) == pytest.approx(0.250, abs=0.0005)
    assert r(lf.delta_p_star2) == pytest.approx(0.184, abs=0.0005)
    assert lf.phi == pytest.approx(0.057, abs=0.0005)
    fk = vdi2230.opening_clamp_load(20.53e3, 24 * MM, -1.7 * MM, 13.8 * MM, 843 * MM**2, 109875e-12)
    assert fk == pytest.approx(68.13e3, abs=10)
    fz = vdi2230.embedding(16e-6, tapped=True)
    assert vdi2230.preload_loss(fz, b.delta_s, p.delta_p) == pytest.approx(5703, abs=10)
    assert vdi2230.assembly_preload(th, 940e6, 0.1).force / 1e3 == pytest.approx(190, abs=0.5)
    ws = vdi2230.working_stress(th, 190000 + 0.057 * 20530, 190000, 0.1)
    assert ws.axial / MPA == pytest.approx(780.3, abs=1)
    assert ws.torsion / MPA == pytest.approx(257.3, abs=1)
    assert ws.equivalent / MPA == pytest.approx(811.5, abs=0.7)
    # R9: σSAb = 1.734·10⁻³ mm⁻² FA (the book's Φ 0.057 and lers 48.7), σab 12.5 MPa.
    tilde = 112869e-12 - math.pi / 64 * (22 * MM) ** 4

    def sab(fa):
        return vdi2230.eccentric_bolt_stress(
            th, 0.057, fa, -1.7 * MM, 24 * MM, 35 * MM, 48.7 * MM, ES, ES, tilde
        )

    assert sab(1) * 1e-6 * 1e3 == pytest.approx(1.734, abs=0.01)
    assert (sab(20530) - sab(6160)) / 2 / MPA == pytest.approx(12.5, abs=0.1)
    assert vdi2230.endurance_limit(th.d) / MPA == pytest.approx(44.6, abs=0.05)
    dkm = vdi2230.friction_diameter(28.87 * MM, 22 * MM)
    assert vdi2230.tightening_torque(th, 190e3, 0.1, 0.1, dkm) == pytest.approx(517, abs=8)


def test_engaged_thread_agrees_with_threads_vdi_compliance():
    """δG + δM here is ADR 0076's `threads.vdi_compliance` (one formula, two places)."""
    for d, p in ((12, 1.75), (20, 2.5)):
        th = vdi2230.thread(d * MM, p * MM)
        for tapped in (True, False):
            g, m = vdi2230.engaged_resilience(th.d, th.d3, ES, 110e9, tapped)
            assert g + m == pytest.approx(threads.vdi_compliance(th.d, th.d3, ES, 110e9, tapped))


def test_plate_resilience_is_continuous_at_its_limits():
    for tapped in (False, True):

        def at(da, tapped=tapped):
            return vdi2230.plate_resilience(
                20 * MM, 13 * MM, da, 40 * MM, ES, tapped, 60 * MM
            ).delta_p

        gr = vdi2230.plate_resilience(20 * MM, 13 * MM, 60 * MM, 40 * MM, ES, tapped).limit_diameter
        assert at(gr - 1e-9) == pytest.approx(at(gr + 1e-9), rel=1e-6)
        assert at(20 * MM + 1e-9) == pytest.approx(at(20 * MM), rel=1e-6)
        assert at(25 * MM) > at(28 * MM)  # more material, stiffer


def test_table_2_on_its_grid_and_between():
    n = vdi2230.load_introduction_factor
    assert n(1, 0, 0) == 0.70
    assert n(6, 0.6, 0.4) == 0.03
    assert n(2, 0.3, 0.1) == pytest.approx(0.21)
    assert n(3, 0.2, 0.05) == pytest.approx(((0.37 + 0.26) / 2 + (0.30 + 0.20) / 2) / 2)
    with pytest.raises(ValueError):
        n(7, 0, 0)


def test_table_a7_example_and_examples_b1_b2():
    est = vdi2230.estimate_diameter
    assert est(8500, "dynamic_eccentric", 1, "12.9") == (25000.0, 40000.0, pytest.approx(0.010))
    assert est(24900, "dynamic_concentric", 1, "12.9") == (40000.0, 63000.0, pytest.approx(0.012))
    assert est(8400, "transverse", 1, "10.9") == (63000.0, 100000.0, pytest.approx(0.018))


def test_opening_and_endurance_limits():
    assert vdi2230.opening_clamp_load(1e4, 0, 0, 0.02, 1e-3, 1e-7) == 0
    assert vdi2230.opening_clamp_load(1e4, 0.03, 0.002, 0.02, 1e-3, 1e-7) > 0
    assert vdi2230.endurance_limit(20 * MM) / MPA == pytest.approx(44.625)
    assert vdi2230.endurance_limit(20 * MM, True, 0.7) / MPA == pytest.approx(1.3 * 44.625)
    assert vdi2230.endurance_limit(20 * MM, cycles=2e5) / MPA == pytest.approx(
        44.625 * 2.15443, abs=0.001
    )


def test_nut_factor_is_the_torque_per_preload_and_diameter():
    """Table A1's M12 at μ 0.10 on an ISO 4014 head: K ≈ 0.139, about the classic 0.14
    for lubricated steel; Puzzle's default K = 0.2 is μ ≈ 0.15-0.16."""
    th = vdi2230.thread(12 * MM, 1.75 * MM)
    dkm = vdi2230.friction_diameter(16.63 * MM, 13.5 * MM)
    k = vdi2230.nut_factor(th, 0.10, 0.10, dkm)
    assert k == pytest.approx(108 / (64.8e3 * 0.012), rel=0.02)
    assert vdi2230.nut_factor(th, 0.16, 0.16, dkm) == pytest.approx(0.2, abs=0.01)
