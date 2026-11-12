"""AISC 360-16 J2 for light welds (ADR 0078 step 2), against the Manual's own closed
forms: a 1/16 in E70 fillet carries φR_n/l = 1.392 kip/in along its axis (Manual Eq.
8-2a, 0.75 × 0.60 × 70 × 0.707 / 16) and 1.5 times that across it (θ = 90°, Eq. J2-5)."""

import numpy as np
import pytest

from puzzle.model.schema import Quantity, Weld
from puzzle.weld_strength import OMEGA, PHI, check, directional_factor, throat

KIP_PER_IN = 4448.2216 / 0.0254  # N/m
KSI = 6.894757e6


def _weld(leg_in, leg2_in=None, **kw):
    q = lambda v: Quantity(value=v, unit="inch")  # noqa: E731
    return Weld(name="W", path="P/e", faces=["P/a", "Q/b"], leg=q(leg_in),
                leg2=q(leg2_in) if leg2_in else None, **kw)  # fmt: skip


def test_the_manuals_sixteenth_of_an_inch():
    w = _weld(1 / 16)
    got = check(w, 70 * KSI, [1.0], [0.0], [1.0])
    per = PHI * got["nominal_N_per_m"][0] / KIP_PER_IN
    assert per == pytest.approx(1.392, abs=5e-4)
    across = check(w, 70 * KSI, [0.0], [1.0], [1.0], directional=True)
    assert PHI * across["nominal_N_per_m"][0] / KIP_PER_IN == pytest.approx(1.5 * 1.392, abs=1e-3)


def test_throats_and_the_directional_factor():
    assert throat(_weld(0.25)) == pytest.approx(0.25 * 0.0254 / np.sqrt(2))
    assert throat(_weld(0.25, 0.5)) == pytest.approx(0.25 * 0.5 / np.hypot(0.25, 0.5) * 0.0254)
    assert directional_factor(np.array([1.0]), np.array([1.0]))[0] == pytest.approx(
        1 + 0.5 * np.sin(np.pi / 4) ** 1.5
    )


def test_utilisations_for_lrfd_and_asd():
    w = _weld(5 / 16)
    rn = 0.6 * 70 * KSI * throat(w)  # N/m
    got = check(w, 70 * KSI, [1.0, 1.0], [0.0, 0.0], [0.5 * PHI * rn, 0.25 * PHI * rn])
    assert got["governing"] == 0
    assert got["utilisation_lrfd"][0] == pytest.approx(0.5)
    assert got["utilisation_asd"][0] == pytest.approx(0.5 * PHI * OMEGA)


def test_cjp_is_its_base_metals():
    got = check(_weld(0.5, type="groove", groove="cjp"), 70 * KSI, [1.0], [0.0], [1.0])
    assert not got["checked"] and "base metal" in got["why"]


def test_the_filler_is_the_welds_or_its_base_metals_match():
    from uuid import uuid4

    from puzzle.materials import add_library_material
    from puzzle.model.schema import Component, MaterialAssignment, PartInstance, Project
    from puzzle.weld_strength import filler

    p = Project(name="w")

    def joint(material):
        m = add_library_material(p, material)
        parts = [PartInstance(name="P", geometry=uuid4())]
        assigned = [MaterialAssignment(material=m.id, selection="P/Body")]
        return Component(name="C", mesh=uuid4(), parts=parts, materials=assigned)

    assert filler(p, joint("structural_steel"), _weld(0.25))[:2] == ("E70XX", 482.6e6)
    with pytest.raises(ValueError, match="AISC 370"):
        filler(p, joint("stainless_steel_304"), _weld(0.25))
    e80 = add_library_material(p, "filler_e80xx")
    own = filler(p, joint("stainless_steel_304"), _weld(0.25, filler=e80.id))
    assert own[0] == "E80XX electrode" and own[1] == pytest.approx(551.6e6)
