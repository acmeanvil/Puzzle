"""Exports (ADR 0043): results and the deformed shape for other software.

One table, `EXPORTS`, lists every format: its name, suffix, what it carries, the
physics it is for, the options it takes, and its writer (a module of this package).
`export` writes one run's results in a format and returns the files written. Each
export also writes a short `<name>.txt` beside it: the study, the run, the solver, the
step and the units, so a file found later says where it came from. Everything is SI
(metres, pascals, ...) unless `units` asks for the display units.

A writer is `write(job) -> (files, notes)`: `job` (`Job`) holds the project, the run's
mesh and results, the chosen step, the path (its suffix set) and the options; `notes`
go in the sidecar (a "length unit" note replaces the default "m").

The options a format takes (`Export.options`), which the Export form, the CLI and MCP
offer only where they apply:

- `step`: one step (the chosen one is `job.step`); without it, every step;
- `quantity`: one result (a quantity key, as the results bar's);
- `selection`: one selection's nodes only;
- `units`: SI or the display units;
- `scale`, `compensate`: the deformed shape's exaggeration, and -u instead of +u.

The wall cloud also reads `quantity` (one of `wall.QUANTITIES`, else all of them) and
`reference_temperature` (K, through `extra`) for its heat transfer coefficient.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from puzzle.errors import PuzzleError

STRUCTURAL, THERMAL, EM, FLUID = "structural", "thermal", "electromagnetic", "fluid"
EVERY = frozenset({STRUCTURAL, THERMAL, EM, FLUID})


@dataclass(frozen=True)
class Export:
    label: str
    suffix: str
    carries: str
    writer: str  # "module:function" in this package
    options: frozenset[str] = frozenset({"step"})
    physics: frozenset[str] = EVERY
    needs_displacement: bool = False
    needs: str = ""  # a step field the chosen step must have ("" any)

    def takes(self, option: str) -> bool:
        return option in self.options


@dataclass
class Job:
    """What a writer gets."""

    project: Any
    record: Any
    mesh: Any
    results: Any
    step: Any  # the chosen StepResult (the last one with `needs`, unless asked)
    path: Path
    quantity: str | None = None
    selection: str | None = None
    scale: float = 1.0
    compensate: bool = False
    units: str = "si"
    root: Path | None = None
    extra: dict[str, Any] = field(default_factory=dict)


EXPORTS = {
    "paraview": Export(
        "ParaView (.pvd + .vtu)", ".pvd", "every field of every step", "paraview:write",
        options=frozenset(),
    ),
    "point_cloud": Export(
        "Point cloud (.csv: x, y, z, values)", ".csv",
        "one result's values per node, for mapping onto another tool's mesh",
        "cloud:write", options=frozenset({"step", "quantity", "selection", "units"}),
    ),
    "deformed_stl": Export(
        "Deformed shape (.stl)", ".stl", "the deformed outer surface", "deformed:write_stl_job",
        options=frozenset({"step", "scale", "compensate"}), physics=frozenset({STRUCTURAL}),
        needs_displacement=True, needs="displacement",
    ),
    "deformed_step": Export(
        "Deformed shape (.step, AP242 tessellated)", ".step",
        "the deformed outer surface as a mesh body, one per part", "deformed:write_step_job",
        options=frozenset({"step", "scale", "compensate"}), physics=frozenset({STRUCTURAL}),
        needs_displacement=True, needs="displacement",
    ),
    "exodus": Export(
        "Exodus II (.exo)", ".exo",
        "every field of every step; parts as element blocks, selections as node and side sets",
        "exodus:write", options=frozenset(),
    ),
    "vtkhdf": Export(
        "VTKHDF (.vtkhdf)", ".vtkhdf", "every field of every step in one HDF5 file",
        "vtkhdf:write", options=frozenset(),
    ),
    "unv": Export(
        "I-DEAS universal file (.unv)", ".unv",
        "nodes, elements, groups and results per step (modes with their frequencies)",
        "unv:write", options=frozenset(), physics=frozenset({STRUCTURAL, THERMAL}),
    ),
    "ensight": Export(
        "EnSight Gold (.case)", ".case",
        "every field of every step, per cell and per node; each face selection a part "
        "named after it", "ensight:write", options=frozenset(),
        physics=frozenset({FLUID, THERMAL}),
    ),
    "cgns": Export(
        "CGNS (.cgns, HDF5)", ".cgns",
        "the mesh, every step's flow solution (cell-centred and nodal) and each face "
        "selection as a boundary condition named after it", "cgns:write",
        options=frozenset(), physics=frozenset({FLUID, THERMAL}),
    ),
    "tecplot": Export(
        "Tecplot ASCII (.dat)", ".dat",
        "every field of every step, cell-centred or nodal; a zone per part and face "
        "selection", "tecplot:write", options=frozenset(),
        physics=frozenset({FLUID, THERMAL}),
    ),
    "wall_cloud": Export(
        "Wall quantities (.csv: x, y, z, values)", ".csv",
        "wall pressure, shear stress, y+ and heat transfer coefficient (with its reference "
        "temperature) at a wall's face centres, for a structure or a conjugate heat "
        "transfer model elsewhere", "wall:write",
        options=frozenset({"step", "selection", "units"}), physics=frozenset({FLUID}),
        needs="velocity",
    ),
    "nastran": Export(
        "Nastran bulk data and punch results (.bdf + .pch)", ".bdf",
        "the mesh, properties, materials and selections as sets (large-field, metres); "
        "displacements, mode eigenvalues and solid stresses per step as punch",
        "nastran:write", options=frozenset(), physics=frozenset({STRUCTURAL}),
    ),
    "abaqus_inp": Export(
        "Abaqus/CalculiX mesh and temperatures (.inp)", ".inp",
        "the mesh, sets, materials and sections; a step's nodal temperatures as "
        "*TEMPERATURE data beside it",
        "abaqus:write", physics=frozenset({STRUCTURAL, THERMAL}),
    ),
    "abaqus_displacement": Export(
        "Abaqus/CalculiX mesh and displacements as *BOUNDARY (.inp)", ".inp",
        "the mesh deck, and a step's nodal displacements as *BOUNDARY data (a submodel's "
        "boundary)", "abaqus:write_displacement",
        options=frozenset({"step", "selection"}), physics=frozenset({STRUCTURAL}),
        needs_displacement=True, needs="displacement",
    ),
    "gltf": Export(
        "glTF 3D picture (.glb)", ".glb",
        "each part's outer surface coloured by one result (the legend's colour map), "
        "deformed by the step's displacement times the scale",
        "gltf:write", options=frozenset({"step", "quantity", "scale"}),
    ),
    "lorentz_cloud": Export(
        "Lorentz force density J × B (.csv point cloud)", ".csv",
        "x, y, z and the body force per volume J × B (N/m^3) per node, for a structural "
        "model elsewhere", "lorentz:write", options=frozenset({"step", "selection"}),
        physics=frozenset({EM}),
    ),
    "morphed_step": Export(
        "Deformed shape (.step, morphed CAD)", ".step",
        "each solid part's CAD faces moved by u: real surfaces CAD can edit, one body per "
        "part", "morphed:write_job",
        options=frozenset({"step", "scale", "compensate"}), physics=frozenset({STRUCTURAL}),
        needs_displacement=True, needs="displacement",
    ),
}  # fmt: skip


def writer(fmt: str):
    module, name = EXPORTS[fmt].writer.split(":")
    return getattr(importlib.import_module(f"{__name__}.{module}"), name)


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
    axes: str | None = None,
    root: Path | None = None,
    **extra,
) -> list[Path]:
    """Write a stored run's results as `fmt` (an `EXPORTS` key) at `path`; with `axes`
    (a coordinate system's name or id), vectors and tensors in its axes (ADR 0049).
    `extra` carries a format's own options (its writer reads `job.extra`)."""
    if fmt not in EXPORTS:
        raise PuzzleError(
            f"no export format '{fmt}'", code="invalid", details={"formats": sorted(EXPORTS)}
        )
    spec = EXPORTS[fmt]
    if root is None:  # the project folder holds the results store
        root = Path(store.root).parent
    record = store.get_run(run_id)
    mesh, results = store.read_mesh(record.mesh_id), store.read_results(run_id)
    if not results.steps:
        raise PuzzleError("the run has no result steps", code="not_found")
    turned = None
    if axes:
        if spec.needs_displacement:
            raise PuzzleError("a deformed shape is exported in global axes only", code="invalid")
        from puzzle.axes import absolute
        from puzzle.commands import resolve
        from puzzle.model.schema import CoordinateSystem
        from puzzle.post.axes import in_axes

        named = resolve(project, axes, CoordinateSystem)
        turned = absolute(project, named, root)
        results = in_axes(mesh, results, turned)
    path = Path(path).with_suffix(spec.suffix)
    path.parent.mkdir(parents=True, exist_ok=True)
    chosen = _step(results, step, spec.needs or None)
    notes = {"format": spec.label}
    if turned is not None:
        letters = {"cartesian": "x, y, z", "cylindrical": "r, θ, z", "spherical": "r, θ, φ"}
        notes["components"] = (
            f"in axes '{named.name}' ({turned.type}: {letters[turned.type]}; the component "
            "slots keep their names, xx meaning the first axis)"
        )
    job = Job(project, record, mesh, results, chosen, path, quantity, selection, scale,
              compensate, units, root, extra)  # fmt: skip
    files, written = writer(fmt)(job)
    notes |= {"step": chosen.label() if spec.takes("step") else f"all {len(results.steps)}",
              "length unit": "m"} | written  # fmt: skip
    files = list(files)
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
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out
