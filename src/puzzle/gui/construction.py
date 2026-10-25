"""Construction geometry in the GUI (ADR 0046): paths and planes for result tools.

New path… and New plane… ask everything in one form. They start from what is picked
in the view (two picked points make a path; one point places a plane, three points
tilt it), else from the model's extent: a path through its thinnest side at the
centre, a plane across its longest side at the middle. Selecting one in the tree draws
it over the model.
"""

from __future__ import annotations

import numpy as np

from puzzle.gui.quick_form import Ask
from puzzle.model.schema import ConstructionGeometry, CoordinateSystem

NORMALS = [
    ("Along X", "x"),
    ("Along Y", "y"),
    ("Along Z", "z"),
    ("Typed below", "typed"),
]
AXIS = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}
PLANES = [("YZ plane (faces its x)", "yz"), ("ZX plane (faces its y)", "zx"),
          ("XY plane (faces its z)", "xy")]  # fmt: skip


def _point(text: str, per_metre: float):
    from puzzle.gui.result_tools import _point as parse

    return parse(text, per_metre)


def _shown(values, per_metre: float) -> str:
    return ", ".join(f"{v * per_metre:.6g}" for v in values)


def bounds(window) -> tuple[np.ndarray, np.ndarray] | None:
    """The shown or last meshed model's extent (min, max), if any."""
    mesh = getattr(window.viewport, "mesh", None)
    if mesh is None and window.meshes:
        mesh = next(iter(window.meshes.values()))
    coords = getattr(mesh, "coords", None)
    if coords is None or not len(coords):
        return None
    return coords.min(axis=0), coords.max(axis=0)


def picked_points(window) -> list[np.ndarray]:
    """Points picked in the view (the Points filter), in pick order."""
    view = window.viewport
    return [np.asarray(view.vertices[e], float) for e in view.picked
            if e[0] == 0 and e in view.vertices]  # fmt: skip


def _unique(window, base: str) -> str:
    taken = {c.name for c in window.session.project.of_type(ConstructionGeometry)}
    n = 1
    while f"{base} {n}" in taken:
        n += 1
    return f"{base} {n}"


def _create(window, data: dict):
    from puzzle.commands.model import Create

    return window.submit(Create(kind="construction", data=data))


def default_path(window) -> tuple[tuple, tuple]:
    points = picked_points(window)
    if len(points) >= 2:
        return tuple(points[0]), tuple(points[1])
    box = bounds(window)
    if box is None:
        return (0.0, 0.0, 0.0), (1.0, 0.0, 0.0)
    lo, hi = box
    axis = int(np.argmin(hi - lo))
    start, end = (lo + hi) / 2, (lo + hi) / 2
    start[axis], end[axis] = lo[axis], hi[axis]
    return tuple(start), tuple(end)


def new_path(window):
    """A straight line between two points (Ansys's Path), for the Path and Linearized
    stress tools."""
    from puzzle.settings import length_unit

    unit, per_metre = length_unit()
    start, end = default_path(window)
    picked = len(picked_points(window)) >= 2

    def check(v) -> str | None:
        a, b = _point(v.get("start", ""), per_metre), _point(v.get("end", ""), per_metre)
        if a is None or b is None:
            return "Type each end as three numbers: x, y, z"
        if np.allclose(a, b):
            return "The two ends are the same point"
        return None if v.get("name") else "Give it a name"

    got = window.ask_form(
        "New path",
        [
            Ask(
                "start", f"From (x, y, z in {unit})", kind="text", default=_shown(start, per_metre)
            ),  # fmt: skip
            Ask("end", f"To (x, y, z in {unit})", kind="text", default=_shown(end, per_metre)),
            Ask("name", "Name", kind="text", default=_unique(window, "Path")),
        ],
        intro="A straight line through the model for result tools: a result along it, or "
        "the stress linearized through a wall. "
        + (
            "It starts from the two points picked in the view."
            if picked
            else "Pick two points in the view (Points) to start from them; the "
            "default crosses the model's thinnest side at its centre."
        ),  # fmt: skip
        check=check,
    )
    if got is None:
        return None
    return _create(
        window,
        {
            "name": got["name"],
            "type": "path",
            "start": list(_point(got["start"], per_metre)),
            "end": list(_point(got["end"], per_metre)),
        },
    )


def default_plane(window) -> tuple[tuple, str, tuple]:
    """(origin, normal choice, typed normal) from the picks, else the model's middle
    across its longest side."""
    points = picked_points(window)
    if len(points) >= 3:
        n = np.cross(points[1] - points[0], points[2] - points[0])
        if np.linalg.norm(n):
            return tuple(points[0]), "typed", tuple(n / np.linalg.norm(n))
    box = bounds(window)
    if box is None:
        origin, axis = (np.zeros(3) if not points else points[0]), 0
    else:
        lo, hi = box
        axis = int(np.argmax(hi - lo))
        origin = points[0] if points else (lo + hi) / 2
    key = "xyz"[axis]
    return tuple(origin), key, AXIS[key]


def new_plane(window):
    """A plane through the model (Ansys's Surface): a result over its cut, or the force
    and moment carried across it."""
    from puzzle.settings import length_unit

    unit, per_metre = length_unit()
    origin, which, typed = default_plane(window)
    systems = [(a.name, str(a.id)) for a in window.session.project.of_type(CoordinateSystem)
               if a.type == "cartesian"]  # fmt: skip
    how = [("Through a point, facing a direction", "point")]
    if systems:
        how.append(("One of a coordinate system's planes", "axes"))
    by_point = lambda v: v.get("how") != "axes"  # noqa: E731

    def check(v) -> str | None:
        if by_point(v):
            if _point(v.get("origin", ""), per_metre) is None:
                return "Type the point as three numbers: x, y, z"
            if v.get("normal") == "typed":
                n = _point(v.get("direction", ""), 1.0)
                if n is None or not np.linalg.norm(n):
                    return "Type the direction as three numbers, not all zero"
        else:
            try:
                float(v.get("offset", "0") or 0)
            except ValueError:
                return "The offset is a number"
        return None if v.get("name") else "Give it a name"

    asks = [
        Ask("how", "Placed", choices=how),
        Ask(
            "origin",
            f"Through (x, y, z in {unit})",
            kind="text",
            default=_shown(origin, per_metre),
            shown=by_point,
        ),  # fmt: skip
        Ask("normal", "Facing", choices=NORMALS, default=which, shown=by_point),
        Ask(
            "direction",
            "Direction (x, y, z)",
            kind="text",
            default=", ".join(f"{v:.6g}" for v in typed),
            shown=lambda v: by_point(v) and v.get("normal") == "typed",
        ),  # fmt: skip
    ]
    if systems:
        asks += [
            Ask("axes", "Coordinate system", choices=systems, shown=lambda v: not by_point(v)),
            Ask("plane", "Its plane", choices=PLANES, shown=lambda v: not by_point(v)),
            Ask(
                "offset",
                f"Moved along its normal ({unit})",
                kind="text",
                default="0",
                shown=lambda v: not by_point(v),
            ),  # fmt: skip
        ]
    asks.append(Ask("name", "Name", kind="text", default=_unique(window, "Plane")))
    picks = len(picked_points(window))
    got = window.ask_form(
        "New plane",
        asks,
        intro="A plane through the model for result tools: a result over the cut it makes, "
        "or the force and moment carried across it. "
        + (
            "It starts from the points picked in the view."
            if picks
            else "Pick a point in the view (Points) to place it there, or three to tilt it "
            "through them."
        ),  # fmt: skip
        check=check,
    )
    if got is None:
        return None
    data = {"name": got["name"], "type": "plane"}
    if by_point(got):
        normal = _point(got["direction"], 1.0) if got["normal"] == "typed" else AXIS[got["normal"]]
        data |= {"origin": list(_point(got["origin"], per_metre)), "normal": list(normal)}
    else:
        data |= {
            "axes": got["axes"],
            "plane": got["plane"],
            "offset": float(got.get("offset") or 0) / per_metre,
        }
    return _create(window, data)


def construction_choices(window, kind: str) -> list[tuple[str, str]]:
    """The project's construction paths or planes, as (name, id)."""
    return [(c.name, str(c.id)) for c in window.session.project.of_type(ConstructionGeometry)
            if c.type == kind]  # fmt: skip


def describe(c: ConstructionGeometry, project) -> str:
    """The tree's text after the name: what it is."""
    if c.type == "path":
        return "path"
    if c.axes is not None:
        axes = project.objects.get(c.axes)
        return f"plane: {c.plane.upper()} of {axes.name if axes else '?'}"
    return "plane"
