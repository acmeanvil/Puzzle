"""Contact results (ADR 0056): fields on each contact's source (slave) nodes, and the
per-contact numbers of the Contact tool's Results tab.

Fields, one value per slave node:
- `contact_pressure` (Pa), `contact_shear` (Pa, the frictional stress's size);
- `contact_gap` (m: + open, − penetration);
- `contact_sliding` (m, the slip's size);
- `contact_status`: 0 open, 1 sliding, 2 sticking (a closed frictionless contact
  slides).

Solvers give them differently. CalculiX writes pressure and shears (CPRESS, CSHEAR1/2),
opening and slips (COPEN, CSLIP1/2) on every node, zero away from contact; `finish`
keeps the slave nodes and works the status out from the pressure and μ. Code_Aster's
continuous contact solves for the pressure itself, its multiplier LAGS_C, and for
friction semi-multipliers LAGS_F1/F2 (τ = μ p |λ_f|, |λ_f| = 1 sliding), stored with
the displacements; CONT_NOEU adds its status, gap and slip, and nodal forces. Where
the multipliers are missing, the forces are turned into pressures by the face's
consistent area matrix (M p = f, M = ∫ Nᵢ Nⱼ dA): that reproduces LAGS_C on quadratic
faces, but oscillates on linear ones, where Code_Aster integrates at the nodes.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from puzzle.mesh.mesh import Mesh
from puzzle.results.model import Field

FIELDS = ("contact_pressure", "contact_shear", "contact_gap", "contact_sliding",
          "contact_status")  # fmt: skip
STATUS = {0: "open", 1: "sliding", 2: "sticking"}
STICK = 0.99  # a frictional node sticks while its shear is below 99 % of μ p


def _area_matrix(mesh: Mesh, selections: list[str]):
    """The consistent area matrix ∫ Nᵢ Nⱼ dA of these face selections, over their nodes:
    (node ids, matrix) with the matrix as a dense array (contact faces are small)."""
    from puzzle.adapters.common import _quadrature

    blocks = []
    for s in selections:
        group = mesh.groups.get(s)
        if group is not None and group.dim == 2:
            blocks.append(group)
    if not blocks:
        return np.zeros(0, np.int64), np.zeros((0, 0))
    ids = np.unique(np.concatenate([g.node_ids for g in blocks]))
    where = {int(n): i for i, n in enumerate(ids)}
    m = np.zeros((len(ids), len(ids)))
    for g in blocks:
        for conn, n, _x, da in _quadrature(g, mesh):
            local = np.vectorize(where.get)(conn)
            contrib = np.einsum("e,i,j->eij", da, n, n)
            np.add.at(m, (local[:, :, None], local[:, None, :]), contrib)
    return ids, m


def _at(field: Field, ids: np.ndarray) -> np.ndarray:
    """The field's rows at these ids, NaN where it has none (n x components)."""
    order = np.argsort(field.ids)
    pos = np.clip(np.searchsorted(field.ids, ids, sorter=order), 0, max(len(field.ids) - 1, 0))
    values = np.full((len(ids), field.values.shape[1]), np.nan)
    if len(field.ids):
        found = field.ids[order[pos]] == ids
        values[found] = field.values[order[pos[found]]]
    return values


def finish(results, project, component, mesh: Mesh) -> None:
    """Turn each step's raw contact output into the neutral fields, in place."""
    touching = [c for c in component.contacts  # threads: post/thread_fields.py
                if c.type in ("frictionless", "frictional") and c.thread is None]  # fmt: skip
    if not touching:
        return
    slaves = {c.name: np.asarray(mesh.groups[c.source].node_ids) for c in touching
              if c.source in mesh.groups}  # fmt: skip
    if not slaves:
        return
    nodes = np.unique(np.concatenate(list(slaves.values())))
    mu = np.zeros(len(nodes))
    for c in touching:
        if c.name in slaves and c.type in ("frictional", "rough"):
            mu[np.isin(nodes, slaves[c.name])] = c.friction
    matrix = None
    for step in results.steps:
        f = step.fields
        raw = f.pop("contact_calculix", None)
        if raw is not None:  # open, slip1, slip2, pressure, shear1, shear2 on all nodes
            v = _at(raw, nodes)
            pressure, shear = v[:, 3], np.hypot(v[:, 4], v[:, 5])
            status = np.where(pressure > 0, np.where((mu > 0) & (shear < STICK * mu * pressure),
                                                     2, 1), 0)  # fmt: skip
            _put(f, nodes, pressure=pressure, shear=shear, gap=v[:, 0],
                 sliding=np.hypot(v[:, 1], v[:, 2]), status=status)  # fmt: skip
            continue
        normal = f.pop("contact_normal_force", None)
        total = f.pop("contact_force", None)
        if normal is None:
            continue
        if matrix is None:
            sources = [c.source for c in touching if c.name in slaves]
            matrix = (*_area_matrix(mesh, sources),)
            matrix = (*matrix, node_normals(mesh, sources, matrix[0]))
        ids, m, normals = matrix
        normal_vec = f.pop("contact_normal_vector", None)
        if normal_vec is not None:  # signed: RN is a size, and a node may pull
            rn = (np.nan_to_num(_at(normal_vec, ids)) * normals).sum(axis=1)
            rn *= 1.0 if rn.sum() >= 0 else -1.0  # compression positive
        else:
            rn = np.nan_to_num(_at(normal, ids)[:, 0])
        lags_c, lags_f = f.pop("contact_lags_c", None), f.pop("contact_lags_f", None)
        # Code_Aster's own pressure is its contact multiplier; forces are the fallback.
        pressure = -np.nan_to_num(_at(lags_c, ids)[:, 0]) if lags_c is not None else _solve(m, rn)
        shear = np.zeros(len(ids))
        if lags_f is not None and lags_c is not None:  # τ = μ p |λ_f|, |λ_f| = 1 sliding
            size = np.linalg.norm(np.nan_to_num(_at(lags_f, ids)), axis=1)
            mu_here = np.zeros(len(ids))
            for c in touching:
                if c.name in slaves and c.type in ("frictional", "rough"):
                    mu_here[np.isin(ids, slaves[c.name])] = c.friction
            shear = mu_here * np.maximum(pressure, 0.0) * np.minimum(size, 1.0)
        elif total is not None:
            r = np.nan_to_num(_at(total, ids))
            rnv = np.nan_to_num(_at(normal_vec, ids)) if normal_vec is not None else 0.0
            tangential = r - rnv
            shear = np.linalg.norm(np.column_stack([_solve(m, tangential[:, k])
                                                    for k in range(3)]), axis=1)  # fmt: skip
        status_field = f.pop("contact_status_code_aster", None)
        code = np.nan_to_num(_at(status_field, ids)[:, 0]) if status_field is not None else None
        # Code_Aster: 0 no contact, 1 sticking (adhérent), 2 sliding (glissant).
        status = (np.select([code == 1, code == 2], [2, 1], 0) if code is not None
                  else np.where(pressure > 0, 1, 0))  # fmt: skip
        gap = f.pop("contact_gap_code_aster", None)
        slip = f.pop("contact_sliding_code_aster", None)
        _put(f, ids, pressure=np.where(status > 0, pressure, 0.0), shear=shear,
             gap=_at(gap, ids)[:, 0] if gap is not None else np.zeros(len(ids)),
             sliding=_at(slip, ids)[:, 0] if slip is not None else np.zeros(len(ids)),
             status=status)  # fmt: skip


def node_normals(mesh: Mesh, selections: list[str], ids: np.ndarray) -> np.ndarray:
    """Each node's outward normal on these face selections: the mean of its faces'."""
    from puzzle.contact_check import surface

    where = {int(n): i for i, n in enumerate(ids)}
    out = np.zeros((len(ids), 3))
    for s in selections:
        face = surface(mesh, s)
        if face is None:
            continue
        rows = [b.connectivity for b in mesh.groups[s].blocks(mesh)
                if b.dim == 2 and b.element.startswith("tri")]  # fmt: skip
        conn = np.concatenate(rows) if rows else np.zeros((0, 3), np.int64)
        for element, normal in zip(conn, face.normals[: len(conn)], strict=True):
            for n in element:
                if int(n) in where:
                    out[where[int(n)]] += normal
    return out / np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-300)


def _solve(m: np.ndarray, f: np.ndarray) -> np.ndarray:
    if not len(f):
        return f
    try:
        return np.linalg.solve(m, f)
    except np.linalg.LinAlgError:
        return np.linalg.lstsq(m, f, rcond=None)[0]


def _put(fields: dict, ids, **values) -> None:
    for key, v in values.items():
        name = f"contact_{key}"
        fields[name] = Field(name, "node", [""], np.asarray(ids, np.int64),
                             np.asarray(v, float).reshape(-1, 1))  # fmt: skip


@dataclass
class ContactResult:
    """One contact's totals at one step (SI units)."""

    name: str
    normal_force: float  # ∫ p dA over the source face
    tangential_force: float  # ∫ τ dA
    area: float  # where it is closed (status sliding or sticking)
    area_share: float
    max_pressure: float
    max_penetration: float
    max_sliding: float
    sticking_share: float  # of the area in contact
    max_gap: float

    UNITS = {"normal_force": "N", "tangential_force": "N", "area": "m2", "max_pressure": "Pa",
             "max_penetration": "m", "max_sliding": "m", "max_gap": "m"}  # fmt: skip

    def to_dict(self) -> dict:
        """Keys carry their units (normal_force_N, area_m2, ...), for the agent."""
        return {f"{k}_{self.UNITS[k]}" if k in self.UNITS else k: v for k, v in vars(self).items()}


def contact_results(step, component, mesh: Mesh) -> list[ContactResult]:
    """Each touching contact's totals from one step's contact fields."""
    from puzzle.adapters.common import _quadrature

    f = step.fields
    if "contact_pressure" not in f:
        return []
    out = []
    for c in component.contacts:
        if (c.type not in ("frictionless", "frictional") or c.thread is not None
                or c.source not in mesh.groups):  # fmt: skip
            continue
        group = mesh.groups[c.source]
        ids = np.asarray(group.node_ids)
        p = np.nan_to_num(_at(f["contact_pressure"], ids)[:, 0])
        tau = np.nan_to_num(_at(f["contact_shear"], ids)[:, 0]) if "contact_shear" in f else 0 * p
        status = np.nan_to_num(_at(f["contact_status"], ids)[:, 0])
        gap = np.nan_to_num(_at(f["contact_gap"], ids)[:, 0])
        slide = np.nan_to_num(_at(f["contact_sliding"], ids)[:, 0])
        where = {int(n): i for i, n in enumerate(ids)}
        normal = tangential = area = total = stick = 0.0
        for conn, n, _x, da in _quadrature(group, mesh):
            local = np.vectorize(where.get)(conn)
            normal += float((da * (p[local] @ n)).sum())
            tangential += float((da * (tau[local] @ n)).sum())
            closed = (status[local] > 0).astype(float) @ n
            sticking = (status[local] == 2).astype(float) @ n
            area += float((da * closed).sum())
            stick += float((da * sticking).sum())
            total += float(da.sum())
        out.append(ContactResult(
            c.name, normal, tangential, area, area / total if total else 0.0,
            float(p.max()) if len(p) else 0.0, float(max(-gap.min(), 0.0)) if len(gap) else 0.0,
            float(slide.max()) if len(slide) else 0.0, stick / area if area else 0.0,
            float(max(gap.max(), 0.0)) if len(gap) else 0.0,
        ))  # fmt: skip
    return out


def run_contact_results(project, store, run_id: str, step: int | None = None):
    """A stored run's contact totals at one result step (default the last that has
    contact fields): (that step, its results, the steps with contact fields)."""
    from puzzle.errors import PuzzleError

    record = store.get_run(run_id)
    results = store.read_results(run_id)
    steps = [s for s in results.steps if "contact_pressure" in s.fields]
    if not steps:
        raise PuzzleError("this run has no contact results (frictionless or frictional "
                          "contact on CalculiX or Code_Aster)", code="not_found")  # fmt: skip
    chosen = steps[-1 if step is None else max(0, min(step, len(steps)) - 1)]
    study = next((s for s in project.objects.values()
                  if str(getattr(s, "id", "")) == record.study_id), None)  # fmt: skip
    component = project.objects.get(getattr(study, "component", None))
    if component is None:
        raise PuzzleError("the run's study or component is gone", code="not_found")
    mesh = store.read_mesh(record.mesh_id)
    return chosen, contact_results(chosen, component, mesh), steps
