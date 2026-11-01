"""The morph of ADR 0043 step 2.2 without a solver: CAD shapes moved by known fields.

- a uniform expansion u = e (x - x0) is affine, so the morphed box with a hole is the
  box scaled by 1 + e exactly: volume (1 + e)^3 V, the hole's radius (1 + e) r;
- a rigid rotation keeps the volume, and poles off the body (the hole's arcs) still
  land on the rotated shape;
- a bent bar u_y = -k x^2 (curved, not a pole-for-pole map) lands on the bent shape
  once the poles are refined;
- the STEP written reads back as one valid solid, in metres, with the part's name.
"""

import math

import numpy as np
import pytest

pytest.importorskip("OCP")

from puzzle.post.exports import morphed  # noqa: E402

L, B, H, R = 0.2, 0.02, 0.01, 0.004


def _plate():
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    box = BRepPrimAPI_MakeBox(L, B, H).Shape()
    hole = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(0.05, B / 2, -1), gp_Dir(0, 0, 1)), R, 3)
    return BRepAlgoAPI_Cut(box, hole.Shape()).Shape()


def _valid(shape) -> bool:
    from OCP.BRepCheck import BRepCheck_Analyzer

    return BRepCheck_Analyzer(shape).IsValid()


def _bounds(shape):
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    from puzzle.geometry.history import _static

    box = Bnd_Box()
    _static(BRepBndLib, "AddOptimal")(shape, box, False, False)
    lo, hi = box.CornerMin(), box.CornerMax()
    return np.array([lo.X(), lo.Y(), lo.Z(), hi.X(), hi.Y(), hi.Z()])


def test_a_uniform_expansion_is_exact():
    e = 1e-3
    shape = _plate()
    v0, _ = morphed._volume_area(shape)
    moved = morphed.morph(shape, lambda p: e * p, spacing=0.01)
    v1, _ = morphed._volume_area(moved)
    assert _valid(moved)
    assert v1 == pytest.approx((1 + e) ** 3 * v0, rel=1e-6)
    lo, hi = _bounds(moved)[:3], _bounds(moved)[3:]
    assert hi - lo == pytest.approx(np.array([L, B, H]) * (1 + e), abs=1e-6)  # OCC pads boxes


def test_a_rigid_rotation_keeps_the_shape():
    a = math.radians(2.0)
    turn = np.array([[math.cos(a), -math.sin(a), 0], [math.sin(a), math.cos(a), 0], [0, 0, 1]])
    shape = _plate()
    v0, a0 = morphed._volume_area(shape)
    moved = morphed.morph(shape, lambda p: p @ turn.T - p, spacing=0.01)
    v1, a1 = morphed._volume_area(moved)
    assert _valid(moved)
    assert v1 == pytest.approx(v0, rel=1e-5) and a1 == pytest.approx(a0, rel=1e-5)


def test_a_bent_bar_follows_the_curve():
    k = 0.5  # 1/m: the tip moves k L^2 = 20 mm
    shape = _plate()
    bend = lambda p: np.column_stack([0 * p[:, 0], -k * p[:, 0] ** 2, 0 * p[:, 0]])  # noqa: E731
    moved = morphed.morph(shape, bend, spacing=0.0025)
    assert _valid(moved)
    # Points of the top face (z = H) and the hole, bent: where the morphed faces put them.
    x = np.linspace(0.0, L, 41)
    a = np.linspace(0, 2 * np.pi, 13)
    before = np.vstack([np.column_stack([x, np.full_like(x, B / 2), np.full_like(x, H)]),
                        np.column_stack([0.05 + R * np.cos(a), B / 2 + R * np.sin(a),
                                         np.full_like(a, H / 3)])])  # fmt: skip
    after = before + bend(before)
    assert morphed._deviation(shape, moved, before, after) < 1e-5 * k * L**2
    assert _bounds(moved)[1] == pytest.approx(-k * L**2, rel=1e-4)  # lowest y: the tip


def test_the_step_reads_back_as_a_named_solid(tmp_path):
    from puzzle.geometry.prep import read_cad

    moved = morphed.morph(_plate(), lambda p: 1e-3 * p, spacing=0.01)
    path = morphed.write_step({"Plate": moved}, tmp_path / "plate.step")[0]
    assert "SI_UNIT(.MILLI.,.METRE.)" in path.read_text()
    back, labels = read_cad(path)
    assert list(labels) == ["Plate"] and _valid(back)
    v, _ = morphed._volume_area(back)
    assert v == pytest.approx(morphed._volume_area(moved)[0], rel=1e-6)
    two = morphed.write_step({"A": moved, "B": _plate()}, tmp_path / "two.step")[0]
    assert set(read_cad(two)[1]) == {"A", "B"}


def test_a_jump_the_poles_cannot_follow_is_measured():
    """A displacement jumping 1 mm over 0.4 mm, morphed with poles 10 mm apart: the
    morphed faces miss the moved points by far more than 1 % of the jump."""
    jump = 1e-3

    def field(p):
        return np.column_stack([0 * p[:, 0], jump * np.tanh((p[:, 0] - 0.12) / 2e-4), 0 * p[:, 0]])

    shape = _plate()
    moved = morphed.morph(shape, field, spacing=0.01)
    x = np.linspace(0.11, 0.13, 41)
    before = np.column_stack([x, np.full_like(x, B / 2), np.full_like(x, H)])
    assert morphed._deviation(shape, moved, before, before + field(before)) > 0.1 * jump
