"""Threads as a contact correction (ADR 0076, step 1): the geometry, against hand values.

- Normals: a square thread's have no radial part; a 60° form's radial over axial part is
  tan 30° (the helix aside); a left hand flips the circumferential part; upper and
  lower flanks mirror in z.
- The forms' basic profiles against the standards: M16×2 (ISO 724: d2 14.701 mm, D1
  13.835 mm), 1/2-13 UNC (ASME B1.1: E 0.4500 in, K 0.4167 in), Tr 20×4 (ISO 2904: d2
  18 mm), 1-5 Acme (ASME B1.5: E 0.9 in), BSPT (Whitworth height 0.640327 P).
- Pairs: every node of a cylinder pairs with the coaxial one's node at its (θ, z).
- Tributary areas sum to the cylinder's area, flank areas to it times sec α.
- Per-thread binning by pitch from the mouth; the axis fitted to a tilted cylinder.
"""

import math

import numpy as np
import pytest

from puzzle import threads
from puzzle.model.schema import Contact, Quantity, Thread


def _spec(form="iso", size="M16", **kw):
    return threads.spec(Thread(form=form, size=size, **kw))


def _ring(r, n=36, z=(0.0, 0.02), levels=11):
    t = np.linspace(0, 2 * math.pi, n, endpoint=False)
    zs = np.linspace(*z, levels)
    tt, zz = np.meshgrid(t, zs)
    return np.column_stack([r * np.cos(tt.ravel()), r * np.sin(tt.ravel()), zz.ravel()])


FRAME = threads.Frame(np.zeros(3), np.array([0.0, 0.0, 1.0]), (0.008, 0.008), (0.0, 0.02))


def test_square_threads_have_no_radial_part():
    pts = _ring(0.008)
    n = threads.normals(pts, FRAME, _spec("square"), "upper")
    e_r, e_t, e_z = FRAME.basis(pts)
    assert np.allclose(np.einsum("ij,ij->i", n, e_r), 0.0, atol=1e-12)
    assert np.all(np.einsum("ij,ij->i", n, e_z) > 0.99)


def test_a_sixty_degree_form_leans_by_tan_thirty():
    pts = _ring(0.008)
    spec = _spec("iso")
    n = threads.normals(pts, FRAME, spec, "upper")
    e_r, e_t, e_z = FRAME.basis(pts)
    nr, nz = np.einsum("ij,ij->i", n, e_r), np.einsum("ij,ij->i", n, e_z)
    assert np.allclose(nr / nz, math.tan(math.radians(30)), rtol=1e-12)
    # The helix: e_θ / e_z = -L / (2π r) on a right hand, + on a left hand.
    nt = np.einsum("ij,ij->i", n, e_t)
    assert np.allclose(nt / nz, -spec.lead / (2 * math.pi * 0.008), rtol=1e-12)
    left = threads.normals(pts, FRAME, _spec("iso", hand="left"), "upper")
    assert np.allclose(np.einsum("ij,ij->i", left, e_t), -nt)
    # The lower flank mirrors the upper in z and keeps leaning outward.
    low = threads.normals(pts, FRAME, spec, "lower")
    assert np.allclose(np.einsum("ij,ij->i", low, e_z), -nz)
    assert np.allclose(np.einsum("ij,ij->i", low, e_r), nr)


def test_buttress_bears_on_its_steep_flank_one_way_only():
    spec = _spec("buttress", size=None, major=Quantity(value=40, unit="mm"),
                 pitch=Quantity(value=5, unit="mm"))  # fmt: skip
    assert threads.flank_angle(spec, "upper", load_up=True) == 7.0
    assert threads.flank_angle(spec, "lower", load_up=True) == 45.0
    assert threads.bearing_flank(+1.0) == "upper" and threads.bearing_flank(-1.0) == "lower"
    # Bearing "other": the same side bears, on the other angle.
    assert threads.flank_angle(spec, "upper", load_up=False) == 45.0


@pytest.mark.parametrize(
    ("form", "major", "pitch", "d2", "d1"),
    [
        ("iso", 16e-3, 2e-3, 14.701e-3, 13.835e-3),  # ISO 724, M16×2
        ("un", 0.5 * 25.4e-3, 25.4e-3 / 13, 0.4500 * 25.4e-3, 0.4167 * 25.4e-3),  # B1.1
        ("trapezoidal", 20e-3, 4e-3, 18e-3, 16e-3),  # ISO 2904, Tr 20×4
        ("acme", 25.4e-3, 25.4e-3 / 5, 0.9 * 25.4e-3, 0.8 * 25.4e-3),  # B1.5, 1-5 Acme
    ],
)
def test_the_forms_basic_profiles(form, major, pitch, d2, d1):
    got = threads.profile(form, major, pitch)
    assert got["pitch_diameter"] == pytest.approx(d2, abs=2e-6)
    assert got["minor"] == pytest.approx(d1, abs=2e-6)


def test_pipe_threads_taper_and_heights():
    for name, height in (("npt", 0.8), ("bspt", 0.640327)):
        form = threads.FORMS[name]
        assert form.taper == pytest.approx(1 / 16) and form.a2 == pytest.approx(height)
    assert threads.FORMS["bspt"].angles == (27.5, 27.5)


def test_a_named_size_fills_the_thread():
    spec = _spec("iso", "M16")
    assert (spec.major, spec.pitch) == pytest.approx((0.016, 0.002))
    assert spec.pitch_diameter == pytest.approx(14.701e-3, abs=2e-6)
    assert _spec("iso", "M16", starts=2).lead == pytest.approx(0.004)
    with pytest.raises(ValueError, match="size"):
        Thread(form="iso")
    with pytest.raises(ValueError, match="flank angles"):
        Thread(form="custom", major=Quantity(value=10, unit="mm"),
               pitch=Quantity(value=1, unit="mm"))  # fmt: skip


def test_a_threaded_contact_is_meshed_apart():
    c = Contact(name="T", type="bonded", source="A/s", target="B/t", thread=Thread(size="M16"))
    assert c.effective_meshing == "nonconformal"
    with pytest.raises(ValueError, match="meshed apart"):
        Contact(name="T", type="bonded", source="A/s", target="B/t", meshing="conformal",
                thread=Thread(size="M16"))  # fmt: skip


def test_every_node_pairs_with_the_coaxial_cylinders():
    inner, outer = _ring(0.008), _ring(0.00801)  # 10 µm apart, the same (θ, z)
    s, t, d = threads.pairs(inner, np.arange(len(inner)), outer, 1000 + np.arange(len(outer)),
                            FRAME)  # fmt: skip
    assert len(s) == len(inner) and np.all(t == 1000 + s) and np.allclose(d, 0, atol=1e-12)


def test_tributary_and_flank_areas_sum_to_the_cylinders():
    n, levels, r, length = 72, 21, 0.008, 0.02
    pts = _ring(r, n, (0, length), levels)
    tris = []
    for j in range(levels - 1):
        for i in range(n):
            a, b = j * n + i, j * n + (i + 1) % n
            c, d = a + n, b + n
            tris += [(a, b, d), (a, d, c)]
    areas = threads.nodal_areas(np.array(tris), lambda k: pts[k])
    perimeter = 2 * n * r * math.sin(math.pi / n)  # the faceted cylinder's
    assert sum(areas.values()) == pytest.approx(perimeter * length, rel=1e-9)
    assert perimeter * length == pytest.approx(2 * math.pi * r * length, rel=1e-3)
    total = np.array(list(areas.values()))
    flank = threads.flank_areas(total, _spec("iso"), "upper")
    assert flank.sum() == pytest.approx(total.sum() / math.cos(math.radians(30)), rel=1e-12)


def test_forces_bin_by_pitch_from_the_mouth():
    z = np.array([0.0005, 0.0015, 0.0025, 0.0035, 0.0045])
    f = np.array([3.0, 2.0, 1.0, 1.0, 0.5])
    assert threads.per_thread(z, f, 0.002, 0.0) == [5.0, 2.0, 0.5]
    assert threads.per_thread(z, f, 0.002, 0.005, toward=-1) == [1.5, 3.0, 3.0]


def test_the_axis_is_fitted_to_a_tilted_cylinder():
    axis = np.array([1.0, 2.0, 2.0]) / 3
    e1, e2 = threads._perpendiculars(axis)
    t = np.linspace(0, 2 * math.pi, 40, endpoint=False)
    zs = np.linspace(-0.01, 0.01, 5)
    tt, zz = np.meshgrid(t, zs)
    centre = np.array([0.1, -0.2, 0.05])
    radial = np.cos(tt.ravel())[:, None] * e1 + np.sin(tt.ravel())[:, None] * e2
    pts = centre + 0.006 * radial + zz.ravel()[:, None] * axis
    o, a, r = threads.fit_axis(pts, radial)
    assert abs(a @ axis) == pytest.approx(1.0, abs=1e-12) and r == pytest.approx(0.006)
    off = (o - centre) - ((o - centre) @ axis) * axis
    assert np.linalg.norm(off) == pytest.approx(0.0, abs=1e-12)


def test_vdi_compliance_of_an_m16_in_steel():
    """0.5 d / (E A_d3) + 0.33 d / (E A_N) for M16 (d3 13.546 mm), E = 205 GPa."""
    e = 205e9
    got = threads.vdi_compliance(0.016, 13.546e-3, e, e, tapped=True)
    a_d3, a_n = math.pi * 13.546e-3**2 / 4, math.pi * 0.016**2 / 4
    assert got == pytest.approx(0.008 / (e * a_d3) + 0.33 * 0.016 / (e * a_n))
    assert 2e-10 < got < 6e-10  # ~0.38 µm/kN


def test_yamamotos_teeth_give_liu_et_als_coefficients():
    """Liu et al. 2024, Eq. 7 (ISO 60°): screw 0.015, 0.84, 0.106, 1.246 and nut 0.067,
    1.164, 0.293, 1.142 (× (1-ν²) or (1+ν)); Eq. 11's δ5 0.167 d2/P (1∓ν)."""
    nu = 0.3
    screw = threads.yamamoto(30.0, 1.0, nu, nut=False)
    nut = threads.yamamoto(30.0, 1.0, nu, nut=True)
    assert screw == pytest.approx((0.015 + 0.106 + 1.246) * (1 - nu**2) + 0.84 * (1 + nu), rel=2e-3)
    assert nut == pytest.approx((0.067 + 0.293 + 1.142) * (1 - nu**2) + 1.164 * (1 + nu), rel=2e-3)
    d2 = 14.701e-3
    with_radial = threads.yamamoto(30.0, 0.002, nu, nut=False, d2=d2, radial=True)
    plain = threads.yamamoto(30.0, 0.002, nu, nut=False)
    assert with_radial - plain == pytest.approx(0.167 * d2 / 0.002 * (1 - nu), rel=3e-3)
    a, b, c = threads.tooth(30.0, 1.0, nut=False)
    assert (a, b, c) == pytest.approx((0.75, 0.5, 0.2165), abs=2e-4)
