"""The deformed shape, faceted (ADR 0043 step 2): each part's deformed outer surface
from the mesh's boundary with every node moved by u (or -u, compensated), quadratic
faces split in four. As binary STL in metres, or as STEP AP242 with each part a
tessellated face, in millimetres."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from puzzle.errors import PuzzleError


def shape_notes(job, parts) -> dict:
    return {
        "deformation": f"x {job.scale:g}"
        + (" (true size)" if job.scale == 1 else " (exaggerated)"),
        "shape": "compensated: moved by -u" if job.compensate else "deformed: moved by +u",
        "parts": ", ".join(parts),
    }


def write_stl_job(job):
    parts = deformed_surfaces(job.project, job.mesh, job.step, job.scale, job.compensate)
    return write_stl(parts, job.path), shape_notes(job, parts)


def write_step_job(job):
    parts = deformed_surfaces(job.project, job.mesh, job.step, job.scale, job.compensate)
    return write_step(parts, job.path), shape_notes(job, parts) | {"length unit": "mm"}


def deformed_surfaces(project, mesh, step, scale: float = 1.0, compensate: bool = False):
    """Each part's deformed outer surface: part name -> (points (n, 3) m, triangles
    (m, 3) indices). Quadratic faces are split into four flat triangles each."""
    from puzzle.post.vtk import to_grid

    if "displacement" not in step.fields:
        raise PuzzleError("this step has no displacement to deform by", code="not_found")
    grid = to_grid(mesh, step)
    sign = -1.0 if compensate else 1.0
    grid = grid.warp_by_vector("displacement", factor=sign * scale)
    parts = [n for n, g in mesh.groups.items() if "/" not in n and g.dim in (2, 3)]
    out = {}
    element_ids = np.asarray(grid.cell_data["element_id"])
    for name in parts:
        own = mesh.groups[name]
        ids = np.concatenate([b.ids for b in own.blocks(mesh) if b.dim == own.dim] or [[]])
        cells = np.flatnonzero(np.isin(element_ids, ids))
        if not len(cells):
            continue
        piece = grid.extract_cells(cells)
        surface = piece.extract_surface(nonlinear_subdivision=1).triangulate().clean()
        faces = np.asarray(surface.faces).reshape(-1, 4)[:, 1:]
        out[name] = (np.asarray(surface.points, float), faces.astype(np.int64))
    if not out:
        raise PuzzleError("the mesh has no solids or shells to export", code="not_found")
    return out


def write_stl(parts: dict, path: Path) -> list[Path]:
    """Binary STL of every part's surface together (STL has no bodies), in metres."""
    import pyvista as pv

    merged = None
    for points, triangles in parts.values():
        faces = np.column_stack([np.full(len(triangles), 3), triangles]).ravel()
        mesh = pv.PolyData(points, faces)
        merged = mesh if merged is None else merged.merge(mesh)
    merged.save(str(path), binary=True)
    return [path]


def write_step(parts: dict, path: Path) -> list[Path]:
    """STEP AP242 with each part a tessellated face (TRIANGULATED_SURFACE_SET), the file
    in millimetres. The shape is built in metres with OCCT's working unit set to
    metres (Gmsh sets it so in this process too), and OCCT converts on writing."""
    from OCP.BRep import BRep_Builder
    from OCP.gp import gp_Pnt
    from OCP.Interface import Interface_Static
    from OCP.Poly import Poly_Triangle, Poly_Triangulation
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Controller, STEPControl_Writer
    from OCP.TopoDS import TopoDS_Compound, TopoDS_Face

    from puzzle.geometry.history import _static
    from puzzle.geometry.session import occ_lock

    with occ_lock:
        _static(STEPControl_Controller, "Init")()
        set_c, set_i = _static(Interface_Static, "SetCVal"), _static(Interface_Static, "SetIVal")
        set_c("write.step.schema", "AP242DIS")
        set_i("write.step.tessellated", 1)
        set_c("xstep.cascade.unit", "M")
        set_c("write.step.unit", "MM")
        builder, compound = BRep_Builder(), TopoDS_Compound()
        builder.MakeCompound(compound)
        for points, triangles in parts.values():
            mesh = Poly_Triangulation(len(points), len(triangles), False)
            for i, p in enumerate(points, start=1):
                mesh.SetNode(i, gp_Pnt(*(float(c) for c in p)))
            for i, t in enumerate(triangles, start=1):
                mesh.SetTriangle(i, Poly_Triangle(*(int(k) + 1 for k in t)))
            face = TopoDS_Face()
            builder.MakeFace(face, mesh)
            builder.Add(compound, face)
        writer = STEPControl_Writer()
        writer.Transfer(compound, STEPControl_AsIs)
        if str(writer.Write(str(path))).split(".")[-1] != "IFSelect_RetDone":
            raise PuzzleError(f"OCCT could not write {path}", code="export_failed")
    return [path]
