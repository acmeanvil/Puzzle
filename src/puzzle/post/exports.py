"""Exports (ADR 0043): results and the deformed shape for other software.

One table, `EXPORTS`, lists every format with what it carries; `export` writes one
run's results in a format and returns the files written. Each export also writes a
short `<name>.txt` beside it: the study, the run, the solver, the step and the units,
so a file found later says where it came from. Everything is SI (metres, pascals, ...)
unless `units` asks for the display units.

- **paraview:** every field of every step, `.pvd` + one `.vtu` per step;
- **point_cloud:** x, y, z and a result's values per node, as CSV: what Ansys's External
  Data, COMSOL's interpolation functions and Abaqus's mapped fields read to bring a
  field onto their own mesh. Optionally on one selection only;
- **deformed_stl / deformed_step:** the deformed outer surface, each part one body, as
  binary STL or as a STEP AP242 tessellated shell (a mesh body most CAD imports).
  `scale` exaggerates the deformation (1 = true); `compensate` writes the shape moved
  by -u, the one that deforms *into* the design (manufacturing compensation).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from puzzle.errors import PuzzleError


@dataclass(frozen=True)
class Export:
    label: str
    suffix: str
    carries: str
    needs_displacement: bool = False


EXPORTS = {
    "paraview": Export("ParaView (.pvd + .vtu)", ".pvd", "every field of every step"),
    "point_cloud": Export(
        "Point cloud (.csv: x, y, z, values)",
        ".csv",
        "one result's values per node, for mapping onto another tool's mesh",
    ),
    "deformed_stl": Export(
        "Deformed shape (.stl)", ".stl", "the deformed outer surface", needs_displacement=True
    ),
    "deformed_step": Export(
        "Deformed shape (.step, AP242 tessellated)",
        ".step",
        "the deformed outer surface as a mesh body, one per part",
        needs_displacement=True,
    ),
}


def export(
    project,
    store,
    run_id: str,
    fmt: str,
    path: Path,
    *,
    step: int = -1,
    quantity: str | None = None,
    selection: str | None = None,
    scale: float = 1.0,
    compensate: bool = False,
    units: str = "si",
) -> list[Path]:
    """Write a stored run's results as `fmt` (an `EXPORTS` key) at `path`."""
    if fmt not in EXPORTS:
        raise PuzzleError(
            f"no export format '{fmt}'", code="invalid", details={"formats": sorted(EXPORTS)}
        )
    record = store.get_run(run_id)
    mesh, results = store.read_mesh(record.mesh_id), store.read_results(run_id)
    if not results.steps:
        raise PuzzleError("the run has no result steps", code="not_found")
    path = Path(path).with_suffix(EXPORTS[fmt].suffix)
    path.parent.mkdir(parents=True, exist_ok=True)
    chosen = _step(results, step, "displacement" if EXPORTS[fmt].needs_displacement else None)
    notes = {"format": EXPORTS[fmt].label}
    if fmt == "paraview":
        from puzzle.post.vtk import export as vtu

        files = vtu(mesh, results, path)
        notes["steps"] = str(len(results.steps))
    elif fmt == "point_cloud":
        files, what = point_cloud(mesh, results, chosen, quantity, selection, path, units)
        notes |= what
    else:
        parts = deformed_surfaces(project, mesh, chosen, scale, compensate)
        files = write_stl(parts, path) if fmt == "deformed_stl" else write_step(parts, path)
        notes |= {
            "deformation": f"x {scale:g}" + (" (true size)" if scale == 1 else " (exaggerated)"),
            "shape": "compensated: moved by -u" if compensate else "deformed: moved by +u",
            "parts": ", ".join(parts),
        }
    notes |= {"step": chosen.label(), "length unit": "m" if fmt != "deformed_step" else "mm"}
    files.append(_sidecar(path, record, notes))
    return files


def _step(results, index: int, field: str | None):
    steps = [s for s in results.steps if field is None or field in s.fields]
    if not steps:
        raise PuzzleError(f"no step has a {field} result to export", code="not_found")
    try:
        return steps[index]
    except IndexError:
        raise PuzzleError(
            f"step {index} is out of range: the run has {len(steps)}", code="invalid"
        ) from None


def _sidecar(path: Path, record, notes: dict) -> Path:
    lines = [
        f"Exported by Puzzle, {datetime.now(UTC):%Y-%m-%d %H:%M} UTC",
        f"study: {record.study_name}",
        f"run: {record.run_id} ({record.solver}, {record.started_at:%Y-%m-%d %H:%M})",
        *(f"{k}: {v}" for k, v in notes.items()),
    ]
    out = path.with_suffix(".txt")
    out.write_text("\n".join(lines) + "\n")
    return out


# --- point clouds ------------------------------------------------------------------------


def point_cloud(mesh, results, step, quantity, selection, path: Path, units: str):
    """x, y, z and a result per node (a listed quantity, or all of a field's
    components), CSV with a header naming each column's unit."""
    from puzzle.post.derived import _items_of
    from puzzle.post.quantities import compute

    items = _items_of(results)
    if quantity is None:
        quantity = next((i.key for i in items if i.field in step.fields), None)
    item = next((i for i in items if i.key == quantity), None)
    if item is None or item.field not in step.fields:
        raise PuzzleError(
            f"no '{quantity}' in this step",
            code="not_found",
            details={"quantities": [i.key for i in items if i.field in step.fields]},
        )
    field = step.fields[item.field]
    if field.location != "node":
        raise PuzzleError(
            f"{item.label} is per element; a point cloud needs nodes", code="unsupported"
        )
    ids = field.ids
    if selection is not None:
        group = mesh.groups.get(selection)
        if group is None:
            raise PuzzleError(f"no selection '{selection}' in the mesh", code="not_found")
        ids = ids[np.isin(ids, group.node_ids)]
    values = field.at(ids)
    if item.components:  # a component, a magnitude or an equivalent: one column
        columns = [(item.label, compute(item, values, field.components))]
    else:
        columns = [
            (f"{item.label} {c}".strip(), values[:, k]) for k, c in enumerate(field.components)
        ]
    xyz = mesh.node_coords(ids)
    length, scale, unit = "m", 1.0, item.unit
    if units == "display":
        from puzzle.gui.viewport import _display
        from puzzle.settings import length_unit

        length, per_metre = length_unit()
        xyz = xyz * per_metre
        converted = []
        for name, col in columns:
            col, shown = _display(col, item.unit)
            converted.append((name, col))
            unit = shown
        columns = converted
    else:
        from puzzle.model.units import DIMENSIONS

        unit = DIMENSIONS[item.unit][1] if item.unit in DIMENSIONS else ""
    header = [f"x [{length}]", f"y [{length}]", f"z [{length}]"]
    header += [f"{name} [{unit}]" if unit else name for name, _ in columns]
    table = np.column_stack([xyz, *(c for _, c in columns)]) * scale
    path.write_text(
        ",".join(header)
        + "\n"
        + "\n".join(",".join(f"{v:.9g}" for v in row) for row in table)
        + "\n"
    )
    return [path], {
        "result": item.label,
        "nodes": str(len(ids)),
        "on": selection or "the whole model",
        "units": "display units" if units == "display" else "SI",
    }


# --- the deformed shape ------------------------------------------------------------------


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
