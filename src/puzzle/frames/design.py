"""Frame members checked by their code (ADR 0079 step 3), from a stored run.

For each member of a frame (`frames.frame_lines`), its forces along it come from the
run: PyNite's `member_stations` (nine points along each element), else `beam_forces`
at its nodes (Code_Aster and CalculiX beams). Each combination step (or the one static
step) is checked at every station:

- tension or compression (D2; E3, E4, E7) against the axial force,
- flexure about each axis (F2-F11) against the moments, the strong axis per unbraced
  segment of length L_b with C_b from that segment's diagram (F1-1),
- shear along each axis (G2-G6), torsion for HSS (H3.1),
- the interaction (H1-1; H3-6 where HSS torsion passes 0.2 T_c),
- deflection against `deflection_limit` (L/δ), δ the largest distance of a node's
  displaced position from the line through the displaced ends.

`MemberDesign` gives the code, method and lengths (the most specific entry wins). The
code follows the material when `auto`: carbon and low-alloy steel by AISC 360; stainless
(AISC 370) and aluminium (the Aluminum Design Manual) are refused until built.
Nothing here is stored: checks are evaluated on demand, as derived results are.
"""

from __future__ import annotations

import math

import numpy as np

from puzzle.errors import PuzzleError
from puzzle.frames import aisc360
from puzzle.frames.aisc360 import NotCovered, Steel

FORCES = ("N", "Vy", "Vz", "T", "My", "Mz")  # member axes: y the first (strong) axis
STEEL = ("Carbon Steel", "Low Alloy Steel")
STAINLESS = ("Stainless Steel",)
ALUMINIUM = ("Aluminum", "Aluminium")
CODES = {"aisc360": "AISC 360-16", "aisc370": "AISC 370-21",
         "adm": "Aluminum Design Manual 2015"}  # fmt: skip


def code_module(code: str, material, method: str = "LRFD"):
    """The strengths' module for a code: AISC 370's flexure given the alloy family, the
    ADM's given the method (its rupture's φ and Ω differ from yielding's) and its own
    material (F_cy, F_sy, F_su, the temper)."""
    if code == "aisc360":
        return aisc360
    import functools
    from types import SimpleNamespace

    if code == "adm":
        from puzzle.frames import adm

        return SimpleNamespace(
            tension=adm.tension, compression=adm.compression, torsion=adm.torsion,
            interaction=adm.interaction, material=adm.material,
            flexure_x=functools.partial(adm.flexure_x, method=method),
            flexure_y=functools.partial(adm.flexure_y, method=method),
            shear_x=functools.partial(adm.shear_x, method=method),
            shear_y=functools.partial(adm.shear_y, method=method))  # fmt: skip
    from puzzle.frames import aisc370

    alloy = aisc370.family(material)
    return SimpleNamespace(**{n: getattr(aisc370, n) for n in (
        "tension", "compression", "flexure_y", "shear_x", "shear_y", "torsion", "interaction")},
        flexure_x=functools.partial(aisc370.flexure_x, alloy=alloy), alloy=alloy)  # fmt: skip


def _design_for(comp, part: str, member) -> object:
    from puzzle.model.schema import MemberDesign

    keys = [f"{part}/{member.name}", f"{part}/{member.group}" if member.group else None, part, ""]
    for key in keys:
        if key is None:
            continue
        for d in comp.member_design:
            if d.selection == key:
                return d
    return MemberDesign()


def _section_for(comp, part: str, member):
    sections = {b.selection: b for b in comp.beam_sections}
    for key in (f"{part}/{member.name}", f"{part}/{member.group}" if member.group else None, part):
        if key and key in sections:
            return sections[key]
    return None


def _material(project, comp, part: str):
    from puzzle.model.schema import Material

    for a in comp.materials:
        if a.selection.split("/", 1)[0] == part:
            m = project.objects.get(a.material)
            if isinstance(m, Material):
                return m
    return None


def code_for(material, design) -> str:
    """The code a member is checked by, or a NotCovered saying why none."""
    group = (getattr(material, "info", None) or {}).get("group", "") or ""
    if design.code != "auto":
        return design.code
    if group in STAINLESS:
        return "aisc370"
    if group in ALUMINIUM:
        return "adm"
    if group in STEEL or not group:
        return "aisc360"
    raise NotCovered(f"no design code for a {group} member")


def _steel(material) -> Steel:
    props = {k: q.to_si() for k, q in material.properties.items()}
    missing = [
        k for k in ("youngs_modulus", "yield_strength", "tensile_strength") if k not in props
    ]
    if missing:
        raise NotCovered(f"material '{material.name}' has no {', '.join(missing)}")
    e = props["youngs_modulus"]
    g = e / (2 * (1 + props.get("poissons_ratio", 0.3)))
    return Steel(e, g, props["yield_strength"], props["tensile_strength"])


def _rows(field, ids) -> np.ndarray:
    order = np.argsort(field.ids)
    at = order[np.searchsorted(field.ids, ids, sorter=order)]
    if not np.array_equal(field.ids[at], ids):
        raise KeyError("ids not in the field")
    return field.values[at]


def stations(mesh, step, group: str, start, end) -> tuple[np.ndarray, np.ndarray]:
    """(s, forces): distances from the member's start and the six forces at each station,
    in order along it. Forces as magnitudes don't depend on an element's direction, nor
    N's sign (tension positive)."""
    start, end = np.asarray(start, float), np.asarray(end, float)
    axis = (end - start) / np.linalg.norm(end - start)
    g = mesh.groups[group]
    elements = [(int(e), np.asarray(c)) for blk in g.blocks(mesh) if blk.dim == 1
                for e, c in zip(blk.ids, blk.connectivity, strict=True)]  # fmt: skip
    if not elements:
        raise PuzzleError(f"'{group}' has no line elements in the mesh", code="not_found")
    if "member_stations" in step.fields:
        f = step.fields["member_stations"]
        k = len(f.components) // 6
        values = _rows(f, np.array([e for e, _ in elements]))
        s_all, rows = [], []
        for (_, conn), v in zip(elements, values, strict=True):
            a, b = mesh.node_coords(np.array([conn[0], conn[1]]))
            sa, sb = float((a - start) @ axis), float((b - start) @ axis)
            s_all.append(np.linspace(sa, sb, k))
            rows.append(v.reshape(k, 6))
        s, forces = np.concatenate(s_all), np.vstack(rows)
    elif "beam_forces" in step.fields:
        nodes = np.unique(np.concatenate([c for _, c in elements]))
        forces = _rows(step.fields["beam_forces"], nodes)
        s = (mesh.node_coords(nodes) - start) @ axis
    else:
        raise PuzzleError("this run has no member forces (member_stations or beam_forces)",
                          code="not_found")  # fmt: skip
    order = np.argsort(s, kind="stable")
    return s[order], forces[order]


def _deflection(mesh, step, group: str, start, end) -> float:
    """The largest distance of a member node's displaced position from the line through
    its displaced ends (m)."""
    if "displacement" not in step.fields:
        return 0.0
    g = mesh.groups[group]
    nodes = np.unique(np.concatenate([np.asarray(c) for blk in g.blocks(mesh) if blk.dim == 1
                                      for c in blk.connectivity]))  # fmt: skip
    xyz = mesh.node_coords(nodes)
    u = _rows(step.fields["displacement"], nodes)
    start, end = np.asarray(start, float), np.asarray(end, float)
    length = np.linalg.norm(end - start)
    axis = (end - start) / length
    s = (xyz - start) @ axis
    i, j = int(np.argmin(s)), int(np.argmax(s))
    chord = u[i] + np.outer(s / length, u[j] - u[i])
    rel = u - chord
    rel -= np.outer(rel @ axis, axis)
    return float(np.max(np.linalg.norm(rel, axis=1)))


def _segments(length: float, lb: float) -> list[tuple[float, float]]:
    """The unbraced segments along a member: as many equal ones as L_b fits."""
    n = max(1, round(length / lb)) if lb > 0 else 1
    return [(length * k / n, length * (k + 1) / n) for k in range(n)]


def _cb(s, values, lo, hi) -> float:
    """F1-1 over [lo, hi]: M_max from the stations in it, M_A, M_B, M_C at its quarter
    points interpolated along the stations (linear between them, an eighth of an
    element apart for PyNite)."""
    inside = (s >= lo - 1e-9) & (s <= hi + 1e-9)
    m = np.abs(values)
    quarters = np.abs(np.interp([lo + (hi - lo) * f for f in (0.25, 0.5, 0.75)], s, values))
    mmax = max(float(m[inside].max()) if inside.any() else 0.0, float(quarters.max()))
    return aisc360.cb_from(mmax, *quarters)


def check_member(p, steel, design, length: float, s, forces, method: str, code=aisc360) -> dict:
    """Every limit state against one step's forces along a member: the checks with their
    demand, available strength, ratio, station and clause, and the governing one."""
    lx = design.length_x.to_si() if design.length_x else length
    lb = design.unbraced_length.to_si() if design.unbraced_length else length
    ly = design.length_y.to_si() if design.length_y else lb
    lz = design.length_z.to_si() if design.length_z else ly
    n, vy, vz, t, my, mz = (forces[:, k] for k in range(6))
    checks = []

    def add(name, strength, demand, at):
        cap = strength.available(method)
        checks.append({
            "check": name, "limit": strength.limit, "clause": strength.clause,
            "demand": float(demand), "available": float(cap),
            "ratio": float(demand / cap) if cap else math.inf, "at_m": float(at),
            **({"notes": list(strength.notes)} if strength.notes else {}),
        })  # fmt: skip

    tension = min(  # the smaller of yielding and rupture governs
        code.tension(p, steel, design.net_area_ratio), key=lambda x: x.available(method)
    )
    comp = code.compression(p, steel, design.k_x * lx, design.k_y * ly, design.k_z * lz)
    k_t, k_c = int(np.argmax(n)), int(np.argmin(n))
    if n[k_t] > 0:
        add("tension", tension, n[k_t], s[k_t])
    if n[k_c] < 0:
        add("compression", comp, -n[k_c], s[k_c])
    # strong-axis flexure per unbraced segment
    mcx_at = np.empty_like(s)
    longer = lb > length * (1 + 1e-6)  # the unbraced length runs on past this member
    for lo, hi in _segments(length, lb):
        # C_b from the segment's own diagram; 1.0 (always allowed, F1) when the segment
        # runs past the member, whose diagram there isn't known here
        cb = design.cb or (1.0 if longer else _cb(s, my, lo, hi))
        fx = code.flexure_x(p, steel, lb if longer else hi - lo, cb)
        inside = (s >= lo - 1e-9) & (s <= hi + 1e-9)
        mcx_at[inside] = fx.available(method)
        k = int(np.argmax(np.where(inside, np.abs(my), -1)))
        add(f"flexure about x ({lo:.3g}-{hi:.3g} m)" if lb < length else "flexure about x",
            fx, abs(my[k]), s[k])  # fmt: skip
    fy = code.flexure_y(p, steel)
    k = int(np.argmax(np.abs(mz)))
    add("flexure about y", fy, abs(mz[k]), s[k])
    vx_s, vy_s = code.shear_x(p, steel, length), code.shear_y(p, steel, length)
    if vx_s is not None:
        k = int(np.argmax(np.abs(vz)))
        add("shear along the web", vx_s, abs(vz[k]), s[k])
    if vy_s is not None:
        k = int(np.argmax(np.abs(vy)))
        add("shear along the flanges", vy_s, abs(vy[k]), s[k])
    tor = code.torsion(p, steel, length)
    if tor is not None:
        k = int(np.argmax(np.abs(t)))
        add("torsion", tor, abs(t[k]), s[k])
    # interaction at every station
    pc_t, pc_c, mcy = tension.available(method), comp.available(method), fy.available(method)
    ratios = []
    for i in range(len(s)):
        pc = pc_t if n[i] >= 0 else pc_c
        r = code.interaction(abs(n[i]), pc, abs(my[i]), mcx_at[i], abs(mz[i]), mcy)
        if tor is not None and abs(t[i]) > 0.2 * tor.available(method):
            mr = math.hypot(my[i], mz[i])
            r = max(r, aisc360.interaction_torsion(
                abs(n[i]), pc, mr, min(mcx_at[i], mcy), math.hypot(vy[i], vz[i]),
                min(vx_s.available(method), vy_s.available(method)), abs(t[i]),
                tor.available(method)))  # fmt: skip
        ratios.append(r)
    k = int(np.argmax(ratios))
    checks.append({"check": "combined forces", "limit": "interaction",
                   "clause": "H1-1" + (", H3-6" if tor is not None else ""),
                   "demand": float(ratios[k]), "available": 1.0, "ratio": float(ratios[k]),
                   "at_m": float(s[k])})  # fmt: skip
    governing = max(checks, key=lambda c: c["ratio"])
    return {"checks": checks, "governing": governing}


def member_checks(project, comp, mesh, res, member: str | None = None,
                  step: int | None = None) -> dict:  # fmt: skip
    """Each member's checks in every step of a run (or `step`, 1-based): per member its
    section, code, method, length, the governing check over the steps (ratio, step,
    clause) and each step's checks. Members a check doesn't cover say why."""
    from puzzle.frames import frame_lines

    lines = frame_lines(project, comp)
    if not lines:
        raise PuzzleError(f"{comp.name} has no frame (a part made by a `frame` step)",
                          code="not_found")  # fmt: skip
    steps = [st for st in res.steps if st.kind in ("stationary", "combination")]
    if step is not None:
        if not 1 <= step <= len(steps):
            raise PuzzleError(f"step {step}: the run has {len(steps)} static steps",
                              code="not_found")  # fmt: skip
        steps = [steps[step - 1]]
    out, skipped = [], []
    for part, ln in lines.items():
        for m in ln.members:
            if member is not None and member not in (m.name, f"{part}/{m.name}"):
                continue
            row = {"member": m.name, "part": part, "group": m.group,
                   "length_m": ln.length(m)}  # fmt: skip
            design = _design_for(comp, part, m)
            section = _section_for(comp, part, m)
            material = _material(project, comp, part)
            try:
                if section is None:
                    raise NotCovered("no beam section")
                if material is None:
                    raise NotCovered("no material")
                code = code_for(material, design)
                module = code_module(code, material, design.method)
                p = aisc360.props(section)
                steel = module.material(material) if hasattr(module, "material") else _steel(
                    material)  # fmt: skip
            except NotCovered as exc:
                skipped.append({**row, "reason": str(exc)})
                continue
            group = f"{part}/{m.name}"
            per_step, worst = [], None
            length = ln.length(m)
            for k, st in enumerate(steps, start=1):
                s, forces = stations(mesh, st, group, ln.nodes[m.i], ln.nodes[m.j])
                try:
                    result = check_member(p, steel, design, length, s, forces, design.method,
                                          module)  # fmt: skip
                except NotCovered as exc:
                    skipped.append({**row, "reason": str(exc)})
                    per_step = None
                    break
                if design.deflection_limit:
                    delta = _deflection(mesh, st, group, ln.nodes[m.i], ln.nodes[m.j])
                    allowed = length / design.deflection_limit
                    result["checks"].append({
                        "check": "deflection", "limit": f"L/{design.deflection_limit:g}",
                        "clause": "serviceability (L/δ)", "demand": delta,
                        "available": allowed, "ratio": delta / allowed, "at_m": None})  # fmt: skip
                    result["governing"] = max(result["checks"], key=lambda c: c["ratio"])
                label = st.label() if st.kind == "combination" else "static"
                per_step.append({"step": k, "label": label, **result})
                g = result["governing"]
                if worst is None or g["ratio"] > worst["ratio"]:
                    worst = {**g, "step": k, "label": label}
            if per_step is None:
                continue
            out.append({**row, "section": p.designation, "code": CODES[code],
                        "method": design.method, "ratio": worst["ratio"] if worst else 0.0,
                        "passes": bool(worst is None or worst["ratio"] <= 1.0),
                        "governing": worst, "steps": per_step})  # fmt: skip
    out.sort(key=lambda r: -r["ratio"])
    return {"members": out, "not_checked": skipped,
            "worst": out[0]["member"] if out else None}  # fmt: skip
