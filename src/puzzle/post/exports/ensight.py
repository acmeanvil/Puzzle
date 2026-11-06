"""EnSight Gold (ADR 0043, fluid): the long-standing CFD exchange format, read by
EnSight, ParaView and VisIt, and written by Code_Saturne, Fluent and STAR-CCM+.

Written as "C Binary" (little-endian float32 values and coordinates, int32 counts:
about 7 significant digits) with a `.case` file, one geometry file (the mesh doesn't
move) and one file per variable per step. Every step is a time step of one time set
(a variable missing from some steps gets a time set of its own). Parts: 1 is the
volume; then each face selection is a part named after it (`Part/Selection`), so the
boundary names survive. Node and element ids are assigned by position (EnSight's
"assign"), not Puzzle's.

Variables: one per field and location, a scalar, a vector, or a symmetric tensor
(EnSight's 11 22 33 12 13 23 order), else one scalar per component (`<field>_<c>`).
On a boundary part an element variable is the adjacent volume cell's value (CFD
results are per cell; a face has none of its own). Units are SI, said in the
geometry file's second description line.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from puzzle.post.exports import fluid

# Puzzle element -> (EnSight element, node order from Gmsh's or None).
ELEMENTS = {
    "tet4": ("tetra4", None),
    "tet10": ("tetra10", [0, 1, 2, 3, 4, 5, 6, 7, 9, 8]),
    "hex8": ("hexa8", None),
    "wedge6": ("penta6", None),
    "pyramid5": ("pyramid5", None),
    "tri3": ("tria3", None),
    "tri6": ("tria6", None),
    "quad4": ("quad4", None),
    "quad8": ("quad8", None),
}
TENSOR = ["xx", "yy", "zz", "xy", "yz", "zx"]
TENSOR_ORDER = [0, 1, 2, 3, 5, 4]  # ours -> EnSight's 11 22 33 12 13 23


def write(job):
    files, notes = write_ensight(job.mesh, job.results, job.path, fluid.bc_types(job))
    return files, notes


def _text(s: str) -> bytes:
    return s.encode("ascii", "replace")[:79].ljust(80, b"\0")


def _ints(a) -> bytes:
    return np.asarray(a, dtype="<i4").tobytes()


def _floats(a) -> bytes:
    return np.asarray(a, dtype="<f4").tobytes()


class _Part:
    def __init__(self, mesh, name: str, blocks, cells: np.ndarray | None):
        for b in blocks:
            if b.element not in ELEMENTS:
                raise _unsupported(b.element)
        self.name, self.blocks = name, blocks
        self.nodes = np.unique(np.concatenate([b.connectivity.ravel() for b in blocks]))
        self.cells = cells  # the volume cell behind each element (boundaries), or None

    def geometry(self, mesh, number: int) -> bytes:
        out = [_text("part"), _ints([number]), _text(self.name), _text("coordinates")]
        xyz = mesh.coords[fluid.node_index(mesh, self.nodes)]
        out += [_ints([len(self.nodes)]), *(_floats(xyz[:, c]) for c in range(3))]
        for kind, conn in self.by_type():
            local = np.searchsorted(self.nodes, conn) + 1
            out += [_text(kind), _ints([len(conn)]), _ints(local.ravel())]
        return b"".join(out)

    def by_type(self):
        """(EnSight element, connectivity in its order) per element type, in a stable
        order; elements of one type are written together, as EnSight wants."""
        groups: dict[str, list] = {}
        for b in self.blocks:
            kind, order = ELEMENTS[b.element]
            groups.setdefault(kind, []).append(
                b.connectivity[:, order] if order else b.connectivity
            )
        return [(k, np.vstack(v)) for k, v in groups.items()]

    def element_order(self, offsets: list[int]) -> np.ndarray:
        """Positions, in the part's element numbering, of its elements grouped by type."""
        kinds = [ELEMENTS[b.element][0] for b in self.blocks]
        return np.concatenate(
            [np.arange(offsets[i], offsets[i] + len(b.ids))
             for k in dict.fromkeys(kinds) for i, b in enumerate(self.blocks) if kinds[i] == k]
        )  # fmt: skip


def _unsupported(element: str):
    from puzzle.errors import PuzzleError

    return PuzzleError(f"{element} elements can't be exported to EnSight yet", code="unsupported")


def _variables(results):
    """(field name, location) -> (components, [step positions])."""
    out: dict[tuple[str, str], tuple[list[str], list[int]]] = {}
    for i, s in enumerate(results.steps):
        for f in s.fields.values():
            if f.location not in ("node", "element"):
                continue
            comps, steps = out.setdefault((f.name, f.location), (list(f.components), []))
            steps.append(i)
    return out


def _split(name: str, components: list[str]):
    """EnSight variables of a field: (variable name, kind, column indices)."""
    n = len(components)
    if n == 1:
        return [(name, "scalar", [0])]
    if n == 3:
        return [(name, "vector", [0, 1, 2])]
    if components == TENSOR:
        return [(name, "tensor symm", TENSOR_ORDER)]
    return [(f"{name}_{c}", "scalar", [k]) for k, c in enumerate(components)]


def write_ensight(mesh, results, path: Path, bcs: dict[str, str] | None = None):
    vol = fluid.volume(mesh)
    faces = fluid.cell_faces(vol)
    parts = [_Part(mesh, "volume", vol.blocks, None)]
    for name, blocks in fluid.boundaries(mesh).items():
        parts.append(_Part(mesh, name, blocks, fluid.adjacent(faces, blocks)))
    stem = path.with_suffix("")
    base = stem.name
    geo = stem.with_suffix(".geo")
    lo, hi = mesh.coords.min(0), mesh.coords.max(0)
    header = [_text("C Binary"), _text(f"Puzzle export: {results.solver} results"),
              _text(fluid.UNITS_NOTE), _text("node id assign"), _text("element id assign"),
              _text("extents"), _floats(np.column_stack([lo, hi]).ravel())]  # fmt: skip
    geo.write_bytes(b"".join(header + [p.geometry(mesh, i + 1) for i, p in enumerate(parts)]))
    files = [geo]

    times, renumbered = fluid.time_values(results)
    digits = max(4, len(str(len(results.steps) - 1)))
    sets: dict[tuple[int, ...], int] = {}
    lines = []
    variables = _variables(results)
    for (name, location), (components, steps) in variables.items():
        key = tuple(steps)
        ts = sets.setdefault(key, len(sets) + 1)
        for var, kind, columns in _split(name, components):
            both = location == "element" and (name, "node") in variables
            label = f"{var}_cells" if both else var
            pattern = f"{base}.{label}.{'*' * digits}"
            where = "node" if location == "node" else "element"
            lines.append(f"{kind} per {where}: {ts} {label} {pattern}")
            for pos in steps:
                field = next(f for f in results.steps[pos].fields.values()
                             if f.name == name and f.location == location)  # fmt: skip
                out = stem.parent / f"{base}.{label}.{pos:0{digits}d}"
                out.write_bytes(_variable(mesh, vol, parts, field, columns, f"{var} [SI]"))
                files.append(out)
    case = stem.with_suffix(".case")
    text = ["FORMAT", "type: ensight gold", "", "GEOMETRY", f"model: {geo.name}", ""]
    if lines:
        text += ["VARIABLE", *lines, "", "TIME"]
    for key, ts in sets.items():
        text += [
            f"time set: {ts}",
            f"number of steps: {len(key)}",
            "filename numbers: " + " ".join(str(k) for k in key),
            "time values: " + " ".join(f"{times[k]:.9g}" for k in key),
        ]
    case.write_text("\n".join(text) + "\n", encoding="utf-8")
    notes = {
        "parts": "volume, " + ", ".join(p.name for p in parts[1:]) if len(parts) > 1 else "volume",
        "steps": str(len(results.steps)),
        "values": "float32 (about 7 digits); SI units",
    }
    if any(p.cells is not None for p in parts):
        notes["boundary parts"] = "element variables there are the adjacent cell's values"
    if renumbered:
        notes["time values"] = "the step count (the steps' own values don't increase)"
    return [case, *files], notes


def _variable(mesh, vol, parts, field, columns, description: str) -> bytes:
    out = [_text(description)]
    if field.location == "node":
        for i, p in enumerate(parts):
            values = fluid.lookup(field, p.nodes)[:, columns]
            out += [_text("part"), _ints([i + 1]), _text("coordinates")]
            out += [_floats(values[:, c]) for c in range(len(columns))]
        return b"".join(out)
    cell_values = fluid.lookup(field, vol.ids)
    for i, p in enumerate(parts):
        out += [_text("part"), _ints([i + 1])]
        offsets = np.cumsum([0] + [len(b.ids) for b in p.blocks])[:-1].tolist()
        values = cell_values if p.cells is None else _behind(cell_values, p.cells)
        values = values[p.element_order(offsets)][:, columns]
        start = 0
        for kind, conn in p.by_type():
            chunk = values[start : start + len(conn)]
            out += [_text(kind)] + [_floats(chunk[:, c]) for c in range(len(columns))]
            start += len(conn)
    return b"".join(out)


def _behind(cell_values: np.ndarray, cells: np.ndarray) -> np.ndarray:
    out = np.full((len(cells), cell_values.shape[1]), np.nan)
    out[cells >= 0] = cell_values[cells[cells >= 0]]
    return out
