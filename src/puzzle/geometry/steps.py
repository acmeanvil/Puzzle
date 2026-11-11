"""What each geometry operation asks for, for the GUI's step dialogs (G4).

A field: its argument name, a kind, a default and a label. Kinds: length (typed in mm,
stored in m), angle (degrees), number, count, axis, mode, plane, profile (points in mm,
"u, v; u, v; ..." or "dxf: file.dxf"), rule (edges or faces, as JSON), file, feature
(an earlier tool step), faces (picked, or {} for a default), direction, geometry (another
part, by id), half, side (an offset wall's: left, right or both), text. A
length, angle or number may instead be a parameter's name, kept as given.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StepField:
    name: str
    kind: str
    default: object
    label: str = ""


MODE = StepField("mode", "mode", "add", "Combine with the part")
AT = [StepField("x", "length", 0.0), StepField("y", "length", 0.0), StepField("z", "length", 0.0)]

STEPS: dict[str, list[StepField]] = {
    "import": [
        StepField("file", "file", "", "CAD file (STEP, IGES, BREP)"),
        StepField("flat", "yesno", False, "Lay flat faces on the XY plane (2D models)"),
    ],
    "box": [
        StepField("dx", "length", 0.05),
        StepField("dy", "length", 0.03),
        StepField("dz", "length", 0.01),
        StepField("x0", "length", 0.0),
        StepField("y0", "length", 0.0),
        StepField("z0", "length", 0.0),
        MODE,
    ],
    "polyline": [
        StepField(
            "points", "points", [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]], "Points (mm): x, y, z; …"
        ),
    ],
    "frame": [  # ADR 0079: members by name come through commands; a DXF's lines here
        StepField("dxf", "file", "", "DXF drawing (its lines become members)"),
        StepField("layer", "text", "", "Only this layer (empty: every layer)"),
    ],
    # ADR 0065: curves, sweeps and lofts, mirror and scale.
    "spline": [
        StepField(
            "points",
            "points",
            [[0.0, 0.0, 0.0], [0.05, 0.02, 0.0], [0.1, 0.0, 0.0]],
            "Through (mm): x, y, z; …",
        ),
        StepField("closed", "yesno", False, "Closed"),
    ],
    "sweep": [
        StepField("profile", "profile", "-5, -5; 5, -5; 5, 5; -5, 5", "Profile (mm)"),
        StepField("plane", "plane", "yz", "Profile plane (square to the path's start)"),
        StepField("offset", "length", 0.0),
        StepField("path", "points", [[0.0, 0.0, 0.0], [0.1, 0.0, 0.0]], "Path (mm): x, y, z; …"),
        StepField("smooth", "yesno", True, "A smooth curve through the path's points"),
        StepField("frame", "frame", "corrected", "Profile along the path"),
        MODE,
    ],
    "pipe": [
        StepField("r", "length", 0.005, "Outer radius"),
        StepField("inner", "length", 0.0, "Inner radius (0: solid)"),
        StepField("path", "points", [[0.0, 0.0, 0.0], [0.1, 0.0, 0.0]], "Path (mm): x, y, z; …"),
        StepField("smooth", "yesno", True, "A smooth curve through the path's points"),
        MODE,
    ],
    "loft": [
        StepField(
            "sections",
            "sections",
            [
                {
                    "plane": "xy",
                    "offset": 0.0,
                    "profile": {"points": [[0, 0], [0.05, 0], [0.05, 0.05], [0, 0.05]]},
                },
                {
                    "plane": "xy",
                    "offset": 0.04,
                    "profile": {"points": [[0.01, 0.01], [0.04, 0.01], [0.04, 0.04], [0.01, 0.04]]},
                },
            ],
            "Profiles: plane offset (mm): u, v; … | next",
        ),
        StepField("ruled", "yesno", False, "Straight between the profiles"),
        MODE,
    ],
    "mirror": [
        StepField("axis", "axis", "x", "Mirror plane's normal"),
        *AT,
        StepField("keep", "yesno", True, "Keep the original (a whole part from its half)"),
    ],
    "scale": [StepField("factor", "number", 1.0, "Factor"), *AT],
    "patch": [
        StepField("rule", "rule", {"edges": []}, "Boundary edges"),
        StepField("points", "points", [], "Or a boundary (mm): x, y, z; …"),
        MODE,
    ],
    "subd": [
        StepField("file", "file", "", "Control cage (an OBJ of quads)"),
        StepField("levels", "count", 3, "Subdivision levels"),
        StepField("scale", "number", 0.001, "Scale (the file's unit in m: 0.001 for mm)"),
        MODE,
    ],
    "deform": [
        StepField("kind", "deform", "twist", "Deformation"),
        StepField("axis", "axis", "z", "Along"),
        StepField("angle", "angle", 30.0, "Angle (twist, bend)"),
        StepField("factor", "number", 0.8, "End size (taper)"),
        StepField("toward", "axis", "y", "Bent toward (bend)"),
        StepField(
            "corners", "rule", [[0.0, 0.0, 0.0]] * 8, "Corner moves (box; m, eight [dx, dy, dz])"
        ),
    ],
    "thicken": [
        StepField("t", "length", 0.002, "Thickness"),
        StepField("both", "yesno", False, "Half to each side"),
    ],
    "sheet": [
        StepField(
            "points",
            "points",
            [[0.0, 0.0, 0.0], [0.1, 0.0, 0.0], [0.1, 0.1, 0.0], [0.0, 0.1, 0.0]],
            "Corners (mm, in one plane): x, y, z; …",
        ),
    ],
    "face": [
        StepField("profile", "profile", "0, 0; 50, 0; 50, 30; 0, 30", "Profile (mm)"),
        StepField("plane", "plane", "xy"),
        StepField("offset", "length", 0.0),
        MODE,
    ],
    # ADR 0048 round 2: 2D shapes (in the plane's u, v: x, y on the XY plane), sides named.
    "rectangle": [
        StepField("dx", "length", 0.05, "Width"),
        StepField("dy", "length", 0.03, "Height"),
        StepField("x0", "length", 0.0, "Corner x"),
        StepField("y0", "length", 0.0, "Corner y"),
        StepField("plane", "plane", "xy"),
        StepField("offset", "length", 0.0),
        MODE,
    ],
    "circle": [
        StepField("r", "length", 0.01, "Radius"),
        StepField("x", "length", 0.0, "Centre x"),
        StepField("y", "length", 0.0, "Centre y"),
        StepField("plane", "plane", "xy"),
        StepField("offset", "length", 0.0),
        MODE,
    ],
    "annulus": [
        StepField("r_inner", "length", 0.01, "Inner radius"),
        StepField("r_outer", "length", 0.02, "Outer radius"),
        StepField("x", "length", 0.0, "Centre x"),
        StepField("y", "length", 0.0, "Centre y"),
        StepField("plane", "plane", "xy"),
        StepField("offset", "length", 0.0),
        MODE,
    ],
    "polygon": [
        StepField("sides", "count", 6, "Sides"),
        StepField("r", "length", 0.01, "Radius to the corners"),
        StepField("x", "length", 0.0, "Centre x"),
        StepField("y", "length", 0.0, "Centre y"),
        StepField("angle", "angle", 0.0, "First corner's angle"),
        StepField("plane", "plane", "xy"),
        StepField("offset", "length", 0.0),
        MODE,
    ],
    "slot": [
        StepField("length", "length", 0.04, "Length overall"),
        StepField("width", "length", 0.01, "Width"),
        StepField("x", "length", 0.0, "Centre x"),
        StepField("y", "length", 0.0, "Centre y"),
        StepField("angle", "angle", 0.0, "Angle of its length"),
        StepField("plane", "plane", "xy"),
        StepField("offset", "length", 0.0),
        MODE,
    ],
    "offset": [
        StepField("line", "profile", "0, 0; 100, 0", "Line (mm): u, v; …"),
        StepField("t", "length", 0.005, "Wall thickness"),
        StepField("side", "side", "left", "Wall on"),
        StepField("closed", "yesno", False, "Closed line (a ring)"),
        StepField("plane", "plane", "xy"),
        StepField("offset", "length", 0.0),
        MODE,
    ],
    "section": [
        StepField("part", "geometry", "", "Part to cut"),
        StepField("plane", "plane", "xy", "Cutting plane"),
        StepField("offset", "length", 0.0),
        StepField("half", "half", False, "Keep"),
    ],
    "curved_sheet": [
        StepField("r", "length", 0.1, "Radius"),
        StepField("length", "length", 0.2, "Length along x"),
        StepField("angle", "angle", 80.0, "Angle spanned (centred on +z)"),
        StepField("x0", "length", 0.0, "Axis start x"),
        StepField("y0", "length", 0.0, "Axis start y"),
        StepField("z0", "length", 0.0, "Axis start z"),
    ],
    "cylinder": [
        StepField("r", "length", 0.005, "Radius"),
        StepField("h", "length", 0.02, "Height"),
        *AT,
        StepField("axis", "axis", "z"),
        MODE,
    ],
    "cone": [
        StepField("r1", "length", 0.005, "Base radius"),
        StepField("r2", "length", 0.002, "Top radius"),
        StepField("h", "length", 0.01, "Height"),
        *AT,
        StepField("axis", "axis", "z"),
        MODE,
    ],
    "sphere": [StepField("r", "length", 0.005, "Radius"), *AT, MODE],
    "extrude": [
        StepField("profile", "profile", "0, 0; 50, 0; 50, 30; 0, 30", "Profile (mm)"),
        StepField("distance", "length", 0.01),
        StepField("plane", "plane", "xy"),
        StepField("offset", "length", 0.0),
        MODE,
    ],
    "revolve": [
        StepField("profile", "profile", "10, 0; 20, 0; 20, 5; 10, 5", "Profile (mm)"),
        StepField("angle", "angle", 360.0),
        StepField("axis", "uv", "v", "Axis (u or v of the plane)"),
        StepField("plane", "plane", "xz"),
        StepField("offset", "length", 0.0),
        MODE,
    ],
    "hole": [
        StepField("r", "length", 0.003, "Radius"),
        *AT,
        StepField("axis", "axis", "z", "Through, along"),
    ],
    "fillet": [
        StepField("r", "length", 0.002, "Radius"),
        StepField("rule", "rule", {"parallel": "z", "outer": True}, "Edges"),
    ],
    "chamfer": [
        StepField("d", "length", 0.001, "Distance"),
        StepField("rule", "rule", {"parallel": "z", "outer": True}, "Edges"),
    ],
    "move": [
        StepField("dx", "length", 0.0),
        StepField("dy", "length", 0.0),
        StepField("dz", "length", 0.0),
    ],
    "rotate": [StepField("angle", "angle", 90.0), StepField("axis", "axis", "z"), *AT],
    "pattern": [
        StepField("feature", "feature", "", "Repeat the step"),
        StepField("count", "count", 3),
        StepField("dx", "length", 0.01),
        StepField("dy", "length", 0.0),
        StepField("dz", "length", 0.0),
        # ADR 0062 phase 3: a second direction, positions from data, or along a path
        StepField("count2", "count", 1, "Rows (second direction)"),
        StepField("dx2", "length", 0.0),
        StepField("dy2", "length", 0.0),
        StepField("dz2", "length", 0.0),
        StepField(
            "positions",
            "text",
            "",
            "At positions (a Points operator: offsets, the first the original)",
        ),
        StepField("along", "text", "", "Along a path (an earlier polyline or spline)"),
    ],
    "split": [StepField("axis", "axis", "x", "Plane normal"), StepField("at", "length", 0.0)],
    # ADR 0050: a bolt's bearing rings at a hole's mouths.
    "imprint": [
        StepField("hole", "text", "H/wall", "The hole's wall (its face name)"),
        StepField("r", "length", 0.008, "Ring's outer radius"),
    ],  # fmt: skip
    "split_face": [
        StepField("profile", "profile", "", "Sketch to project"),
        StepField("faces", "faces", {}, "Faces to split"),
        StepField("direction", "direction", "both", "Project"),
    ],
    "enclosure": [StepField("margin", "length", 0.01, "Margin")],
    "heal": [StepField("tolerance", "length", 1e-6)],
    "remove": [StepField("rule", "rule", {"holes_below": 0.003}, "Faces")],
    "fragment": [],
    "bolt": [
        StepField(k, "length", v, label)
        for k, v, label in (
            ("head_radius", 0.0112, "Head radius"),
            ("head_height", 0.01, "Head height"),
            ("fillet", 0.0006, "Under-head fillet"),
            ("shank_radius", 0.008, "Shank radius"),
            ("minor_radius", 0.0068, "Thread radius (minor)"),
            ("length", 0.06, "Length"),
            ("thread", 0.038, "Thread length"),
        )
    ],  # fmt: skip
    "nut": [
        StepField("outer_radius", "length", 0.012, "Outer radius"),
        StepField("inner_radius", "length", 0.0068, "Bore radius (minor)"),
        StepField("height", "length", 0.0148, "Height"),
    ],  # fmt: skip
    "washer": [
        StepField("outer_radius", "length", 0.01474, "Outer radius"),
        StepField("inner_radius", "length", 0.0085, "Bore radius"),
        StepField("height", "length", 0.0033, "Thickness"),
    ],  # fmt: skip
    "keep": [
        StepField("bodies", "text", "", "Bodies to keep (names, comma-separated)"),
        StepField("body", "text", "Body", "Name of what is kept"),
    ],  # fmt: skip
}


def parse_profile(text: str) -> dict:
    """ "0, 0; 60, 0; ..." (mm) -> {"points": [[0, 0], [0.06, 0], ...]} (m); or a DXF."""
    text = text.strip()
    if text.lower().startswith("dxf:"):
        return {"dxf": text[4:].strip()}
    points = []
    for pair in text.split(";"):
        if pair.strip():
            points.append([_coordinate(c) for c in pair.replace(" ", "").split(",")])
    return {"points": points}


def parse_sections(text: str) -> list[dict]:
    """ "xy 0: 0, 0; 50, 0; 50, 30 | xy 40: 10, 10; ..." -> a loft's sections: each its
    plane, offset (mm in, m stored) and profile."""
    out = []
    for part in text.split("|"):
        head, _, body = part.partition(":")
        bits = head.split()
        if len(bits) != 2 or not body.strip():
            raise ValueError("each profile is: plane offset: u, v; u, v; … (e.g. xy 40: 0, 0; "
                             "50, 0; 50, 30)")  # fmt: skip
        out.append({"plane": bits[0], "offset": _coordinate(bits[1]),
                    "profile": parse_profile(body)})  # fmt: skip
    return out


def format_sections(sections: list[dict]) -> str:
    return " | ".join(f"{s.get('plane', 'xy')} {_mm(s.get('offset', 0.0))}: "
                      f"{format_profile(s.get('profile', {}))}" for s in sections)  # fmt: skip


def _coordinate(text: str):
    """A number in mm (stored in m), or a parameter's name (its value is already SI)."""
    try:
        return float(text) / 1000
    except ValueError:
        return text


def format_profile(profile: dict) -> str:
    if "dxf" in profile:
        return f"dxf: {profile['dxf']}"
    pts = profile.get("points", [])
    return "; ".join(f"{_mm(u)}, {_mm(v)}" for u, v in pts)


def _mm(v) -> str:
    return f"{v * 1000:g}" if isinstance(v, (int, float)) else str(v)
