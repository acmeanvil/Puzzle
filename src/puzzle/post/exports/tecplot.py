"""Tecplot ASCII (ADR 0043, fluid): a `.dat` file Tecplot 360, ParaView and VisIt read.

One finite-element zone per part and step, DATAPACKING=BLOCK: the volume (FEBRICK, or
FETETRAHEDRON when every cell is a tetrahedron) and each face selection
(FEQUADRILATERAL, or FETRIANGLE), its zone title the selection's name. Tetrahedra,
pyramids and wedges are bricks with repeated corners, triangles quadrilaterals with
the last corner repeated, as Tecplot's FE zones want; Tecplot's classic FE zones have
no mid-side nodes, so a quadratic mesh is written by its corners. Cell fields are
CELLCENTERED (VARLOCATION), nodal ones NODAL; on a face zone a cell field is the
adjacent volume cell's value. A step is a SOLUTIONTIME (the steps of one part share a
STRANDID), and later steps share the first's coordinates and connectivity
(VARSHARELIST, CONNECTIVITYSHAREZONE). A field missing from a step is PASSIVE there.
Variable names carry their SI unit (`p [Pa]`); values have 9 significant digits.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from puzzle.post.exports import fluid

UNITS = {
    "velocity": "m/s",
    "pressure": "Pa",
    "temperature": "K",
    "turbulent_kinetic_energy": "m^2/s^2",
    "turbulent_dissipation": "m^2/s^3",
    "specific_dissipation": "1/s",
    "turbulent_viscosity": "Pa s",
    "displacement": "m",
    "stress": "Pa",
    "heat_flux": "W/m^2",
}
# Corner positions for an 8-node brick, per volume family; 4-node quads per face.
BRICK = {
    "hex": [0, 1, 2, 3, 4, 5, 6, 7],
    "tet": [0, 1, 2, 2, 3, 3, 3, 3],
    "pyramid": [0, 1, 2, 3, 4, 4, 4, 4],
    "wedge": [0, 1, 2, 2, 3, 4, 5, 5],
}
QUAD = {"quad": [0, 1, 2, 3], "tri": [0, 1, 2, 2]}


def write(job):
    return write_tecplot(job.mesh, job.results, job.path)


def _columns(results):
    """[(field, location, component index, variable name)] over every step."""
    seen: dict[tuple[str, str], list[str]] = {}
    for s in results.steps:
        for f in s.fields.values():
            if f.location in ("node", "element"):
                seen.setdefault((f.name, f.location), list(f.components))
    out = []
    for (name, location), comps in seen.items():
        both = location == "element" and (name, "node") in seen
        label = f"{name} (cells)" if both else name
        unit = UNITS.get(name)
        for k, c in enumerate(comps):
            var = f"{label} {c}".strip() if c else label
            out.append((name, location, k, f"{var} [{unit}]" if unit else var))
    return out


def _block(values: np.ndarray) -> str:
    """Values five to a line (Tecplot ASCII lines should stay short)."""
    text = [f"{v:.9g}" for v in values]
    return "\n".join(" ".join(text[i : i + 5]) for i in range(0, len(text), 5)) + "\n"


def write_tecplot(mesh, results, path: Path):
    vol = fluid.volume(mesh)
    faces = fluid.cell_faces(vol)
    parts = [("volume", vol.blocks, None)]
    parts += [(name, blocks, fluid.adjacent(faces, blocks))
              for name, blocks in fluid.boundaries(mesh).items()]  # fmt: skip
    columns = _columns(results)
    names = ["X [m]", "Y [m]", "Z [m]"] + [c[3] for c in columns]
    cell_vars = [i + 4 for i, c in enumerate(columns) if c[1] == "element"]
    out = [f'TITLE = "Puzzle export: {results.solver} results (SI units)"',
           "VARIABLES = " + " ".join(f'"{n}"' for n in names)]  # fmt: skip
    times, _ = fluid.time_values(results)
    zone = 0
    first: dict[str, int] = {}
    tetra = all(fluid.family(b.element) == "tet" for b in vol.blocks)
    vol_cells = {id(f): fluid.lookup(f, vol.ids) for s in results.steps for f in s.fields.values()
                 if f.location == "element"}  # fmt: skip
    for k, step in enumerate(results.steps):
        for p, (name, blocks, behind) in enumerate(parts):
            corners, cells = _connectivity(blocks, name == "volume", tetra)
            nodes = np.unique(corners)
            zone += 1
            kind = ("FETETRAHEDRON" if tetra else "FEBRICK") if name == "volume" else (
                "FEQUADRILATERAL")  # fmt: skip
            header = [f'ZONE T="{name}"', f"NODES={len(nodes)}", f"ELEMENTS={len(corners)}",
                      "DATAPACKING=BLOCK", f"ZONETYPE={kind}",
                      f"SOLUTIONTIME={times[k]:.9g}", f"STRANDID={p + 1}"]  # fmt: skip
            if cell_vars:
                located = ",".join(f"[{v}]=CELLCENTERED" for v in cell_vars)
                header.append(f"VARLOCATION=({located})")
            present = {(f.name, f.location): f for f in step.fields.values()}
            passive = [i + 4 for i, c in enumerate(columns) if (c[0], c[1]) not in present]
            if passive:
                header.append("PASSIVEVARLIST=(" + ",".join(f"[{v}]" for v in passive) + ")")
            shared = name in first
            if shared:
                header += [f"VARSHARELIST=([1-3]={first[name]})",
                           f"CONNECTIVITYSHAREZONE={first[name]}"]  # fmt: skip
            else:
                first[name] = zone
            out.append(", ".join(header))
            body = []
            if not shared:
                xyz = mesh.coords[fluid.node_index(mesh, nodes)]
                body += [_block(xyz[:, c]) for c in range(3)]
            for fname, location, comp, _ in columns:
                f = present.get((fname, location))
                if f is None:
                    continue
                if location == "node":
                    body.append(_block(fluid.lookup(f, nodes)[:, comp]))
                else:
                    values = vol_cells[id(f)][:, comp]
                    if behind is None:
                        body.append(_block(values[cells]))
                    else:
                        picked = np.full(len(behind), np.nan)
                        picked[behind >= 0] = values[behind[behind >= 0]]
                        body.append(_block(picked[cells]))
            if not shared:
                local = np.searchsorted(nodes, corners) + 1
                body.append("\n".join(" ".join(map(str, row)) for row in local) + "\n")
            out.append("".join(body).rstrip("\n"))
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    notes = {
        "zones": f"{len(parts)} parts x {len(results.steps)} steps: "
        + ", ".join(p[0] for p in parts),
        "values": "9 significant digits; SI units, named in each variable",
    }
    if any(b.element in ("tet10", "tri6", "quad8", "hex20") for _, bl, _ in parts for b in bl):
        notes["quadratic elements"] = "written by their corner nodes (Tecplot FE zones)"
    if len(parts) > 1:
        notes["face zones"] = "cell fields there are the adjacent cell's values"
    return [path], notes


def _connectivity(blocks, volume: bool, tetra: bool):
    """Corner node ids per element in Tecplot's shape, and each element's position in
    the part's own order (Volume order for the volume, the faces' order otherwise)."""
    rows, order, start = [], [], 0
    for b in blocks:
        fam = fluid.family(b.element)
        pick = ([0, 1, 2, 3] if tetra else BRICK[fam]) if volume else QUAD[fam]
        rows.append(b.connectivity[:, pick])
        order.append(np.arange(start, start + len(b.ids)))
        start += len(b.ids)
    return np.vstack(rows), np.concatenate(order)
