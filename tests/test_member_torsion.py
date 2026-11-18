"""Torsion a member check can't cover is said, not left out (ADR 0090, ADR-6).

ADR 0079 step 5: stainless torsion (AISC 370 G8, G9) and the ADM's aren't built; AISC
360 checks torsion of HSS only (H3.1). A member carrying such a torque keeps its other
checks and gets a warning naming the torque (the maintainer, 2026-11-17). A square HSS
100 × 100 × 6 mm of S30400 carries a 1 kN·m torque: warned on 370, as the same tube in
6061-T6 is on the ADM; in A992 steel it is checked by H3.1(b), whose strength is worked
here by hand (C = 2(B − t)(H − t)t − 4.5(4 − π)t³, h/t below 2.45 √(E/F_y) so
F_cr = 0.6 F_y, φ 0.90).
"""

import math

import numpy as np
import pytest

from puzzle.frames import aisc360, design
from puzzle.frames.aisc360 import Props
from puzzle.materials import library_material

B, T_WALL = 0.100, 0.006  # m


def _tube() -> Props:
    b, t = B, T_WALL
    inner = b - 2 * t
    area = b * b - inner * inner
    i = (b**4 - inner**4) / 12
    j = t * (b - t) ** 3  # Bredt: 4 A_m² t / perimeter
    return Props("RHS", area, i, i, 0.0, 0.0, 0.0, 0.0, j, d=b, bf=b, t=t, h=b - 3 * t)


def _forces(torque: float) -> tuple[np.ndarray, np.ndarray]:
    s = np.linspace(0.0, 2.0, 5)
    f = np.zeros((5, 6))
    f[:, 0] = 1e3  # a little tension, so the tension check runs
    f[:, 3] = torque
    return s, f


def _check(key: str, torque: float):
    from puzzle.model.schema import MemberDesign

    m = library_material(key)
    d = MemberDesign()
    code = design.code_for(m, d)
    module = design.code_module(code, m, d.method)
    steel = module.material(m) if hasattr(module, "material") else design._steel(m)
    s, f = _forces(torque)
    return design.check_member(_tube(), steel, d, 2.0, s, f, d.method, module)


@pytest.mark.parametrize(("key", "why"), [("stainless_steel_304", "G8, G9"),
                                          ("aluminium_6061_t6", "H.2")])  # fmt: skip
def test_torsion_on_stainless_and_aluminium_is_warned_of(key, why):
    got = _check(key, 1e3)
    assert len(got["warnings"]) == 1 and why in got["warnings"][0]
    assert "1000 N·m" in got["warnings"][0]
    # the other checks stand, with no torsion among them
    plain = _check(key, 0.0)
    assert [c["ratio"] for c in got["checks"]] == [c["ratio"] for c in plain["checks"]]
    # no torque, or round-off of one, warns of nothing
    for torque in (0.0, 1e-12):
        found = _check(key, torque)
        assert found["warnings"] == []
        assert not any(c["check"] == "torsion" for c in found["checks"])


def test_torsion_on_a_steel_tube_is_checked_by_h31b():
    m = library_material("astm_a992")
    fy = m.properties["yield_strength"].to_si()
    got = _check("astm_a992", 1e3)
    tor = next(c for c in got["checks"] if c["check"] == "torsion")
    c = 2 * (B - T_WALL) * (B - T_WALL) * T_WALL - 4.5 * (4 - math.pi) * T_WALL**3
    assert tor["clause"] == "H3.1(b)"
    assert tor["available"] == pytest.approx(0.90 * 0.6 * fy * c, rel=1e-9)
    assert got["warnings"] == []


def test_torque_on_a_steel_i_is_warned_of():
    p = Props("I", 5e-3, 1e-4, 1e-5, 0, 0, 0, 0, 2e-7, d=0.3, bf=0.15, tf=0.01, tw=0.006)
    s, f = _forces(500.0)
    assert "Design Guide 9" in design.unchecked(p, f, aisc360)
    assert design.unchecked(p, _forces(0.0)[1], aisc360) is None
