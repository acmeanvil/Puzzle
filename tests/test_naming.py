"""Persistent naming across geometry edits (ADR 0014): the prototype's measurements.

A bracket (plate, two bolt holes, four rounded corners) carries four selections. Each
edit regenerates the history; selections resolve in tiers:

1. history: names carried through OCCT's own operation history;
2. signatures: surface kind, area and centroid re-find faces where there is no
   history (a re-imported STEP);
3. detached: flagged, never guessed.
"""

import math

import pytest

pytest.importorskip("OCP")

from puzzle.geometry.history import (  # noqa: E402
    Op,
    match_signatures,
    regenerate,
    signature,
)

SELECTIONS = {
    "Bolt holes": ["Hole1/wall", "Hole2/wall"],
    "Fixed end": ["Plate/-x"],
    "Top": ["Plate/+z"],
    "Rounds": [f"Round/edge-{i}" for i in range(1, 5)],
}
PARAMS = {"r": 5.0, "x2": 60.0, "round": 4.0}


def _bracket(*, fillet=True, split=False):
    ops = [
        Op("box", "Plate", {"dx": 80, "dy": 40, "dz": 10}),
        Op("hole", "Hole1", {"x": 20, "y": 20, "r": "r"}),
        Op("hole", "Hole2", {"x": "x2", "y": 20, "r": "r"}),
    ]
    if fillet:
        ops.append(Op("fillet", "Round", {"rule": {"parallel": "z", "outer": True}, "r": "round"}))
    if split:
        ops.append(Op("split", "Middle", {"axis": "x", "at": 40}))
    return ops


def _area(faces):
    return sum(signature(f)[1] for f in faces)


def test_side_names_follow_the_axes():
    shape = regenerate([Op("box", "B", {"dx": 8, "dy": 4, "dz": 1})])
    for side, (axis, at) in {
        "-x": (0, 0),
        "+x": (0, 8),
        "-y": (1, 0),
        "+y": (1, 4),
        "-z": (2, 0),
        "+z": (2, 1),
    }.items():
        (face,) = shape.names[f"B/{side}"]
        assert signature(face)[2][axis] == pytest.approx(at)


def test_every_face_has_one_name():
    shape = regenerate(_bracket(), PARAMS)
    named = [f for fs in shape.names.values() for f in fs]
    assert len(shape.faces()) == len(named) == 12
    assert all(any(f.IsSame(g) for g in named) for f in shape.faces())


@pytest.mark.parametrize(
    ("edit", "params", "kwargs", "detached"),
    [
        ("hole radius 5 -> 6 mm", {"r": 6.0}, {}, set()),
        ("hole 2 moved 5 mm", {"x2": 55.0}, {}, set()),
        ("round radius 4 -> 2 mm", {"round": 2.0}, {}, set()),
        ("rounds removed", {}, {"fillet": False}, {"Rounds"}),
        ("split at x = 40", {}, {"split": True}, set()),
    ],
)
def test_history_carries_names_through_edits(edit, params, kwargs, detached):
    shape = regenerate(_bracket(**kwargs), PARAMS | params)
    lost = set()
    for selection, members in SELECTIONS.items():
        faces, missing = shape.resolve(members)
        if missing:
            lost.add(selection)
            assert len(missing) == len(members)  # a removed feature goes as a whole
        else:
            assert faces
    print(f"{edit}: detached {sorted(lost) or 'none'}")
    assert lost == detached

    if "radius" in edit and "hole" in edit:  # the right faces, with the new geometry
        walls, _ = shape.resolve(SELECTIONS["Bolt holes"])
        assert _area(walls) == pytest.approx(2 * 2 * math.pi * 6 * 10, rel=1e-6)
    if "split" in edit:  # the selection follows the split onto both halves
        top, _ = shape.resolve(SELECTIONS["Top"])
        assert len(top) == 2
        assert _area(top) == pytest.approx(
            _area(regenerate(_bracket(), PARAMS).resolve(SELECTIONS["Top"])[0]), rel=1e-9
        )


def _reimport(params):
    """Signature matches of each selection after a history-free re-import."""
    before = regenerate(_bracket(), PARAMS)
    faces = regenerate(_bracket(), PARAMS | params).faces()  # as from a STEP: no names
    report = {}
    for selection, members in SELECTIONS.items():
        old = [signature(f) for f in before.resolve(members)[0]]
        found = match_signatures(old, faces, tol=1.0)
        report[selection] = (sum(f is not None for f in found), len(found))
    return report


def test_signatures_refind_faces_after_a_small_change():
    report = _reimport({"x2": 60.5})  # one hole moved 0.5 mm
    print("re-import, hole moved 0.5 mm:", report)
    assert all(found == total for found, total in report.values())


def test_signatures_flag_faces_that_changed():
    report = _reimport({"round": 2.0})  # every round, and the faces they trim, change
    print("re-import, rounds 4 -> 2 mm:", report)
    assert report["Rounds"] == (0, 4)  # a quarter of the area: not the same faces
    assert report["Fixed end"] == (0, 1)  # 10% wider: flagged, not guessed
    assert report["Bolt holes"] == (2, 2)  # untouched by the change
