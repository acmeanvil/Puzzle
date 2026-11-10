"""Threaded contacts' results (ADR 0076), from the displacements, for every solver.

Each pair's flank force is its spring's (or gap's) along the flank normal n: the
screw's push into the nut, n · (u_screw - u_nut) (less the clearance, and none in
tension, for a gap), times the pair's stiffness. Rigid threads (equations) carry no
spring, so they get no fields. Per step with a displacement:

- `thread_force` (nodes of the screw's face): normal, axial and radial (N; axial along
  the thread's axis, radial outward);
- `thread_pressure` (Pa): the normal force over the pair's flank area;
- `thread_load` (global): each thread's axial force, the first engaged (at the mouth)
  first, components "<contact>: <k>";
- `thread_wedge` (global): each contact's total radial force (N), the nut's opening.
"""

from __future__ import annotations

import numpy as np

from puzzle.results.model import Field


def pair_forces(tp, u) -> dict[str, np.ndarray]:
    """flank -> each pair's normal force (N) from the displacement field `u`."""
    screw, nut = (tp.source, tp.target) if tp.screw_is_source else (tp.target, tp.source)
    us, un = _vectors(u, screw), _vectors(u, nut)
    out = {}
    from puzzle.adapters.thread_pairs import TURN

    for flank in tp.flanks:
        push = np.einsum("ij,ij->i", tp.normals[flank], us - un)
        if tp.kind == "frictionless" and flank != TURN:
            push = np.maximum(push - tp.clearance, 0.0)
        out[flank] = tp.stiffness[flank] * push
    return out


def _vectors(u: Field, ids) -> np.ndarray:
    v = np.asarray(u.at(np.asarray(ids)), dtype=float)
    if v.shape[1] < 3:
        v = np.hstack([v, np.zeros((len(v), 3 - v.shape[1]))])
    return v[:, :3]


def finish(results, project, comp, mesh) -> None:
    """Add the thread fields to each step of `results` with a displacement."""
    from puzzle import threads
    from puzzle.adapters.thread_pairs import thread_pairs

    threaded = [c for c in comp.contacts if c.thread is not None and
                c.thread.compliance != "rigid"]  # fmt: skip
    if not threaded:
        return
    pairs = {}
    for c in threaded:
        try:
            pairs[c.name] = thread_pairs(project, comp, mesh, c)
        except Exception:  # noqa: BLE001 - results are extras: a run never fails on them
            continue
    for step in results.steps:
        u = step.fields.get("displacement")
        if u is None or u.location != "node":
            continue
        ids, rows, pressure, loads, load_names, wedges = [], [], [], [], [], []
        for name, tp in pairs.items():
            forces = pair_forces(tp, u)
            normal = np.zeros(len(tp.source))
            axial = np.zeros(len(tp.source))
            radial = np.zeros(len(tp.source))
            area = np.zeros(len(tp.source))
            from puzzle.adapters.thread_pairs import TURN

            forces.pop(TURN, None)  # the hold against turning: no flank load
            for flank, f in forces.items():
                n = tp.normals[flank]
                normal += f
                axial += f * np.einsum("ij,ij->i", n, tp.e_z)
                radial += f * np.einsum("ij,ij->i", n, tp.e_r)
                area += tp.areas[flank]
            screw = tp.source if tp.screw_is_source else tp.target
            ids.append(screw)
            rows.append(np.column_stack([normal, axial, radial]))
            pressure.append(normal / np.maximum(area / len(forces), 1e-300))
            toward = -tp.pulled  # the threads run from the mouth into the nut
            per = threads.per_thread(tp.z, tp.pulled * axial, tp.pitch, tp.mouth, toward)
            loads += per
            load_names += [f"{name}: {k + 1}" for k in range(len(per))]
            wedges.append(float(radial.sum()))
        if not ids:
            continue
        node_ids = np.concatenate(ids)
        step.fields["thread_force"] = Field(
            "thread_force", "node", ["normal", "axial", "radial"], node_ids, np.vstack(rows)
        )
        step.fields["thread_pressure"] = Field(
            "thread_pressure", "node", [""], node_ids, np.concatenate(pressure)[:, None]
        )
        step.fields["thread_load"] = Field(
            "thread_load", "global", load_names, np.array([0]), np.array([loads])
        )
        step.fields["thread_wedge"] = Field(
            "thread_wedge", "global", list(pairs), np.array([0]), np.array([wedges])
        )
