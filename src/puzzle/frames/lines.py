"""A frame's lines (ADR 0079): nodes and the members between them, from a list or a DXF.

A frame is a line body: each member a straight edge between two nodes, members meeting
at a node sharing its vertex, so they are joined there (rigidly, unless a release says
otherwise). The `frame` history step builds it (`geometry/history.py`), naming each
member's edge by its name and each group's edges by the group's: a DXF's layer is a
group, so a layer "Columns" becomes the edge selection a section goes on.

Pure Python (no OpenCASCADE), so it is tested and used without OCP.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

DXF_UNITS = {1: 0.0254, 2: 0.3048, 4: 0.001, 5: 0.01, 6: 1.0, 14: 0.1}  # $INSUNITS -> m
TOLERANCE = 1e-6  # m: nodes closer than this are one node


class FrameError(ValueError):
    pass


@dataclass
class Member:
    name: str
    i: str  # its start node
    j: str  # its end node
    group: str | None = None


@dataclass
class Lines:
    nodes: dict[str, tuple[float, float, float]] = field(default_factory=dict)
    members: list[Member] = field(default_factory=list)

    def groups(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for m in self.members:
            if m.group:
                out.setdefault(m.group, []).append(m.name)
        return out

    def length(self, m: Member) -> float:
        a, b = self.nodes[m.i], self.nodes[m.j]
        return math.dist(a, b)


def from_args(args: dict, root: Path | None = None) -> Lines:
    """The `frame` step's arguments: `nodes` {name: [x, y, z]} and `members` (each
    [name, i, j] or [name, i, j, group], or a dict with those keys), or a `dxf` file
    (with `layer`, `scale`); both may be given, the DXF's added to the listed."""
    lines = Lines()
    for name, xyz in (args.get("nodes") or {}).items():
        x, y, z = (list(xyz) + [0.0, 0.0, 0.0])[:3]
        lines.nodes[str(name)] = (float(x), float(y), float(z))
    for k, m in enumerate(args.get("members") or [], start=1):
        if isinstance(m, dict):
            name, i, j, group = m.get("name", f"M{k}"), m["i"], m["j"], m.get("group")
        else:
            name, i, j, *rest = m
            group = rest[0] if rest else None
        lines.members.append(Member(str(name), str(i), str(j), group))
    if args.get("dxf"):
        _add_dxf(lines, args, root)
    check(lines)
    return lines


def check(lines: Lines) -> None:
    if not lines.members:
        raise FrameError("a frame needs at least one member")
    names = set()
    for m in lines.members:
        if m.name in names:
            raise FrameError(f"two members are named '{m.name}'")
        names.add(m.name)
        for end in (m.i, m.j):
            if end not in lines.nodes:
                raise FrameError(f"member '{m.name}' names node '{end}', which isn't listed")
        if lines.length(m) < TOLERANCE:
            raise FrameError(f"member '{m.name}' has no length (its two ends coincide)")
    if names & set(lines.groups()):
        raise FrameError(f"a group has a member's name: {sorted(names & set(lines.groups()))}")


def _add_dxf(lines: Lines, args: dict, root: Path | None) -> None:
    """LINE, LWPOLYLINE and POLYLINE entities as members (a polyline's segments each one),
    in 3D (a LINE's z kept, an LWPOLYLINE at its elevation); the layer as the group.
    Units: `scale` if given, else the drawing's $INSUNITS (mm when unset)."""
    import ezdxf

    file = Path(args["dxf"])
    if not file.is_absolute() and root is not None:
        file = root / file
    if not file.exists():
        raise FrameError(f"no DXF file {file}")
    doc = ezdxf.readfile(file)
    scale = args.get("scale") or DXF_UNITS.get(doc.header.get("$INSUNITS", 4), 0.001)
    only = args.get("layer")
    segments: list[tuple[tuple, tuple, str]] = []
    for e in doc.modelspace():
        layer = e.dxf.layer
        if only and layer != only:
            continue
        kind = e.dxftype()
        if kind == "LINE":
            segments.append((tuple(e.dxf.start), tuple(e.dxf.end), layer))
        elif kind == "LWPOLYLINE":
            z = float(e.dxf.elevation or 0.0)
            pts = [(p[0], p[1], z) for p in e.get_points("xy")]
            if e.closed and pts:
                pts.append(pts[0])
            segments += [(a, b, layer) for a, b in zip(pts, pts[1:], strict=False)]
        elif kind == "POLYLINE":
            pts = [tuple(v.dxf.location) for v in e.vertices]
            if e.is_closed and pts:
                pts.append(pts[0])
            segments += [(a, b, layer) for a, b in zip(pts, pts[1:], strict=False)]
    if not segments:
        raise FrameError(f"no lines in {file.name}" + (f" on layer {only}" if only else ""))
    count = len(lines.members)
    for a, b, layer in segments:
        pa = tuple(float(c) * scale for c in (list(a) + [0.0])[:3])
        pb = tuple(float(c) * scale for c in (list(b) + [0.0])[:3])
        if math.dist(pa, pb) < TOLERANCE:
            continue
        count += 1
        lines.members.append(Member(f"M{count}", _node(lines, pa), _node(lines, pb),
                                    None if layer == "0" else layer))  # fmt: skip


def _node(lines: Lines, xyz: tuple) -> str:
    """The node at `xyz`, or a new one N<k>: nodes within TOLERANCE are one."""
    for name, p in lines.nodes.items():
        if math.dist(p, xyz) < TOLERANCE:
            return name
    name = f"N{len(lines.nodes) + 1}"
    while name in lines.nodes:
        name += "'"
    lines.nodes[name] = xyz
    return name
