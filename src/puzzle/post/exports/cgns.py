"""CGNS (ADR 0043, fluid): the CFD standard for meshes and flow solutions, read by
Fluent, CFX, STAR-CCM+, SU2, ParaView and VisIt.

Written with h5py following the CGNS SIDS and its HDF5 file mapping (every node a group
with `name`, `label`, `type` and `flags` attributes and its value in a ` data` dataset,
children in creation order), CGNS 4.4. One base (cell and physical dimension 3), one
unstructured zone:

- `GridCoordinates`: CoordinateX/Y/Z in metres (float64);
- element sections: the volume cells first (numbered 1..NCell, as cell-centred data
  wants), one section per element type, then each face selection's faces as a section
  named after the selection;
- `ZoneBC`: one `BC_t` per face selection, its `PointList` the section's faces
  (GridLocation FaceCenter), FamilySpecified, its family (a `Family_t` in the base,
  same name) holding the BC type from the selection's fluid feature in `FamilyBC`
  (BCWall, BCInflow, BCOutflow, BCSymmetryPlane; BCTypeUserDefined otherwise): VTK's
  reader takes FamilySpecified and BCWall patches only. CGNS names are at most 32
  characters and can't hold "/", so `Part/Selection` is written `Part_Selection`
  (shortened, and numbered if two collide); the exact name is in a Descriptor_t child
  `PuzzleSelection`;
- every step as a `FlowSolution_t` of the cell-centred fields and one of the nodal
  fields (GridLocation Vertex), listed in `BaseIterativeData` (TimeValues) and, with
  more than one step, the zone's `ZoneIterativeData`: `FlowSolutionPointers` the cell
  solutions, and `FlowSolutionVertexPointers` (not in the SIDS, which allows one list;
  VTK's reader picks each step's vertex solution with it) the nodal ones;
- SIDS names where there are some (VelocityX, Pressure, Temperature,
  TurbulentEnergyKinetic, TurbulentDissipation, TurbulentDissipationRate for omega,
  ViscosityEddy), else the field's name with its component (`StressXX`);
- `DataClass` Dimensional with `DimensionalUnits` Kilogram, Meter, Second, Kelvin,
  Degree: everything SI, float64.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from puzzle.errors import PuzzleError
from puzzle.post.exports import fluid

# Puzzle element -> (CGNS ElementType_t, node order from Gmsh's or None).
ELEMENTS = {
    "tet4": (10, None),
    "tet10": (11, [0, 1, 2, 3, 4, 5, 6, 7, 9, 8]),
    "pyramid5": (12, None),
    "wedge6": (14, None),
    "hex8": (17, None),
    "tri3": (5, None),
    "tri6": (6, None),
    "quad4": (7, None),
    "quad8": (8, None),
}
TYPE_NAMES = {10: "TETRA_4", 11: "TETRA_10", 12: "PYRA_5", 14: "PENTA_6", 17: "HEXA_8",
              5: "TRI_3", 6: "TRI_6", 7: "QUAD_4", 8: "QUAD_8"}  # fmt: skip
NAMES = {
    "velocity": "Velocity",
    "pressure": "Pressure",
    "temperature": "Temperature",
    "turbulent_kinetic_energy": "TurbulentEnergyKinetic",
    "turbulent_dissipation": "TurbulentDissipation",
    "specific_dissipation": "TurbulentDissipationRate",
    "turbulent_viscosity": "ViscosityEddy",
    "density": "Density",
}


def write(job):
    return write_cgns(job.mesh, job.results, job.path, fluid.bc_types(job))


def _camel(name: str) -> str:
    return NAMES.get(name) or "".join(w[:1].upper() + w[1:] for w in name.split("_"))


def array_names(name: str, components: list[str]) -> list[str]:
    """The DataArray_t names of a field's components."""
    base = _camel(name)
    if components == [""]:
        return [base]
    return [base + c.upper() for c in components]


class _Writer:
    def __init__(self, h5):
        import h5py

        self.h5py = h5py
        self.file = h5

    def _attr(self, obj, key: str, text: str, size: int) -> None:
        h5t, h5a, h5s = self.h5py.h5t, self.h5py.h5a, self.h5py.h5s
        tid = h5t.C_S1.copy()
        tid.set_size(size)
        tid.set_strpad(h5t.STR_NULLTERM)
        attr = h5a.create(obj.id, key.encode(), tid, h5s.create(h5s.SCALAR))
        attr.write(np.array(text.encode("ascii"), dtype=f"S{size}"), mtype=tid)

    def node(self, parent, name: str, label: str, data=None, kind: str | None = None):
        if len(name) > 32 or "/" in name:
            raise ValueError(f"not a CGNS node name: {name!r}")
        g = parent.create_group(name, track_order=True)
        if data is None:
            kind = "MT"
        elif isinstance(data, str):
            kind = "C1"
            data = np.frombuffer(data.encode("ascii"), dtype=np.int8)
        elif kind is None:
            data = np.asarray(data)
            kind = {"i4": "I4", "i8": "I8", "f4": "R4", "f8": "R8", "i1": "C1"}[data.dtype.str[1:]]
        self._attr(g, "name", name, 33)
        self._attr(g, "label", label, 33)
        self._attr(g, "type", kind, 3)
        g.attrs.create("flags", np.array([1], dtype=np.int32))
        if data is not None:
            g.create_dataset(" data", data=data)
        return g


def _names(selections: list[str]) -> dict[str, str]:
    """CGNS names (32 characters, no '/') for our selections, unique."""
    out, used = {}, set()
    for s in selections:
        base = s.replace("/", "_")[:32]
        name, k = base, 1
        while name in used:
            k += 1
            tail = f"_{k}"
            name = base[: 32 - len(tail)] + tail
        used.add(name)
        out[s] = name
    return out


def write_cgns(mesh, results, path: Path, bcs: dict[str, str] | None = None):
    import h5py

    bcs = bcs or {}
    vol = fluid.volume(mesh)
    bounds = fluid.boundaries(mesh)
    for b in vol.blocks + [b for blocks in bounds.values() for b in blocks]:
        if b.element not in ELEMENTS:
            raise PuzzleError(f"{b.element} elements can't be exported to CGNS yet",
                              code="unsupported")  # fmt: skip
    # Only nodes the elements use (a fluid mesh's nodes all are), numbered from 1.
    used = np.unique(np.concatenate([b.connectivity.ravel() for b in vol.blocks]))
    index = fluid.node_index(mesh, used)
    steps = results.steps
    times, renumbered = fluid.time_values(results)
    with h5py.File(path, "w", track_order=True) as h5:
        w = _Writer(h5)
        w._attr(h5, "name", "HDF5 MotherNode", 33)
        w._attr(h5, "label", "Root Node of HDF5 File", 33)
        w._attr(h5, "type", "MT", 3)
        h5.create_dataset(" format", data=np.frombuffer(b"IEEE_LITTLE_32\0", dtype=np.int8))
        version = f"HDF5 Version {h5py.version.hdf5_version}".encode().ljust(33, b"\0")
        h5.create_dataset(" hdf5version", data=np.frombuffer(version, dtype=np.int8))
        w.node(h5, "CGNSLibraryVersion", "CGNSLibraryVersion_t", np.array([4.4], np.float32))
        base = w.node(h5, "Base", "CGNSBase_t", np.array([3, 3], np.int32))
        w.node(base, "DataClass", "DataClass_t", "Dimensional")
        si = ("Kilogram", "Meter", "Second", "Kelvin", "Degree")
        units = np.array([list(u.ljust(32).encode()) for u in si], np.int8)
        w.node(base, "DimensionalUnits", "DimensionalUnits_t", units, "C1")
        ncell = len(vol.ids)
        zone = w.node(base, "Zone", "Zone_t", np.array([[len(used)], [ncell], [0]], np.int64))
        w.node(zone, "ZoneType", "ZoneType_t", "Unstructured")
        grid = w.node(zone, "GridCoordinates", "GridCoordinates_t")
        for c, axis in enumerate("XYZ"):
            w.node(grid, f"Coordinate{axis}", "DataArray_t", mesh.coords[index, c].astype("f8"))

        def section(name, blocks, start):
            """One element section per type; returns the next element number."""
            types: dict[int, list] = {}
            for b in blocks:
                kind, order = ELEMENTS[b.element]
                types.setdefault(kind, []).append(
                    b.connectivity[:, order] if order else b.connectivity
                )
            first = start
            for kind, conns in types.items():
                conn = np.searchsorted(used, np.vstack(conns)) + 1
                label = name if len(types) == 1 else f"{name[:24]}_{TYPE_NAMES[kind]}"
                s = w.node(zone, label, "Elements_t", np.array([kind, 0], np.int32))
                w.node(s, "ElementRange", "IndexRange_t",
                       np.array([start, start + len(conn) - 1], np.int64))  # fmt: skip
                w.node(s, "ElementConnectivity", "DataArray_t", conn.ravel().astype(np.int64))
                start += len(conn)
            return first, start

        # The volume first, in Volume order (one type per block run, as written).
        order = []  # Volume positions in written order
        offsets = np.cumsum([0] + [len(b.ids) for b in vol.blocks])
        kinds = [ELEMENTS[b.element][0] for b in vol.blocks]
        for kind in dict.fromkeys(kinds):
            for i in range(len(vol.blocks)):
                if kinds[i] == kind:
                    order.append(np.arange(offsets[i], offsets[i + 1]))
        order = np.concatenate(order)
        _, start = section("Volume", vol.blocks, 1)
        names = _names(list(bounds))
        ranges = {}
        for sel, blocks in bounds.items():
            ranges[sel] = section(names[sel], blocks, start)
            start = ranges[sel][1]
        if bounds:
            zbc = w.node(zone, "ZoneBC", "ZoneBC_t")
            for sel, (lo, hi) in ranges.items():
                family = w.node(base, names[sel], "Family_t")
                w.node(family, "FamilyBC", "FamilyBC_t", bcs.get(sel, "BCTypeUserDefined"))
                bc = w.node(zbc, names[sel], "BC_t", "FamilySpecified")
                w.node(bc, "GridLocation", "GridLocation_t", "FaceCenter")
                w.node(bc, "PointList", "IndexArray_t", np.arange(lo, hi, dtype=np.int64)[:, None])
                w.node(bc, "FamilyName", "FamilyName_t", names[sel])
                w.node(bc, "PuzzleSelection", "Descriptor_t", sel)

        pointers: dict[str, list[str]] = {"Cell": [], "Vertex": []}
        written = []
        kinds = {f.location for s in steps for f in s.fields.values()}
        for k, step in enumerate(steps):
            cells = {n: f for n, f in step.fields.items() if f.location == "element"}
            nodes = {n: f for n, f in step.fields.items() if f.location == "node"}
            for where, fields, ids, place in (
                ("CellCenter", cells, vol.ids[order], "Cell"),
                ("Vertex", nodes, used, "Vertex"),
            ):
                if ("element" if place == "Cell" else "node") not in kinds:
                    continue
                name = f"FlowSolution{place}{k + 1:04d}"
                sol = w.node(zone, name, "FlowSolution_t")
                w.node(sol, "GridLocation", "GridLocation_t", where)
                pointers[place].append(name)
                for f in fields.values():
                    values = fluid.lookup(f, ids)
                    for c, array in enumerate(array_names(f.name, list(f.components))):
                        w.node(sol, array, "DataArray_t", values[:, c].astype("f8"))
                        written.append(array)
        n = len(steps)
        it = w.node(base, "BaseIterativeData", "BaseIterativeData_t", np.array([n], np.int32))
        w.node(it, "TimeValues", "DataArray_t", np.array(times, "f8"))
        w.node(it, "IterationValues", "DataArray_t", np.arange(1, n + 1, dtype=np.int32))
        if n > 1:
            zit = w.node(zone, "ZoneIterativeData", "ZoneIterativeData_t")
            for place, key in (("Cell", "FlowSolutionPointers"),
                               ("Vertex", "FlowSolutionVertexPointers")):  # fmt: skip
                if not pointers[place]:
                    continue
                if key == "FlowSolutionVertexPointers" and not pointers["Cell"]:
                    key = "FlowSolutionPointers"
                chars = np.array([list(p.ljust(32).encode()) for p in pointers[place]], np.int8)
                w.node(zit, key, "DataArray_t", chars, "C1")
    notes = {
        "zone": f"one unstructured zone: {ncell} cells, {len(used)} nodes",
        "boundaries": ", ".join(f"{names[s]} ({s})" for s in bounds) or "none",
        "arrays": ", ".join(dict.fromkeys(written)) or "none",
        "steps": str(len(steps)),
        "units": "SI (DimensionalUnits Kilogram, Meter, Second, Kelvin), float64",
    }
    if renumbered:
        notes["time values"] = "the step count (the steps' own values don't increase)"
    return [path], notes
