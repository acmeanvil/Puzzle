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


# --- a beam bolt's tapped end (ADR 0076 step 4) ---------------------------------------


@dataclass
class BoltThread:
    """A beam bolt's tapped end as written, as CalculiX's `inp._thread_end`: the
    engaged core moves level by level rigidly with the beam's end node (u = u_m + θ × r)
    but for its own slide w along the axis (a slide node's DX), the slides chained by
    the core's axial springs E A_d3 / Δz (the mouth's level, toward the joint, has
    none), each wall node bearing on its level through a flank spring along the flank's
    normal, and the end's twist about the axis held by the RBE3's twist row."""

    index: int  # the fastener's number (F<i>)
    label: str  # its end, "A" or "B": the beam's end node E<i><label>
    walls: np.ndarray  # the thread face's nodes
    normals: np.ndarray  # (n, 3) the bearing flank's, the bolt's outward
    stiffness: np.ndarray  # (n,) flank springs (N/m)
    level_of: np.ndarray  # (n,) each wall node's level
    arms: np.ndarray  # (levels, 3) from the end node to each level's point on the axis
    axis: np.ndarray  # from the other end toward this one
    centre: np.ndarray  # the end's centre, where the beam's end node is
    core: np.ndarray  # (levels - 1,) the core's springs, level j - 1 to j
    twist: list[tuple[int, str, float]]  # the RBE3's twist row: Σ c u (wall dofs)
    slides: np.ndarray | None = None  # (levels - 1,) node ids, level 1 on
    cores: np.ndarray | None = None  # (levels - 1,) helper node ids
    flanks: np.ndarray | None = None  # (n,) helper node ids


def bolt_threads(project, comp, mesh: Mesh, fasteners) -> list[BoltThread]:
    """The beam bolts' tapped ends: a bolt with a thread whose end selection is the
    tapped thread `bolts.with_joints` made (" (thread)")."""
    from puzzle import threads
    from puzzle.adapters.common import fastener_ends, rbe3_rows
    from puzzle.adapters.thread_pairs import face_shares, modulus, normals
    from puzzle.model.schema import Material

    out = []
    for i, f in enumerate(fasteners, start=1):
        if f.formulation != "beam" or f.thread is None:
            continue
        a, b = fastener_ends(f, mesh, f"fastener '{f.name}'")
        for label, sel, end, other in (("A", f.source, a, b), ("B", f.target, b, a)):
            if not sel or not sel.endswith(" (thread)") or (label == "B" and f.ground):
                continue
            spec = threads.spec(f.thread)
            ids, areas, _n = face_shares(mesh.groups[sel], mesh)
            xyz = mesh.node_coords(ids)
            axis = (end.centre - other.centre) / np.linalg.norm(end.centre - other.centre)
            frame = threads.Frame(np.asarray(end.centre, dtype=float), axis)
            _r, _t, s = frame.cylindrical(xyz)
            # The bolt pulls this end back toward the other: -axis, the lower flank bears.
            load_up = f.thread.bearing == "other"
            flank = threads.bearing_flank(-1.0)
            n = normals(xyz, frame, spec, flank, load_up, planar=False)
            alpha = np.radians(threads.flank_angle(spec, flank, load_up))
            e_bolt = project.get(f.material, Material).properties["youngs_modulus"].to_si()
            k = threads.stiffness(
                areas / np.cos(alpha),
                spec,
                e_bolt,
                modulus(project, comp, sel),
                f.thread.compliance,
                rigid_core=True,
            )  # fmt: skip  (no core contraction)
            step = spec.pitch / 20  # levels closer than this share a core node
            levels = np.unique(np.round(s / step) * step)
            level_of = np.searchsorted(levels, np.round(s / step) * step)
            d3 = spec.major - 1.226869 * spec.pitch if spec.form in ("iso", "un") else spec.minor
            core_k = e_bolt * np.pi * d3**2 / 4
            core = core_k / np.maximum(np.diff(levels), 1e-9)
            arms = frame.origin + levels[:, None] * axis - end.centre  # the end node is there
            six = rbe3_rows(end, mesh)
            twist: dict[tuple[int, int], float] = {}
            for m in range(3):
                for nid, dof, c in six[3 + m]:
                    twist[(nid, dof)] = twist.get((nid, dof), 0.0) + axis[m] * c
            row = [(nid, ("DX", "DY", "DZ")[dof - 1], c)
                   for (nid, dof), c in twist.items() if abs(c) > 1e-14]  # fmt: skip
            out.append(BoltThread(i, label, np.asarray(ids, dtype=np.int64), n,
                                  np.asarray(k, dtype=float), level_of, arms, axis,
                                  np.asarray(end.centre, dtype=float), core, row))  # fmt: skip
    return out


def bolt_helpers(base: Mesh, ends: list[BoltThread]) -> Mesh:
    """`base` with each tapped end's slide, core-spring and flank-spring nodes, ids past
    its largest, at the end's centre (their places don't matter: the relations say
    what they follow)."""
    if not ends:
        return base
    from puzzle.adapters.code_aster.comm import _sorted_mesh

    nxt = int(base.node_ids.max()) + 1
    ids, xyz = [base.node_ids], [base.coords]
    for bt in ends:
        levels = len(bt.arms)
        for name, count in (("slides", levels - 1), ("cores", levels - 1),
                            ("flanks", len(bt.walls))):  # fmt: skip
            new = np.arange(nxt, nxt + count, dtype=np.int64)
            nxt += count
            setattr(bt, name, new)
            ids.append(new)
            xyz.append(np.repeat(bt.centre[None], count, axis=0))
    return _sorted_mesh(np.concatenate(ids), np.vstack(xyz), list(base.blocks),
                        dict(base.groups))  # fmt: skip


def register_bolts(points, ends: list[BoltThread], out: Written) -> None:
    """Groups and relations for the beam bolts' tapped ends (see `BoltThread`), added
    to `out`. Springs are point springs on helper nodes, one discrete group per kind
    with the stiffness through √(k / k0) in the relations, as the contacts' flanks."""
    axes = ("DX", "DY", "DZ")
    turns = ("DRX", "DRY", "DRZ")
    for e, bt in enumerate(ends, start=1):
        tag = f"QB{e}"
        master = f"E{bt.index}{bt.label}"
        group: dict[int, str] = {}

        def named(node: int, tag=tag, group=group) -> str:
            if node not in group:
                group[node] = f"{tag}N{len(group) + 1}"
                points.add(group[node], node)
                out.node_groups.append(group[node])
            return group[node]

        def spring_set(kind: str, nodes, k0: float, tag=tag) -> list[str]:
            into = f"{tag}K{kind}"
            names = []
            for i, node in enumerate(nodes, start=1):
                h = f"{tag}{kind}{i}"
                points.add(h, int(node), into)
                out.node_groups.append(h)
                names.append(h)
            out.loads["DDL_IMPO"].append(f"_F(GROUP_MA='{into}', DY=0.0, DZ=0.0)")
            out.cara.append(f"_F(GROUP_MA='{into}', CARA='K_T_D_N', VALE=({k0:.10e}, 0.0, 0.0))")
            return names

        slides: list[str] = []
        if len(bt.core):  # one level only: the core moves with the end node
            # The slides: dofs only (DX the slide), a negligible stiffness to have them
            # (1e-9 of the core's lost MUMPS nine digits: "singular", FACTOR_11).
            core0 = float(bt.core.max())
            slides = spring_set("W", bt.slides, 1e-6 * core0)
            cores = spring_set("C", bt.cores, core0)
            for j, (c, kj) in enumerate(zip(cores, bt.core, strict=True)):
                s = float(np.sqrt(kj / core0))  # DX_c = s (w_j - w_j-1); level 0's is 0
                terms = [(c, "DX", 1.0), (slides[j], "DX", -s)]
                if j:
                    terms.append((slides[j - 1], "DX", s))
                out.loads["LIAISON_DDL"].append(_relation(terms))
        k0 = float(bt.stiffness.max()) or 1.0
        flanks = spring_set("H", bt.flanks, k0)
        for h, wall, n, kk, lv in zip(flanks, bt.walls, bt.normals, bt.stiffness, bt.level_of,
                                      strict=True):  # fmt: skip
            # DX_h = s n · (u_level - u_wall), u_level = u_m + θ_m × r + w a.
            s = float(np.sqrt(max(kk, 0.0) / k0))
            arm = np.cross(bt.arms[int(lv)], n)  # n · (θ × r) = θ · (r × n)
            w = named(int(wall))
            terms = [(h, "DX", 1.0)]
            terms += [(master, a, -s * float(n[c])) for c, a in enumerate(axes)]
            terms += [(master, a, -s * float(arm[c])) for c, a in enumerate(turns)]
            if int(lv):
                terms.append((slides[int(lv) - 1], "DX", -s * float(n @ bt.axis)))
            terms += [(w, a, s * float(n[c])) for c, a in enumerate(axes)]
            out.loads["LIAISON_DDL"].append(_relation(terms))
        # The twist about the axis: the end node's turn = the wall's (the RBE3's row).
        terms = [(master, a, float(bt.axis[c])) for c, a in enumerate(turns)]
        terms += [(named(int(nid)), dof, -c) for nid, dof, c in bt.twist]
        out.loads["LIAISON_DDL"].append(_relation(terms))
