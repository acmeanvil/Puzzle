"""History geometry operations (G1, ADR 0014): shapes, face and body names, parameters.

Each operation is checked by measure: face areas and volumes against closed forms, so
a name is on the face it claims, not just present.
"""

import math

import pytest

pytest.importorskip("OCP")

from puzzle.geometry.history import (  # noqa: E402
    GeometryError,
    Op,
    body_signature,
    regenerate,
    signature,
)

MM = 1e-3


def area(shape, name):
    faces, missing = shape.resolve([name])
    assert not missing, f"no face '{name}'"
    return sum(signature(f)[1] for f in faces)


def volume(shape, body="Body"):
    return sum(body_signature(b)[0] for b in shape.bodies[body])


def test_primitives_name_their_faces():
    cyl = regenerate([Op("cylinder", "Pin", {"r": 5 * MM, "h": 20 * MM, "axis": "x"})])
    assert area(cyl, "Pin/wall") == pytest.approx(2 * math.pi * 5 * MM * 20 * MM)
    assert area(cyl, "Pin/start") == pytest.approx(math.pi * (5 * MM) ** 2)
    start, end = (signature(cyl.names[f"Pin/{k}"][0])[2][0] for k in ("start", "end"))
    assert start == pytest.approx(0) and end == pytest.approx(20 * MM)  # along +x

    cone = regenerate([Op("cone", "Tip", {"r1": 4 * MM, "r2": 2 * MM, "h": 6 * MM})])
    assert area(cone, "Tip/start") == pytest.approx(math.pi * 16 * MM**2)
    assert area(cone, "Tip/end") == pytest.approx(math.pi * 4 * MM**2)
    ball = regenerate([Op("sphere", "Ball", {"r": 3 * MM})])
    assert area(ball, "Ball/surface") == pytest.approx(4 * math.pi * 9 * MM**2)
    assert volume(ball) == pytest.approx(4 / 3 * math.pi * 27 * MM**3)


L_PROFILE = {"points": [[0, 0], [0.06, 0], [0.06, 0.01], [0.01, 0.01], [0.01, 0.04], [0, 0.04]]}


def test_extrude_names_start_end_and_each_side():
    s = regenerate([Op("extrude", "Angle", {"profile": L_PROFILE, "distance": 0.03})])
    lengths = [0.06, 0.01, 0.05, 0.03, 0.01, 0.04]  # the profile's segments, in order
    for i, length in enumerate(lengths, start=1):
        assert area(s, f"Angle/side-{i}") == pytest.approx(length * 0.03)
    l_area = 0.06 * 0.01 + 0.01 * 0.03
    assert area(s, "Angle/start") == pytest.approx(l_area)
    assert signature(s.names["Angle/end"][0])[2][2] == pytest.approx(0.03)
    assert volume(s) == pytest.approx(l_area * 0.03)


def test_revolve_makes_a_ring_and_a_sector():
    ring = {"points": [[0.01, 0], [0.02, 0], [0.02, 0.005], [0.01, 0.005]]}
    full = regenerate([Op("revolve", "Washer", {"profile": ring, "plane": "xz", "axis": "v"})])
    assert volume(full) == pytest.approx(math.pi * (0.02**2 - 0.01**2) * 0.005)
    assert "Washer/start" not in full.names  # a full turn has no ends
    quarter = regenerate(
        [Op("revolve", "Arc", {"profile": ring, "plane": "xz", "axis": "v", "angle": 90})]
    )
    assert volume(quarter) == pytest.approx(volume(full) / 4)
    assert area(quarter, "Arc/start") == pytest.approx(0.01 * 0.005)


def test_add_merges_the_seam_and_keeps_both_names():
    s = regenerate(
        [
            Op("box", "A", {"dx": 0.02, "dy": 0.01, "dz": 0.01}),
            Op("box", "B", {"x0": 0.02, "dx": 0.02, "dy": 0.01, "dz": 0.01}),
        ]
    )
    assert len(s.faces()) == 6  # one 40 mm block, not two with a seam
    top = s.names["A/+z"]
    assert top == s.names["B/+z"] or any(f.IsSame(g) for f in top for g in s.names["B/+z"])
    assert area(s, "A/+z") == pytest.approx(0.04 * 0.01)
    assert volume(s) == pytest.approx(0.04 * 0.01 * 0.01)


def test_cut_and_intersect():
    base = Op("box", "Block", {"dx": 0.02, "dy": 0.02, "dz": 0.02})
    pocket = Op(
        "cylinder", "Bore", {"r": 0.005, "h": 0.01, "x": 0.01, "y": 0.01, "z": 0.01, "mode": "cut"}
    )
    s = regenerate([base, pocket])
    assert volume(s) == pytest.approx(0.02**3 - math.pi * 0.005**2 * 0.01)
    assert "Bore/end" not in s.names  # it lay on the block's top, which the cut removed
    assert area(s, "Bore/start") == pytest.approx(math.pi * 0.005**2)  # the pocket floor
    ball = Op("sphere", "Ball", {"r": 0.01, "x": 0.01, "y": 0.01, "z": 0.01, "mode": "intersect"})
    assert volume(regenerate([base, ball])) == pytest.approx(4 / 3 * math.pi * 0.01**3)


def test_fillet_and_chamfer_by_rules():
    plate = Op("box", "Plate", {"dx": 0.08, "dy": 0.04, "dz": 0.01})
    s = regenerate([plate, Op("fillet", "Round", {"r": 0.004, "rule": {"face": "Plate/+z"}})])
    assert len([n for n in s.names if n.startswith("Round/")]) == 4  # the top's four edges
    c = regenerate(
        [plate, Op("chamfer", "Bevel", {"d": 0.002, "rule": {"between": ["Plate/+x", "Plate/+z"]}})]
    )
    assert area(c, "Bevel/edge-1") == pytest.approx(0.04 * 0.002 * math.sqrt(2), rel=1e-6)


def test_patterns_copy_a_feature_with_names():
    ops = [
        Op("box", "Plate", {"dx": 0.1, "dy": 0.03, "dz": 0.01}),
        Op("hole", "Hole", {"x": 0.02, "y": 0.015, "r": 0.004}),
        Op("pattern", "Row", {"feature": "Hole", "count": 3, "dx": 0.03}),
    ]
    s = regenerate(ops)
    walls = ["Hole/wall", "Hole#2/wall", "Hole#3/wall"]
    centres = [signature(s.names[w][0])[2][0] for w in walls]
    assert centres == pytest.approx([0.02, 0.05, 0.08])
    disc = [
        Op("cylinder", "Disc", {"r": 0.05, "h": 0.005}),
        Op("hole", "Bolt", {"x": 0.035, "y": 0.0, "r": 0.003}),
        Op("pattern", "Circle", {"feature": "Bolt", "count": 6, "angle": 60}),
    ]
    d = regenerate(disc)
    assert all(f"Bolt#{i}/wall" in d.names for i in range(2, 7))
    assert volume(d) == pytest.approx(math.pi * 0.005 * (0.05**2 - 6 * 0.003**2), rel=1e-6)


def test_move_and_rotate_keep_names():
    s = regenerate(
        [
            Op("box", "B", {"dx": 0.02, "dy": 0.01, "dz": 0.01}),
            Op("move", "Shift", {"dz": 0.05}),
            Op("rotate", "Turn", {"angle": 90, "axis": "z"}),
        ]
    )
    x, y, z = signature(s.names["B/+x"][0])[2]  # +x turned onto +y
    assert (x, y, z) == pytest.approx((-0.005, 0.02, 0.055), abs=1e-12)  # (x, y) -> (-y, x)


def test_enclosure_adds_an_air_body_sharing_the_interface():
    s = regenerate(
        [
            Op("box", "Part", {"dx": 0.02, "dy": 0.02, "dz": 0.02}),
            Op("enclosure", "Air", {"margin": 0.01}),
        ]
    )
    assert set(s.bodies) == {"Body", "Air"}
    assert volume(s, "Air") == pytest.approx(0.04**3 - 0.02**3)
    assert area(s, "Air/+x") == pytest.approx(0.04 * 0.04)
    assert area(s, "Part/+z") == pytest.approx(0.02 * 0.02)  # the interface keeps its name


def test_dxf_profile_in_millimetres_with_a_hole(tmp_path):
    ezdxf = pytest.importorskip("ezdxf")
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 4  # mm
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (80, 0), (80, 40), (0, 40)], close=True)
    msp.add_circle((20, 20), 5)
    doc.saveas(tmp_path / "plate.dxf")
    s = regenerate(
        [Op("extrude", "Plate", {"profile": {"dxf": "plate.dxf"}, "distance": 0.01})],
        root=tmp_path,
    )
    assert volume(s) == pytest.approx((0.08 * 0.04 - math.pi * 0.005**2) * 0.01, rel=1e-6)
    curved = [n for n, fs in s.names.items() if signature(fs[0])[0] == "Cylinder"]
    assert len(curved) == 1  # the circle's side is named like any other


def test_parameters_drive_the_shape_and_names_survive():
    ops = [
        Op("extrude", "Angle", {"profile": L_PROFILE, "distance": "depth"}),
        Op("hole", "Bolt", {"x": 0.04, "y": 0.005, "r": "bolt_r", "axis": "z"}),
    ]
    thin = regenerate(ops, {"depth": 0.02, "bolt_r": 0.003})
    thick = regenerate(ops, {"depth": 0.03, "bolt_r": 0.004})
    for s, depth in ((thin, 0.02), (thick, 0.03)):
        assert area(s, "Angle/side-1") == pytest.approx(0.06 * depth)
    assert area(thick, "Bolt/wall") > area(thin, "Bolt/wall")


@pytest.mark.parametrize(
    ("ops", "message"),
    [
        ([Op("box", "B", {"dx": "missing", "dy": 1, "dz": 1})], "unknown parameter"),
        (
            [
                Op("box", "B", {"dx": 1, "dy": 1, "dz": 1}),
                Op("fillet", "F", {"r": 0.1, "rule": {"face": "Nope"}}),
            ],
            "matches no edges",
        ),
        (
            [
                Op("box", "B", {"dx": 1, "dy": 1, "dz": 1}),
                Op("box", "C", {"dx": 1, "dy": 1, "dz": 1, "mode": "glue"}),
            ],
            "mode must be",
        ),
        ([Op("pattern", "P", {"feature": "X", "count": 2, "dx": 1})], "no earlier tool"),
        ([Op("spline", "S", {})], "unknown operation"),
    ],
)
def test_mistakes_are_explained(ops, message):
    with pytest.raises(GeometryError, match=message):
        regenerate(ops)
