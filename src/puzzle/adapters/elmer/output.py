"""Read Elmer's ASCII .vtu output and its eigenvalues into solver-neutral results."""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

from puzzle.results.model import Field, Results, StepResult

STRESS = ["xx", "yy", "zz", "xy", "yz", "xz"]  # Elmer's names; Puzzle calls the last "zx"
ENERGY_LINE = re.compile(r"Tot\. Electric Energy\s*:\s*([-+0-9.Ee]+)")
EIGEN_LINE = re.compile(r"EigenSolve:\s+(\d+):\s+([-+0-9.Ee]+)\s+([-+0-9.Ee]+)")


def read_vtu(path: Path) -> dict[str, np.ndarray]:
    """Point data arrays by name, as (n, components). Cell data is ignored."""
    root = ET.parse(path).getroot()
    arrays = {}
    point_data = root.find(".//PointData")
    for da in point_data.iter("DataArray") if point_data is not None else []:
        name = da.get("Name")
        if name is None or da.get("format", da.get("FORMAT", "")).lower() != "ascii":
            continue
        values = np.array((da.text or "").split(), dtype=float)
        k = int(da.get("NumberOfComponents", "1"))
        arrays[name] = values.reshape(-1, k)
    return arrays


def eigenvalues(log_text: str) -> list[float]:
    """Eigenvalues (omega^2) from the solver log, in mode order."""
    found: dict[int, float] = {}
    for m in EIGEN_LINE.finditer(log_text):
        found[int(m.group(1))] = float(m.group(2))
    return [found[k] for k in sorted(found)]


def read_series(
    vtus: list[Path], times: list[float], node_ids: np.ndarray, log_text: str
) -> Results:
    """One result step per output file (transient), or the single steady/modal result."""
    if len(vtus) == 1:
        return read_results(vtus[0], node_ids, log_text)
    results = Results(solver="elmer")
    for vtu, t in zip(vtus, times, strict=False):
        (step,) = read_results(vtu, node_ids, log_text).steps
        step.value = t
        results.steps.append(step)
    return results


def read_results(vtu: Path, node_ids: np.ndarray, log_text: str) -> Results:
    """`node_ids` maps Elmer node n (1-based) to Puzzle's node id node_ids[n - 1]."""
    data = read_vtu(vtu)
    if "GeometryIds" in data:
        ids = node_ids[data["GeometryIds"][:, 0].astype(np.int64) - 1]
    else:
        # Elmer writes one point per mesh node, in its node order (1..N); Puzzle numbered
        # Elmer's nodes in its own node order, so point i is node_ids[i].
        n_points = next(iter(data.values())).shape[0] if data else 0
        if n_points != len(node_ids):
            raise ValueError(f"Elmer wrote {n_points} points for {len(node_ids)} mesh nodes")
        ids = node_ids
    order = np.argsort(ids)
    ids = ids[order]

    def field(name: str, comps: list[str], values: np.ndarray) -> Field:
        return Field(name, "node", comps, ids, values[order])

    results = Results(solver="elmer")
    modes = sorted(
        int(n.rsplit("EigenMode", 1)[1]) for n in data if n.startswith("displacement EigenMode")
    )
    if modes:
        lam = eigenvalues(log_text)
        for m in modes:
            hz = math.sqrt(max(lam[m - 1], 0.0)) / (2 * math.pi) if m <= len(lam) else float("nan")
            disp = field("displacement", ["x", "y", "z"], data[f"displacement EigenMode{m}"])
            results.steps.append(StepResult(1, "modal", hz, m, {"displacement": disp}))
        return results

    step = StepResult(1, "", 1.0)
    if "displacement" in data:
        step.fields["displacement"] = field("displacement", ["x", "y", "z"], data["displacement"])
    if all(f"stress_{c}" in data for c in STRESS):
        stress = np.column_stack([data[f"stress_{c}"][:, 0] for c in STRESS])
        step.fields["stress"] = field("stress", ["xx", "yy", "zz", "xy", "yz", "zx"], stress)
    if "temperature" in data:
        step.fields["temperature"] = field("temperature", [""], data["temperature"])
    if "potential" in data:
        step.fields["electric_potential"] = field("electric_potential", [""], data["potential"])
    if "electric field" in data:
        step.fields["electric_field"] = field(
            "electric_field", ["x", "y", "z"], data["electric field"]
        )
    energy = ENERGY_LINE.findall(log_text)
    if energy:
        # A whole-model value: stored as a one-entry "global" field.
        step.fields["electric_energy"] = Field(
            "electric_energy", "global", [""], np.array([0]), np.array([[float(energy[-1])]])
        )
    results.steps.append(step)
    return results
