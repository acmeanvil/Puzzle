"""A frame's seismic weight, levels and ELF loads (ADR 0079 step 4), from the model alone.

- `node_weights`: the effective seismic weight W at each frame node (N): each member's
  self weight ρ A g L (its material's density) half to each end, plus the gravity
  loads of the design's `mass_cases` with their factors: forces on nodes, and line
  loads (half each end). Gravity features are self weight already counted.
- `levels`: the nodes above the base (the lowest held node) grouped by elevation, each
  level's height above the base, weight and nodes. h_n is the highest level's height.
- `elf_commands`: the ELF base shear and its distribution, written as `force`
  features on each level's nodes in proportion to their weight, case `Ex` or `Ey`
  (marked as generated, so running it again replaces them), as one Edit.
- `report`: the ELF numbers, and from a run its modes' participation, the response
  spectrum's base shear and scaling, and the story drifts.
"""

from __future__ import annotations

import numpy as np

from puzzle.errors import PuzzleError
from puzzle.frames import seismic
from puzzle.frames.seismic import G

GENERATED = {"puzzle": {"generated": "elf"}}


def _frame(project, comp):
    from puzzle.frames import frame_lines

    lines = frame_lines(project, comp)
    if not lines:
        raise PuzzleError(f"{comp.name} has no frame", code="not_found")
    return lines


def _members_of(lines, selection: str):
    """(part, member) pairs a selection names: Part/Member, Part/Group or the part."""
    part, _, rest = selection.partition("/")
    ln = lines.get(part)
    if ln is None:
        return []
    return [(part, m) for m in ln.members if not rest or rest in (m.name, m.group)]


def node_weights(project, comp, design) -> tuple[dict[tuple[str, str], float], list[str]]:
    """W at each (part, node), N; and notes on loads not counted."""
    from puzzle.frames import constants
    from puzzle.frames.design import _material, _section_for

    lines = _frame(project, comp)
    weights: dict[tuple[str, str], float] = {}
    notes: list[str] = []

    def add(key, w):
        weights[key] = weights.get(key, 0.0) + w

    for part, ln in lines.items():
        material = _material(project, comp, part)
        rho = material.properties["density"].to_si() if material and "density" in \
            material.properties else 0.0  # fmt: skip
        if not rho:
            notes.append(f"part {part}: its material has no density, so no self weight")
        for m in ln.members:
            section = _section_for(comp, part, m)
            if section is None:
                continue
            w = rho * constants(section)[0] * G * ln.length(m)
            add((part, m.i), w / 2)
            add((part, m.j), w / 2)
    factors = design.mass_cases
    for ph in comp.physics:
        if ph.type != "solid_mechanics":
            continue
        for f in ph.features:
            if f.case not in factors or f.type not in ("force", "line_load"):
                continue
            k = factors[f.case]
            z = f.values.get("z")
            if z is None:
                continue
            down = -z.to_si() * k
            if f.type == "force":
                part, _, node = f.selection.partition("/")
                if part in lines and node in lines[part].nodes:
                    add((part, node), down)
                else:
                    notes.append(f"force on '{f.selection}': not a frame node, not in W")
            else:
                for part, m in _members_of(lines, f.selection):
                    w = down * lines[part].length(m)
                    add((part, m.i), w / 2)
                    add((part, m.j), w / 2)
    return weights, notes


def _held_nodes(comp, lines) -> list[tuple[str, str]]:
    held = []
    for ph in comp.physics:
        for f in ph.features:
            if f.type in ("fixed", "displacement", "node_spring"):
                part, _, node = f.selection.partition("/")
                if part in lines and node in lines[part].nodes:
                    held.append((part, node))
    return held


def levels(project, comp, design) -> tuple[list[dict], float, list[str]]:
    """[{height, weight, nodes: [(part, node)], node_weights}] above the base, lowest
    first; the base's elevation; notes."""
    lines = _frame(project, comp)
    weights, notes = node_weights(project, comp, design)
    z = {(p, n): xyz[2] for p, ln in lines.items() for n, xyz in ln.nodes.items()}
    held = _held_nodes(comp, lines)
    if not held:
        raise PuzzleError("the frame has no supports: no base for its levels", code="invalid")
    base = min(z[k] for k in held)
    size = max(max(z.values()) - base, 1e-9)
    names = {f"{p}/{n}": (p, n) for p, n in z}
    grouped = seismic.group_levels({k: v for k, v in ((f"{p}/{n}", z[(p, n)]) for p, n in z)},
                                   base, 1e-3 * size)  # fmt: skip
    out = []
    for group in grouped:
        keys = [names[g] for g in group]
        nw = {k: weights.get(k, 0.0) for k in keys}
        out.append({"height": float(np.mean([z[k] for k in keys]) - base),
                    "weight": sum(nw.values()), "nodes": keys, "node_weights": nw})  # fmt: skip
    below = sum(w for k, w in weights.items() if z[k] <= base + 1e-3 * size)
    if below:
        notes.append(f"{below:.4g} N of weight at the base is not in W (12.7.2)")
    return out, base, notes


def elf(project, comp, study, period: float | None = None):
    """The ELF procedure for the study's seismic parameters: (Elf, levels, notes)."""
    if study.seismic is None:
        raise PuzzleError(f"study '{study.name}' has no seismic parameters", code="invalid")
    lv, _base, notes = levels(project, comp, study.seismic)
    if not lv:
        raise PuzzleError("no levels above the base", code="invalid")
    height = max(x["height"] for x in lv)
    return seismic.elf(study.seismic, lv, height, period), lv, notes


def elf_commands(project, comp, study, direction: str = "x", period: float | None = None):
    """The ELF story forces as loads: one Edit of the component setting its physics'
    features, earlier generated ELF loads in that direction replaced."""
    from puzzle.commands.model import Edit, EditOp
    from puzzle.model.schema import Feature, Quantity

    if direction not in ("x", "y"):
        raise PuzzleError("ELF loads go along x or y", code="invalid")
    result, lv, notes = elf(project, comp, study, period)
    case = f"E{direction}"
    k = next((i for i, ph in enumerate(comp.physics) if ph.type == "solid_mechanics"), None)
    if k is None:
        raise PuzzleError(f"{comp.name} has no solid mechanics physics", code="invalid")
    kept = [f for f in comp.physics[k].features
            if not (f.extensions == GENERATED and f.case == case)]  # fmt: skip
    for level in result.levels:
        total = level["weight"] or 1.0
        for (part, node), w in level["node_weights"].items():
            if not w:
                continue
            force = level["force"] * w / total
            kept.append(Feature(type="force", selection=f"{part}/{node}", case=case,
                                values={direction: Quantity(value=force, unit="N")},
                                extensions=GENERATED))  # fmt: skip
    value = [f.model_dump(mode="json") for f in kept]
    edit = Edit(object=str(comp.id), ops=[EditOp(op="set", path=f"physics.{k}.features",
                                                 value=value)])  # fmt: skip
    return edit, result, notes


def _elf_dict(result) -> dict:
    return {
        "period_s": result.period, "period": result.period_note,
        "cs": result.cs, "cs_clause": result.cs_clause, "weight_N": result.weight,
        "base_shear_N": result.base_shear, "k": result.k,
        "levels": [{"height_m": lv["height"], "weight_N": lv["weight"], "cvx": lv["cvx"],
                    "force_N": lv["force"], "story_shear_N": lv["shear"]}
                   for lv in result.levels],
    }  # fmt: skip


def report(project, comp, study, mesh=None, res=None) -> dict:
    """The study's seismic numbers: ELF (period from the run's modes where there is
    one), the modes' participation, each spectrum direction's base shear and scale, and
    the story drifts of the spectrum steps (or of static steps named Ex or Ey)."""
    out: dict = {}
    modes = [s for s in (res.steps if res is not None else []) if s.kind == "modal"]
    if modes:
        out["modes"] = []
        for s in modes:
            row = {"mode": s.mode, "frequency_Hz": s.value, "period_s": 1 / s.value}
            for name in ("participation_factor", "effective_mass", "mass_fraction"):
                f = s.fields.get(name)
                if f is not None:
                    row[name] = dict(zip(f.components, map(float, f.values[0]), strict=True))
            out["modes"].append(row)
        fractions = [m.get("mass_fraction", {}) for m in out["modes"]]
        out["mass_participation"] = {d: float(sum(f.get(d, 0.0) for f in fractions))
                                     for d in ("x", "y", "z")}  # fmt: skip
    for direction in ("x", "y"):
        period = None
        if modes:
            best = max(out["modes"], key=lambda m: m.get("mass_fraction", {}).get(direction, 0))
            period = best["period_s"]
        result, lv, notes = elf(project, comp, study, period)
        out[f"elf_{direction}"] = {**_elf_dict(result), "notes": notes}
    if res is None:
        return out
    lv, _base, _notes = levels(project, comp, study.seismic)
    lines = _frame(project, comp)
    drifts = {}
    for s in res.steps:
        name = None
        if s.kind == "spectrum":
            name = s.label()
            shear = s.fields.get("spectrum_base_shear")
            if shear is not None:
                out.setdefault("spectrum", {})[name] = dict(zip(
                    shear.components, map(float, shear.values[0]), strict=True))  # fmt: skip
        elif s.kind == "combination" and s.label() in ("Ex", "Ey"):
            name = s.label()
        if name is None or "displacement" not in s.fields:
            continue
        axis = "x" if ("Ex" in name or name.endswith("x")) else "y"
        deflection = _node_values(mesh, s.fields["displacement"], lines, "xyz".index(axis))
        scale = 1.0
        shear = s.fields.get("spectrum_base_shear")
        if shear is not None:
            scale = float(shear.values[0][shear.components.index("drift_scale")])
        drifts[name] = seismic.drifts([{**x, "nodes": [f"{p}/{n}" for p, n in x["nodes"]],
                                        "node_weights": {f"{p}/{n}": w for (p, n), w in
                                                         x["node_weights"].items()}}
                                       for x in lv], deflection, study.seismic, scale)  # fmt: skip
    if drifts:
        out["drifts"] = drifts
    return out


def _node_values(mesh, field, lines, component: int) -> dict[str, float]:
    """A field's component at each frame node ("Part/Node"), by nearest mesh node."""
    xyz = mesh.node_coords(field.ids)
    out = {}
    for part, ln in lines.items():
        for n, p in ln.nodes.items():
            k = int(np.argmin(np.linalg.norm(xyz - np.asarray(p), axis=1)))
            out[f"{part}/{n}"] = float(field.values[k, component])
    return out


def mesh_levels(project, comp, design, mesh) -> list[dict]:
    """The levels with mesh node ids (for the PyNite case: drift and scaling)."""
    lv, _base, _ = levels(project, comp, design)
    lines = _frame(project, comp)
    ids = np.array(sorted(mesh.beam_nodes()))
    coords = mesh.node_coords(ids)
    out = []
    for x in lv:
        nodes = []
        for part, n in x["nodes"]:
            p = np.asarray(lines[part].nodes[n])
            k = int(np.argmin(np.linalg.norm(coords - p, axis=1)))
            nodes.append(int(ids[k]))
        out.append({"height": x["height"], "weight": x["weight"], "nodes": nodes,
                    "weights": [x["node_weights"][key] for key in x["nodes"]]})  # fmt: skip
    return out
