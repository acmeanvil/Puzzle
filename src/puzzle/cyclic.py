"""Cyclic symmetry and linear periodicity (ADR 0081 steps 3 and 4): one sector of a
whole of identical sectors, or one slice of a whole repeating along a line (u_t = u_s,
R the identity).

The mesher meshes the target cut face as the source face turned about the axis
(`mesh.generate._periodic`), so each target node lies where a source node turns to.
`pairs` finds them; `rows` gives the constraints each pair makes, as linear equations over
(node, dof) with Puzzle's 1-based dofs (1-3 displacements, 11 temperature):

- solid mechanics: u_t - R u_s = 0, three rows per pair;
- heat transfer: T_t - T_s = 0.

Components a plain support already holds at zero are left out of the rows, and the rows
are reduced so each dependent dof (a target's first) is in one equation only. An adapter
writes the result as its own constraint equations (CalculiX *EQUATION).

Only the cyclically symmetric solution is found (nodal diameter 0), which is the whole
answer for loads repeated in every sector: statics, heat, transients. Modes and buckling
of other nodal diameters need each solver's own cyclic analysis and are refused.
"""

from __future__ import annotations

import numpy as np

from puzzle.adapters.common import InputError

STEPS = ("stationary", "transient")  # what the sector's own solution answers
PHYSICS = ("solid_mechanics", "heat_transfer")


def pairs(comp, mesh, c=None) -> np.ndarray:
    """(n, 2) node ids: each target node and the source node it follows, for the
    component's cyclic symmetry, or `c` (a CyclicSymmetry or LinearPeriodicity)."""
    from scipy.spatial import cKDTree

    c = c or comp.cyclic
    for name in (c.source, c.target):
        if name not in mesh.groups:
            raise InputError(f"symmetry: no selection '{name}' in the mesh")
    src = mesh.groups[c.source].node_ids
    tgt = mesh.groups[c.target].node_ids
    landed = mesh.node_coords(src) @ c.rotation().T + c.shift()
    size = float(np.ptp(mesh.coords, axis=0).max()) or 1.0
    distance, at = cKDTree(landed).query(mesh.node_coords(tgt))
    if len(src) != len(tgt) or distance.max() > 1e-6 * size:
        raise InputError(
            f"symmetry: the nodes of '{c.target}' don't lie where those of '{c.source}' "
            f"land (worst {distance.max():.3g} m); remesh, since both faces must be "
            "meshed together",
            code="invalid",
        )
    return np.column_stack([tgt, src[at]])


def held_at_zero(comp, mesh, planar: bool = False) -> dict[int, set[int]]:
    """node -> the dofs a plain support holds at zero there (fixed, displacement, a
    prescribed temperature counts only for its value 0 K, so not at all)."""
    dofs = (1, 2) if planar else (1, 2, 3)
    out: dict[int, set[int]] = {}
    for ph in comp.physics:
        for f in ph.features:
            if f.type not in ("fixed", "displacement") or f.selection not in mesh.groups:
                continue
            if f.axes is not None or f.fields or f.steps:
                continue  # not a plain global hold in every step
            if f.type == "fixed":
                held = set(dofs)
            else:
                held = {"xyz".index(a) + 1 for a, q in f.values.items()
                        if a in "xyz" and q.to_si() == 0.0}  # fmt: skip
            for n in mesh.groups[f.selection].node_ids.tolist():
                out.setdefault(int(n), set()).update(held)
    return out


def rows(comp, mesh, physics: set[str], planar: bool = False) -> list[list[tuple[int, int, float]]]:
    """The constraint equations, each [(node, dof, coefficient), ...], its dependent term
    first."""
    from puzzle.adapters.calculix.inp import _reduced

    dims = 2 if planar else 3
    held = held_at_zero(comp, mesh, planar)
    hot = prescribed_temperatures(comp, mesh) if "heat_transfer" in physics else set()
    out = []
    linked = [
        (c.rotation(), t, s) for c in comp.repeats() for t, s in pairs(comp, mesh, c).tolist()
    ]
    for r, t, s in linked:
        if "solid_mechanics" in physics:
            columns = [(t, d) for d in range(1, dims + 1)] + (
                [] if s == t else [(s, d) for d in range(1, dims + 1)]
            )
            free = [col for col in columns if col[1] not in held.get(col[0], set())]
            a = np.zeros((dims, len(free)))
            for i in range(dims):
                for j, (n, d) in enumerate(free):
                    if n == t:
                        a[i, j] += 1.0 if d == i + 1 else 0.0
                    if n == s:
                        a[i, j] -= r[i, d - 1]
            index = list(range(len(free)))
            for terms in _reduced(a, index):
                out.append([(free[j][0], free[j][1], coef) for j, coef in terms])
        if "heat_transfer" in physics and s != t:
            if t in hot and s in hot:
                continue  # both prescribed: nothing left to join
            # A prescribed temperature can't be the dependent term: the other side then is.
            out.append([(s, 11, 1.0), (t, 11, -1.0)] if t in hot else
                       [(t, 11, 1.0), (s, 11, -1.0)])  # fmt: skip
    return out


def prescribed_temperatures(comp, mesh) -> set[int]:
    """Nodes a temperature feature holds in every step."""
    return {
        int(n)
        for ph in comp.physics
        for f in ph.features
        if f.type == "temperature" and not f.steps and f.selection in mesh.groups
        for n in mesh.groups[f.selection].node_ids.tolist()
    }


def check(project, study, comp, caps) -> list[str]:
    """What a study with a cyclic or periodic component can't do here, in words."""
    if not comp.repeats():
        return []
    problems = []
    taken = getattr(caps, "cyclic", frozenset())
    for step in study.solve_steps() if comp.cyclic is not None else []:
        if step.type not in STEPS:
            problems.append(
                f"cyclic symmetry gives the sector's symmetric solution only, so a {step.type} "
                "step would miss the other nodal diameters; model the whole, or use "
                "stationary or transient steps"
            )
    for ph in comp.physics:
        if ph.type not in taken:
            where = (
                "CalculiX takes solid mechanics and heat, Code_Aster solid mechanics"
                if ph.type in PHYSICS
                else "only solid mechanics and heat take it"
            )
            what = "cyclic symmetry" if comp.cyclic is not None else "periodic symmetry"
            problems.append(f"{what} in {ph.type.replace('_', ' ')} is not written "
                            f"on this solver yet ({where})")  # fmt: skip
    return problems
