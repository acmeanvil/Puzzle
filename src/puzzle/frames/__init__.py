"""Frames of line members (ADR 0079): the "stick model" SAP2000 and RISA work in.

A frame is a component whose parts are line bodies (ADR 0027), usually one geometry
made by the `frame` history step (`frames.lines`: members between named nodes, or a
DXF's lines with layers as groups). Puzzle's beams already give it sections and their
orientations (ADR 0055), supports and loads at nodes, and CalculiX and Code_Aster beam
solves; a frame adds:

- **member ends** on the component (`MemberRelease`: dofs released, or held by a spring,
  at a node; `MemberOffset`: a rigid length at a member's end, a joint's panel zone),
  taken by the PyNite adapter only so far (`Capabilities.member_ends`);
- **member loads**: `line_load` (N/m along the member, global x, y, z), self weight by
  `gravity`, `node_spring` supports;
- the **PyNite adapter** (`adapters/pynite`): each line element a PyNite member, so
  stations are the mesh's nodes and results land in the store as any solver's.

`geometry(...)` makes a frame's Geometry with a point selection per node; `summary`
lists members for the agent (MCP `frame_members`).
"""

from __future__ import annotations

import math

import numpy as np

from puzzle.frames.lines import FrameError, Lines, Member, from_args

__all__ = ["FrameError", "Lines", "Member", "constants", "from_args", "geometry", "summary"]

DOFS = ("ux", "uy", "uz", "rx", "ry", "rz")  # in the member's axes: x along it


def node_selections(lines: Lines, scale: float):
    """A point selection per node, found again by position (`near` its point, within a
    thousandth of the frame's size)."""
    from puzzle.model.schema import NamedSelection

    d = max(scale * 1e-3, 1e-6)
    return [
        NamedSelection(
            name=n, entity="vertex", rule={"near": {"point": list(xyz), "distance": d}}
        )  # fmt: skip
        for n, xyz in lines.nodes.items()
    ]


def _size(lines: Lines) -> float:
    pts = np.array(list(lines.nodes.values()))
    return float(np.linalg.norm(np.ptp(pts, axis=0))) or 1.0


def geometry(name: str, nodes: dict | None = None, members: list | None = None, *,
             dxf: str | None = None, layer: str | None = None, scale: float | None = None,
             root=None):  # fmt: skip
    """A frame's Geometry: one `frame` step named "Frame", edge selections per member and
    group, point selections per node. Lengths in metres."""
    from puzzle.model.schema import Geometry, HistoryStep, NamedSelection

    args: dict = {}
    if nodes:
        args["nodes"] = {k: list(v) for k, v in nodes.items()}
    if members:
        args["members"] = [list(m) if not isinstance(m, dict) else m for m in members]
    if dxf:
        args["dxf"] = dxf
        if layer:
            args["layer"] = layer
        if scale:
            args["scale"] = scale
    lines = from_args(args, root)
    edges = [NamedSelection(name=m.name, entity="edge", members=[f"Frame/{m.name}"])
             for m in lines.members]  # fmt: skip
    edges += [NamedSelection(name=g, entity="edge", members=[f"Frame/{g}"])
              for g in lines.groups()]  # fmt: skip
    return Geometry(
        name=name,
        history=[HistoryStep(kind="frame", name="Frame", args=args)],
        selections=edges + node_selections(lines, _size(lines)),
    )


def constants(b) -> tuple[float, float, float, float]:
    """A beam section's (A, I1, I2, J): I1 about its first axis (the strong one for a
    rectangle whose height lies across it, as Code_Aster's IY), I2 about the second."""
    if b.shape == "library":
        from puzzle import sections

        sec = sections.get(b.standard, b.designation)
        return sec.A, sec.I1, sec.I2, sec.J
    if b.shape == "rectangle":
        w, h = b.width.to_si(), b.height.to_si()
        a, short, long_ = w * h, min(w, h), max(w, h)
        # Roark: J = a b³ [1/3 − 0.21 (b/a)(1 − b⁴/(12 a⁴))], a ≥ b
        j = long_ * short**3 * (1 / 3 - 0.21 * short / long_ * (1 - short**4 / (12 * long_**4)))
        return a, w * h**3 / 12, h * w**3 / 12, j
    if b.shape == "circle":
        r = b.diameter.to_si() / 2
        i = math.pi * r**4 / 4
        return math.pi * r**2, i, i, 2 * i
    if b.shape == "tube":
        ro = b.diameter.to_si() / 2
        ri = ro - b.thickness.to_si()
        i = math.pi * (ro**4 - ri**4) / 4
        return math.pi * (ro**2 - ri**2), i, i, 2 * i
    # box: width along the first axis, height across, wall t (thin-walled J, Bredt)
    w, h, t = b.width.to_si(), b.height.to_si(), b.thickness.to_si()
    a = w * h - (w - 2 * t) * (h - 2 * t)
    i1 = (w * h**3 - (w - 2 * t) * (h - 2 * t) ** 3) / 12
    i2 = (h * w**3 - (h - 2 * t) * (w - 2 * t) ** 3) / 12
    am = (w - t) * (h - t)
    j = 4 * am**2 * t / (2 * (w - t) + 2 * (h - t))
    return a, i1, i2, j


def frame_lines(project, comp) -> dict[str, Lines]:
    """Each part's frame lines (parts whose geometry is one `frame` step)."""
    from puzzle.model.schema import Geometry

    out = {}
    for part in comp.parts:
        geo = project.objects.get(part.geometry)
        if not isinstance(geo, Geometry):
            continue
        steps = [s for s in geo.history if s.kind == "frame" and not s.suppressed]
        if len(steps) == 1:
            out[part.name] = from_args(steps[0].args)
    return out


def summary(project, comp) -> dict:
    """The frame for the agent: members (length, ends, group, section), releases, offsets,
    supports and loads by selection. Lengths in metres."""
    lines = frame_lines(project, comp)
    sections = {b.selection: b for b in comp.beam_sections}

    def section_of(part, m):
        for key in (f"{part}/{m.name}", f"{part}/{m.group}" if m.group else None, part):
            b = sections.get(key) if key else None
            if b is not None:
                return b.designation if b.shape == "library" else b.shape
        return None

    members = [
        {
            "part": part,
            "name": m.name,
            "i": m.i,
            "j": m.j,
            "group": m.group,
            "length_m": round(ln.length(m), 9),
            "section": section_of(part, m),
        }  # fmt: skip
        for part, ln in lines.items()
        for m in ln.members
    ]
    features = [
        {
            "type": f.type,
            "selection": f.selection,
            "values": {k: f"{q.value:g} {q.unit}" for k, q in f.values.items()},
        }  # fmt: skip
        for ph in comp.physics
        if ph.type == "solid_mechanics"
        for f in ph.features
    ]
    return {
        "component": comp.name,
        "nodes": {
            part: {n: list(xyz) for n, xyz in ln.nodes.items()} for part, ln in lines.items()
        },
        "members": members,
        "without_section": [m["name"] for m in members if m["section"] is None],
        "releases": [r.model_dump(mode="json") for r in comp.member_releases],
        "offsets": [o.model_dump(mode="json") for o in comp.member_offsets],
        "supports_and_loads": features,
    }
