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
    if not welds:
        return
    from puzzle.adapters.weld_pairs import weld_pairs

    pairs = []
    for w in welds:
        try:
            pairs.append(weld_pairs(project, comp, mesh, w))
        except Exception:  # noqa: BLE001 - results are extras: a run never fails on them
            continue
    if not pairs:
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
    welds = [w for w in comp.welds if w.formulation == "line"]
    if weld is not None:
        welds = [w for w in welds if w.name == weld]
        if not welds:
            raise PuzzleError(f"no light weld '{weld}' in {comp.name}", code="not_found")
    out = []
    for w in welds:
        wp = weld_pairs(project, comp, mesh, w)
        rows = line.values[order[np.searchsorted(line.ids, wp.stations, sorter=order)]]
        size = rows[:, c["magnitude"]]
        at = int(np.argmax(size))
        out.append({
            "weld": w.name,
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
