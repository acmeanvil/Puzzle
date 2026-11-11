"""A frame solved by PyNite (ADR 0079), run in PyNite's own environment: it reads
case.json beside it and writes results.json. It must not import puzzle.

case.json (SI): nodes {id: [x, y, z]}; materials {key: [E, G, nu, rho]}; sections {key:
[A, Iy, Iz, J]}; members [{name, i, j, material, section, y: the section's first axis,
releases: 12 bools (i end's ux..rz, then j's)}]; supports {node: 6 bools}; springs
{node: {dof: k}}; displacements {node: {dof: value}}; node_loads {node: {dof: value}};
line_loads [{member, direction (FX, FY, FZ), w}].

results.json: nodes {id: [ux, uy, uz, rx, ry, rz]}; reactions {node: [6]}; members
{name: [[N, Vy, Vz, T, My, Mz] at i, the same at j]} in the member's axes (x along it, y
the section's first axis), PyNite's signs.
"""

import json
import math
import sys
import time
from pathlib import Path

DOFS = ("DX", "DY", "DZ", "RX", "RY", "RZ")
REACTIONS = ("FX", "FY", "FZ", "MX", "MY", "MZ")
COMBO = "Combo 1"


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


def main() -> int:
    from Pynite import FEModel3D

    here = Path(__file__).resolve().parent
    case = json.loads((here / "case.json").read_text(encoding="utf-8"))
    start = time.time()
    _event("stage", name="model")
    model = FEModel3D()
    for nid, (x, y, z) in case["nodes"].items():
        model.add_node(nid, x, y, z)
    for key, (e, g, nu, rho) in case["materials"].items():
        model.add_material(key, e, g, nu, rho)
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
    for node, loads in case.get("node_loads", {}).items():
        for dof, value in loads.items():
            model.add_node_load(node, dof, value)
    for load in case.get("line_loads", []):
        w = load["w"]
        model.add_member_dist_load(load["member"], load["direction"], w, w)
    _event("stage", name="solve")
    model.analyze_linear(check_statics=False)
    nodes = {nid: [getattr(n, d)[COMBO] for d in DOFS] for nid, n in model.nodes.items()}
    held = set(case["supports"]) | set(case.get("springs", {})) | set(case.get("displacements", {}))
    reactions = {
        nid: [getattr(model.nodes[nid], f"Rxn{d}")[COMBO] for d in REACTIONS] for nid in held
    }
    members = {}
    for name, member in model.members.items():
        length = member.L()
        ends = []
        for x in (0.0, length):
            ends.append([
                member.axial(x, COMBO), member.shear("Fy", x, COMBO), member.shear("Fz", x, COMBO),
                member.torque(x, COMBO), member.moment("My", x, COMBO),
                member.moment("Mz", x, COMBO),
            ])  # fmt: skip
        members[name] = ends
    out = {"nodes": nodes, "reactions": reactions, "members": members,
           "seconds": time.time() - start}  # fmt: skip
    (here / "results.json").write_text(json.dumps(out), encoding="utf-8")
    _event("converged", step=1, increment=1, iterations=1, time=1.0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
