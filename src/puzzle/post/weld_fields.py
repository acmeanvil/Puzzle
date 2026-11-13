"""A light weld's results (ADR 0078 decision 2), after every solve, from the
displacements: adapters only write the springs (`adapters.weld_pairs`).

- `weld_line_force` (N/m, at the path's nodes): the line force the weld carries at each
  station, the force on the other part from the path's part over the station's share of
  the path, in the weld's frame: `along` the path, `throat_normal` (normal to the
  throat's plane, which holds the path and the bisector between the legs),
  `throat_shear` (in that plane, across the path), `magnitude`, and x, y, z; and
  `twist` (N·m/m), the moment per length it carries about the path itself: its pairs
  spread over the legs, so a fillet resists turning about its own line, as a bead
  does, which the codes' line-force checks leave out.
- `weld` (global, one row per light weld): the total force it carries (x, y, z), its
  largest line force `peak`, and the moment of its forces about the origin (mx, my,
  mz; forces at the path's part's nodes), for equilibrium.

What AISC's checks take is the line force (ADR 0078 decision 5): mesh-insensitive, no
peak stress at a toe or root.
"""

from __future__ import annotations

import numpy as np

from puzzle.results.model import Field

LINE_COMPONENTS = ["along", "throat_normal", "throat_shear", "magnitude", "x", "y", "z",
                   "twist"]  # fmt: skip
WELD_COMPONENTS = ["x", "y", "z", "peak", "mx", "my", "mz"]


def pair_forces(wp, u: Field) -> np.ndarray:
    """(pairs, 3) each pair's force on the other part's node (N)."""
    from puzzle.post.thread_fields import _vectors

    return wp.k * (_vectors(u, wp.a) - _vectors(u, wp.b))


def line_forces(wp, u: Field) -> np.ndarray:
    """(stations, 3) the line force at each station (N/m, global axes)."""
    total = np.zeros((len(wp.stations), 3))
    np.add.at(total, wp.station_of, pair_forces(wp, u))
    return total / np.maximum(wp.widths, 1e-300)[:, None]


def twists(wp, u: Field) -> np.ndarray:
    """(stations,) the moment per length about the path at each station (N·m/m)."""
    f = pair_forces(wp, u)
    arm = wp.xa - wp.xyz[wp.station_of]
    about = np.einsum("ij,ij->i", np.cross(arm, f), wp.tangent[wp.station_of])
    total = np.zeros(len(wp.stations))
    np.add.at(total, wp.station_of, about)
    return total / np.maximum(wp.widths, 1e-300)


def in_frame(wp, line: np.ndarray, twist: np.ndarray | None = None) -> np.ndarray:
    """The line forces as LINE_COMPONENTS rows."""
    t, m = wp.tangent, wp.bisector
    q = np.cross(t, m)
    rows = np.column_stack([
        np.einsum("ij,ij->i", line, t),
        np.einsum("ij,ij->i", line, q),
        np.einsum("ij,ij->i", line, m),
        np.linalg.norm(line, axis=1),
        line,
        np.zeros(len(line)) if twist is None else twist,
    ])  # fmt: skip
    return rows


def finish(results, project, comp, mesh) -> None:
    """Add the weld fields to each step of `results` with a displacement."""
    welds = [w for w in getattr(comp, "welds", []) if w.formulation == "line"]
    beads = [w for w in getattr(comp, "welds", []) if w.formulation == "modelled"]
    if not welds and not beads:
        return
    from puzzle.adapters.weld_pairs import weld_pairs

    pairs = []
    for w in welds:
        try:
            pairs.append(weld_pairs(project, comp, mesh, w))
        except Exception:  # noqa: BLE001 - results are extras: a run never fails on them
            continue
    if not pairs and not beads:
        return
    for step in results.steps:
        u = step.fields.get("displacement")
        if u is None or u.location != "node":
            continue
        ids, rows, totals = [], [], []
        for wp in pairs:
            line = line_forces(wp, u)
            ids.append(wp.stations)
            rows.append(in_frame(wp, line, twists(wp, u)))
            f = pair_forces(wp, u)
            moment = np.cross(wp.xa, f).sum(axis=0)
            totals.append([*f.sum(axis=0), float(np.linalg.norm(line, axis=1).max()), *moment])
        stress = step.fields.get("stress")
        for w in beads:  # ADR 0078 step 4: from the section through the bead's throat
            try:
                got = None
                if stress is not None:
                    got = (groove(mesh, stress, w) if w.type == "groove"
                           else modelled(project, mesh, stress, comp, w))  # fmt: skip
            except Exception:  # noqa: BLE001 - as above
                got = None
            if got is None:
                continue
            root, line_rows, force, moment = got
            ids.append(root)
            rows.append(line_rows)
            totals.append([*force, float(line_rows[:, 3].max()), *moment])
        if not totals:
            continue
        step.fields["weld_line_force"] = Field(
            "weld_line_force", "node", LINE_COMPONENTS, np.concatenate(ids), np.vstack(rows)
        )
        step.fields["weld"] = Field(
            "weld", "global", WELD_COMPONENTS, np.arange(1, len(totals) + 1), np.array(totals)
        )


def _strength(project, comp, w, rows, c, wp, directional) -> dict:
    """The weld's AISC 360 J2 check at its stations, or why there is none."""
    from puzzle import weld_strength

    try:
        name, fexx, code = weld_strength.filler(project, comp, w)
    except ValueError as exc:
        return {"checked": False, "why": str(exc)}
    across = np.hypot(rows[:, c["throat_normal"]], rows[:, c["throat_shear"]])
    if code == weld_strength.ADM:  # ADR 0078 step 6: aluminium by the ADM's J.2
        try:
            base = weld_strength.welded_base_strength(project, comp, w)
        except ValueError as exc:
            return {"checked": False, "why": str(exc)}
        got = weld_strength.check_aluminium(w, fexx, base, rows[:, c["along"]], across,
                                            rows[:, c["magnitude"]], directional)  # fmt: skip
        if not got["checked"]:
            return got
        at = got["governing"]
        nominal = float(got["nominal_N_per_m"][at])
        return {"checked": True, "code": code, "clause": got["clause"], "filler": name,
                "F_tuw_filler_Pa": fexx, "F_tuw_base_Pa": base, "throat_m": got["throat_m"],
                "governed_by": got["governed_by"],
                "design_strength_lrfd_N_per_m": weld_strength.ADM_PHI * nominal,
                "allowable_strength_asd_N_per_m": nominal / weld_strength.ADM_OMEGA,
                "utilisation_lrfd": float(got["utilisation_lrfd"][at]),
                "utilisation_asd": float(got["utilisation_asd"][at]),
                "governing_at_m": [float(v) for v in wp.xyz[at]]}  # fmt: skip
    got = weld_strength.check(w, fexx, rows[:, c["along"]], across, rows[:, c["magnitude"]],
                              directional)  # fmt: skip
    if not got["checked"]:
        return got
    at = got["governing"]
    return {
        "checked": True, "code": code, "clause": got["clause"], "filler": name,
        "F_EXX_Pa": fexx, "throat_m": got["throat_m"],
        "design_strength_lrfd_N_per_m": float(weld_strength.PHI * got["nominal_N_per_m"][at]),
        "allowable_strength_asd_N_per_m": float(got["nominal_N_per_m"][at] / weld_strength.OMEGA),
        "utilisation_lrfd": float(got["utilisation_lrfd"][at]),
        "utilisation_asd": float(got["utilisation_asd"][at]),
        "governing_at_m": [float(v) for v in wp.xyz[at]],
        "instantaneous_centre": _ic(w, fexx, got["throat_m"], rows, c, wp),
        "not_checked": "the base metal along the weld (J4) and Table J2.4's minimum size: "
        "they need the plates' thicknesses",
    }  # fmt: skip


def _ic(w, fexx, te, rows, c, wp) -> dict:
    """The group's in-plane strength by the instantaneous centre of rotation (AISC 360-10
    J2.4(b), the Manual's Part 8 tables' method) against its solved in-plane resultant:
    the force and moment about its centroid, in the plane fitted to its stations."""
    from puzzle import weld_strength

    if w.type != "fillet":
        return {"checked": False, "why": "fillet welds only"}
    xyz = np.asarray(wp.xyz, float)
    if len(xyz) < 3:
        return {"checked": False, "why": "too few stations"}
    centroid = np.average(xyz, axis=0, weights=wp.widths)
    spread = np.cov((xyz - centroid).T, aweights=wp.widths)
    values, vectors = np.linalg.eigh(spread)
    normal = vectors[:, 0]
    size = float(np.max(np.linalg.norm(xyz - centroid, axis=1))) or 1.0
    if values[0] > (1e-3 * size) ** 2:
        return {"checked": False, "why": "the weld's path isn't planar: the instantaneous "
                "centre method is for groups loaded in their plane"}  # fmt: skip
    e1, e2 = vectors[:, 2], vectors[:, 1]
    f = rows[:, [c["x"], c["y"], c["z"]]] * wp.widths[:, None]  # N at each station
    force = f.sum(axis=0)
    moments = np.sum(np.cross(xyz - centroid, f), axis=0)
    moment = float(moments @ normal)
    bending = float(np.linalg.norm(moments - moment * normal))
    inplane = force - (force @ normal) * normal
    pts = np.column_stack([(xyz - centroid) @ e1, (xyz - centroid) @ e2])
    tangent = np.column_stack([wp.tangent @ e1, wp.tangent @ e2])
    tangent /= np.linalg.norm(tangent, axis=1, keepdims=True)
    leg = min(w.leg.to_si(), w.leg2.to_si() if w.leg2 is not None else w.leg.to_si())
    magnitude = float(np.linalg.norm(inplane))
    out = {"checked": True, "in_plane_force_N": magnitude, "moment_N_m": moment,
           "out_of_plane_force_N": float(force @ normal), "out_of_plane_moment_N_m": bending,
           "clause": "AISC 360-10 J2.4(b) (360-16 J2.4's user note, Manual Part 8)",
           **({"note": "the group is also bent out of its plane, which the instantaneous "
               "centre leaves out: the stations' J2 check above covers it"}
              if bending > 1e-3 * abs(moment) + 1e-9 else {})}  # fmt: skip
    try:
        if magnitude < 1e-9 * max(abs(moment) / size, 1.0):  # pure moment
            mn = weld_strength.ic_moment_capacity(pts, tangent, wp.widths, fexx, te, leg)
            out |= {"Mn_N_m": mn, "utilisation_lrfd": abs(moment) / (weld_strength.PHI * mn),
                    "utilisation_asd": abs(moment) * weld_strength.OMEGA / mn}  # fmt: skip
            return out
        u2 = np.array([inplane @ e1, inplane @ e2]) / magnitude
        # a point on the load's line: r × F = M n, r = (F × n) M / |F|², in the plane
        r3 = np.cross(inplane, normal) * moment / magnitude**2
        line = np.array([r3 @ e1, r3 @ e2])
        got = weld_strength.ic_capacity(pts, tangent, wp.widths, fexx, te, leg, u2, line)
    except ValueError as exc:
        return {"checked": False, "why": str(exc)}
    pn = got["Pn_N"]
    return out | {"Pn_N": pn, "eccentricity_m": abs(moment) / magnitude,
                  "utilisation_lrfd": magnitude / (weld_strength.PHI * pn),
                  "utilisation_asd": magnitude * weld_strength.OMEGA / pn,
                  "method": got["method"]}  # fmt: skip


def summary(results, comp, step=None) -> list[dict]:
    """Per light weld at a step (the last by default): its total force (N), its largest
    line force (N/m) and where along it, for tables and the agent."""
    steps = [s for s in results.steps if "weld" in s.fields]
    if not steps:
        return []
    chosen = steps[-1] if step is None else steps[step]
    g = chosen.fields["weld"]
    welds = [w for w in comp.welds if w.formulation == "line"]
    welds += [w for w in comp.welds if w.formulation == "modelled"]
    return [
        {"weld": w.name, "force": [float(v) for v in g.values[i, :3]],
         "peak_line_force": float(g.values[i, 3])}
        for i, w in enumerate(welds[: len(g.values)])
    ]  # fmt: skip


def results(project, comp, mesh, res, weld: str | None = None, step: int | None = None,
            directional: bool = False) -> dict:  # fmt: skip
    """Each light weld's forces in a run's step with weld results (the last by default,
    1-based among them): its total force (N), largest line force (N/m) and where, mean
    line force, welded length, its stations' line forces in its own frame, and its
    strength by AISC 360 J2 (`weld_strength`; `directional` takes Eq. J2-5's increase)."""
    from puzzle.adapters.weld_pairs import weld_pairs
    from puzzle.errors import PuzzleError

    steps = [s for s in res.steps if "weld_line_force" in s.fields]
    if not steps:
        raise PuzzleError("this run has no weld results: no light weld in it",
                          code="not_found")  # fmt: skip
    k = len(steps) if step is None else step
    if not 1 <= k <= len(steps):
        raise PuzzleError(f"step {k}: the run has {len(steps)} steps with weld results",
                          code="not_found")  # fmt: skip
    chosen = steps[k - 1]
    line = chosen.fields["weld_line_force"]
    c = {name: i for i, name in enumerate(line.components)}
    order = np.argsort(line.ids)
    welds = [w for w in comp.welds if w.formulation in ("line", "modelled")]
    if weld is not None:
        welds = [w for w in welds if w.name == weld]
        if not welds:
            raise PuzzleError(f"no weld '{weld}' in {comp.name}", code="not_found")
    out = []
    for w in welds:
        wp = (weld_pairs(project, comp, mesh, w) if w.formulation == "line"
              else groove_stations(mesh, w) if w.type == "groove"
              else bead_stations(project, comp, mesh, w))  # fmt: skip
        if wp is None:
            continue
        rows = line.values[order[np.searchsorted(line.ids, wp.stations, sorter=order)]]
        size = rows[:, c["magnitude"]]
        at = int(np.argmax(size))
        out.append({
            "weld": w.name,
            "formulation": w.formulation,
            "length_m": wp.length,
            "force_N": [float(v) for v in (rows[:, [c["x"], c["y"], c["z"]]]
                                           * wp.widths[:, None]).sum(axis=0)],
            "peak_line_force_N_per_m": float(size[at]),
            "peak_at_m": [float(v) for v in wp.xyz[at]],
            "mean_line_force_N_per_m": float(np.average(size, weights=wp.widths)),
            "strength": _strength(project, comp, w, rows, c, wp, directional),
            "stations": [
                {"s_m": float(s), **{name: float(r[c[name]]) for name in LINE_COMPONENTS}}
                for s, r in zip(wp.s, rows, strict=True)
            ][:200],
        })  # fmt: skip
    return {"step": k, "welds": out}


def fatigue(project, comp, mesh, res, weld: str, category: str = "F", cycles: float = 2e6,
            steps: tuple[int, ...] = ()) -> dict:  # fmt: skip
    """A light weld's fatigue check by AISC 360-16 Appendix 3 (ADR 0078 step 5): the
    stress range between two steps with weld results (1-based; one step: from zero,
    the last by default) against F_SR(n) of the detail's category.

    - Category F (Table A-3.1 Case 8.2, shear on the throat of fillet welds): the line
      force's range |Δq| over the effective throat, at every station.
    - Categories A to E′ (the base metal at the weld's toe, as Table A-3.1 classes the
      detail): the largest principal stress range at the weld's footprint nodes, from
      the solid's nodal stress; mesh-dependent, as any hot-spot read of a solid is."""
    from puzzle import weld_strength
    from puzzle.adapters.weld_pairs import weld_pairs
    from puzzle.errors import PuzzleError

    found = [s for s in res.steps if "weld_line_force" in s.fields]
    if not found:
        raise PuzzleError("this run has no weld results", code="not_found")
    chosen = list(steps) or [len(found)]
    if len(chosen) > 2 or any(not 1 <= k <= len(found) for k in chosen):
        raise PuzzleError(f"steps: one or two of 1..{len(found)}", code="invalid")
    w = next((x for x in comp.welds if x.name == weld and x.formulation == "line"), None)
    if w is None:
        raise PuzzleError(f"no light weld '{weld}' in {comp.name}", code="not_found")
    try:
        allowed = weld_strength.allowable_range(category, cycles)
    except ValueError as exc:
        raise PuzzleError(str(exc), code="invalid") from None
    wp = weld_pairs(project, comp, mesh, w)
    out = {"weld": w.name, "category": category, "cycles": cycles,
           "allowable_range_Pa": allowed, "steps": chosen,
           "clause": "AISC 360-16 Appendix 3, Eq. A-3-2" if category == "F"
           else "AISC 360-16 Appendix 3, Eq. A-3-1"}  # fmt: skip
    if category == "F":

        def line(k):
            f = found[k - 1].fields["weld_line_force"]
            c = {n: i for i, n in enumerate(f.components)}
            order = np.argsort(f.ids)
            rows = f.values[order[np.searchsorted(f.ids, wp.stations, sorter=order)]]
            return rows[:, [c["x"], c["y"], c["z"]]]

        delta = line(chosen[0]) - (line(chosen[1]) if len(chosen) == 2 else 0.0)
        te = weld_strength.throat(w)
        ranges = np.linalg.norm(delta, axis=1) / te
        at = int(np.argmax(ranges))
        return out | {"stress_range_Pa": float(ranges[at]), "ratio": float(ranges[at] / allowed),
                      "at_m": [float(v) for v in wp.xyz[at]],
                      "where": "the throat (shear)"}  # fmt: skip
    nodes = np.unique(np.concatenate([wp.a, wp.b]))
    if not all("stress" in found[k - 1].fields for k in chosen):
        raise PuzzleError("a toe category needs the solid's stress in the run", code="invalid")

    def tensor(k):
        f = found[k - 1].fields["stress"]
        order = np.argsort(f.ids)
        at = np.searchsorted(f.ids, nodes, sorter=order)
        ok = at < len(order)
        rows = np.zeros((len(nodes), 6))
        rows[ok] = f.values[order[at[ok]]]
        return rows

    d = tensor(chosen[0]) - (tensor(chosen[1]) if len(chosen) == 2 else 0.0)
    full = np.array([[[r[0], r[3], r[5]], [r[3], r[1], r[4]], [r[5], r[4], r[2]]] for r in d])
    principal = np.abs(np.linalg.eigvalsh(full)).max(axis=1)
    k = int(np.argmax(principal))
    return out | {"stress_range_Pa": float(principal[k]), "ratio": float(principal[k] / allowed),
                  "at_m": [float(v) for v in mesh.node_coords(nodes[[k]])[0]],
                  "where": "the base metal at the weld's footprint (the largest principal "
                  "stress range; mesh-dependent)"}  # fmt: skip


def structural_stress(project, comp, mesh, res, weld: str, steps: tuple[int, ...] = (),
                      thickness: float | None = None, cycles: float | None = None,
                      **options) -> dict:  # fmt: skip
    """A light weld's fatigue at the attachment's toe by ASME VIII-2 5.5.5 (ADR 0078 step
    5): the structural stress normal to the crack plane through the path's part at the
    weld, from the weld's own line forces (they are the nodal forces the attachment
    hands the weld, so mesh-insensitive as the method wants): membrane f/t, f the line
    force along the attachment (in its plane, normal to the path, + pulling away), and
    bending 6 m/t², m the moment about the path (`twist`). Their ranges between two
    steps (or from zero) at each station go through `weld_strength.asme_cycles`;
    `thickness` is the attachment's (a shell's section by default). With `cycles`, the
    damage n/N at the worst station."""
    from puzzle import weld_strength
    from puzzle.adapters.weld_pairs import weld_pairs
    from puzzle.errors import PuzzleError

    found = [s for s in res.steps if "weld_line_force" in s.fields]
    if not found:
        raise PuzzleError("this run has no weld results", code="not_found")
    chosen = list(steps) or [len(found)]
    if len(chosen) > 2 or any(not 1 <= k <= len(found) for k in chosen):
        raise PuzzleError(f"steps: one or two of 1..{len(found)}", code="invalid")
    w = next((x for x in comp.welds if x.name == weld), None)
    if w is None:
        raise PuzzleError(f"no weld '{weld}' in {comp.name}", code="not_found")
    part = w.path.split("/", 1)[0]
    if thickness is None:
        section = next((s for s in comp.shell_sections
                        if s.selection.split("/", 1)[0] == part), None)  # fmt: skip
        if section is None:
            raise PuzzleError(f"give the thickness of {part} at the toe (it is no shell)",
                              code="invalid")  # fmt: skip
        thickness = section.thickness.to_si()
    if w.formulation == "modelled":  # a bead's leg A, or a groove's joint, into the part
        wp = (groove_stations(mesh, w) if w.type == "groove"
              else bead_stations(project, comp, mesh, w))  # fmt: skip
        if wp is None:
            raise PuzzleError(f"weld '{weld}' isn't in this run's mesh", code="not_found")
        into = wp.da
    else:
        wp = weld_pairs(project, comp, mesh, w)
        # Into the attachment: the pairs' arms on the path's part, square to the path.
        arm = wp.xa - wp.xyz[wp.station_of]
        into = np.zeros_like(wp.xyz)
        np.add.at(into, wp.station_of, arm)
        into -= np.einsum("ij,ij->i", into, wp.tangent)[:, None] * wp.tangent
        into /= np.maximum(np.linalg.norm(into, axis=1, keepdims=True), 1e-300)

    def at(k):
        f = found[k - 1].fields["weld_line_force"]
        c = {n: i for i, n in enumerate(f.components)}
        order = np.argsort(f.ids)
        rows = f.values[order[np.searchsorted(f.ids, wp.stations, sorter=order)]]
        line = rows[:, [c["x"], c["y"], c["z"]]]
        membrane = np.einsum("ij,ij->i", line, into) / thickness
        twist = rows[:, c["twist"]]
        if w.formulation == "modelled" and w.type == "groove":
            # the path runs along the plate's surface: the moment about its mid-plane
            # is the path's less the line force's across the half depth
            twist = twist - np.einsum("ij,ij->i", np.cross(wp.depth_dir * w.leg.to_si() / 2,
                                                           line), wp.tangent)  # fmt: skip
        bending = 6 * twist / thickness**2
        return membrane, bending

    m1, b1 = at(chosen[0])
    m2, b2 = at(chosen[1]) if len(chosen) == 2 else (np.zeros_like(m1), np.zeros_like(b1))
    stations = []
    for i in range(len(wp.stations)):
        top = [m1[i] + abs(b1[i]), m2[i] + abs(b2[i])]
        row = weld_strength.asme_cycles(m1[i] - m2[i], b1[i] - b2[i], thickness,
                                        smax=max(top), smin=min(top), **options)  # fmt: skip
        stations.append({"at_m": [float(v) for v in wp.xyz[i]],
                         "membrane_range_Pa": float(m1[i] - m2[i]),
                         "bending_range_Pa": float(b1[i] - b2[i]), **row})  # fmt: skip
    worst = min(range(len(stations)), key=lambda i: stations[i]["cycles"])
    out = {"weld": w.name, "steps": chosen, "thickness_m": float(thickness),
           "where": f"the toe on {part}", "clause": weld_strength.ASME,
           "worst": stations[worst], "stations": stations}  # fmt: skip
    if cycles is not None:
        out["cycles"] = float(cycles)
        out["damage"] = float(cycles / stations[worst]["cycles"])
    return out


def _unit(v):
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return np.divide(v, n, out=np.zeros_like(v), where=n > 0)


def _pieces(project, comp, weld) -> list[dict]:
    """A modelled bead's `weld_bead` steps as pieces of its path: the polyline (closed
    back to its start), its cumulative lengths, where it starts along the weld (`s0`),
    the legs at each point and the root's cut-back."""
    from puzzle.model.schema import Geometry

    part = next((p for p in comp.parts if p.name == f"{weld.name} bead"), None)
    geo = project.objects.get(part.geometry) if part is not None else None
    if not isinstance(geo, Geometry):
        return []
    out = []
    for op in geo.history:
        if op.kind != "weld_bead":
            continue
        a = op.args
        pts = np.asarray(a["points"], float)
        n = len(pts)
        va = np.asarray(a["a"], float).reshape(-1, 3)
        vb = np.asarray(a["b"], float).reshape(-1, 3)
        va, vb = (np.repeat(v, n, axis=0) if len(v) == 1 else v for v in (va, vb))
        if a.get("closed"):
            pts, va, vb = (np.vstack([x, x[:1]]) for x in (pts, va, vb))
        cum = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))])
        out.append({"poly": pts, "cum": cum, "s0": float(a.get("s0", 0.0)), "a": va, "b": vb,
                    "root": float(a.get("root", 0.0)),
                    "closed": bool(a.get("closed"))})  # fmt: skip
    return out


def _clipped(a, b, points):
    """Each point's nearest point on the segment a-b."""
    ab = b - a
    t = np.clip((points - a) @ ab / max(float(ab @ ab), 1e-300), 0.0, 1.0)
    return a + np.outer(t, ab)


def _legs_at(piece, k: int, f):
    """The leg directions on segment k at fraction f along it, square to it."""
    t = _unit(piece["poly"][k + 1] - piece["poly"][k])
    a = (1 - f) * piece["a"][k] + f * piece["a"][k + 1]
    b = (1 - f) * piece["b"][k] + f * piece["b"][k + 1]
    da, db = _unit(a - (a @ t) * t), _unit(b - (b @ t) * t)
    return t, da, db


def bead_stations(project, comp, mesh, weld):
    """A modelled weld's stations (ADR 0078 step 4): the bead's root nodes (on its root
    face's edge along leg A, or where its legs meet) in order along its path, with what
    the checks read off a light weld's pairs (xyz, s, widths, tangent, bisector, length)
    and its pieces for the throat cuts; None when the bead isn't meshed."""
    from types import SimpleNamespace

    from puzzle.adapters.weld_pairs import _project

    part = f"{weld.name} bead"
    body, la, lb = (mesh.groups.get(n) for n in (part, f"{part}/Leg A", f"{part}/Leg B"))
    pieces = _pieces(project, comp, weld)
    if body is None or la is None or lb is None or not pieces:
        return None
    gap = mesh.groups.get(f"{part}/Root")
    root = np.intersect1d(la.node_ids if gap is None else gap.node_ids,
                          lb.node_ids if gap is None else la.node_ids).astype(np.int64)  # fmt: skip
    if len(root) < 2:
        return None
    rx = mesh.node_coords(root)
    fits = [_project(rx, pc["poly"], pc["cum"]) for pc in pieces]
    which = np.argmin(np.stack([d for _s, d, _n in fits]), axis=0)
    s = np.array([pieces[w]["s0"] + fits[w][0][i] for i, w in enumerate(which)])
    order = np.argsort(s)
    root, rx, s, which = root[order], rx[order], s[order], which[order]
    n = len(root)
    widths = np.zeros(n)
    tangent, da, db = np.zeros((n, 3)), np.zeros((n, 3)), np.zeros((n, 3))
    for w, pc in enumerate(pieces):
        idx = np.flatnonzero(which == w)
        if not len(idx):
            continue
        lo, hi = pc["s0"], pc["s0"] + pc["cum"][-1]
        local = s[idx]
        if pc["closed"]:  # the first and last stations share the seam
            nxt, prv = np.roll(local, -1), np.roll(local, 1)
            nxt[-1] += hi - lo
            prv[0] -= hi - lo
            widths[idx] = (nxt - prv) / 2
        else:
            edges = np.concatenate([[lo], (local[1:] + local[:-1]) / 2, [hi]])
            widths[idx] = np.diff(edges)
        for i in idx:
            sl = s[i] - pc["s0"]
            k = min(max(int(np.searchsorted(pc["cum"], sl, side="right")) - 1, 0),
                    len(pc["cum"]) - 2)  # fmt: skip
            f = (sl - pc["cum"][k]) / max(pc["cum"][k + 1] - pc["cum"][k], 1e-300)
            tangent[i], da[i], db[i] = _legs_at(pc, k, min(max(f, 0.0), 1.0))
    bisector = _unit(da + db)
    return SimpleNamespace(stations=root, xyz=rx, s=s, widths=widths, tangent=tangent,
                           bisector=bisector, da=da, db=db, which=which, pieces=pieces,
                           length=float(sum(pc["cum"][-1] for pc in pieces)),
                           closed=len(pieces) == 1 and pieces[0]["closed"])  # fmt: skip


def modelled(project, mesh, stress, comp, weld):
    """A modelled weld's line forces (ADR 0078 step 4) from sections through its throat:
    per straight segment of its path, the plane holding the segment's root line and the
    bisector between its legs, cut through the bead's own elements, the triangles kept
    that lie on that segment. Each triangle's traction force (from the leg-A side onto
    the leg-B side, the other part's: the force on the other part, as a light weld's)
    and its moment about the root line go to the nearest station along the path.
    Returns (station ids, rows of LINE_COMPONENTS, the total force, its moment about the
    origin), or None when the bead isn't in this mesh."""
    from puzzle.errors import PuzzleError
    from puzzle.mesh import Mesh
    from puzzle.post.probes import _cut
    from puzzle.results.model import StepResult

    frame = bead_stations(project, comp, mesh, weld)
    if frame is None:
        return None
    part = f"{weld.name} bead"
    body = mesh.groups[part]
    blocks = [b for b in body.blocks(mesh) if b.dim == 3]
    own = Mesh(mesh.node_ids, mesh.coords, blocks, {part: body})
    step = StepResult(0, "stationary", 0.0, None, {"stress": stress})
    names = ("xx", "yy", "zz", "xy", "yz", "zx")
    n = len(frame.stations)
    total, turning = np.zeros((n, 3)), np.zeros(n)
    force, moment = np.zeros(3), np.zeros(3)
    reach = 3 * max(np.linalg.norm(pc["a"], axis=1).max() for pc in frame.pieces)
    for w, pc in enumerate(frame.pieces):
        mine = np.flatnonzero(frame.which == w)
        if not len(mine):
            continue
        for k in range(len(pc["cum"]) - 1):
            t, da, db = _legs_at(pc, k, 0.5)
            m = _unit(da + db)
            q = _unit(np.cross(t, m))
            if q @ da < 0:
                q = -q
            origin = (pc["poly"][k] + pc["poly"][k + 1]) / 2 + pc["root"] * (da + db) / 2
            try:
                cut, faces, areas, centres, nn = _cut(own, step, origin, q)
            except PuzzleError:
                continue
            # The triangles on this segment: between the mitre planes at its two corners
            # (normal to the sum of the tangents there: the two segments' throat planes,
            # each the other's mirror in it, meet on it, so the cuts close up), or within
            # an open piece's ends (the plane runs on to an intermittent weld's next
            # pieces on its line); and near its root line.
            along = (centres - pc["poly"][k]) @ t
            span = pc["cum"][k + 1] - pc["cum"][k]
            near = pc["poly"][k] + np.outer(np.clip(along, 0.0, span), t)
            on = np.linalg.norm(centres - near, axis=1) < reach
            count = len(pc["cum"]) - 1
            for corner, other, before in ((k, k - 1, True), (k + 1, k + 1, False)):
                if pc["closed"] or 0 < corner < count:
                    tj = _unit(pc["poly"][other % count + 1] - pc["poly"][other % count])
                    side = (centres - pc["poly"][corner % count]) @ (t + tj)
                    on &= side >= 0 if before else side <= 0
                else:
                    on &= along >= -1e-9 * span if before else along <= span * (1 + 1e-9)
            sl = pc["cum"][k] + np.clip(along, 0.0, span)
            if not on.any():
                continue
            sv = np.asarray(cut.point_data["stress"], float).reshape(cut.n_points, -1)
            xx, yy, zz, xy, yz, zx = (sv[:, stress.components.index(c)] for c in names)
            traction = np.stack([xx * nn[0] + xy * nn[1] + zx * nn[2],
                                 xy * nn[0] + yy * nn[1] + yz * nn[2],
                                 zx * nn[0] + yz * nn[1] + zz * nn[2]], axis=1)  # fmt: skip
            # sigma n, n toward leg A: the force on the leg-B side (the other part's)
            f = (traction[faces].mean(axis=1) * areas[:, None])[on]
            c = centres[on]
            twist = np.einsum("ij,j->i", np.cross(c - near[on], f), t)
            gs = pc["s0"] + sl[on]
            nearest = mine[np.argmin(np.abs(gs[:, None] - frame.s[mine][None, :]), axis=1)]
            np.add.at(total, nearest, f)
            np.add.at(turning, nearest, twist)
            force += f.sum(axis=0)
            moment += np.cross(c, f).sum(axis=0)
    width = np.maximum(frame.widths, 1e-300)
    line = total / width[:, None]
    q = np.cross(frame.tangent, frame.bisector)
    rows = np.column_stack([
        np.einsum("ij,ij->i", line, frame.tangent), np.einsum("ij,ij->i", line, q),
        np.einsum("ij,ij->i", line, frame.bisector), np.linalg.norm(line, axis=1), line,
        turning / width,
    ])  # fmt: skip
    return frame.stations, rows, force, moment


def groove_stations(mesh, weld):
    """A modelled groove weld's stations: its path's nodes in order along it, with the
    joint's plane (fitted to the path's part's face) and `da` from the joint into the
    path's part; None when its faces aren't meshed."""
    from types import SimpleNamespace

    from puzzle.adapters.weld_pairs import _chain, _project

    path, face = mesh.groups.get(weld.path), mesh.groups.get(weld.faces[0])
    other = mesh.groups.get(weld.faces[1].split("/", 1)[0])
    if path is None or face is None or other is None:
        return None
    corners, closed = _chain(path, mesh)
    poly = mesh.node_coords(corners)
    if closed:
        poly = np.vstack([poly, poly[:1]])
    cum = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(poly, axis=0), axis=1))])
    nodes = np.unique(path.node_ids.astype(np.int64))
    xyz = mesh.node_coords(nodes)
    s, _d, _near = _project(xyz, poly, cum)
    order = np.argsort(s)
    nodes, xyz, s = nodes[order], xyz[order], s[order]
    if closed and len(s) > 1 and cum[-1] - s[-1] < 1e-9 * cum[-1]:
        nodes, xyz, s = nodes[:-1], xyz[:-1], s[:-1]
    fx = mesh.node_coords(face.node_ids)
    centre = fx.mean(axis=0)
    normal = np.linalg.svd(fx - centre)[2][-1]
    toward = mesh.node_coords(other.node_ids).mean(axis=0) - centre
    if normal @ toward < 0:
        normal = -normal  # out of the path's part, toward the other
    n = len(nodes)
    tangent = np.zeros((n, 3))
    for i in range(n):
        k = min(max(int(np.searchsorted(cum, s[i], side="right")) - 1, 0), len(cum) - 2)
        tangent[i] = _unit(poly[k + 1] - poly[k])
    edges = (s[1:] + s[:-1]) / 2
    widths = np.diff(np.concatenate([[0.0], edges, [cum[-1]]]))
    if weld.pattern == "intermittent":
        welded = sum(min(weld.segment.to_si(), cum[-1] - k * weld.pitch.to_si())
                     for k in range(int(cum[-1] // weld.pitch.to_si()) + 1)
                     if k * weld.pitch.to_si() < cum[-1])  # fmt: skip
    else:
        welded = float(cum[-1])
    into = np.tile(-normal, (n, 1))
    # down the joint from the path: in the joint's plane, square to the path, toward
    # the middle of the path's part's face
    down = np.cross(normal, tangent)
    down *= np.sign(np.einsum("ij,j->i", down, centre - xyz.mean(axis=0)) + 1e-300)[:, None]
    return SimpleNamespace(stations=nodes, xyz=xyz, s=s, widths=widths, tangent=tangent,
                           depth_dir=down,
                           bisector=_unit(np.cross(tangent, normal)), da=into, normal=normal,
                           poly=poly, cum=cum, length=welded, closed=closed)  # fmt: skip


def groove(mesh, stress, weld):
    """A modelled groove weld's line forces: the section parallel to the joint a
    twentieth of its depth inside the path's part, cut through that part's elements and
    kept within the groove's depth of the path: the force each triangle passes on toward
    the other part, and its moment about the path, at the nearest station. As
    `modelled`'s return; `throat_normal` here is along the joint's normal."""
    from puzzle.adapters.weld_pairs import _project
    from puzzle.mesh import Mesh
    from puzzle.post.probes import _cut
    from puzzle.results.model import StepResult

    frame = groove_stations(mesh, weld)
    if frame is None:
        return None
    part = weld.path.split("/", 1)[0]
    body = mesh.groups[part]
    blocks = [b for b in body.blocks(mesh) if b.dim == 3]
    own = Mesh(mesh.node_ids, mesh.coords, blocks, {part: body})
    step = StepResult(0, "stationary", 0.0, None, {"stress": stress})
    depth = weld.leg.to_si()
    origin = frame.xyz[0] - frame.normal * depth / 20  # the path lies on the joint
    cut, faces, areas, centres, nn = _cut(own, step, origin, frame.normal)
    sl, dist, near = _project(centres, frame.poly, frame.cum)
    on = dist <= depth * 1.05
    sv = np.asarray(cut.point_data["stress"], float).reshape(cut.n_points, -1)
    xx, yy, zz, xy, yz, zx = (sv[:, stress.components.index(c)]
                              for c in ("xx", "yy", "zz", "xy", "yz", "zx"))  # fmt: skip
    traction = np.stack([xx * nn[0] + xy * nn[1] + zx * nn[2], xy * nn[0] + yy * nn[1] + yz * nn[2],
                         zx * nn[0] + yz * nn[1] + zz * nn[2]], axis=1)  # fmt: skip
    # sigma n, n toward the other part, is the force on the path's part's side from it:
    # its opposite is what the joint passes on to the other part
    f = -(traction[faces].mean(axis=1) * areas[:, None])[on]
    c = centres[on]
    t = frame.tangent[0]
    twist = np.einsum("ij,j->i", np.cross(c - near[on], f), t)
    nearest = np.argmin(np.abs(sl[on][:, None] - frame.s[None, :]), axis=1)
    n = len(frame.stations)
    total, turning = np.zeros((n, 3)), np.zeros(n)
    np.add.at(total, nearest, f)
    np.add.at(turning, nearest, twist)
    width = np.maximum(frame.widths, 1e-300)
    line = total / width[:, None]
    rows = np.column_stack([
        np.einsum("ij,ij->i", line, frame.tangent), line @ frame.normal,
        np.einsum("ij,ij->i", line, frame.bisector), np.linalg.norm(line, axis=1), line,
        turning / width,
    ])  # fmt: skip
    return frame.stations, rows, f.sum(axis=0), np.cross(c, f).sum(axis=0)
