"""Single-angle cantilevers (ADR 0055) against beam theory in principal axes.

A 1 m cantilever along x, its first axis y: one leg along y, the other along x × y = z
(along −z when mirrored). Its major principal axis u lies α from y toward that leg, v
square to it. A tip load F bends it about each principal axis apart:

    δ = (F·u) u (L³/(3E Iv) + L/(G Au)) + (F·v) v (L³/(3E Iu) + L/(G Av)),

so a load along one leg also moves the tip along the other, by an amount and a sign
set by α: what fixes the sign of α in the solvers (Code_Aster's GENERALE beams and
CalculiX's spring beams). The load acts at the centroid, as the beam elements take it
(the shear centre at the heel is not modelled, as in most beam codes: a real angle
loaded at its centroid also twists a little).
"""

from __future__ import annotations

import numpy as np
import pytest
from solvers import available
from test_beam_sections import E, G, P, _frame, _tip, q

from puzzle import sections

ANGLES = [("aisc", "L4X4X1/2"), ("aisc", "L5X3-1/2X1/4"), ("en", "L 200 x 100 x 14")]


def _expected(sec, load, mirror):
    t = np.radians(sec.alpha)
    first, other = np.array([0.0, 1.0, 0.0]), np.array([0.0, 0.0, -1.0 if mirror else 1.0])
    u = np.cos(t) * first + np.sin(t) * other
    v = np.cross(np.array([1.0, 0.0, 0.0]), u)
    flex_u = 1 / (3 * E * sec.I2) + 1 / (G * sec.A1)  # along u: bending about v
    flex_v = 1 / (3 * E * sec.I1) + 1 / (G * sec.A2)
    return (load @ u) * u * flex_u + (load @ v) * v * flex_v


SOLVERS = [pytest.param(s, marks=pytest.mark.skipif(not available(s), reason=f"no {s}"))
           for s in ("code_aster", "calculix")]  # fmt: skip


@pytest.mark.parametrize("solver", SOLVERS)
@pytest.mark.parametrize("standard, designation", ANGLES, ids=[a[1] for a in ANGLES])
@pytest.mark.parametrize("mirror", [False, True], ids=["legs y and z", "mirrored"])
def test_an_angle_bends_about_its_principal_axes(tmp_path, solver, standard, designation, mirror):
    pytest.importorskip("OCP")
    sec = sections.get(standard, designation)
    kw = {"shape": "library", "standard": standard, "designation": designation,
          "orientation": (0.0, 1.0, 0.0), "mirror": mirror}  # fmt: skip
    run, u = _frame(tmp_path, solver, [[0, 0, 0], [1, 0, 0]], kw, ("x", 1.0),
                    {"z": q(-P, "N")})  # fmt: skip
    got = np.asarray(_tip(run, u, (1, 0, 0)), float)
    want = _expected(sec, np.array([0.0, 0.0, -P]), mirror)
    hand = "mirrored" if mirror else ""
    print(f"{solver} {designation} {hand}: tip y {got[1]:.5g}, z {got[2]:.5g}; "
          f"expected {want[1]:.5g}, {want[2]:.5g}")  # fmt: skip
    # The load along one leg moves the tip along the other: its sign is α's.
    assert np.sign(got[1]) == np.sign(want[1]) != 0
    assert got[1:] == pytest.approx(want[1:], rel=1e-2)
