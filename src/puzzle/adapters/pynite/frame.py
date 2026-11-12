"""A frame solved by PyNite (ADR 0079), run in PyNite's own environment: it reads
case.json beside it and writes results.json. It must not import puzzle.

case.json (SI): nodes {id: [x, y, z]}; materials {key: [E, G, nu, rho]}; sections {key:
[A, Iy, Iz, J]}; members [{name, i, j, material, section, y: the section's first axis,
releases: 12 bools (i end's ux..rz, then j's)}]; supports {node: 6 bools}; springs
{node: {dof: k}}; displacements {node: {dof: value}}; node_loads {node: {dof: value}};
line_loads [{member, direction (FX, FY, FZ), w}].

results.json: nodes {id: [ux, uy, uz, rx, ry, rz]}; reactions {node: [6]}; members
{name: [[N, Vy, Vz, T, My, Mz] at i, the same at j]} in the member's axes (x along it, y
the section's first axis), PyNite's signs; stations {name: the same six at STATIONS
points evenly along it, i to j}, for the member checks' diagrams.
"""

import json
import math
import sys
import time
from pathlib import Path

DOFS = ("DX", "DY", "DZ", "RX", "RY", "RZ")
REACTIONS = ("FX", "FY", "FZ", "MX", "MY", "MZ")
COMBO = "Combo 1"
STATIONS = 9


def _event(kind, **values):
    print("PUZZLE-EVENT " + json.dumps({"kind": kind, **values}), flush=True)


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True))


def _roll(member, wanted) -> float:
    """The member's rotation (degrees about its x) that turns PyNite's default local y
    onto the section's first axis `wanted` (made square to the member first)."""
    t = member.T()
    x, y0 = list(t[0, 0:3]), list(t[1, 0:3])
    w = [c - _dot(wanted, x) * xc for c, xc in zip(wanted, x, strict=True)]
    size = math.sqrt(_dot(w, w))
    if size < 1e-9:
        raise SystemExit(f"member {member.name}: its section's first axis runs along it")
    w = [c / size for c in w]
    return math.degrees(math.atan2(_dot(_cross(y0, w), x), _dot(y0, w)))


def _results(model, held, combo) -> dict:
    """One combination's displacements, reactions and member end forces."""
    nodes = {nid: [getattr(n, d)[combo] for d in DOFS] for nid, n in model.nodes.items()}
    reactions = {
        nid: [getattr(model.nodes[nid], f"Rxn{d}")[combo] for d in REACTIONS] for nid in held
    }
    members, stations = {}, {}
    for name, member in model.members.items():
        length = member.L()
        at = [length * k / (STATIONS - 1) for k in range(STATIONS)]
        rows = [[
            member.axial(x, combo), member.shear("Fy", x, combo), member.shear("Fz", x, combo),
            member.torque(x, combo), member.moment("My", x, combo), member.moment("Mz", x, combo),
        ] for x in at]  # fmt: skip
        members[name] = [rows[0], rows[-1]]
        stations[name] = rows
    return {"nodes": nodes, "reactions": reactions, "members": members, "stations": stations}


def _build(case, loads: bool = True, weight_density: bool = False):
    """The PyNite model: nodes, materials (rho as weight density for PyNite's self weight
    when `weight_density`), sections, members, supports, and with `loads` the loads."""
    from Pynite import FEModel3D

    model = FEModel3D()
    for nid, (x, y, z) in case["nodes"].items():
        model.add_node(nid, x, y, z)
    g = case.get("modal", {}).get("gravity", 9.80665)
    for key, (e, gm, nu, rho) in case["materials"].items():
        model.add_material(key, e, gm, nu, rho * g if weight_density else rho)
    for key, (a, iy, iz, j) in case["sections"].items():
        model.add_section(key, a, iy, iz, j)
    for m in case["members"]:
        model.add_member(m["name"], m["i"], m["j"], m["material"], m["section"])
        member = model.members[m["name"]]
        member.rotation = _roll(member, m["y"])
        if any(m.get("releases", [])):
            model.def_releases(m["name"], *m["releases"])
    for node, held in case["supports"].items():
        model.def_support(node, *held)
    for node, springs in case.get("springs", {}).items():
        for dof, k in springs.items():
            model.def_support_spring(node, dof, k)
    for node, given in case.get("displacements", {}).items():
        for dof, value in given.items():
            model.def_node_disp(node, dof, value)
    if not loads:
        return model
    for node, node_loads in case.get("node_loads", {}).items():
        for dof, value in node_loads.items():
            model.add_node_load(node, dof, value)
    for load in case.get("point_loads", []):
        model.add_node_load(load["node"], load["direction"], load["P"], case=load["case"])
    for load in case.get("line_loads", []):
        w = load["w"]
        model.add_member_dist_load(load["member"], load["direction"], w, w,
                                   case=load.get("case", "Case 1"))  # fmt: skip
    return model


def _spectrum(t, sds, sd1, tl):
    """ASCE 7-16 Figure 11.4-1 (as puzzle.frames.seismic.spectrum, which this can't import)."""
    ts = sd1 / sds
    t0 = 0.2 * ts
    if t < t0:
        return sds * (0.4 + 0.6 * t / t0)
    if t <= ts:
        return sds
    if t <= tl:
        return sd1 / t
    return sd1 * tl / t**2


MASS, MASS_SELF = "__mass__", "__mass_self__"


def _modal(case, held) -> dict:
    """Modes with their participation (from PyNite's mass matrix), and with a response
    spectrum each mode's equivalent static loads M φ Γ S_a g I_e/R solved in x and y."""
    import numpy as np

    block = case["modal"]
    g = block["gravity"]
    model = _build(case, loads=True, weight_density=True)
    model.add_member_self_weight("FZ", -1.0, case=MASS_SELF)
    model.add_load_combo(MASS, {MASS_SELF: 1.0, **block["mass_cases"]})
    count = min(block["modes"], 6 * len(case["nodes"]) - 1)
    model.analyze_modal(num_modes=count, mass_combo_name=MASS, mass_direction="Z", gravity=g,
                        check_stability=False)  # fmt: skip
    freqs = [float(f) for f in model.frequencies]
    nodes = list(model.nodes.values())
    size = 6 * len(nodes)
    mass = model.M(MASS, "Z", g, sparse=True).tocsr()
    free = np.ones(size, bool)
    for n in nodes:
        for k, d in enumerate(DOFS):
            if getattr(n, f"support_{d}"):
                free[n.ID * 6 + k] = False
    r = np.zeros((3, size))
    for n in nodes:
        for k in range(3):
            r[k, n.ID * 6 + k] = 1.0 if free[n.ID * 6 + k] else 0.0
    total = [float(r[k] @ (mass @ r[k])) for k in range(3)]
    modes, shapes = [], []
    for i, f in enumerate(freqs, start=1):
        combo = f"Mode {i}"
        phi = np.zeros(size)
        for n in nodes:
            for k, d in enumerate(DOFS):
                phi[n.ID * 6 + k] = getattr(n, d)[combo]
        phi *= free
        mphi = mass @ phi
        generalized = float(phi @ mphi)
        lx = [float(r[k] @ mphi) for k in range(3)]
        gamma = [v / generalized for v in lx]
        meff = [v * v / generalized for v in lx]
        shapes.append((phi, mphi, gamma, f))
        modes.append({"frequency": float(f), "gamma": gamma, "meff": meff,
                      "nodes": {nid: [getattr(n, d)[combo] for d in DOFS]
                                for nid, n in model.nodes.items()}})  # fmt: skip
    out = {"modes": modes, "mass": total}
    sp = block.get("spectrum")
    if not sp:
        return out
    loaded = _build(case, loads=False)
    names = {n.ID: nid for nid, n in model.nodes.items()}
    combos = []
    for direction in sp["directions"]:
        axis = "xyz".index(direction)
        for i, (_phi, mphi, gamma, f) in enumerate(shapes, start=1):
            sa = _spectrum(1.0 / f, sp["sds"], sp["sd1"], sp["tl"])
            force = mphi * gamma[axis] * sa * g * sp["ie"] / sp["r"]
            name = f"S{i}{direction}"
            for k, value in enumerate(force):
                if value and free[k]:
                    loaded.add_node_load(names[k // 6], ("FX", "FY", "FZ", "MX", "MY", "MZ")[k % 6],
                                         float(value), case=name)  # fmt: skip
            loaded.add_load_combo(name, {name: 1.0})
            combos.append((direction, i, name))
    loaded.analyze_linear(check_statics=False)
    out["spectrum"] = {}
    for direction, _i, name in combos:
        out["spectrum"].setdefault(direction, []).append(_results(loaded, held, name))
    return out


def main() -> int:
    here = Path(__file__).resolve().parent
    case = json.loads((here / "case.json").read_text(encoding="utf-8"))
    start = time.time()
    _event("stage", name="model")
    held = set(case["supports"]) | set(case.get("springs", {})) | set(case.get("displacements", {}))
    combos = case.get("combos") or []
    out: dict = {}
    if not case.get("modal") or combos:
        model = _build(case)
        for c in combos:
            model.add_load_combo(c["name"], c["factors"])
        _event("stage", name="solve")
        if case.get("pdelta"):  # second order (ADR 0079 step 2), each combination on its own
            model.analyze_PDelta(check_stability=False)
        else:
            model.analyze_linear(check_statics=False)
        if combos:
            out["combos"] = {c["name"]: _results(model, held, c["name"]) for c in combos}
        else:
            out.update(_results(model, held, COMBO))
    if case.get("modal"):
        _event("stage", name="modes")
        out["modal"] = _modal(case, held)
    out["seconds"] = time.time() - start
    (here / "results.json").write_text(json.dumps(out), encoding="utf-8")
    _event("converged", step=1, increment=1, iterations=1, time=1.0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
