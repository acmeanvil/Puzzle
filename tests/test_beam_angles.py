"""Single angles (ADR 0055): principal axes from the tables, and which way they lie.

An angle's legs run from the heel along its first axis and its second (member × first);
both tables give the major principal axis at α from the first axis toward the second.
Checked here against the legs' own product of inertia (thin rectangles, no fillets),
which fixes the sign of α; tests/integration/test_beam_angles.py checks the solvers.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from puzzle import sections
from puzzle.beams import principal


def _legs(along_x: float, along_y: float, t: float):
    """Ix, Iy, Ixy about the centroid of legs from the heel along +x and +y."""
    rects = [(0.0, along_x, 0.0, t), (0.0, t, t, along_y)]
    area = sum((x1 - x0) * (y1 - y0) for x0, x1, y0, y1 in rects)
    cx = sum((x1 - x0) * (y1 - y0) * (x0 + x1) / 2 for x0, x1, y0, y1 in rects) / area
    cy = sum((x1 - x0) * (y1 - y0) * (y0 + y1) / 2 for x0, x1, y0, y1 in rects) / area
    ix = iy = ixy = 0.0
    for x0, x1, y0, y1 in rects:
        w, h = x1 - x0, y1 - y0
        mx, my = (x0 + x1) / 2 - cx, (y0 + y1) / 2 - cy
        ix += w * h**3 / 12 + w * h * my**2
        iy += h * w**3 / 12 + w * h * mx**2
        ixy += w * h * mx * my
    return ix, iy, ixy


def _angles():
    for standard in sections.STANDARDS:
        for family in sections.families(standard):
            for name in sections.designations(standard, family):
                sec = sections.get(standard, name)
                if sec.shape == "L":
                    yield sec


def test_every_table_angle_has_its_major_axis_where_its_legs_put_it():
    checked = 0
    for sec in _angles():
        s = sec.sizes
        # Both tables: the x leg is AISC's d and EN's bf (the shorter), the y leg the other.
        x_leg = s["d"] if sec.standard == "aisc" else s.get("bf", s["d"])
        y_leg = s.get("b", s["d"]) if sec.standard == "aisc" else s["d"]
        ix, iy, ixy = _legs(x_leg, y_leg, s["t"])
        theta = 0.5 * math.degrees(math.atan2(-2 * ixy, ix - iy))  # major axis from x
        mean, radius = (ix + iy) / 2, math.hypot((ix - iy) / 2, ixy)
        assert theta == pytest.approx(sec.alpha, abs=0.6), sec.designation
        # The tables include the root fillet; thin legs don't: a few per cent.
        assert mean + radius == pytest.approx(sec.I1, rel=0.06), sec.designation
        assert mean - radius == pytest.approx(sec.I2, rel=0.06), sec.designation
        assert sec.I1 > sec.I2 and sec.J > 0
        checked += 1
    assert checked > 200  # AISC's and EN's angles


def test_equal_angles_lie_at_45_degrees_and_en_j_from_thin_walls():
    sec = sections.get("en", "L 200 x 200 x 19")
    assert sec.alpha == 45.0
    t, b = 0.019, 0.2
    assert pytest.approx((2 * b - t) * t**3 / 3) == sec.J
    # Against AISC's tabulated J on an equal angle, the same formula is close.
    aisc = sections.get("aisc", "L8X8X1")
    s = aisc.sizes
    assert (2 * s["d"] - s["t"]) * s["t"] ** 3 / 3 == pytest.approx(aisc.J, rel=0.02)


def test_principal_turns_toward_the_other_leg_or_away_when_mirrored():
    first, along = np.array([0.0, 1.0, 0.0]), np.array([1.0, 0.0, 0.0])
    u = principal(first, along, 30.0, mirror=False)  # the other leg along x × y = z
    assert u == pytest.approx([0.0, math.cos(math.radians(30)), math.sin(math.radians(30))])
    m = principal(first, along, 30.0, mirror=True)
    assert m == pytest.approx([0.0, math.cos(math.radians(30)), -math.sin(math.radians(30))])
    # The member the other way round puts the other leg on the other side.
    assert principal(first, -along, 30.0, mirror=False) == pytest.approx(m)


def test_a_beam_section_takes_single_angles_now():
    from puzzle.model.schema import BeamSection

    b = BeamSection(selection="F", shape="library", standard="aisc", designation="L4X4X1/2")
    assert not b.mirror and not b.principal
