"""Coordinate systems that follow something else (ADR 0049 step 3), resolved.

A `CoordinateSystem` may be defined
- **from geometry:** a planar face (origin at its centroid, z along its outward normal,
  x from the system's own `x_axis` laid in the face), a cylindrical or conical face (z
  along its axis, the origin on the axis level with the face's centre), a spherical
  face (its centre), or an edge (the point `along` it, x along its tangent there; z
  along a circle's axis, else from the system's `xy_direction`);
- **attached to a part** (`component`, `part`): it follows the part instance's
  placement, so an assembly moves without redoing its systems;
- **relative to another system:** its origin and axes are given in that system's.

`absolute` gives the system as plain global points (origin, a point on x, one in the
xy plane), so every user of `centre()`, `orientation()` and `frames()` reads it as any
other. The stored object keeps how it was made, so after an edit it re-derives; a face
or edge that is gone is an error that names it.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from puzzle.errors import PuzzleError


class AxesError(PuzzleError):
    code = "not_found"


def absolute(project, cs, root: Path | None = None, _seen: tuple = ()):
    """`cs` in global terms, as three points; unchanged if it follows nothing."""
    if not cs.derived():
        return cs
    if cs.id in _seen:
        raise AxesError(f"axes '{cs.name}' are defined relative to themselves", code="invalid")
    origin, frame = _own(project, cs, root)
    if cs.part is not None:
        from puzzle.geometry.model import rotation_matrix
        from puzzle.model.schema import Component

        comp = project.get(cs.component, Component)
        part = next((p for p in comp.parts if p.name == cs.part), None)
        if part is None:
            raise AxesError(f"axes '{cs.name}': component '{comp.name}' has no part '{cs.part}'")
        turn = rotation_matrix(part.rotation)
        origin, frame = turn @ origin + np.asarray(part.translation, float), turn @ frame
    if cs.relative_to is not None:
        from puzzle.model.schema import CoordinateSystem

        parent = absolute(project, project.get(cs.relative_to, CoordinateSystem), root,
                          (*_seen, cs.id))  # fmt: skip
        turn = parent.orientation()
        origin, frame = parent.centre() + turn @ origin, turn @ frame
    points = [
        tuple(float(c) for c in v) for v in (origin, origin + frame[:, 0], origin + frame[:, 1])
    ]
    return cs.model_copy(update={
        "points": points, "angles": None, "origin": points[0],
        "relative_to": None, "component": None, "part": None, "geometry": None,
        "entity": None, "entity_name": None,
    })  # fmt: skip


def resolved_project(project, root: Path | None = None):
    """A copy of the project with every derived system made absolute (`run_study`
    hands this to the adapters), or the project itself when none is derived."""
    from puzzle.model.schema import CoordinateSystem

    derived = [cs for cs in project.of_type(CoordinateSystem) if cs.derived()]
    if not derived:
        return project
    import dataclasses

    objects = dict(project.objects)
    for cs in derived:
        objects[cs.id] = absolute(project, cs, root)
    return dataclasses.replace(project, objects=objects)


# --- a system's own frame: from geometry, or as typed ---------------------------------------


def _own(project, cs, root) -> tuple[np.ndarray, np.ndarray]:
    """Its origin and axes (columns) in its own frame: the geometry's, before any part's
    placement or parent system."""
    if cs.entity is None:
        plain = cs.model_copy(update={"relative_to": None, "component": None, "part": None})
        return plain.centre(), plain.orientation()
    shape = _entity(project, cs, root)
    if cs.entity == "face":
        return _from_face(cs, shape)
    return _from_edge(cs, shape)


def _entity(project, cs, root):
    from puzzle.geometry.naming import named_geometry
    from puzzle.model.schema import Component, Geometry

    geometry_id = cs.geometry
    if geometry_id is None and cs.part is not None:
        comp = project.get(cs.component, Component)
        geometry_id = next((p.geometry for p in comp.parts if p.name == cs.part), None)
    if geometry_id is None:
        raise AxesError(f"axes '{cs.name}' from a {cs.entity} name no geometry")
    geo = project.get(geometry_id, Geometry)
    named = named_geometry(project, geo, root or Path("."))
    shapes = named.of.get(cs.entity, {}).get(cs.entity_name)
    if not shapes:
        raise AxesError(
            f"axes '{cs.name}': {cs.entity} '{cs.entity_name}' is gone from geometry "
            f"'{geo.name}' (reattach it, or define the axes another way)",
            path=cs.name,
        )
    return shapes[0]


def _frame(z: np.ndarray, hint: np.ndarray) -> np.ndarray:
    """Columns x, y, z with z given and x as near the hint as the plane across z allows."""
    z = z / np.linalg.norm(z)
    x = hint - (hint @ z) * z
    if np.linalg.norm(x) < 1e-9:
        x = np.eye(3)[int(np.argmin(np.abs(z)))]
        x = x - (x @ z) * z
    x = x / np.linalg.norm(x)
    return np.column_stack([x, np.cross(z, x), z])


def _vec(d) -> np.ndarray:
    return np.array([d.X(), d.Y(), d.Z()], float)


def _from_face(cs, face):
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cone, GeomAbs_Cylinder, GeomAbs_Plane, GeomAbs_Sphere
    from OCP.TopAbs import TopAbs_REVERSED

    from puzzle.geometry.history import signature

    surface = BRepAdaptor_Surface(face)
    kind = surface.GetType()
    centre = np.asarray(signature(face)[2], float)
    hint = np.asarray(cs.x_axis, float)
    if kind == GeomAbs_Plane:
        n = _vec(surface.Plane().Axis().Direction())
        if face.Orientation() == TopAbs_REVERSED:
            n = -n
        return centre, _frame(n, hint)
    if kind in (GeomAbs_Cylinder, GeomAbs_Cone):
        ax = (surface.Cylinder() if kind == GeomAbs_Cylinder else surface.Cone()).Axis()
        z, at = _vec(ax.Direction()), _vec(ax.Location())
        origin = at + ((centre - at) @ z) * z  # on the axis, level with the face
        return origin, _frame(z, hint)
    if kind == GeomAbs_Sphere:
        return _vec(surface.Sphere().Location()), _frame(np.asarray(cs.axis, float), hint)
    raise AxesError(f"axes '{cs.name}': face '{cs.entity_name}' is not flat, round or spherical",
                    code="invalid")  # fmt: skip


def _from_edge(cs, edge):
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GeomAbs import GeomAbs_Circle
    from OCP.gp import gp_Pnt, gp_Vec

    curve = BRepAdaptor_Curve(edge)
    t = curve.FirstParameter() + cs.along * (curve.LastParameter() - curve.FirstParameter())
    point, tangent = gp_Pnt(), gp_Vec()
    curve.D1(t, point, tangent)
    x = _vec(tangent)
    x = x / np.linalg.norm(x)
    if curve.GetType() == GeomAbs_Circle:
        z = _vec(curve.Circle().Axis().Direction())
    else:
        z = np.cross(x, np.asarray(cs.xy_direction, float))
        if np.linalg.norm(z) < 1e-9:
            z = np.cross(x, np.eye(3)[int(np.argmin(np.abs(x)))])
    z = z / np.linalg.norm(z)
    return np.array([point.X(), point.Y(), point.Z()]), np.column_stack([x, np.cross(z, x), z])
