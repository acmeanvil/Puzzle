"""Beam sections' outlines for the view (ADR 0055 decision 5): `post.beam_shapes`.

The drawn polygons hold each table section's area (fillets drawn where the table gives
a root radius, AISC's hollow-section corners as 2t outside and t inside) within a few
per cent, and their second moments say the web lies along the second axis: ∫v² dA is
the table's strong-axis I1. Swept along a member, the solid is as wide as the flanges
along the first axis and as deep as the web along the second (member × first).
"""

import numpy as np
import pytest

from puzzle import sections
from puzzle.model.schema import BeamSection, Quantity
from puzzle.post import beam_shapes as bs


def _second_moments(regions):
    """(∫v² dA, ∫u² dA) about the centroid, from the polygons (Green's theorem)."""

    def loop(p):
        u, v = p[:, 0], p[:, 1]
        u1, v1 = np.roll(u, -1), np.roll(v, -1)
        cross = u * v1 - u1 * v
        return (
            ((v**2 + v * v1 + v1**2) * cross).sum() / 12,
            ((u**2 + u * u1 + u1**2) * cross).sum() / 12,
        )

    total = np.zeros(2)
    for outer, hole in regions:
        total += loop(outer)
        if hole is not None:
            total -= loop(hole)
    return total


def _table_sections():
    for standard in sections.STANDARDS:
        for d in sections.designations(standard):
            try:
                sec = sections.get(standard, d)
            except KeyError:
                continue  # a row the library doesn't take as a section yet
            yield sec


def test_areas_match_the_tables():
    """Every table section's outline within 5 % of its area (M shapes, small and drawn
    without their fillets, within 10 %)."""
    worst = {}
    for sec in _table_sections():
        off = bs.area(bs.library_outline(sec)) / sec.A - 1
        key = sec.family if sec.family == "M" else sec.shape
        worst[key] = max(worst.get(key, 0.0), abs(off))
    assert {"I", "C", "T", "L", "2L", "RHS", "CHS"} <= set(worst)
    assert worst["M"] < 0.10
    assert all(v < 0.05 for k, v in worst.items() if k != "M"), worst


@pytest.mark.parametrize(
    "standard, designation",
    [
        ("aisc", "W12X26"),
        ("en", "IPE 300"),
        ("aisc", "C10X15.3"),
        ("aisc", "WT6X13"),
        ("aisc", "HSS6X4X1/4"),
        ("aisc", "Pipe6STD"),
        ("en", "UPN 200"),
    ],  # fmt: skip
)
def test_the_web_lies_along_the_second_axis(standard, designation):
    """∫v² dA is the strong axis's I1 (and ∫u² dA the weak I2), so the outline stands
    as the solver bends it."""
    sec = sections.get(standard, designation)
    regions = bs.library_outline(sec)
    strong, weak = _second_moments(regions)
    assert strong == pytest.approx(sec.I1, rel=0.06)
    assert weak == pytest.approx(sec.I2, rel=0.3)  # a channel's flanges taper
    np.testing.assert_allclose(bs._centroid(regions), 0.0, atol=1e-9)


def test_angles_put_a_leg_along_each_axis():
    """A single angle's legs: b along the first axis, d along the second; a double
    angle's two, back to back with the designation's gap."""
    sec = sections.Section("aisc", "L5X3X1/2", "L", "L", 0.0024, 0, 0, 0, 0, 0, None,
                           {"d": 0.0762, "b": 0.127, "t": 0.0127})  # fmt: skip
    ((loop, _),) = bs.library_outline(sec)
    assert np.ptp(loop[:, 0]) == pytest.approx(0.127)
    assert np.ptp(loop[:, 1]) == pytest.approx(0.0762)
    assert bs.double_angle_gap("2L4X3X5/8X3/8LLBB") == pytest.approx(0.375 * 0.0254)
    assert bs.double_angle_gap("2L12X12X1-3/8X1-1/2") == pytest.approx(1.5 * 0.0254)
    assert bs.double_angle_gap("2L12X12X1-3/8") == 0.0
    two = sections.get("aisc", "2L4X3X5/8X3/8LLBB")
    right, left = (r[0] for r in bs.library_outline(two))
    assert right[:, 0].min() - left[:, 0].max() == pytest.approx(0.375 * 0.0254)


def test_puzzles_own_shapes():
    mm = lambda v: Quantity(value=v, unit="mm")  # noqa: E731
    cases = {
        "rectangle": (dict(width=mm(20), height=mm(40)), 20e-6 * 40),
        "box": (dict(width=mm(20), height=mm(40), thickness=mm(2)), 800e-6 - 16e-6 * 36),
        "circle": (dict(diameter=mm(30)), np.pi * 0.015**2),
        "tube": (dict(diameter=mm(30), thickness=mm(3)), np.pi * (0.015**2 - 0.012**2)),
    }
    for shape, (sizes, want) in cases.items():
        regions = bs.outline(BeamSection(selection="F", shape=shape, **sizes))
        assert bs.area(regions) == pytest.approx(want, rel=0.01), shape
    ((outer, _),) = bs.outline(BeamSection(selection="F", width=mm(20), height=mm(40)))
    assert np.ptp(outer[:, 0]) == pytest.approx(0.02)  # the width along the first axis


def test_a_swept_member_stands_on_its_axes():
    """A W12X26 along z, its first axis y: the flanges' width along y, the depth along
    x (z × y = -x), the length along z; closed caps on both ends."""
    pytest.importorskip("pyvista")
    sec = sections.get("aisc", "W12X26")
    member = bs.Member("F", "W12X26", bs.library_outline(sec), np.array([1, 2, 3]),
                       np.array([10, 11]), np.array([0.0, 1.0, 0.0]),
                       np.array([0.0, 0.0, 1.0]))  # fmt: skip
    xyz = {1: (0, 0, 0), 2: (0, 0, 0.5), 3: (0, 0, 1.0)}
    solid = bs.solid([member], lambda ids: np.array([xyz[i] for i in ids], float),
                     lambda ids: np.asarray(ids, float))  # fmt: skip
    lo, hi = np.asarray(solid.bounds).reshape(3, 2).T
    assert hi[1] - lo[1] == pytest.approx(sec.sizes["bf"])
    assert hi[0] - lo[0] == pytest.approx(sec.sizes["d"])
    assert hi[2] - lo[2] == pytest.approx(1.0)
    assert solid.n_open_edges == 0  # sides and caps close it
    assert solid.volume == pytest.approx(bs.area(member.regions), rel=1e-6)  # caps outward
    assert set(np.unique(solid.point_data["value"])) == {1.0, 2.0, 3.0}
    hollow = bs.Member("F", "box", bs.outline(BeamSection(
        selection="F", shape="box", width=Quantity(value=20, unit="mm"),
        height=Quantity(value=40, unit="mm"), thickness=Quantity(value=2, unit="mm"))),
        np.array([1, 3]), np.array([10]), np.array([0.0, 1.0, 0.0]),
        np.array([0.0, 0.0, 1.0]))  # fmt: skip
    tube = bs.solid([hollow], lambda ids: np.array([xyz[i] for i in ids], float))
    assert tube.n_open_edges == 0
    assert tube.volume == pytest.approx(bs.area(hollow.regions), rel=1e-6)
