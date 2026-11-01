"""I-DEAS universal file (ADR 0043 step 3): ASCII, the common language of test-analysis
correlation (Simcenter Testlab, ME'scope, FEMtools) and of many FE tools.

Datasets written, with the record layouts of the universal file format as the SDRL
(University of Cincinnati) "Universal File Datasets Summary" gives them, as reproduced in
pyuff's dataset modules:

- 164 units: SI (metre, newton; temperatures absolute, kelvin);
- 2411 nodes, double precision: `4I10` label, export and displacement coordinate system
  (1, 1: global), colour; `1P3D25.16` coordinates in metres;
- 2412 elements: `6I10` label, FE descriptor, physical and material property table (the
  part's number), colour, node count; beams a `3I10` record (orientation node, fore and
  aft cross sections: 0); `8I10` node labels. Node orders are I-DEAS's: corners and mid
  nodes alternate around a quadratic element's edges (Gmsh's UNV writer's maps);
- 2467 permanent groups: every part and selection by its name (`20A2`, 40 characters),
  `8I10` group record, then two entities per line (type 7 node or 8 element, label, 0, 0);
- 2414 analysis data, per step and result: displacement (3 DOF vector), stress and
  strain (symmetric tensor, I-DEAS's order xx, xy, yy, xz, yz, zz), temperature, heat flux
  and reaction force, others as "unknown" scalars, vectors or tensors named in ID line 2;
  values `6E13.5` (single precision, data type 2). Records 9-13 say the analysis: static
  (load case), normal mode (mode number, frequency in Hz), transient (time step, time),
  frequency response (frequency), buckling (mode, eigenvalue = load factor).

A node without a value at a step has no record there (the format's "no data").
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np

from puzzle.errors import PuzzleError
from puzzle.post.exports.series import blocks, values_at

# Gmsh element -> (FE descriptor for a solid, a shell, a plane stress and an
# axisymmetric face), node order from Gmsh's.
DESCRIPTORS = {
    "tet4": ((111, None, None, None), None),
    "tet10": ((118, None, None, None), [0, 4, 1, 5, 2, 6, 7, 9, 8, 3]),
    "hex8": ((115, None, None, None), None),
    "wedge6": ((112, None, None, None), None),
    "tri3": ((None, 91, 41, 81), None),
    "tri6": ((None, 92, 42, 82), [0, 3, 1, 4, 2, 5]),
    "quad4": ((None, 94, 44, 84), None),
    "quad8": ((None, 95, 45, 85), [0, 4, 1, 5, 2, 6, 3, 7]),
    "line2": ((21, None, None, None), None),
    "line3": ((24, None, None, None), [0, 2, 1]),
}
BEAMS = {21, 22, 24}

# Stored field -> (model type, result type, data characteristic) of record 9.
RESULTS = {
    "displacement": (1, 8, 2),
    "stress": (1, 2, 4),
    "strain": (1, 3, 4),
    "reaction_force": (1, 9, 2),
    "velocity": (1, 11, 2),
    "acceleration": (1, 12, 2),
    "temperature": (2, 5, 1),
    "heat_flux": (2, 6, 2),
}
UNKNOWN = {1: (94, 1), 3: (95, 2), 6: (97, 4)}  # components -> result type, characteristic
TENSOR = [0, 3, 1, 5, 4, 2]  # xx, yy, zz, xy, yz, zx -> I-DEAS's xx, xy, yy, xz, yz, zz
ANALYSIS = {"stationary": 1, "modal": 2, "transient": 4, "frequency_domain": 5, "buckling": 6}
DELIMITER = f"{-1:6d}"


def write(job):
    title = f"{job.record.study_name}, run {job.record.run_id} ({job.record.solver})"
    return write_unv(job.mesh, job.results, job.path, title)


def write_unv(mesh, results, path: Path, title: str = "Puzzle results"):
    """The mesh, groups and every step's results as one universal file; returns
    ([path], notes)."""
    found = blocks(mesh)
    if any(b.element == "pyramid5" for b in found):
        raise PuzzleError("UNV has no pyramid element; this mesh has some", code="unsupported")
    out: list[str] = []
    _units(out)
    _nodes(out, mesh)
    parts = list(dict.fromkeys(b.part for b in found))
    _elements(out, mesh, found, parts)
    names = _groups(out, mesh, found)
    written, skipped = _results(out, mesh, found, results, title)
    path.write_text("\n".join(out) + "\n")
    notes = {"units": "SI (dataset 164: m, N, K)", "groups": str(len(names)),
             "result datasets": str(written)}  # fmt: skip
    cut = [n for n in names if len(n) > 40]
    if cut:
        notes["names cut to 40 characters"] = ", ".join(cut)
    if skipped:
        notes["not written"] = ", ".join(sorted(skipped))
    return [path], notes


def _d(values) -> str:
    """Numbers as Fortran 1P3D25.16 (D exponent)."""
    return "".join(f"{v:25.16E}".replace("E", "D") for v in values)


def _e(values) -> list[str]:
    """Numbers as 6E13.5, six to a line; values too small for two exponent digits are 0."""
    v = np.where(np.abs(values) < 1e-99, 0.0, values)
    text = [f"{x:13.5E}" for x in v]
    return ["".join(text[i : i + 6]) for i in range(0, len(text), 6)]


def _ints(values, per_line: int = 8) -> list[str]:
    text = [f"{int(x):10d}" for x in values]
    return ["".join(text[i : i + per_line]) for i in range(0, len(text), per_line)]


def _units(out) -> None:
    out += [DELIMITER, f"{164:6d}", f"{1:10d}{'SI: Meter (newton)':<20s}{1:10d}",
            _d([1.0, 1.0, 1.0]), _d([273.15]), DELIMITER]  # fmt: skip


def _nodes(out, mesh) -> None:
    out += [DELIMITER, f"{2411:6d}"]
    for n, xyz in zip(mesh.node_ids, mesh.coords, strict=True):
        out.append(f"{int(n):10d}{1:10d}{1:10d}{11:10d}")
        out.append(_d(xyz))
    out.append(DELIMITER)


def _descriptor(mesh, b) -> int:
    solid, shell, plane, axi = DESCRIPTORS[b.element][0]
    if b.kind == "region":
        return axi if mesh.axisymmetric else plane
    return shell if b.kind == "shell" else solid


def _elements(out, mesh, found, parts) -> None:
    out += [DELIMITER, f"{2412:6d}"]
    for b in found:
        code, order = _descriptor(mesh, b), DESCRIPTORS[b.element][1]
        table = parts.index(b.part) + 1
        conn = b.connectivity[:, order] if order else b.connectivity
        for e, nodes in zip(b.ids, conn, strict=True):
            out.append(f"{int(e):10d}{code:10d}{table:10d}{table:10d}{7:10d}{len(nodes):10d}")
            if code in BEAMS:
                out.append(f"{0:10d}{0:10d}{0:10d}")
            out += _ints(nodes)
    out.append(DELIMITER)


def _groups(out, mesh, found) -> list[str]:
    """Every part and selection as a permanent group: its elements (the written ones on
    its entities) and its nodes."""
    by_entity: dict = {}
    for b in mesh.blocks:
        by_entity.setdefault(b.entity, set()).update(int(i) for i in b.ids)
    written = {int(i) for b in found for i in b.ids}
    out += [DELIMITER, f"{2467:6d}"]
    names = []
    for number, (name, g) in enumerate(mesh.groups.items(), 1):
        elements = sorted(set().union(*(by_entity.get(e, set()) for e in g.entities)) & written)
        entities = [(8, e) for e in elements] + [(7, int(n)) for n in g.node_ids]
        out.append("".join(f"{v:10d}" for v in [number, 0, 0, 0, 0, 0, 0, len(entities)]))
        out.append(name[:40])
        flat = [v for kind, label in entities for v in (kind, label, 0, 0)]
        out += _ints(flat)
        names.append(name)
    out.append(DELIMITER)
    return names


def _results(out, mesh, found, results, title) -> tuple[int, set[str]]:
    label, skipped = 0, set()
    stamp = f"{datetime.now():%d-%b-%y %H:%M:%S}"
    counters: dict[int, int] = {}
    element_ids = np.concatenate([b.ids for b in found])
    for step in results.steps:
        analysis = ANALYSIS.get(step.kind, 0)
        counters[step.index] = counters.get(step.index, 0) + 1
        for f in step.fields.values():
            if f.location not in ("node", "element"):
                skipped.add(f"{f.name} (a global value)")
                continue
            kind = _kind(f)
            if kind is None:
                skipped.add(f"{f.name} ({len(f.components)} components)")
                continue
            model, result, characteristic = kind
            label += 1
            width = len(f.components)
            ids = mesh.node_ids if f.location == "node" else element_ids
            values = values_at(f, np.asarray(ids), width)
            if width == 6:
                values = values[:, TENSOR]
            keep = ~np.isnan(values).any(axis=1)
            name = f"{f.name}: {step.label()}"
            out += [DELIMITER, f"{2414:6d}", f"{label:10d}", name[:80],
                    f"{1 if f.location == 'node' else 2:10d}",
                    f.name[:80], (f.name if result >= 93 else title)[:80] or "NONE",
                    stamp, step.label()[:80], "NONE",
                    "".join(f"{v:10d}" for v in [model, analysis, characteristic, result, 2,
                                                  width])]  # fmt: skip
            out += _ints(_integers(step, counters[step.index]))
            out.append(f"{0:10d}{0:10d}")
            out += _e(np.array(_reals(step)))
            for i, row in zip(np.asarray(ids)[keep], values[keep], strict=True):
                out.append(f"{int(i):10d}" if f.location == "node" else f"{int(i):10d}{width:10d}")
                out += _e(row)
            out.append(DELIMITER)
    return label, skipped


def _kind(f):
    if f.name in RESULTS and len(f.components) == {1: 1, 2: 3, 4: 6}[RESULTS[f.name][2]]:
        return RESULTS[f.name]
    if len(f.components) in UNKNOWN:
        result, characteristic = UNKNOWN[len(f.components)]
        model = 2 if f.name in ("temperature", "heat_flux") else 1
        return model, result, characteristic
    return None


def _integers(step, count: int) -> list[int]:
    """Record 10: design set, iteration, solution set, boundary condition, load set,
    mode number, time step number, frequency number."""
    mode = step.mode or 0
    time_step = count if step.kind == "transient" else 0
    frequency = count if step.kind == "frequency_domain" else 0
    load = 0 if step.kind == "modal" else step.index
    return [1, 0, 1, 0, load, mode, time_step, frequency]


def _reals(step) -> list[float]:
    """Records 12 and 13: time, frequency, eigenvalue, modal mass, viscous and hysteretic
    damping; then the complex eigenvalue and modal A/B parts (zero here). Modal mass and
    damping are not stored by Puzzle (0)."""
    value = float(step.value)
    time = value if step.kind == "transient" else 0.0
    frequency = value if step.kind in ("modal", "frequency_domain") else 0.0
    eigenvalue = value if step.kind == "buckling" else 0.0
    return [time, frequency, eigenvalue, 0.0, 0.0, 0.0] + [0.0] * 6
