"""The beam bolt (ADR 0027): rigid arms to the middle and six springs there reproduce a
two-node Timoshenko beam's 12 x 12 stiffness term for term, at any length (a short bolt
included, where shear governs)."""

import numpy as np
import pytest

from puzzle.adapters.common import beam_bolt_stiffness
from puzzle.materials import library_material


def _timoshenko(e, nu, d, length):
    """The textbook two-node Timoshenko beam along x (Przemieniecki), dofs per node
    ux, uy, uz, rx, ry, rz."""
    g = e / (2 * (1 + nu))
    a, i = np.pi * d**2 / 4, np.pi * d**4 / 64
    kappa = 6 * (1 + nu) / (7 + 6 * nu)
    phi = 12 * e * i / (kappa * g * a * length**2)
    k = np.zeros((12, 12))
    k[0, 0] = k[6, 6] = e * a / length
    k[0, 6] = k[6, 0] = -e * a / length
    k[3, 3] = k[9, 9] = g * 2 * i / length
    k[3, 9] = k[9, 3] = -g * 2 * i / length
    c = e * i / (length**3 * (1 + phi))
    for v, r, s in ((1, 5, 1), (2, 4, -1)):  # uy with rz, uz with ry (sign of coupling)
        k[v, v] = k[v + 6, v + 6] = 12 * c
        k[v, v + 6] = k[v + 6, v] = -12 * c
        k[v, r] = k[r, v] = k[v, r + 6] = k[r + 6, v] = s * 6 * c * length
        k[v + 6, r] = k[r, v + 6] = k[v + 6, r + 6] = k[r + 6, v + 6] = -s * 6 * c * length
        k[r, r] = k[r + 6, r + 6] = (4 + phi) * c * length**2
        k[r, r + 6] = k[r + 6, r] = (2 - phi) * c * length**2
    return k


def _arms_and_springs(springs, length):
    """Each end's motion carried rigidly to the middle, the springs between."""
    kt, kr = np.diag(springs["translation"]), np.diag(springs["rotation"])
    k_mid = np.block([[kt, np.zeros((3, 3))], [np.zeros((3, 3)), kr]])
    spring = np.block([[k_mid, -k_mid], [-k_mid, k_mid]])

    def arm(offset):  # middle = end + θ × offset
        t = np.eye(6)
        d = np.asarray(offset, float)
        t[0:3, 3:6] = -np.array([[0, -d[2], d[1]], [d[2], 0, -d[0]], [-d[1], d[0], 0]])
        return t

    t = np.zeros((12, 12))
    t[0:6, 0:6] = arm((length / 2, 0, 0))
    t[6:12, 6:12] = arm((-length / 2, 0, 0))
    return t.T @ spring @ t


@pytest.mark.parametrize("length_over_d", [0.5, 1.0, 2.0, 10.0])
def test_springs_and_arms_are_the_timoshenko_beam(length_over_d):
    steel = library_material("structural_steel")
    e = steel.properties["youngs_modulus"].to_si()
    nu = steel.properties["poissons_ratio"].to_si()
    d = 0.008
    length = length_over_d * d
    got = _arms_and_springs(beam_bolt_stiffness(steel, d, length), length)
    want = _timoshenko(e, nu, d, length)
    assert np.allclose(got, want, rtol=1e-9, atol=1e-6 * np.abs(want).max())
