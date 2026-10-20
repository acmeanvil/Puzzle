"""Geometry as a feature history with persistent face names (ADR 0004, ADR 0014).

A history is a list of operations. Regenerating it builds the shape with OpenCASCADE
(OCP) and names every face by the operation that made it, e.g. "Plate/+z",
"Hole1/wall", "Round/edge-2". Names are carried through later operations by OCCT's
own history (Modified / Generated / IsDeleted):

- a face an operation leaves alone, or only trims, keeps its name;
- a face split in two keeps its name on both pieces (a selection follows the split);
- a face an operation removes loses its name (a selection on it is detached).

Edges for fillets are picked by rules evaluated at each regeneration, not by index.
Values may name parameters: a string is looked up in `params`.

This is the persistent-naming prototype; `tests/test_naming.py` measures how many
selections each tier resolves across edits.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Splitter
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet
from OCP.BRepGProp import BRepGProp
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Line, GeomAbs_Plane
from OCP.gp import gp_Ax2, gp_Dir, gp_Pln, gp_Pnt
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Shape

try:  # OCP 8 moved OCCT's collections into one module
    from OCP.collections import List_TopoDS_Shape as TopTools_ListOfShape
except ImportError:
    from OCP.TopTools import TopTools_ListOfShape


def _static(cls, name: str):
    """A static OCCT method: OCP 7 names it `Name_s`, OCP 8 plain `Name`."""
    return getattr(cls, f"{name}_s", None) or getattr(cls, name)


Names = dict[str, list]  # persistent name -> faces (several after a split)


@dataclass
class Op:
    """One history step: `kind` with its `args` (numbers or parameter names)."""

    kind: str  # "box", "hole", "fillet", "split"
    name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class NamedShape:
    shape: TopoDS_Shape
    names: Names

    def faces(self) -> list:
        return _faces(self.shape)

    def resolve(self, names: list[str]) -> tuple[list, list[str]]:
        """Faces for persistent names, and the names that no longer exist."""
        found, missing = [], []
        for n in names:
            if self.names.get(n):
                found.extend(self.names[n])
            else:
                missing.append(n)
        return found, missing


def regenerate(ops: list[Op], params: dict[str, float] | None = None) -> NamedShape:
    params = params or {}

    def value(v):
        return float(params[v]) if isinstance(v, str) else float(v)

    shape, names = None, {}
    for op in ops:
        # Numbers and parameter names resolve to floats; rules and axis names stay as given.
        a = {k: (v if k in ("rule", "axis") else value(v)) for k, v in op.args.items()}
        if op.kind == "box":
            origin = (a.get("x0", 0.0), a.get("y0", 0.0), a.get("z0", 0.0))
            size = (a["dx"], a["dy"], a["dz"])
            shape = BRepPrimAPI_MakeBox(gp_Pnt(*origin), *size).Shape()
            # Name each side by its outward direction ("-x" ... "+z"), from geometry:
            # OCCT's Left/Front/... faces do not follow the axes as their names suggest.
            centre = [o + d / 2 for o, d in zip(origin, size, strict=True)]
            names = {}
            for f in _faces(shape):
                c = signature(f)[2]
                offsets = [(c[i] - centre[i]) / size[i] for i in range(3)]
                axis = max(range(3), key=lambda i: abs(offsets[i]))
                names[f"{op.name}/{'-+'[offsets[axis] > 0]}{'xyz'[axis]}"] = [f]
        elif op.kind == "hole":
            # A through hole along z: a cylinder cut from the shape.
            lo, hi = _bounds_z(shape)
            margin = 0.1 * (hi - lo)  # relative: the model may be in mm or m
            mk = BRepPrimAPI_MakeCylinder(
                gp_Ax2(gp_Pnt(a["x"], a["y"], lo - margin), gp_Dir(0, 0, 1)),
                a["r"],
                hi - lo + 2 * margin,
            )
            tool = mk.Shape()
            cut = BRepAlgoAPI_Cut(shape, tool)
            # Cut tracks the tool's faces too: the cylinder wall becomes the hole's wall.
            names = _carry(cut, {**names, f"{op.name}/wall": _lateral(tool)})
            shape = cut.Shape()
        elif op.kind == "fillet":
            mk = BRepFilletAPI_MakeFillet(shape)
            edges = _edges_by_rule(shape, op.args["rule"])
            for e in edges:
                mk.Add(a["r"], e)
            mk.Build()
            new = _carry(mk, names)
            for i, e in enumerate(edges, start=1):
                gen = _list(mk.Generated(e))
                if gen:
                    new[f"{op.name}/edge-{i}"] = gen
            names, shape = new, mk.Shape()
        elif op.kind == "split":
            # A plane normal to an axis through `at` splits faces it crosses.
            normal = {"x": gp_Dir(1, 0, 0), "y": gp_Dir(0, 1, 0), "z": gp_Dir(0, 0, 1)}
            axis = op.args["axis"]
            origin = gp_Pnt(*(a["at"] if c == axis else 0.0 for c in "xyz"))
            plane = BRepBuilderAPI_MakeFace(gp_Pln(origin, normal[axis]), -1e3, 1e3, -1e3, 1e3)
            sp = BRepAlgoAPI_Splitter()
            args, tools = TopTools_ListOfShape(), TopTools_ListOfShape()
            args.Append(shape)
            tools.Append(plane.Face())
            sp.SetArguments(args)
            sp.SetTools(tools)
            sp.Build()
            names, shape = _carry(sp, names), sp.Shape()
        else:
            raise ValueError(f"unknown operation '{op.kind}'")
    # Keep only faces that are in the final shape (OCCT may report intermediates).
    final = _faces(shape)
    names = {n: [f for f in fs if any(f.IsSame(g) for g in final)] for n, fs in names.items()}
    return NamedShape(shape, {n: fs for n, fs in names.items() if fs})


# --- naming through operations --------------------------------------------------------


def _carry(algo, names: Names) -> Names:
    """Each name's faces after `algo`: unchanged, modified (maybe several), or gone."""
    out: Names = {}
    for name, faces in names.items():
        kept = []
        for f in faces:
            if algo.IsDeleted(f):
                continue
            modified = _list(algo.Modified(f))
            kept += modified or [f]
        if kept:
            out[name] = kept
    return out


def _list(shapes: TopTools_ListOfShape) -> list:
    return [_static(TopoDS, "Face")(s) if s.ShapeType() == TopAbs_FACE else s for s in shapes]


def _faces(shape) -> list:
    out, ex = [], TopExp_Explorer(shape, TopAbs_FACE)
    while ex.More():
        f = _static(TopoDS, "Face")(ex.Current())
        if not any(f.IsSame(g) for g in out):
            out.append(f)
        ex.Next()
    return out


def _lateral(cylinder) -> list:
    return [f for f in _faces(cylinder) if BRepAdaptor_Surface(f).GetType() == GeomAbs_Cylinder]


def _bounds(shape) -> tuple[float, float, float, float, float, float]:
    """The tight bounding box: OCCT's default one is padded by ~1e-7 (CLAUDE.md)."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    box = Bnd_Box()
    _static(BRepBndLib, "AddOptimal")(shape, box, False, False)
    lo, hi = box.CornerMin(), box.CornerMax()  # OCP 8 cannot return Get()'s struct
    return lo.X(), lo.Y(), lo.Z(), hi.X(), hi.Y(), hi.Z()


def _bounds_z(shape) -> tuple[float, float]:
    _, _, z0, _, _, z1 = _bounds(shape)
    return z0, z1


def _edges_by_rule(shape, rule: dict) -> list:
    """Edges matching a rule, in a stable order (by midpoint).

    {"parallel": "z", "outer": True}: straight edges along z on the outer bounding box.
    """
    x0, y0, _, x1, y1, _ = _bounds(shape)
    axis = "xyz".index(rule.get("parallel", "z"))
    found, ex = [], TopExp_Explorer(shape, TopAbs_EDGE)
    while ex.More():
        e = _static(TopoDS, "Edge")(ex.Current())
        c = BRepAdaptor_Curve(e)
        ex.Next()
        if any(e.IsSame(g) for g, _ in found) or c.GetType() != GeomAbs_Line:
            continue
        d = c.Line().Direction()
        if abs((d.X(), d.Y(), d.Z())[axis]) < 1 - 1e-9:
            continue
        mid = c.Value(0.5 * (c.FirstParameter() + c.LastParameter()))
        tol = max(1e-6 * max(x1 - x0, y1 - y0), 1e-9)
        on_x = min(abs(mid.X() - x0), abs(mid.X() - x1)) < tol
        on_y = min(abs(mid.Y() - y0), abs(mid.Y() - y1)) < tol
        if rule.get("outer") and not (on_x and on_y):
            continue
        found.append((e, (round(mid.X(), 9), round(mid.Y(), 9), round(mid.Z(), 9))))
    return [e for e, _ in sorted(found, key=lambda t: t[1])]


# --- signatures, the second tier --------------------------------------------------------


def signature(face) -> tuple[str, float, tuple[float, float, float]]:
    """Surface kind, area and centroid: what re-finds a face without history."""
    kind = {GeomAbs_Plane: "Plane", GeomAbs_Cylinder: "Cylinder"}.get(
        BRepAdaptor_Surface(face).GetType(), "Other"
    )
    props = GProp_GProps()
    _static(BRepGProp, "SurfaceProperties")(face, props)
    c = props.CentreOfMass()
    return kind, props.Mass(), (c.X(), c.Y(), c.Z())


def match_signatures(old: list, new_faces: list, *, tol: float, area_tol: float = 0.02) -> list:
    """For each old signature, the new face with the same kind, an area within
    `area_tol` and the nearest centroid within `tol` (or None)."""
    import math

    new = [(f, signature(f)) for f in new_faces]
    out = []
    for kind, area, centre in old:
        best, best_d = None, tol
        for f, (k, a, c) in new:
            if k != kind or abs(a - area) > area_tol * max(area, 1e-30):
                continue
            d = math.dist(c, centre)
            if d <= best_d:
                best, best_d = f, d
        out.append(best)
    return out


def write_brep(shape, path) -> None:
    from OCP.BRepTools import BRepTools

    _static(BRepTools, "Write")(shape, str(path))
