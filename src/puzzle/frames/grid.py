"""A frame from a grid (ADR 0079 step 6), as SAP2000's and RISA's grid templates make one:
bay widths along x and y, story heights up z, then members generated on the grid lines.

- Nodes at every grid intersection, named by their grid lines: `A1-0` is column line A
  (x), row 1 (y), level 0 (the base); levels count up from 0.
- Columns (group "Columns") at every plan point between consecutive levels;
  beams (groups "Beams X" and "Beams Y") along the grid lines at each level above the
  base; bracing (group "Braces") in the bays named, X (both diagonals, meeting at a
  node at the bay's centre) or chevron (inverted V, to a node at the beam's middle),
  on the frame lines at y = the first or last row (x bays) and x = the first or last
  column line (y bays), each story.
- `plan(...)` returns the nodes and members `frames.geometry` takes, so a grid frame is
  the same `frame` history step as one typed in or read from a DXF, and edits like one.

Bays and heights in metres. A 2D frame is a grid with no y bays.
"""

from __future__ import annotations

from dataclasses import dataclass, field

LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"  # I and O left out, as drawings do


def _column_line(k: int) -> str:
    return (
        LETTERS[k]
        if k < len(LETTERS)
        else f"{LETTERS[k // len(LETTERS) - 1]}{LETTERS[k % len(LETTERS)]}"
    )


@dataclass
class Grid:
    bays_x: list[float]
    bays_y: list[float] = field(default_factory=list)
    stories: list[float] = field(default_factory=list)
    beams: bool = True
    columns: bool = True
    bracing: str = "none"  # none, x, chevron
    braced_bays: str = "ends"  # ends (the first and last bay of each braced line), all

    def check(self) -> None:
        if not self.bays_x or any(b <= 0 for b in self.bays_x + self.bays_y + self.stories):
            raise ValueError("a grid needs at least one x bay, and every bay and story positive")
        if not self.stories:
            raise ValueError("a grid needs at least one story height")
        if self.bracing not in ("none", "x", "chevron"):
            raise ValueError(f"bracing is none, x or chevron, not {self.bracing!r}")


def _cumulative(values: list[float]) -> list[float]:
    out, at = [0.0], 0.0
    for v in values:
        at += v
        out.append(at)
    return out


def plan(grid: Grid) -> tuple[dict[str, list[float]], list[list[str]]]:
    """(nodes {name: [x, y, z]}, members [[name, i, j, group]])."""
    grid.check()
    xs, ys, zs = _cumulative(grid.bays_x), _cumulative(grid.bays_y), _cumulative(grid.stories)
    nodes: dict[str, list[float]] = {}
    members: list[list[str]] = []

    def node(i: int, j: int, k: int) -> str:
        name = f"{_column_line(i)}{j + 1}-{k}"
        nodes.setdefault(name, [xs[i], ys[j], zs[k]])
        return name

    def member(name: str, a: str, b: str, group: str) -> None:
        members.append([name, a, b, group])

    for k in range(len(zs)):
        for i in range(len(xs)):
            for j in range(len(ys)):
                node(i, j, k)
    if grid.columns:
        for k in range(1, len(zs)):
            for i in range(len(xs)):
                for j in range(len(ys)):
                    a, b = node(i, j, k - 1), node(i, j, k)
                    member(f"C{a}", a, b, "Columns")
    if grid.beams:
        for k in range(1, len(zs)):
            for j in range(len(ys)):
                for i in range(len(xs) - 1):
                    a, b = node(i, j, k), node(i + 1, j, k)
                    member(f"BX{a}", a, b, "Beams X")
            for i in range(len(xs)):
                for j in range(len(ys) - 1):
                    a, b = node(i, j, k), node(i, j + 1, k)
                    member(f"BY{a}", a, b, "Beams Y")
    if grid.bracing != "none":
        _brace(grid, xs, ys, zs, nodes, members, node)
    return nodes, members


def _bays(count: int, which: str) -> list[int]:
    if which == "all" or count <= 2:
        return list(range(count))
    return [0, count - 1]


def _brace(grid, xs, ys, zs, nodes, members, node) -> None:
    lines_x = [0, len(ys) - 1] if len(ys) > 1 else [0]  # frame lines along x (rows)
    lines_y = [0, len(xs) - 1] if len(ys) > 1 else []  # along y (column lines)
    for k in range(1, len(zs)):
        for j in sorted(set(lines_x)):
            for i in _bays(len(xs) - 1, grid.braced_bays):
                corners = (
                    node(i, j, k - 1),
                    node(i + 1, j, k - 1),
                    node(i, j, k),
                    node(i + 1, j, k),
                )
                mid = ((xs[i] + xs[i + 1]) / 2, ys[j])
                _one(grid.bracing, corners, mid, zs[k - 1], zs[k], nodes, members, f"x{j}")
        for i in sorted(set(lines_y)):
            for j in _bays(len(ys) - 1, grid.braced_bays):
                corners = (
                    node(i, j, k - 1),
                    node(i, j + 1, k - 1),
                    node(i, j, k),
                    node(i, j + 1, k),
                )
                mid = (xs[i], (ys[j] + ys[j + 1]) / 2)
                _one(grid.bracing, corners, mid, zs[k - 1], zs[k], nodes, members, f"y{i}")


def _one(kind, corners, mid, z0, z1, nodes, members, tag) -> None:
    bl, br, tl, tr = corners
    if kind == "x":
        centre = f"X{bl}{tag}"
        nodes[centre] = [mid[0], mid[1], (z0 + z1) / 2]
        for end in (bl, br, tl, tr):
            members.append([f"D{end}{centre}", end, centre, "Braces"])
    else:  # chevron: both from the bottom corners to the beam's middle
        top = f"M{tl}{tag}"
        nodes[top] = [mid[0], mid[1], z1]
        for end in (bl, br):
            members.append([f"D{end}{top}", end, top, "Braces"])
        _split_beam(members, tl, tr, top)


def _split_beam(members, a, b, middle) -> None:
    """The beam a-b made two, meeting the chevron's node at its middle."""
    for k, m in enumerate(members):
        if {m[1], m[2]} == {a, b}:
            members[k] = [m[0], m[1], middle, m[3]]
            members.append([f"{m[0]}b", middle, m[2], m[3]])
            return


def parse_lengths(text: str) -> list[float]:
    """'6, 6, 8' or '3@6' (three bays of 6) or '4 x 3.5', in metres."""
    out: list[float] = []
    for part in text.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        for sep in ("@", "x", "*"):
            if sep in part:
                count, _, value = part.partition(sep)
                out += [float(value)] * int(float(count))
                break
        else:
            out.append(float(part))
    return out
