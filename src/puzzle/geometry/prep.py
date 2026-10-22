"""Geometry preparation (G2, ADR 0014): import, heal, remove features, share topology.

These run as history operations (see puzzle.geometry.history), so preparing imported
CAD is recorded and replayed like any other step:

| kind | args | effect |
| --- | --- | --- |
| import | file, scale; unit (BREP only) | faces `<name>/face-N`, bodies by STEP part |
| heal | tolerance (m; default 1e-6 of the size) | fix and sew faces, make solids |
| remove | rule | delete faces; the neighbours extend to close the gap |
| fragment | - | touching bodies share faces where they meet (conformal) |

`remove` rules: {"fillets_below": r} rounds of radius at most r, {"holes_below": r}
cylindrical holes of radius at most r, {"face": name or [names]}, {"area_below": a}.
"""

from __future__ import annotations

import math
from pathlib import Path

from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Torus
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_SOLID

UNITS = {"m": 1.0, "mm": 1e-3, "cm": 1e-2, "um": 1e-6, "in": 0.0254, "ft": 0.3048}


class PrepError(ValueError):
    pass


# --- import ------------------------------------------------------------------------------


def read_cad(path: Path, unit: str | None = None, scale: float = 1.0):
    """A STEP, IGES or BREP file as one shape in metres, and its solids by part name.

    OCCT's STEP and IGES readers convert the file's own unit to millimetres; `scale`
    corrects a file that declares the wrong one. BREP has no unit: `unit` says what it
    is in (metres by default, as Puzzle writes it).
    """
    from puzzle.geometry.history import _static

    path = Path(path)
    if not path.exists():
        raise PrepError(f"no file {path}")
    suffix = path.suffix.lower()
    labels: dict[str, list] = {}
    if suffix in (".step", ".stp"):
        shape, labels = _read_step(path)
        factor = 1e-3 * scale
    elif suffix in (".iges", ".igs"):
        from OCP.IGESControl import IGESControl_Reader

        reader = IGESControl_Reader()
        if reader.ReadFile(str(path)) != 1:  # IFSelect_RetDone
            raise PrepError(f"cannot read {path.name}")
        reader.TransferRoots()
        shape, factor = reader.OneShape(), 1e-3 * scale
    elif suffix == ".brep":
        from OCP.BRep import BRep_Builder
        from OCP.BRepTools import BRepTools
        from OCP.TopoDS import TopoDS_Shape

        shape = TopoDS_Shape()
        _static(BRepTools, "Read")(shape, str(path), BRep_Builder())
        if unit is not None and unit not in UNITS:
            raise PrepError(f"unit must be one of {sorted(UNITS)}, not '{unit}'")
        factor = UNITS[unit or "m"] * scale
    else:
        raise PrepError(f"import reads STEP, IGES and BREP, not '{suffix}'")
    if shape.IsNull():
        raise PrepError(f"{path.name} holds no shape")
    if not math.isclose(factor, 1.0):
        shape, labels = _scaled(shape, labels, factor)
    return shape, labels


def _read_step(path: Path):
    """STEP through the document reader, which keeps part names ({name: [solids]})."""
    from OCP.STEPCAFControl import STEPCAFControl_Reader
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name

    try:  # OCP 8 moved OCCT's collections into one module
        from OCP.collections import Sequence_TDF_Label as TDF_LabelSequence
    except ImportError:
        from OCP.TDF import TDF_LabelSequence
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_ShapeTool

    from puzzle.geometry.history import _static, _subshapes

    doc = TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))
    reader = STEPCAFControl_Reader()
    reader.SetNameMode(True)
    if reader.ReadFile(str(path)) != 1 or not reader.Transfer(doc):
        raise PrepError(f"cannot read {path.name}")
    tool = _static(XCAFDoc_DocumentTool, "ShapeTool")(doc.Main())
    get_shape = _static(XCAFDoc_ShapeTool, "GetShape")
    is_assembly = _static(XCAFDoc_ShapeTool, "IsAssembly")
    components = _static(XCAFDoc_ShapeTool, "GetComponents")
    is_reference = _static(XCAFDoc_ShapeTool, "IsReference")
    referred = _static(XCAFDoc_ShapeTool, "GetReferredShape")
    name_id = _static(TDataStd_Name, "GetID")()

    def name_of(label) -> str:
        attr = TDataStd_Name()
        return attr.Get().ToExtString() if label.FindAttribute(name_id, attr) else ""

    labels: dict[str, list] = {}
    roots = TDF_LabelSequence()
    tool.GetFreeShapes(roots)

    def walk(label) -> None:
        """Leaf parts with their names; an instance's placement is in its own shape."""
        target = label
        if is_reference(label):
            from OCP.TDF import TDF_Label

            target = TDF_Label()
            referred(label, target)
        if is_assembly(target):
            children = TDF_LabelSequence()
            components(target, children, False)
            for i in range(1, children.Length() + 1):
                walk(children.Value(i))
            return
        name = name_of(label) or name_of(target)
        solids = _subshapes(get_shape(label), TopAbs_SOLID)
        if name and solids:
            labels.setdefault(name, []).extend(solids)

    for i in range(1, roots.Length() + 1):
        walk(roots.Value(i))
    shape = reader.Reader().OneShape() if hasattr(reader, "Reader") else None
    if shape is None or shape.IsNull():
        shapes = [get_shape(roots.Value(i)) for i in range(1, roots.Length() + 1)]
        if len(shapes) == 1:
            shape = shapes[0]
        else:
            from OCP.BRep import BRep_Builder
            from OCP.TopoDS import TopoDS_Compound

            shape, builder = TopoDS_Compound(), BRep_Builder()
            builder.MakeCompound(shape)
            for s in shapes:
                builder.Add(shape, s)
    # Name lookups must use the solids of the shape we return.
    solids = _subshapes(shape, TopAbs_SOLID)
    labels = {n: [s for s in solids if any(s.IsSame(x) for x in xs)] for n, xs in labels.items()}
    return shape, {n: xs for n, xs in labels.items() if xs}


def _scaled(shape, labels, factor: float):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Pnt, gp_Trsf

    from puzzle.geometry.history import _list

    trsf = gp_Trsf()
    trsf.SetScale(gp_Pnt(0, 0, 0), factor)
    mk = BRepBuilderAPI_Transform(shape, trsf, True)
    moved = {n: _list(mk.ModifiedShape(s) for s in ss) for n, ss in labels.items()}
    return mk.Shape(), moved


# --- heal -------------------------------------------------------------------------------


def heal(shape, tolerance: float):
    """Fix faces and wires, sew the faces into shells, make solids of closed shells.

    Returns the healed shape and a function mapping an old face (or solid) to its
    healed pieces, for carrying names.
    """
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Sewing
    from OCP.ShapeFix import ShapeFix_Shape

    from puzzle.geometry.history import _list, _subshapes

    sewing = BRepBuilderAPI_Sewing(tolerance)
    sewing.Add(shape)
    sewing.Perform()
    sewn = sewing.SewedShape()
    fix = ShapeFix_Shape(sewn)
    fix.SetPrecision(tolerance)
    fix.SetMaxTolerance(10 * tolerance)
    fix.Perform()
    healed = fix.Shape()
    context = fix.Context()
    final = _subshapes(healed, TopAbs_FACE) + _subshapes(healed, TopAbs_SOLID)

    def mapped(sub) -> list:
        # Faces inside the sewn shape are sub-shapes: Modified() only knows whole inputs.
        step = sewing.ModifiedSubShape(sub) if sewing.IsModifiedSubShape(sub) else sub
        value = context.Value(step)
        pieces = _list([value]) if not value.IsNull() else []
        out = []
        for p in pieces:
            if p.ShapeType() in (TopAbs_FACE, TopAbs_SOLID):
                out += [p]
            else:  # a face may come back inside a shell or compound
                out += _subshapes(p, TopAbs_FACE)
        return [p for p in out if any(p.IsSame(f) for f in final)] or (
            [sub] if any(sub.IsSame(f) for f in final) else []
        )

    return _solidify(healed), mapped


def _solidify(shape):
    """Closed shells left by sewing become solids."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeSolid
    from OCP.TopAbs import TopAbs_SHELL

    from puzzle.geometry.history import _subshapes

    if _subshapes(shape, TopAbs_SOLID):
        return shape
    shells = _subshapes(shape, TopAbs_SHELL)
    if len(shells) != 1:
        return shape
    from OCP.TopoDS import TopoDS

    from puzzle.geometry.history import _static

    mk = BRepBuilderAPI_MakeSolid(_static(TopoDS, "Shell")(shells[0]))
    return mk.Solid() if mk.IsDone() else shape


# --- remove features ----------------------------------------------------------------------


def faces_by_rule(state, rule: dict) -> list:
    """Faces a `remove` rule picks, in stable order."""
    from puzzle.geometry.history import _faces, signature

    faces = _faces(state.shape)
    picked = []
    if "face" in rule:
        names = rule["face"] if isinstance(rule["face"], list) else [rule["face"]]
        picked = [f for n in names for f in state.names.get(n, [])]
    slack = 1 + 1e-9
    for f in faces:
        surface = BRepAdaptor_Surface(f)
        kind, radius = surface.GetType(), _radius(surface)
        rounded = kind == GeomAbs_Torus or (kind == GeomAbs_Cylinder and _is_round(surface))
        if "fillets_below" in rule and rounded and radius <= rule["fillets_below"] * slack:
            picked.append(f)
        bore = kind == GeomAbs_Cylinder and not _is_round(surface)
        if "holes_below" in rule and bore and radius <= rule["holes_below"] * slack:
            picked.append(f)
        if "area_below" in rule and signature(f)[1] <= rule["area_below"]:
            picked.append(f)
    unique = []
    for f in picked:
        if not any(f.IsSame(u) for u in unique):
            unique.append(f)
    return unique


def _radius(surface) -> float:
    """A fillet's radius: a torus's minor radius, a cylinder's radius."""
    kind = surface.GetType()
    if kind == GeomAbs_Torus:
        return surface.Torus().MinorRadius()
    if kind == GeomAbs_Cylinder:
        return surface.Cylinder().Radius()
    return math.inf


def _is_round(surface) -> bool:
    """A cylindrical face that is part of a turn (a fillet), not a full bore."""
    span = surface.LastUParameter() - surface.FirstUParameter()
    return span < 2 * math.pi - 1e-6


def remove_faces(shape, faces: list):
    """OCCT's defeaturing: delete the faces and extend their neighbours to close it."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Defeaturing

    algo = BRepAlgoAPI_Defeaturing()
    algo.SetShape(shape)
    for f in faces:
        algo.AddFaceToRemove(f)
    algo.SetRunParallel(False)
    algo.Build()
    if not algo.IsDone():
        raise PrepError("the features could not be removed (their neighbours do not meet)")
    return algo


# --- share topology ---------------------------------------------------------------------


def fragment(shape):
    """Split every body by the others where they touch, so they share those faces."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_BuilderAlgo

    from puzzle.geometry.history import TopTools_ListOfShape, _subshapes

    solids = _subshapes(shape, TopAbs_SOLID)
    if len(solids) < 2:
        raise PrepError("fragment needs at least two bodies")
    algo = BRepAlgoAPI_BuilderAlgo()
    args = TopTools_ListOfShape()
    for s in solids:
        args.Append(s)
    algo.SetArguments(args)
    algo.Build()
    if not algo.IsDone():
        raise PrepError("the bodies could not be fragmented")
    return algo


# --- check ------------------------------------------------------------------------------


def check(shape, small: float | None = None) -> list[dict]:
    """What would trouble meshing or solving, worst first. Each finding: kind,
    severity (error, warning, note), a message, and a count or measure.

    `small` is the length below which edges count as tiny (default: 1/1000 of the size).
    """
    from OCP.BRepCheck import BRepCheck_Analyzer

    from puzzle.geometry.history import _bounds, _faces, _subshapes, body_signature, signature

    b = _bounds(shape)
    size = math.dist(b[:3], b[3:]) or 1.0
    small = small or 1e-3 * size
    out: list[dict] = []

    if not BRepCheck_Analyzer(shape).IsValid():
        out.append(_finding("invalid", "error", "the shape is not valid B-rep; heal it first"))
    solids = _subshapes(shape, TopAbs_SOLID)
    if not solids:
        out.append(
            _finding("not_solid", "error", "no solid bodies: only surfaces (heal can sew them)")
        )

    free = _free_edges(shape)
    if free:
        length = sum(_length(e) for e in free)
        out.append(
            _finding(
                "open",
                "error" if solids else "warning",
                f"{len(free)} free edges ({length * 1e3:.3g} mm in all): the surface has gaps",
                count=len(free),
            )
        )

    edges = _subshapes(shape, TopAbs_EDGE)
    short = [e for e in edges if _length(e) < small]
    if short:
        shortest = min(_length(e) for e in short)
        out.append(
            _finding(
                "short_edges",
                "warning",
                f"{len(short)} edges shorter than {small * 1e3:.3g} mm (shortest "
                f"{shortest * 1e3:.3g} mm): the mesh will refine around them",
                count=len(short),
            )
        )

    tiny, slivers = [], []
    for f in _faces(shape):
        area = signature(f)[1]
        perimeter = sum(_length(e) for e in _subshapes(f, TopAbs_EDGE))
        if area < small**2:
            tiny.append(f)
        elif perimeter > 0 and 4 * math.pi * area / perimeter**2 < 0.01:  # very thin
            slivers.append(f)
    if tiny:
        out.append(_finding("tiny_faces", "warning", f"{len(tiny)} tiny faces", count=len(tiny)))
    if slivers:
        out.append(
            _finding(
                "slivers",
                "warning",
                f"{len(slivers)} sliver faces (long and thin)",
                count=len(slivers),
            )
        )

    overlaps, unshared = [], []
    for i, a in enumerate(solids):
        for c in solids[i + 1 :]:
            common = _common_volume(a, c)
            if common > 1e-9 * min(body_signature(a)[0], body_signature(c)[0]):
                overlaps.append(common)
            elif _touch(a, c, small) and not _share_face(a, c):
                unshared.append((a, c))
    if overlaps:
        out.append(
            _finding(
                "overlap",
                "error",
                f"{len(overlaps)} pairs of bodies overlap ({sum(overlaps) * 1e9:.3g} mm^3)",
                count=len(overlaps),
            )
        )
    if unshared:
        out.append(
            _finding(
                "not_shared",
                "note",
                f"{len(unshared)} pairs of bodies touch without sharing faces: fragment them "
                "for a conformal mesh, or bond them with a contact",
                count=len(unshared),
            )
        )
    order = {"error": 0, "warning": 1, "note": 2}
    return sorted(out, key=lambda f: order[f["severity"]])


def _finding(kind: str, severity: str, message: str, count: int | None = None) -> dict:
    out = {"kind": kind, "severity": severity, "message": message}
    return out | ({"count": count} if count is not None else {})


def _length(edge) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    from puzzle.geometry.history import _static

    props = GProp_GProps()
    _static(BRepGProp, "LinearProperties")(edge, props)
    return props.Mass()


def _free_edges(shape) -> list:
    """Edges used by a single face: the borders of holes in the surface."""
    from OCP.BRep import BRep_Tool
    from OCP.TopExp import TopExp

    try:  # OCP 8 moved OCCT's collections into one module
        from OCP.collections import (
            IndexedDataMap_TopoDS_Shape_List_TopoDS_Shape_TopTools_ShapeMapHasher as EdgeFaces,
        )
    except ImportError:
        from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape as EdgeFaces

    from puzzle.geometry.history import _static

    ancestors = EdgeFaces()
    _static(TopExp, "MapShapesAndAncestors")(shape, TopAbs_EDGE, TopAbs_FACE, ancestors)
    degenerated = _static(BRep_Tool, "Degenerated")
    out = []
    for i in range(1, ancestors.Extent() + 1):
        edge = ancestors.FindKey(i)
        from OCP.TopoDS import TopoDS

        e = _static(TopoDS, "Edge")(edge)
        if ancestors.FindFromIndex(i).Size() == 1 and not degenerated(e):
            out.append(e)
    return out


def _common_volume(a, b) -> float:
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common

    from puzzle.geometry.history import _subshapes, body_signature

    common = BRepAlgoAPI_Common(a, b)
    return sum(body_signature(s)[0] for s in _subshapes(common.Shape(), TopAbs_SOLID))


def _touch(a, b, tol: float) -> bool:
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape

    dist = BRepExtrema_DistShapeShape(a, b)
    return dist.IsDone() and dist.Value() <= tol


def _share_face(a, b) -> bool:
    from puzzle.geometry.history import _faces

    fb = _faces(b)
    return any(f.IsSame(g) for f in _faces(a) for g in fb)


def shape_of(project, geometry, root: Path):
    """A project geometry as an OCCT shape: its history regenerated (with the project's
    parameters, SI), or its source file read."""
    from puzzle.geometry.history import Op, regenerate
    from puzzle.model.schema import ParameterSet

    if geometry.history:
        params = {
            k: q.to_si() for ps in project.of_type(ParameterSet) for k, q in ps.values.items()
        }
        ops = [Op(s.kind, s.name, dict(s.args)) for s in geometry.history]
        return regenerate(ops, params, root).shape
    return read_cad(Path(root) / geometry.source)[0]


def check_geometry(project, geometry, root: Path) -> list[dict]:
    """The check report of a project geometry (see `check`)."""
    return check(shape_of(project, geometry, root))
