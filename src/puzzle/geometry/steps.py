"""What each geometry operation asks for, for the GUI's step dialogs (G4).

A field: its argument name, a kind, a default and a label. Kinds: length (typed in mm,
stored in m), angle (degrees), number, count, axis, mode, plane, profile (points in mm,
"u, v; u, v; ..." or "dxf: file.dxf"), rule (edges or faces, as JSON), file, feature
(an earlier tool step), text. A length, angle or number may instead be a parameter's
name, kept as given.
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
    "import": [StepField("file", "file", "", "CAD file (STEP, IGES, BREP)")],
    "box": [
        StepField("dx", "length", 0.05),
        StepField("dy", "length", 0.03),
        StepField("dz", "length", 0.01),
        StepField("x0", "length", 0.0),
        StepField("y0", "length", 0.0),
        StepField("z0", "length", 0.0),
        MODE,
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
    ],
    "split": [StepField("axis", "axis", "x", "Plane normal"), StepField("at", "length", 0.0)],
    "enclosure": [StepField("margin", "length", 0.01, "Margin")],
    "heal": [StepField("tolerance", "length", 1e-6)],
    "remove": [StepField("rule", "rule", {"holes_below": 0.003}, "Faces")],
    "fragment": [],
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
