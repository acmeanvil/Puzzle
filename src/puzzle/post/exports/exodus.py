"""Exodus II (ADR 0043 step 3): the mesh, every step as a time step, nodal, element and
global variables, one element block per part, and the selections as node sets and
(face selections) side sets named after them, so a receiver's loads land on them.

Written by us with SciPy's netCDF-3 writer (64-bit offset), following the Exodus II
format as Sandia describes it ("EXODUS II: A Finite Element Data Model", SAND92-2137,
and its netCDF mapping in the SEACAS documentation), the same layout VTK's IOSS writer
produces. We write it ourselves because VTK's writers can't carry our numbers and
units: `vtkIOSSWriter` renumbers elements 1..n and has no place for info records,
`vtkExodusIIWriter` names no sets. Coordinates and values are SI (m, Pa, K, ...), as the
info records say. Element numbers (`elem_num_map`) and node numbers (`node_num_map`)
are Puzzle's own; side numbers follow Exodus's own face numbering per element type.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from puzzle.post.exports.series import (
    Kind,
    block_names,
    blocks,
    component_names,
    fields,
    step_times,
    values_at,
)

# Gmsh element -> (Exodus type for a solid/region, for a shell, node order from Gmsh).
# Exodus orders a TETRA10's edges 1-2, 2-3, 3-1, 1-4, 2-4, 3-4; Gmsh ends with 3-4, 2-4.
TYPES = {
    "tet4": ("TETRA4", None, None),
    "tet10": ("TETRA10", None, [0, 1, 2, 3, 4, 5, 6, 7, 9, 8]),
    "hex8": ("HEX8", None, None),
    "wedge6": ("WEDGE6", None, None),
    "pyramid5": ("PYRAMID5", None, None),
    "tri3": ("TRI3", "TRISHELL3", None),
    "tri6": ("TRI6", "TRISHELL6", None),
    "quad4": ("QUAD4", "SHELL4", None),
    "quad8": ("QUAD8", "SHELL8", None),
    "line2": ("BAR2", None, None),
    "line3": ("BAR3", None, None),
}

# Exodus side numbering (1-based in the file), as corner positions in Exodus/Gmsh order.
SIDES = {
    "tet": [(0, 1, 3), (1, 2, 3), (0, 3, 2), (0, 2, 1)],
    "hex": [(0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (0, 4, 7, 3), (0, 3, 2, 1), (4, 5, 6, 7)],
    "wedge": [(0, 1, 4, 3), (1, 2, 5, 4), (0, 3, 5, 2), (0, 2, 1), (3, 4, 5)],
    "pyramid": [(0, 1, 4), (1, 2, 4), (2, 3, 4), (3, 0, 4), (0, 3, 2, 1)],
    "tri": [(0, 1), (1, 2), (2, 0)],  # a 2D model's faces: their sides are edges
    "quad": [(0, 1), (1, 2), (2, 3), (3, 0)],
}
FAMILY = {"tet4": "tet", "tet10": "tet", "hex8": "hex", "wedge6": "wedge",
          "pyramid5": "pyramid", "tri3": "tri", "tri6": "tri", "quad4": "quad",
          "quad8": "quad"}  # fmt: skip
CORNERS = {"tri3": 3, "tri6": 3, "quad4": 4, "quad8": 4, "line2": 2, "line3": 2}

UNITS = "SI units: m, kg, s, N, Pa, K, W (stress in Pa, temperature in K)"


def write(job):
    title = f"Puzzle: {job.record.study_name}, run {job.record.run_id} ({job.record.solver})"
    return write_exodus(job.mesh, job.results, job.path, title)


def write_exodus(mesh, results, path: Path, title: str = "Puzzle results"):
    """Write `results` on `mesh` as one Exodus II file; returns ([path], notes)."""
    from scipy.io import netcdf_file

    from puzzle import __version__

    found = blocks(mesh)
    names = block_names(found)
    steps = results.steps
    times, what = step_times(steps)
    kinds = fields(steps)
    planar = mesh.planar
    dim = 2 if planar else 3
    node_ids = np.asarray(mesh.node_ids, np.int64)
    by_id = np.argsort(node_ids)

    def index(ids):  # node ids -> 1-based positions in the file
        return by_id[np.searchsorted(node_ids, ids, sorter=by_id)] + 1

    selections = {n: g for n, g in mesh.groups.items() if "/" in n}
    node_sets = [(n, index(g.node_ids)) for n, g in selections.items() if len(g.node_ids)]
    side_sets, skipped = _side_sets(mesh, found, selections)
    notes = {"time values": what, "units": UNITS, "node sets": str(len(node_sets)),
             "side sets": str(len(side_sets))}  # fmt: skip
    if skipped:
        notes["no side set"] = ", ".join(skipped) + " (no solid element faces: node sets only)"
    if what.startswith("step number"):
        notes["steps"] = "; ".join(f"{i + 1}: {s.label()}" for i, s in enumerate(steps))

    longest = max([len(n) for n in [*names, *selections]] + [32])
    f = netcdf_file(str(path), "w", version=2)
    try:
        f.title = title[:80].encode()
        f.api_version = np.float32(8.03)
        f.version = np.float32(8.03)
        f.floating_point_word_size = np.int32(8)
        f.file_size = np.int32(1)
        f.maximum_name_length = np.int32(longest)
        f.int64_status = np.int32(0)
        for name, size in [("time_step", None), ("len_string", 33), ("len_line", 81),
                           ("four", 4), ("len_name", longest + 1), ("num_dim", dim),
                           ("num_nodes", len(node_ids)),
                           ("num_elem", sum(len(b.ids) for b in found)),
                           ("num_el_blk", len(found))]:  # fmt: skip
            f.createDimension(name, size)
        stamp = datetime.now(UTC)
        _text(f, "qa_records", ("num_qa_rec", "four", "len_string"),
              [["Puzzle", __version__, f"{stamp:%Y/%m/%d}", f"{stamp:%H:%M:%S}"]])  # fmt: skip
        info = [title, UNITS, f"time values: {what}"]
        info += [f"step {i + 1}: {s.label()}" for i, s in enumerate(steps)]
        _text(f, "info_records", ("num_info", "len_line"), info)
        f.createVariable("time_whole", "d", ("time_step",))[:] = np.array(times)
        for k, axis in enumerate("xyz"[:dim]):
            f.createVariable(f"coord{axis}", "d", ("num_nodes",))[:] = mesh.coords[:, k]
        _text(f, "coor_names", ("num_dim", "len_name"), list("xyz"[:dim]))
        f.createVariable("node_num_map", "i", ("num_nodes",))[:] = node_ids.astype(np.int32)
        f.createVariable("elem_num_map", "i", ("num_elem",))[:] = np.concatenate(
            [b.ids for b in found]
        ).astype(np.int32)
        f.createVariable("eb_status", "i", ("num_el_blk",))[:] = 1
        prop = f.createVariable("eb_prop1", "i", ("num_el_blk",))
        prop[:] = np.arange(1, len(found) + 1)
        prop.name = b"ID"
        _text(f, "eb_names", ("num_el_blk", "len_name"), names)
        for i, b in enumerate(found, 1):
            solid, shell, order = TYPES[b.element]
            conn = b.connectivity[:, order] if order else b.connectivity
            f.createDimension(f"num_el_in_blk{i}", len(b.ids))
            f.createDimension(f"num_nod_per_el{i}", conn.shape[1])
            var = f.createVariable(f"connect{i}", "i", (f"num_el_in_blk{i}", f"num_nod_per_el{i}"))
            var[:] = index(conn.ravel()).reshape(conn.shape).astype(np.int32)
            var.elem_type = (shell if b.kind == "shell" and shell else solid).encode()
        _sets(f, "ns", "num_node_sets", "num_nod_ns", node_sets)
        if side_sets:
            f.createDimension("num_side_sets", len(side_sets))
            f.createVariable("ss_status", "i", ("num_side_sets",))[:] = 1
            prop = f.createVariable("ss_prop1", "i", ("num_side_sets",))
            prop[:] = np.arange(1, len(side_sets) + 1)
            prop.name = b"ID"
            _text(f, "ss_names", ("num_side_sets", "len_name"), [n for n, _, _ in side_sets])
            for i, (_, elems, sides) in enumerate(side_sets, 1):
                f.createDimension(f"num_side_ss{i}", len(elems))
                f.createVariable(f"elem_ss{i}", "i", (f"num_side_ss{i}",))[:] = elems
                f.createVariable(f"side_ss{i}", "i", (f"num_side_ss{i}",))[:] = sides
        _variables(f, steps, kinds, node_ids, found)
    finally:
        f.close()
    return [path], notes


def _variables(f, steps, kinds: list[Kind], node_ids, found) -> None:
    """Nodal, element and global variables at every step (NaN where a step lacks one)."""
    nodal = [k for k in kinds if k.location == "node"]
    if nodal:
        names = [n for k in nodal for n in component_names(k)]
        f.createDimension("num_nod_var", len(names))
        _text(f, "name_nod_var", ("num_nod_var", "len_name"), names)
        v = 0
        for k in nodal:
            data = np.stack(
                [values_at(s.fields.get(k.name), node_ids, len(k.components)) for s in steps]
            )
            for c in range(len(k.components)):
                v += 1
                var = f.createVariable(f"vals_nod_var{v}", "d", ("time_step", "num_nodes"))
                var[:] = data[:, :, c]
    cells = [k for k in kinds if k.location == "element"]
    if cells:
        names = [n for k in cells for n in component_names(k)]
        f.createDimension("num_elem_var", len(names))
        _text(f, "name_elem_var", ("num_elem_var", "len_name"), names)
        table = f.createVariable("elem_var_tab", "i", ("num_el_blk", "num_elem_var"))
        table[:] = np.ones((len(found), len(names)), np.int32)
        for i, b in enumerate(found, 1):
            v = 0
            for k in cells:
                data = np.stack(
                    [values_at(_cell_field(s, k), b.ids, len(k.components)) for s in steps]
                )
                for c in range(len(k.components)):
                    v += 1
                    dims = ("time_step", f"num_el_in_blk{i}")
                    f.createVariable(f"vals_elem_var{v}eb{i}", "d", dims)[:] = data[:, :, c]
    rows = _global_rows(steps, [k for k in kinds if k.location == "global"])
    if rows:
        f.createDimension("num_glo_var", len(rows))
        _text(f, "name_glo_var", ("num_glo_var", "len_name"), [r[0] for r in rows])
        data = np.array([[r[1](s) for r in rows] for s in steps])
        f.createVariable("vals_glo_var", "d", ("time_step", "num_glo_var"))[:] = data


def _cell_field(step, kind: Kind):
    field = step.fields.get(kind.name)
    return field if field is not None and field.location == "element" else None


def _global_rows(steps, kinds: list[Kind]):
    """One global variable per row and component of a global field: (name, value at a
    step). A field with several rows (one per bolt, say) names each by its id."""
    rows = []
    for k in kinds:
        ids = sorted({int(i) for s in steps if k.name in s.fields for i in s.fields[k.name].ids})
        for i in ids:
            for c, comp in enumerate(k.components):
                name = k.name + (f"_{i}" if len(ids) > 1 else "") + (f"_{comp}" if comp else "")
                rows.append((name, _global_value(k.name, i, c)))
    return rows


def _global_value(name, row, c):
    def value(step):
        field = step.fields.get(name)
        if field is None:
            return np.nan
        hit = np.flatnonzero(np.asarray(field.ids) == row)
        return float(np.asarray(field.values)[hit[0], c]) if len(hit) else np.nan

    return value


def _side_sets(mesh, found, selections):
    """Face selections (a 2D model's edge selections) as (name, element numbers, sides):
    each face element is matched by its corners to a side of a solid element, of the
    selection's own part where two share the face. Selections with no such faces (on
    shells, say) are listed as skipped."""
    face_dim = 1 if mesh.planar else 2
    solids = [b for b in found if b.kind in ("solid", "region") and b.element in FAMILY]
    if not solids:
        return [], [n for n, g in selections.items() if g.dim == face_dim]
    numbers = np.cumsum([0] + [len(b.ids) for b in found])
    starts = {id(b): numbers[k] for k, b in enumerate(found)}
    tables = {}  # part -> (its sides' keys sorted, their positions); "" = every part
    keys, elems, sides, parts = [], [], [], []
    for b in solids:
        for s, corners in enumerate(SIDES[FAMILY[b.element]], 1):
            keys.append(_key(b.connectivity[:, list(corners)]))
            elems.append(starts[id(b)] + 1 + np.arange(len(b.ids)))
            sides.append(np.full(len(b.ids), s))
            parts.append(np.full(len(b.ids), b.part, dtype=object))
    keys, elems, sides, parts = (np.concatenate(a) for a in (keys, elems, sides, parts))
    out, skipped = [], []
    for name, group in selections.items():
        if group.dim != face_dim:
            continue
        faces = [b for b in group.blocks(mesh) if b.dim == face_dim and b.element in CORNERS]
        if not faces:
            skipped.append(name)
            continue
        query = np.concatenate([_key(b.connectivity[:, : CORNERS[b.element]]) for b in faces])
        part = name.split("/", 1)[0]
        hit = _match(tables, part, keys, parts, query)
        if hit is None:
            hit = _match(tables, "", keys, parts, query)
        if hit is None:
            skipped.append(name)
            continue
        out.append((name, elems[hit].astype(np.int32), sides[hit].astype(np.int32)))
    return out, skipped


def _key(corners: np.ndarray) -> np.ndarray:
    """Faces' corner node ids as sortable keys (sorted, padded to four)."""
    k = np.sort(np.asarray(corners, np.int64), axis=1)
    k = np.ascontiguousarray(np.hstack([k, np.full((len(k), 4 - k.shape[1]), -1, np.int64)]))
    return k.view([("a", np.int64), ("b", np.int64), ("c", np.int64), ("d", np.int64)]).ravel()


def _match(tables, part, keys, parts, query):
    """Positions in `keys` of each query face (within `part`, "" any), or None if any
    face has no match."""
    if part not in tables:
        rows = np.flatnonzero(parts == part) if part else np.arange(len(keys))
        order = rows[np.argsort(keys[rows], kind="stable")]
        tables[part] = (keys[order], order)
    sorted_keys, order = tables[part]
    if not len(sorted_keys):
        return None
    pos = np.clip(np.searchsorted(sorted_keys, query), 0, len(sorted_keys) - 1)
    if not np.all(sorted_keys[pos] == query):
        return None
    return order[pos]


def _sets(f, short, count, size, sets) -> None:
    if not sets:
        return
    f.createDimension(count, len(sets))
    f.createVariable(f"{short}_status", "i", (count,))[:] = 1
    prop = f.createVariable(f"{short}_prop1", "i", (count,))
    prop[:] = np.arange(1, len(sets) + 1)
    prop.name = b"ID"
    _text(f, f"{short}_names", (count, "len_name"), [n for n, _ in sets])
    for i, (_, members) in enumerate(sets, 1):
        f.createDimension(f"{size}{i}", len(members))
        f.createVariable(f"node_{short}{i}", "i", (f"{size}{i}",))[:] = members.astype(np.int32)


def _text(f, name, dims, rows) -> None:
    """A netCDF char variable: one (padded) string per row, or rows of strings."""
    if dims[0] not in f.dimensions:
        f.createDimension(dims[0], len(rows))
    width = f.dimensions[dims[-1]]
    shape = (len(rows), *[f.dimensions[d] for d in dims[1:]])
    data = np.zeros(shape, "S1")
    flat = data.reshape(-1, width)
    strings = [s for r in rows for s in (r if isinstance(r, list) else [r])]
    for k, s in enumerate(strings):
        raw = s.encode("utf-8")[: width - 1]
        flat[k, : len(raw)] = np.frombuffer(raw, "S1")
    f.createVariable(name, "c", dims)[:] = data
