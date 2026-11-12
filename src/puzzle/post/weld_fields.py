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
        "not_checked": "the base metal along the weld (J4) and Table J2.4's minimum size: "
        "they need the plates' thicknesses",
    }  # fmt: skip


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
