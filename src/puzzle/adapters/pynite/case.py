"""PyNite's case (ADR 0079): the line mesh as PyNite members, written as case.json beside
the runner (`frame.py`), and the results read back onto the mesh.

Each line element is a PyNite member between its two mesh nodes (a three-node element
two members through its middle), so a member's stations are the mesh's nodes: refine
the mesh for finer diagrams. A member's section and orientation come from the beam
section on its edges (after `beams.with_orientations`), its material from the part's
assignment. Member ends (ADR 0079) are the elements of a release's or offset's members
at its node.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import numpy as np

from puzzle.adapters.common import InputError, body_load, globalize
from puzzle.frames import DOFS, constants
from puzzle.mesh.mesh import Mesh
from puzzle.model.schema import Component, Material, Project, Study

CASE, RESULTS = "case.json", "results.json"
RUNNER = Path(__file__).with_name("frame.py")
RIGID = 1e4  # a rigid offset: its member's section constants times this
# PyNite's member forces to Code_Aster's EFGE (and CalculiX's springs') signs: N tension
# positive and the rest as Code_Aster's, measured on the same frames (test_frames.py
# `test_member_force_signs`): PyNite gives N, Vy, Vz, T and Mz the other sign, My the same.
SIGNS = np.array([-1.0, -1.0, -1.0, -1.0, 1.0, -1.0])
# The features a frame takes here (ADR 0079); the rest are refused by the capabilities.
FEATURES = frozenset({"fixed", "displacement", "force", "line_load", "gravity", "node_spring"})


def pynite_python() -> Path | None:
    """A Python whose environment has PyNite: PUZZLE_PYNITE_PYTHON, else the pixi
    `pynite` environment beside Puzzle's own."""
    explicit = os.environ.get("PUZZLE_PYNITE_PYTHON")
    if explicit:
        return Path(explicit)
    from puzzle.jobs.runtime import environment

    env = environment("pynite")
    if env is None:
        return None
    for exe in (env / "python.exe", env / "bin" / "python"):
        if exe.exists():
            return exe
    return None


def _members(project: Project, comp: Component, mesh: Mesh):
    """[(name, n1, n2, element id, material key, section key, first axis)] and the
    materials {key: [E, G, nu, rho]} and sections {key: [A, Iy, Iz, J]} they use."""
    materials: dict[str, list[float]] = {}
    sections: dict[str, list[float]] = {}
    members, covered = [], {}
    for k, b in enumerate(comp.beam_sections, start=1):
        group = mesh.groups.get(b.selection)
        if group is None:
            raise InputError(f"a beam section names '{b.selection}', which is not meshed")
        a, i1, i2, j = constants(b)
        part = b.selection.split("/", 1)[0]
        for blk in group.blocks(mesh):
            if blk.dim != 1:
                continue
            for eid, conn in zip(blk.ids, blk.connectivity, strict=True):
                if int(eid) in covered:
                    raise InputError(
                        f"beam sections on '{covered[int(eid)]}' and '{b.selection}' both "
                        "cover the same elements", code="invalid")  # fmt: skip
                covered[int(eid)] = b.selection
                pairs = [(conn[0], conn[1])] if len(conn) == 2 else [
                    (conn[0], conn[2]), (conn[2], conn[1])]  # fmt: skip
                m = _material(project, comp, part, mesh, [int(n) for n in conn])
                mat = str(m.id)
                if mat not in materials:
                    props = {key: q.to_si() for key, q in m.properties.items()}
                    for key in ("youngs_modulus", "poissons_ratio"):
                        if key not in props:
                            raise InputError(f"material '{m.name}' is missing {key}")
                    e, nu = props["youngs_modulus"], props["poissons_ratio"]
                    materials[mat] = [e, e / (2 * (1 + nu)), nu, props.get("density", 0.0)]
                sections[f"S{k}"] = [a, i1, i2, j]
                for s, (n1, n2) in enumerate(pairs):
                    name = f"E{int(eid)}" + ("" if len(pairs) == 1 else "abc"[s])
                    members.append((name, int(n1), int(n2), int(eid), mat, f"S{k}",
                                    list(map(float, b.orientation))))  # fmt: skip
    every = {int(e) for blk in mesh.beam_blocks() for e in blk.ids}
    if every - set(covered):
        raise InputError(
            "some frame members have no beam section: give each line body one, or a section "
            "on each member or group", code="invalid")  # fmt: skip
    return members, materials, sections


def _material(project, comp, part, mesh, nodes) -> Material:
    mine = [a for a in comp.materials if a.selection.split("/", 1)[0] == part]
    for a in mine:
        group = mesh.groups.get(a.selection)
        if group is not None and set(nodes) <= {int(n) for n in group.node_ids}:
            return project.get(a.material, Material)
    if not mine:
        raise InputError(f"the line body '{part}' has no material", code="invalid")
    return project.get(mine[0].material, Material)


def _nodes(mesh: Mesh, name: str) -> list[int]:
    group = mesh.groups.get(name)
    if group is None:
        raise InputError(f"no selection '{name}' in the mesh")
    return [int(n) for n in group.node_ids]


def _ends(comp: Component, mesh: Mesh, members) -> dict[str, list[bool]]:
    """Each member's 12 releases (i end's ux..rz, then j's) from the member releases."""
    out: dict[str, list[bool]] = {}
    for r in comp.member_releases:
        if r.springs:
            raise InputError(
                "PyNite frees a member's end fully; a partially fixed end (a spring) isn't "
                "written yet", code="unsupported")  # fmt: skip
        at = set(_nodes(mesh, r.at))
        within = set(_elements(mesh, r.selection))
        hit = False
        for name, n1, n2, eid, *_ in members:
            if eid not in within or not ({n1, n2} & at):
                continue
            flags = out.setdefault(name, [False] * 12)
            for dof in r.released:
                k = DOFS.index(dof)
                if n1 in at:
                    flags[k] = True
                if n2 in at:
                    flags[6 + k] = True
            hit = True
        if not hit:
            raise InputError(f"release at '{r.at}': no member of '{r.selection}' ends there")
    for name, flags in out.items():
        if flags[3] and flags[9]:
            raise InputError(f"member {name}: torsion is released at both ends; hold one")
    return out


def _elements(mesh: Mesh, selection: str) -> list[int]:
    group = mesh.groups.get(selection)
    if group is None:
        raise InputError(f"no selection '{selection}' in the mesh")
    return [int(e) for blk in group.blocks(mesh) if blk.dim == 1 for e in blk.ids]


def _rigid(comp: Component, mesh: Mesh, members) -> set[str]:
    """Members inside a rigid end offset: both their nodes within its length of the node
    (a mesh node must lie at the length, within 1 % of the member's element)."""
    out: set[str] = set()
    for o in comp.member_offsets:
        length = o.length.to_si()
        origin = mesh.node_coords(np.array(_nodes(mesh, o.at)))
        within = set(_elements(mesh, o.selection))
        found = False
        for name, n1, n2, eid, *_ in members:
            if eid not in within:
                continue
            p = mesh.node_coords(np.array([n1, n2]))
            d = np.min(np.linalg.norm(p[:, None, :] - origin[None, :, :], axis=2), axis=1)
            size = float(np.linalg.norm(p[1] - p[0]))
            if np.all(d <= length + 0.01 * size):
                out.add(name)
            if np.any(np.abs(d - length) <= 0.01 * size):
                found = True
        if not found:
            raise InputError(
                f"offset at '{o.at}': no mesh node lies {length:g} m along '{o.selection}'; "
                "set the mesh size so one does", code="invalid")  # fmt: skip
    return out


def write_case(project: Project, study: Study, mesh: Mesh, workdir: Path, inputs=()) -> Path:
    from puzzle.adapters.base import refuse_inputs

    refuse_inputs(inputs, "PyNite")
    steps = study.solve_steps()
    if len(steps) != 1 or steps[0].type != "stationary":
        raise InputError("PyNite runs one static step here so far", code="unsupported")
    comp = project.get(study.component, Component)
    if not mesh.beam_blocks():
        raise InputError("a PyNite study needs a frame: line bodies with beam sections")
    if any(b.dim > 1 for b in mesh.blocks):
        raise InputError("PyNite solves frames of line members only, not solids or shells",
                         code="unsupported")  # fmt: skip
    members, materials, sections = _members(project, comp, mesh)
    releases = _ends(comp, mesh, members)
    rigid = _rigid(comp, mesh, members)
    for name in rigid:  # its own section, stiffened
        sec = next(m[5] for m in members if m[0] == name)
        sections.setdefault(f"{sec}R", [v * RIGID for v in sections[sec]])
    used = sorted({n for m in members for n in (m[1], m[2])})
    xyz = mesh.node_coords(np.array(used))
    case: dict = {
        "nodes": {str(n): [float(c) for c in p] for n, p in zip(used, xyz, strict=True)},
        "materials": materials,
        "sections": sections,
        "members": [
            {"name": name, "i": str(n1), "j": str(n2), "material": mat,
             "section": sec + ("R" if name in rigid else ""), "y": y,
             "releases": releases.get(name, [False] * 12)}  # fmt: skip
            for name, n1, n2, _eid, mat, sec, y in members
        ],
        "supports": {}, "springs": {}, "displacements": {}, "node_loads": {}, "line_loads": [],
    }  # fmt: skip
    features = [f for ph in comp.physics if ph.type == "solid_mechanics" for f in ph.features
                if f.active(1)]  # fmt: skip
    for f in globalize(project, features):
        if f.type not in FEATURES:
            raise InputError(f"PyNite doesn't take {f.type} yet", code="unsupported")
        if f.axes is not None and f.type != "gravity":
            raise InputError(f"PyNite takes {f.type} in global axes only so far",
                             code="unsupported")  # fmt: skip
        v = {k: q.to_si() for k, q in f.values.items()}
        if f.type == "fixed":
            for n in _nodes(mesh, f.selection):
                case["supports"][str(n)] = [True] * 6
        elif f.type == "displacement":
            for n in _nodes(mesh, f.selection):
                held = case["supports"].setdefault(str(n), [False] * 6)
                for k, a in enumerate("xyz"):
                    if a not in v:
                        continue
                    held[k] = True
                    if v[a]:
                        case["displacements"].setdefault(str(n), {})[f"D{a.upper()}"] = v[a]
        elif f.type == "node_spring":
            for n in _nodes(mesh, f.selection):
                for key, k in v.items():
                    if k:
                        dof = ("R" if key.startswith("kr") else "D") + key[-1].upper()
                        case["springs"].setdefault(str(n), {})[dof] = k
        elif f.type == "force":
            nodes = _nodes(mesh, f.selection)
            if mesh.groups[f.selection].dim != 0:
                raise InputError(f"a force on a frame goes on a node; on '{f.selection}' "
                                 "use a line load", code="unsupported")  # fmt: skip
            for n in nodes:
                loads = case["node_loads"].setdefault(str(n), {})
                for a in "xyz":
                    if v.get(a):
                        loads[f"F{a.upper()}"] = loads.get(f"F{a.upper()}", 0.0) + v[a] / len(nodes)
        elif f.type == "line_load":
            within = set(_elements(mesh, f.selection))
            for name, _n1, _n2, eid, *_ in members:
                if eid in within:
                    case["line_loads"] += [{"member": name, "direction": f"F{a.upper()}",
                                            "w": v[a]} for a in "xyz" if v.get(a)]  # fmt: skip
        elif f.type == "gravity":  # self weight: ρ A g per length
            g = np.asarray(body_load(f).uniform or (0.0, 0.0, 0.0), float)
            within = set(_elements(mesh, f.selection)) if f.selection in mesh.groups else None
            for name, _n1, _n2, eid, mat, sec, _y in members:
                if within is not None and eid not in within:
                    continue
                rho, area = materials[mat][3], sections[sec][0]
                if not rho:
                    raise InputError("self weight needs a density in every member's material",
                                     code="incomplete_material")  # fmt: skip
                case["line_loads"] += [{"member": name, "direction": f"F{a.upper()}",
                                        "w": rho * area * float(c)}
                                       for a, c in zip("xyz", g, strict=True) if c]  # fmt: skip
    if not case["supports"] and not case["springs"]:
        raise InputError("the frame has no support", code="invalid")
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / CASE).write_text(json.dumps(case), encoding="utf-8")
    shutil.copyfile(RUNNER, workdir / RUNNER.name)
    return workdir / RUNNER.name


def read_results(workdir: Path):
    """results.json onto the mesh: displacement and rotation (node, global), reaction
    force and moment at held nodes, `beam_forces` (node: N, Vy, Vz, T, My, Mz in each
    member's axes, averaged over the members meeting there, as Code_Aster's EFGE_NOEU,
    with its signs), and `member_forces` (element: those six at its start, then at its
    end)."""
    from puzzle.results.model import Field, Results, StepResult

    data = json.loads((workdir / RESULTS).read_text(encoding="utf-8"))
    case = json.loads((workdir / CASE).read_text(encoding="utf-8"))
    ids = np.array([int(n) for n in data["nodes"]])
    values = np.array(list(data["nodes"].values()), float)
    fields = {
        "displacement": Field("displacement", "node", ["x", "y", "z"], ids, values[:, :3]),
        "rotation": Field("rotation", "node", ["x", "y", "z"], ids, values[:, 3:]),
    }
    if data["reactions"]:
        held = np.array([int(n) for n in data["reactions"]])
        r = np.array(list(data["reactions"].values()), float)
        fields["reaction_force"] = Field("reaction_force", "node", ["x", "y", "z"], held, r[:, :3])
        fields["reaction_moment"] = Field("reaction_moment", "node", ["x", "y", "z"], held,
                                          r[:, 3:])  # fmt: skip
    sums: dict[int, np.ndarray] = {}
    counts: dict[int, int] = {}
    elements: dict[int, list] = {}
    for m in case["members"]:
        ends = np.asarray(data["members"][m["name"]], float) * SIGNS  # (2, 6)
        for node, row in ((int(m["i"]), ends[0]), (int(m["j"]), ends[1])):
            sums[node] = sums.get(node, np.zeros(6)) + row
            counts[node] = counts.get(node, 0) + 1
        eid = int(m["name"][1:].rstrip("abc"))
        elements.setdefault(eid, []).append(ends)
    nodes = np.array(sorted(sums))
    names = ["N", "Vy", "Vz", "T", "My", "Mz"]
    fields["beam_forces"] = Field("beam_forces", "node", names, nodes,
                                  np.array([sums[n] / counts[n] for n in nodes]))  # fmt: skip
    eids = np.array(sorted(elements))
    rows = [np.concatenate([elements[e][0][0], elements[e][-1][1]]) for e in eids]
    fields["member_forces"] = Field(
        "member_forces",
        "element",
        [f"{c}_{end}" for end in ("i", "j") for c in names],
        eids,
        np.array(rows),
    )
    out = Results(solver="pynite")
    out.steps.append(StepResult(1, "stationary", 1.0, fields=fields))
    return out
