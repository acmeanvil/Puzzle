"""Result trackers (ADR 0054 3): probes followed while a solve runs.

A `ResultProbe` with `track` set, whose source is the study being solved, becomes a
`Tracker`: which nodal field, which component, which nodes and how they reduce to one
number. CalculiX's parser reads them from the `.frd` it writes each increment;
Code_Aster prints them in its Newton table (`SUIVI_DDL`). What a tracker can't follow
here says why (`trackers` returns those notes), and the probe still works on the
finished result."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from puzzle.mesh.mesh import Mesh

# Nodal fields a solver writes as it goes, and their SI units.
FIELDS = {"displacement": "m", "temperature": "K", "reaction_force": "N"}
COMPONENTS = {"x": 0, "y": 1, "z": 2, "value": 0}


@dataclass(frozen=True)
class Tracker:
    name: str  # the probe's name
    field: str  # displacement, temperature or reaction_force
    what: str  # total (magnitude), x, y, z or value
    nodes: np.ndarray = field(repr=False)  # node ids
    reduce: str = "value"  # value (one node), max, min, mean, or sum (a reaction)
    unit: str = ""
    selection: str | None = None  # the mesh group, where the probe has one

    def value(self, ids: np.ndarray, values: np.ndarray) -> float | None:
        """The tracked number from one field's node ids and values (n x components)."""
        where = np.isin(ids, self.nodes)
        if not where.any():
            return None
        rows = np.asarray(values, dtype=float)[where]
        if rows.ndim == 1:
            rows = rows[:, None]
        if self.reduce == "sum":  # a reaction: the force summed, then its component
            total = rows.sum(axis=0)
            return float(
                np.linalg.norm(total) if self.what == "total" else total[COMPONENTS[self.what]]
            )
        per = (
            np.linalg.norm(rows, axis=1) if self.what == "total" else rows[:, COMPONENTS[self.what]]
        )
        return float({"max": per.max, "min": per.min, "mean": per.mean}.get(self.reduce, per.max)())


def trackers(project, study, mesh: Mesh) -> tuple[list[Tracker], list[str]]:
    """The study's tracked probes as trackers, and a note for each it can't follow."""
    from puzzle.model.schema import ResultProbe

    out: list[Tracker] = []
    notes: list[str] = []
    for probe in project.of_type(ResultProbe):
        if not getattr(probe, "track", False) or probe.source != study.id:
            continue
        name, _, what = probe.quantity.partition(":")
        if probe.type == "reaction":
            name, what = "reaction_force", what if what in ("x", "y", "z") else "total"
        if name not in FIELDS or what not in ("total", "x", "y", "z", "value"):
            notes.append(f"{probe.name}: only displacement, temperature and reaction force "
                         "are tracked while solving")  # fmt: skip
            continue
        if probe.type == "point" and probe.point is not None:
            xyz = np.asarray(mesh.coords, dtype=float)
            k = int(np.argmin(np.linalg.norm(xyz - np.asarray(probe.point), axis=1)))
            nodes, reduce, selection = np.array([mesh.node_ids[k]]), "value", None
        elif probe.type in ("selection", "reaction") and probe.selection in mesh.groups:
            nodes = np.asarray(mesh.groups[probe.selection].node_ids)
            reduce = "sum" if probe.type == "reaction" else probe.reduction
            selection = probe.selection
            if reduce == "integral":
                notes.append(f"{probe.name}: an integral is not tracked while solving")
                continue
        else:
            notes.append(f"{probe.name}: a {probe.type} probe is not tracked while solving")
            continue
        out.append(Tracker(probe.name, name, what, nodes, reduce, FIELDS[name], selection))
    return out, notes
