"""Geometry from data (ADR 0062 phase 3): points, grids and profiles as operators.

All three compute before a solve (they read no results), so a geometry's history can
name them as it names a Number: a `profile` argument (a face, an extrusion, a
revolve) takes a Profile's value, a polyline's `points` a Points' value, a pattern's
`positions` a Points' offsets, and `name[i]`, `name[i].x` one entry of a list or
points (in a sketch's dimensions too). `history.ops_of` puts the values in.

- **Points** [D1, D2]: x, y and z (numbers or lists, a number repeated), or a table's
  x, y, z columns, optionally in a coordinate system (`axes`): its x, y, z, or r, θ, z
  for a cylindrical one. The output is a table with columns x, y, z (m), so every
  table operator takes it, and its columns are lists again ("and back").
- **Grid** [D8]: points on a plane (an axis plane at an origin, or a construction
  plane), counts and sizes along its two directions; or in a box (low, high, three
  counts). Rows are u fastest.
- **Profile** [E1]: a polyline through points, a circle, a rectangle (centred, or from
  its corner) or a regular polygon, as the profile spec `geometry/profile.py` reads.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from puzzle.errors import PuzzleError


class Points:
    """Positions (m) as an (n, 3) array, offered as a table of x, y, z too."""

    type = "points"

    def __init__(self, xyz, label: str = "") -> None:
        self.xyz = np.asarray(xyz, float).reshape(-1, 3)
        self.label = label

    def table(self):
        from puzzle.operators.evaluate import Table

        rows = [list(map(float, r)) for r in self.xyz]
        return Table(["x", "y", "z"], rows, ["length"] * 3)

    def text(self) -> str:
        from puzzle.operators.evaluate import quantity_text

        if not len(self.xyz):
            return "no points"
        first = ", ".join(quantity_text(float(v), "length") for v in self.xyz[0])
        more = f" … {len(self.xyz)} points" if len(self.xyz) > 1 else ""
        return f"({first}){more}"


class Profile:
    """A profile spec (`geometry/profile.py`): points or a circle, in metres."""

    type = "profile"

    def __init__(self, spec: dict, label: str = "") -> None:
        self.spec, self.label = spec, label

    def text(self) -> str:
        from puzzle.operators.evaluate import quantity_text

        if "circle" in self.spec:
            return f"circle, radius {quantity_text(self.spec['circle'][2], 'length')}"
        return f"outline of {len(self.spec.get('points', []))} corners"


def _values(ctx, op, port: str, default: float | None = None) -> np.ndarray | None:
    """A port's numbers (a number, a list, or a points/table column of that name)."""
    from puzzle.operators import evaluate as ev

    got = ev._input(ctx, op, port)
    if got is None:
        return None if default is None else np.array([default])
    if isinstance(got, ev.Number):
        return np.array([got.value])
    if isinstance(got, ev.ListValue):
        return np.asarray(got.values, float)
    raise PuzzleError(f"'{op.name}': {port} is a number or a list", code="invalid")


def _from_axes(ctx, op, xyz: np.ndarray) -> np.ndarray:
    """Coordinates in the operator's coordinate system as global ones."""
    from puzzle.model.schema import CoordinateSystem

    if op.axes is None:
        return xyz
    cs = ctx.project.objects.get(op.axes)
    if not isinstance(cs, CoordinateSystem):
        raise PuzzleError(f"'{op.name}': its coordinate system is gone", code="not_found")
    from puzzle.axes import absolute

    cs = absolute(ctx.project, cs, ctx.root or ".")
    if cs.type == "cylindrical":  # r, θ (degrees), z
        r, t, z = xyz[:, 0], np.radians(xyz[:, 1]), xyz[:, 2]
        xyz = np.column_stack([r * np.cos(t), r * np.sin(t), z])
    return cs.centre() + xyz @ np.asarray(cs.orientation()).T


def points(ctx, op) -> dict[str, Any]:
    from puzzle.operators import evaluate as ev

    table = ev._input(ctx, op, "table")
    if table is not None:
        if isinstance(table, Points):
            xyz = table.xyz
        else:
            missing = [c for c in "xyz" if c not in table.columns]
            if missing:
                raise PuzzleError(f"'{op.name}': the table has no {', '.join(missing)} column",
                                  code="invalid")  # fmt: skip
            xyz = np.column_stack([table.frame[c].to_numpy() for c in "xyz"]).astype(float)
    else:
        parts = [_values(ctx, op, c, 0.0) for c in "xyz"]
        n = max(len(p) for p in parts)
        if any(len(p) not in (1, n) for p in parts):
            raise PuzzleError(f"'{op.name}': x, y and z lists differ in length", code="invalid")
        xyz = np.column_stack([np.broadcast_to(p, n) for p in parts])
    return {"out": Points(_from_axes(ctx, op, xyz), op.name)}


def _one(ctx, op, port: str, default: float) -> float:
    got = _values(ctx, op, port, default)
    return float(got[0])


def grid(ctx, op) -> dict[str, Any]:
    nu, nv = int(_one(ctx, op, "nu", 3)), int(_one(ctx, op, "nv", 3))
    if op.region == "box":
        low = np.array([_one(ctx, op, f"{c}0", 0.0) for c in "xyz"])
        high = np.array([_one(ctx, op, f"{c}1", 0.0) for c in "xyz"])
        nw = int(_one(ctx, op, "nw", 2))
        axes = [np.linspace(low[k], high[k], n) for k, n in enumerate((nu, nv, nw))]
        w, v, u = np.meshgrid(axes[2], axes[1], axes[0], indexing="ij")
        return {"out": Points(np.column_stack([u.ravel(), v.ravel(), w.ravel()]), op.name)}
    size_u, size_v = _one(ctx, op, "size_u", 0.1), _one(ctx, op, "size_v", 0.1)
    if op.target is not None:  # a construction plane's frame
        from puzzle.model.schema import ConstructionGeometry

        plane = ctx.project.objects.get(op.target)
        if not isinstance(plane, ConstructionGeometry) or plane.type != "plane":
            raise PuzzleError(f"'{op.name}': its target is not a construction plane",
                              code="invalid")  # fmt: skip
        origin, normal = plane.frame(ctx.project)
        u_dir = np.cross(normal, [0.0, 0.0, 1.0])
        if np.linalg.norm(u_dir) < 1e-9:
            u_dir = np.array([1.0, 0.0, 0.0])
        u_dir /= np.linalg.norm(u_dir)
        v_dir = np.cross(normal, u_dir)
    else:
        origin = np.array([_one(ctx, op, f"{c}0", 0.0) for c in "xyz"])
        i, j = {"xy": (0, 1), "yz": (1, 2), "zx": (2, 0)}[op.plane]
        u_dir, v_dir = np.eye(3)[i], np.eye(3)[j]
    us = np.linspace(0.0, size_u, nu) if nu > 1 else np.zeros(1)
    vs = np.linspace(0.0, size_v, nv) if nv > 1 else np.zeros(1)
    vv, uu = np.meshgrid(vs, us, indexing="ij")
    xyz = origin + np.outer(uu.ravel(), u_dir) + np.outer(vv.ravel(), v_dir)
    return {"out": Points(xyz, op.name)}


def profile(ctx, op) -> dict[str, Any]:
    from puzzle.operators import evaluate as ev

    shape = op.shape
    if shape == "polyline":
        got = ev._input(ctx, op, "points")
        if not isinstance(got, Points) or len(got.xyz) < 3:
            raise PuzzleError(f"'{op.name}': a polyline needs three points or more wired in",
                              code="inconsistent")  # fmt: skip
        return {"out": Profile({"points": got.xyz[:, :2].tolist()}, op.name)}
    cx, cy = _one(ctx, op, "x", 0.0), _one(ctx, op, "y", 0.0)
    if shape == "circle":
        r = _one(ctx, op, "radius", 0.01)
        return {"out": Profile({"circle": [cx, cy, r]}, op.name)}
    if shape == "rectangle":
        w, h = _one(ctx, op, "width", 0.02), _one(ctx, op, "height", 0.01)
        x0, y0 = (cx - w / 2, cy - h / 2) if op.centred else (cx, cy)
        pts = [[x0, y0], [x0 + w, y0], [x0 + w, y0 + h], [x0, y0 + h]]
        return {"out": Profile({"points": pts}, op.name)}
    r, sides = _one(ctx, op, "radius", 0.01), int(_one(ctx, op, "sides", 6))
    if sides < 3:
        raise PuzzleError(f"'{op.name}': a polygon has three sides or more", code="invalid")
    turn = math.radians(_one(ctx, op, "angle", 0.0))
    pts = [[cx + r * math.cos(turn + 2 * math.pi * k / sides),
            cy + r * math.sin(turn + 2 * math.pi * k / sides)] for k in range(sides)]  # fmt: skip
    return {"out": Profile({"points": pts}, op.name)}


GEOMETRIC = ("points", "grid", "profile")


def values(project) -> dict[str, Any]:
    """Every pre-solve geometric operator's value by name: Points as (n, 3) lists, a
    Profile as its spec, a list as its numbers (for `name[i]`)."""
    from puzzle.model.schema import Operator
    from puzzle.operators import evaluate as ev
    from puzzle.operators import is_pre

    out: dict[str, Any] = {}
    ctx = ev.Context(project, None)
    for op in project.of_type(Operator):
        if op.type not in (*GEOMETRIC, "list", "series", "range") or not is_pre(project, op):
            continue
        try:
            got = ev.value(ctx, op)
        except PuzzleError:
            continue  # its problem says why
        if isinstance(got, Points):
            out[op.name] = got.xyz.tolist()
        elif isinstance(got, Profile):
            out[op.name] = got.spec
        elif isinstance(got, ev.ListValue) and got.numeric:
            out[op.name] = [float(v) for v in got.values]
    return out


def substitute(args: dict, found: dict, keys=("profile", "points", "positions")) -> dict:
    """History arguments with geometric operators' values put in: a `profile`, `points`
    or `positions` naming one takes its value; `name[i]` or `name[i].x` anywhere (a
    sketch's dimensions too) one entry of a list or points."""
    import re

    entry = re.compile(r"^\s*([A-Za-z_][\w ]*?)\s*\[(\d+)\](?:\.([xyz]))?\s*$")

    def one(v):
        if isinstance(v, str):
            m = entry.match(v)
            if m and m.group(1) in found:
                seq, k, axis = found[m.group(1)], int(m.group(2)), m.group(3)
                if not isinstance(seq, list) or k >= len(seq):
                    raise PuzzleError(f"'{v}': no entry {k}", code="invalid")
                item = seq[k]
                if axis is not None:
                    if not isinstance(item, list):
                        raise PuzzleError(f"'{v}': {m.group(1)} holds numbers", code="invalid")
                    return float(item["xyz".index(axis)])
                return float(item) if not isinstance(item, list) else item
        if isinstance(v, dict):
            return {k: one(x) for k, x in v.items()}
        if isinstance(v, list):
            return [one(x) for x in v]
        return v

    out = {}
    for k, v in args.items():
        if k in keys and isinstance(v, str) and v in found:
            out[k] = found[v]
        else:
            out[k] = one(v)
    return out
