"""OpenCourant's output as Puzzle's results (ADR 0085 decision 2).

Each animation frame (`puzzleA001.vtk`… from anim_to_vtk: legacy VTK with NODE_ID,
Displacement, Velocity, Acceleration and per element ELEMENT_ID, the stress tensor,
von Mises and plastic strain) is a step of kind `transient` at its time. Element
values are averaged to the nodes (the other adapters' results are nodal). The time
history (`puzzleT01.csv` from th_to_csv: energies, momentum, mass, added mass, the time
step) gives each frame its energies as global fields, interpolated at the frame's time;
the whole history stays in the run folder for operators and the Solution panel.
"""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

from puzzle.results.model import Field, Results, StepResult

STRESS = [0, 4, 8, 1, 5, 2]  # a 3 x 3 tensor's xx, yy, zz, xy, yz, zx
NODE_FIELDS = {"Displacement": "displacement", "Velocity": "velocity",
               "Acceleration": "acceleration"}  # fmt: skip
# The time history's columns -> global fields, J or kg (ADR 0085 decision 5)
HISTORY = {
    "INTERNAL ENERGY": "internal_energy",
    "KINETIC ENERGY": "kinetic_energy",
    "ROTATION ENERGY": "rotational_energy",
    "EXTERNAL WORK": "external_work",
    "CONTACT ENERGY": "contact_energy",
    "HOURGLASS ENERGY": "hourglass_energy",
    "PLASTIC WORK": "plastic_work",
    "ADDED MASS": "added_mass",
    "MASS": "mass",
    "TIME STEP": "time_step",
}


def history(workdir: Path, name: str) -> dict[str, np.ndarray]:
    """The time history's global columns by Puzzle's names, plus "time"."""
    path = workdir / f"{name}T01.csv"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    if len(rows) < 2:
        return {}
    head = [h.strip() for h in rows[0]]
    while head and not head[-1]:  # th_to_csv ends its header with a comma
        head.pop()
    data = np.array([[float(x) for x in r[: len(head)]] for r in rows[1:] if len(r) >= len(head)])
    out = {"time": data[:, 0]}
    for column, key in HISTORY.items():
        if column in head:
            out[key] = data[:, head.index(column)]
    return out


def _nodal(cell_ids, cell_values, conn: dict[int, np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """Element values averaged at their nodes."""
    sums: dict[int, np.ndarray] = {}
    counts: dict[int, int] = {}
    for e, value in zip(cell_ids.tolist(), cell_values, strict=True):
        for n in conn.get(int(e), ()):
            n = int(n)
            sums[n] = sums.get(n, 0.0) + value
            counts[n] = counts.get(n, 0) + 1
    ids = np.array(sorted(sums), dtype=np.int64)
    return ids, np.array([sums[i] / counts[i] for i in ids]).reshape(len(ids), -1)


def read(workdir: Path, name: str, conn: dict[int, np.ndarray], mesh_nodes: set[int]) -> Results:
    import pyvista as pv

    frames = sorted(workdir.glob(f"{name}A[0-9][0-9][0-9].vtk"))
    if not frames:
        raise FileNotFoundError(f"no animation frames in {workdir} (see solver.log)")
    hist = history(workdir, name)
    steps = []
    for frame in frames:
        grid = pv.read(str(frame))
        t = float(np.asarray(grid.field_data["TIME"]).ravel()[0]) if "TIME" in grid.field_data \
            else 0.0  # fmt: skip
        fields: dict[str, Field] = {}
        nodes = np.asarray(grid.point_data["NODE_ID"], dtype=np.int64)
        keep = np.array([int(n) in mesh_nodes for n in nodes], bool)
        for vtk_name, key in NODE_FIELDS.items():
            if vtk_name in grid.point_data:
                values = np.asarray(grid.point_data[vtk_name], float).reshape(len(nodes), 3)
                fields[key] = Field(key, "node", ["x", "y", "z"], nodes[keep], values[keep])
        cells = np.asarray(grid.cell_data["ELEMENT_ID"], dtype=np.int64) if \
            "ELEMENT_ID" in grid.cell_data else None  # fmt: skip
        if cells is not None:
            solid = np.array([int(e) in conn for e in cells], bool)
            for vtk_name in [k for k in grid.cell_data if k.endswith("_Stress")]:
                tensor = np.asarray(grid.cell_data[vtk_name], float).reshape(len(cells), 9)
                ids, values = _nodal(cells[solid], tensor[solid][:, STRESS], conn)
                fields["stress"] = Field("stress", "node", ["xx", "yy", "zz", "xy", "yz", "zx"],
                                         ids, values)  # fmt: skip
            for vtk_name in [k for k in grid.cell_data if "Plastic" in k or k.endswith("_EPSP")]:
                eps = np.asarray(grid.cell_data[vtk_name], float).reshape(len(cells), -1)[:, :1]
                ids, values = _nodal(cells[solid], eps[solid], conn)
                fields["plastic_strain_equivalent"] = Field(
                    "plastic_strain_equivalent", "node", [""], ids, values
                )
        if hist and len(hist["time"]) > 1:
            for key, series in hist.items():
                if key != "time":
                    value = float(np.interp(t, hist["time"], series))
                    fields[key] = Field(key, "global", [""], np.array([0]), np.array([[value]]))
        steps.append(StepResult(1, "transient", t, fields=fields))
    return Results("opencourant", steps)
