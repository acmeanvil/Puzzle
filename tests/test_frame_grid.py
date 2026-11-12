"""Frames from a grid (ADR 0079 step 6): nodes at the intersections, members on the grid
lines, bracing in the bays, counted by hand."""

import pytest

from puzzle.frames.grid import Grid, parse_lengths, plan


def test_lengths_are_typed_as_drawings_write_them():
    assert parse_lengths("6, 6, 8") == [6, 6, 8]
    assert parse_lengths("3@6") == [6, 6, 6]
    assert parse_lengths("2 x 3.5; 4") == [3.5, 3.5, 4]


def test_a_plane_frame():
    nodes, members = plan(Grid([6, 6, 8], [], [4, 3.5]))
    assert len(nodes) == 4 * 3  # four column lines, three levels
    assert nodes["D1-2"] == [20.0, 0.0, 7.5]
    groups = [m[3] for m in members]
    assert groups.count("Columns") == 4 * 2 and groups.count("Beams X") == 3 * 2
    assert "Beams Y" not in groups and "Braces" not in groups


def test_a_space_frame_with_x_bracing_in_its_end_bays():
    nodes, members = plan(Grid([6, 6, 6], [5, 5], [3.5], bracing="x"))
    groups = [m[3] for m in members]
    assert groups.count("Columns") == 4 * 3
    assert groups.count("Beams X") == 3 * 3 and groups.count("Beams Y") == 4 * 2
    # x lines y = 0 and 10, end bays (2 each); y lines x = 0 and 18, end bays (2 each):
    # 8 braced bays, four diagonals each to a centre node
    assert groups.count("Braces") == 8 * 4
    assert len(nodes) == 4 * 3 * 2 + 8


def test_chevron_bracing_splits_its_beam():
    nodes, members = plan(Grid([6], [], [3.5], bracing="chevron"))
    beams = [m for m in members if m[3] == "Beams X"]
    assert len(beams) == 2  # the beam over the chevron, in two
    top = next(n for n in nodes if n.startswith("M"))
    assert nodes[top] == [3.0, 0.0, 3.5]
    assert {m[2] for m in members if m[3] == "Braces"} == {top}


def test_bad_grids_are_refused():
    with pytest.raises(ValueError, match="story"):
        plan(Grid([6], [], []))
    with pytest.raises(ValueError, match="bracing"):
        plan(Grid([6], [], [3], bracing="k"))
