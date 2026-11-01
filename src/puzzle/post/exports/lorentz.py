"""The Lorentz force density (ADR 0043, electromagnetic): f = J × B per node, as a point
cloud of body forces for a structural model elsewhere (Ansys External Data, COMSOL's
interpolation functions, Abaqus mapped fields).

CSV: x, y, z [m], the force density's components and magnitude [N/m^3], at every node
where both the current density J [A/m^2] and the magnetic flux density B [T] are stored
(Elmer's and FEniCSx's magnetostatics store both per node); one selection's nodes if
asked. Outside conductors J is zero and so is the force. The last column, dV [m^3], is
each node's share of the volume, ∫N dV over its elements (ADR 0044 stage 2): ∑ dV f is
the total force ∫ f dV of the field as interpolated, which a conservative import keeps
(quadratic elements give corner nodes a small negative share).
"""

from __future__ import annotations

import numpy as np

from puzzle.errors import PuzzleError

J, B = "current_density", "magnetic_flux_density"


def write(job):
    missing = [name for name in (J, B) if name not in job.step.fields]
    if missing:
        words = {J: "current density J", B: "magnetic flux density B"}
        raise PuzzleError(
            "the Lorentz force J × B needs the current density and the magnetic flux "
            f"density at nodes; this step has no {' and no '.join(words[m] for m in missing)} "
            "(a magnetostatics run on Elmer or FEniCSx stores both)",
            code="not_found",
            details={"missing": missing, "fields": sorted(job.step.fields)},
        )
    j, b = job.step.fields[J], job.step.fields[B]
    if j.location != "node" or b.location != "node":
        raise PuzzleError("J × B is written from nodal J and B only", code="unsupported")
    ids = np.intersect1d(j.ids, b.ids)
    if job.selection is not None:
        group = job.mesh.groups.get(job.selection)
        if group is None:
            raise PuzzleError(f"no selection '{job.selection}' in the mesh", code="not_found")
        ids = ids[np.isin(ids, group.node_ids)]
    if not len(ids):
        raise PuzzleError("no node has both J and B", code="not_found")
    force = lorentz(j.at(ids), b.at(ids))
    xyz = job.mesh.node_coords(ids)
    header = "x [m],y [m],z [m],f_x [N/m^3],f_y [N/m^3],f_z [N/m^3],|f| [N/m^3]"
    columns = [xyz, force, np.linalg.norm(force, axis=1)]
    share = _volumes(job, ids)
    if share is not None:
        header += ",dV [m^3]"
        columns.append(share)
    table = np.column_stack(columns)
    job.path.write_text(
        header + "\n" + "\n".join(",".join(f"{v:.9g}" for v in row) for row in table) + "\n"
    )
    return [job.path], {
        "result": "Lorentz force density J × B (body force per volume)",
        "nodes": str(len(ids)),
        "on": job.selection or "every node with J and B",
        "units": "SI: m, N/m^3",
    }


def _volumes(job, ids) -> np.ndarray | None:
    """Each node's ∫N dV over the selection's elements (a body), else the whole mesh's;
    None when the mesh has elements the integration doesn't take."""
    from puzzle.adapters.common import InputError, node_weights
    from puzzle.mesh.mesh import Group

    group = job.mesh.groups.get(job.selection) if job.selection is not None else None
    if group is None or group.dim != 3:
        entities = sorted({b.entity for b in job.mesh.blocks if b.dim == 3})
        group = Group("mesh", 3, entities, job.mesh.node_ids)
    try:
        w = node_weights(group, job.mesh, "body")
    except InputError:
        return None
    return np.array([w.get(int(i), 0.0) for i in ids])


def lorentz(j: np.ndarray, b: np.ndarray) -> np.ndarray:
    """f = J × B (N/m^3) from J (A/m^2) and B (T), row by row."""
    return np.cross(np.asarray(j, float), np.asarray(b, float))
