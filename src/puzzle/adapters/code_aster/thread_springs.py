"""Threaded contacts on Code_Aster (ADR 0076), from `adapters.thread_pairs`.

Per node pair and flank, along the flank's normal n (the screw's outward one):

- bonded and no_separation with the teeth's give: a helper node h whose DX is the
  pair's opening, DX_h = n · (u_nut - u_screw) (LIAISON_DDL, scaled by sqrt(k / k0)),
  its DY (DZ) held, and a point spring K_T_D_N = (k0, 0, 0) on it to ground. The
  spring acts along n whatever the nodes' places, as CalculiX's helper half a pitch out
  does, and needs no element between two coincident nodes (a SEG2 of zero length has
  no axis);
- bonded and no_separation `rigid`: LIAISON_DDL n · (u_screw - u_nut) = 0;
- frictionless: the same helper with DIS_CHOC (RIGI_NOR = k) in STAT_NON_LINE, DX_h
  the opening plus the clearance (COEF_IMPO): it closes as DX_h goes negative (the
  screw moving the clearance into the nut along n), and 1e-4 k in tension (`SLACK`),
  so the screw is held before a flank closes.

A 3D thread also has a `turn` spring per pair (`thread_pairs.TURN`, circumferential),
written as a linear flank whatever the contact's type: friction's hold against turning.

The relations are a charge of their own (`thload`), never ramped: the clearance is
there from the start.

A 2D axisymmetric model (AXIS) is per radian, as its nodal forces are: the ring's
stiffness is written over 2π.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from puzzle.mesh.mesh import Mesh

THREADS = frozenset({"bonded", "no_separation", "frictionless"})  # ADR 0076; not friction yet
FLANK = {"upper": "U", "lower": "L", "turn": "T"}  # turn: 3D, against turning
SLACK = 1e-4  # a unilateral flank's stiffness in tension, a fraction of k (as CalculiX's)


@dataclass
class _Flank:
    flank: str
    normals: np.ndarray  # (n, 3)
    stiffness: np.ndarray  # (n,) N/m as written (per radian in AXIS)
    unilateral: bool = False  # a frictionless flank (never the turn spring: linear)
    helpers: np.ndarray | None = None  # (n,) helper node ids; None when rigid


@dataclass
class ThreadSet:
    """One threaded contact as written: its pairs and, per flank, the helpers."""

    name: str
    screw: np.ndarray
    nut: np.ndarray
    clearance: float
    flanks: list[_Flank] = field(default_factory=list)


def helpers(project, comp, mesh: Mesh, base: Mesh) -> tuple[Mesh, list[ThreadSet]]:
    """`base` with a helper node per pair and flank (ids past its largest, at the screw
    node's place), and each threaded contact's pairs. -> (mesh, thread sets)"""
    from puzzle.adapters.thread_pairs import thread_pairs

    out: list[ThreadSet] = []
    ids, xyz = [base.node_ids], [base.coords]
    nxt = int(base.node_ids.max()) + 1
    scale = 1.0 / (2 * math.pi) if mesh.axisymmetric else 1.0
    for c in comp.contacts:
        if c.thread is None:
            continue
        tp = thread_pairs(project, comp, mesh, c)
        screw, nut = (tp.source, tp.target) if tp.screw_is_source else (tp.target, tp.source)
        ts = ThreadSet(c.name, np.asarray(screw, dtype=np.int64),
                       np.asarray(nut, dtype=np.int64), tp.clearance)  # fmt: skip
        for flank in tp.flanks:
            # A 3D thread's turn spring (thread_pairs.TURN) holds the screw from turning
            # (friction would): linear, and rigid with a rigid thread, as on CalculiX.
            unilateral = c.type == "frictionless" and flank != "turn"
            rigid = c.thread.compliance == "rigid" and not unilateral
            f = _Flank(flank, np.asarray(tp.normals[flank], dtype=float),
                       np.asarray(tp.stiffness[flank], dtype=float) * scale,
                       unilateral)  # fmt: skip
            if not rigid:
                f.helpers = np.arange(nxt, nxt + len(screw), dtype=np.int64)
                nxt += len(screw)
                ids.append(f.helpers)
                xyz.append(mesh.node_coords(ts.screw))
            ts.flanks.append(f)
        out.append(ts)
    if len(ids) == 1:
        return base, out
    from puzzle.adapters.code_aster.comm import _sorted_mesh

    return _sorted_mesh(np.concatenate(ids), np.vstack(xyz), list(base.blocks),
                        dict(base.groups)), out  # fmt: skip


@dataclass
class Written:
    """What the .comm gets from the threads: node groups to make, load items by
    keyword, AFFE_CARA_ELEM DISCRET items, DIS_CHOC sets with their RIGI_NOR."""

    node_groups: list[str] = field(default_factory=list)
    loads: dict[str, list[str]] = field(default_factory=dict)
    cara: list[str] = field(default_factory=list)
    gaps: list[tuple[str, float]] = field(default_factory=list)


def register(points, sets: list[ThreadSet], planar: bool) -> Written:
    """Point elements for the threads' nodes (each in its own group, as the relations
    name them) and helpers (also in one set per flank, which gets the discrete
    model), and what the .comm writes for them."""
    out = Written(loads={"LIAISON_DDL": [], "DDL_IMPO": []})
    axes = ("DX", "DY") if planar else ("DX", "DY", "DZ")
    zero = ", ".join(["0.0"] * (len(axes) - 1))
    for j, ts in enumerate(sets, start=1):
        group: dict[int, str] = {}

        def named(node: int, j=j, group=group) -> str:
            if node not in group:
                group[node] = f"Q{j}N{len(group) + 1}"
                points.add(group[node], node)
                out.node_groups.append(group[node])
            return group[node]

        screw = [named(int(n)) for n in ts.screw]
        nut = [named(int(n)) for n in ts.nut]
        for f in ts.flanks:
            letter = FLANK[f.flank]
            if f.helpers is None:  # rigid: n · (u_screw - u_nut) = 0
                for s, t, n in zip(screw, nut, f.normals, strict=True):
                    out.loads["LIAISON_DDL"].append(_relation(
                        [(s, a, float(n[k])) for k, a in enumerate(axes)]
                        + [(t, a, -float(n[k])) for k, a in enumerate(axes)]))  # fmt: skip
                continue
            # One stiffness k0 for the flank's set, each pair's own k through its
            # relation: DX_h = s (opening + clearance), s = sqrt(k / k0), so ½ k0 DX_h² =
            # ½ k (opening + clearance)². One discrete group per flank: a group per
            # stiffness (each pair's, in 3D) took Code_Aster minutes to assign.
            k0 = float(np.max(f.stiffness)) or 1.0
            scale = np.sqrt(np.clip(f.stiffness, 0.0, None) / k0)
            into = f"Q{j}K{letter}"
            gap = ts.clearance if f.unilateral else 0.0
            for i, (n, si) in enumerate(zip(f.normals, scale, strict=True)):
                h = f"Q{j}{letter}{i + 1}"
                points.add(h, int(f.helpers[i]), into)
                out.node_groups.append(h)
                # DX_h = s (n · (u_nut - u_screw) + the clearance): what is left open
                # along n (DIS_CHOC closes as it goes below 0; its DIST_1 narrows the
                # gap instead, an interference)
                out.loads["LIAISON_DDL"].append(_relation(
                    [(h, "DX", 1.0)]
                    + [(nut[i], a, -si * float(n[c])) for c, a in enumerate(axes)]
                    + [(screw[i], a, si * float(n[c])) for c, a in enumerate(axes)],
                    si * gap))  # fmt: skip
            held = ", ".join(f"{a}=0.0" for a in axes[1:])
            out.loads["DDL_IMPO"].append(f"_F(GROUP_MA='{into}', {held})")
            out.cara.append(f"_F(GROUP_MA='{into}', CARA='K_T_D_N', VALE=({k0:.10e}, {zero}))")
            if f.unilateral:
                out.gaps.append((into, k0))
                # SLACK k in tension too, on a second point element: with a clearance
                # (or exactly closed) nothing would hold the screw at the start, and
                # Newton did not converge (as CalculiX's law).
                slack = f"Q{j}S{letter}"
                for i in range(len(scale)):
                    points.add(f"Q{j}Z{letter}{i + 1}", int(f.helpers[i]), slack)
                out.cara.append(f"_F(GROUP_MA='{slack}', CARA='K_T_D_N', "
                                f"VALE=({SLACK * k0:.10e}, {zero}))")  # fmt: skip
    return out


def _relation(terms: list[tuple[str, str, float]], value: float = 0.0) -> str:
    """A LIAISON_DDL item: Σ coef × dof = value, terms with a negligible coefficient
    left out."""
    terms = [t for t in terms if abs(t[2]) > 1e-12]
    return (
        f"_F(GROUP_NO=({', '.join(repr(g) for g, _, _ in terms)},), "
        f"DDL=({', '.join(repr(d) for _, d, _ in terms)},), "
        f"COEF_MULT=({', '.join(f'{c:.12g}' for _, _, c in terms)},), COEF_IMPO={value:.12g})"
    )


def summary(c) -> str:
    """What the .comm gives a threaded contact, in words (ADR 0056)."""
    rigid = c.thread.compliance == "rigid" and c.type != "frictionless"
    if rigid:
        how = "LIAISON_DDL n · (u_screw − u_nut) = 0 per node pair"
    elif c.type == "frictionless":
        how = ("a helper node per pair whose DX is the opening along n (LIAISON_DDL), "
               "DIS_CHOC on it in STAT_NON_LINE (the teeth's stiffness, 1e-4 of it in "
               "tension)")  # fmt: skip
    else:
        how = ("a helper node per pair whose DX is the opening along n (LIAISON_DDL), a "
               "point spring K_T_D_N of the teeth's stiffness on it")  # fmt: skip
    flanks = "the bearing flank" if c.type == "bonded" else "both flanks"
    return (f"Thread ({c.thread.form}), {flanks}: {how}; in 3D a linear spring per pair "
            "against turning; a 2D axisymmetric model's stiffness per radian")  # fmt: skip
