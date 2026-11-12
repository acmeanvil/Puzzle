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
DEFAULT_CASE = "Case 1"  # PyNite's, combined as "Combo 1" when the study has no combinations


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


def _case_of(f, combos) -> str:
    """A load's case: its own where the study combines cases (every load needs one
    then), else PyNite's one default case."""
    if not combos:
        return DEFAULT_CASE
    if f.type in ("fixed", "displacement", "node_spring"):
        return ""
    if not f.case:
        raise InputError(
            f"the {f.type} on '{f.selection}' has no load case, and the study combines "
            "cases: give each load one (D, L, W...)",
            code="invalid",
        )
    return f.case


VERTICAL = "Z"  # gravity acts along -z in a frame (ADR 0079)
SELF = "#self"  # a case's self weight, apart in a modal run
SEISMIC = {"Ex", "Ey"}  # the spectrum's cases (ADR 0079 step 4)


def _modal(case: dict, project, comp, study, mesh, settings: dict, spectrum: bool) -> None:
    """A modal step's block: modes, the mass (members' density, consistent, plus the
    seismic design's mass cases as lumped masses from their gravity loads), and with a
    response spectrum its parameters and the levels for scaling and drift."""
    from puzzle.frames.seismic import G

    design = study.seismic
    block: dict = {"modes": int(settings.get("modes", 12)), "gravity": G,
                   "mass_cases": dict(design.mass_cases) if design else {}}  # fmt: skip
    if spectrum:
        from puzzle.frames.seismic_frame import mesh_levels

        directions = str(settings.get("spectrum_directions", "x,y"))
        block["spectrum"] = {
            "sds": design.sds, "sd1": design.sd1, "tl": design.tl, "r": design.r,
            "ie": design.ie,
            "directions": [d.strip() for d in directions.split(",") if d.strip()],
        }  # fmt: skip
        if any(d not in ("x", "y") for d in block["spectrum"]["directions"]):
            raise InputError(f"spectrum directions are x and y, not {directions!r}")
        case["seismic"] = {"design": design.model_dump(mode="json"),
                           "levels": mesh_levels(project, comp, design, mesh)}  # fmt: skip
    case["modal"] = block


def _notional(case: dict, settings: dict, fraction: float = 0.002) -> None:
    """AISC 360 C2.2b, the Direct Analysis Method's notional loads: in each combination,
    0.002 times the factored gravity load at each node, laterally, in each direction the
    step names (`notional_directions`, default "x,y": both senses of each), each a
    combination of its own, replacing the original ones."""
    from puzzle.frames.combinations import notional

    nodes = case["nodes"]
    lengths = {
        m["name"]: float(np.linalg.norm(np.subtract(nodes[m["j"]], nodes[m["i"]])))
        for m in case["members"]
    }
    ends = {m["name"]: (m["i"], m["j"]) for m in case["members"]}
    by_case: dict[str, dict[str, float]] = {}  # case -> node -> downward load (N)
    for p in case["point_loads"]:
        if p["direction"] == f"F{VERTICAL}":
            node = by_case.setdefault(p["case"], {})
            node[p["node"]] = node.get(p["node"], 0.0) - p["P"]
    for w in case["line_loads"]:
        if w["direction"] == f"F{VERTICAL}":
            node = by_case.setdefault(w["case"], {})
            for n in ends[w["member"]]:
                node[n] = node.get(n, 0.0) - 0.5 * w["w"] * lengths[w["member"]]
    combos = case["combos"] or [{"name": "Loads", "factors": {DEFAULT_CASE: 1.0}}]
    wanted = str(settings.get("notional_directions", "x,y"))
    directions = [d.strip().lower() for d in wanted.split(",") if d.strip()]
    if not directions or any(d.lstrip("+-") not in ("x", "y") for d in directions):
        raise InputError(f"notional loads go along x or y, not {wanted!r}", code="invalid")
    out = []
    for c in combos:
        gravity: dict[str, float] = {}
        for name, factor in c["factors"].items():
            for n, y in by_case.get(name, {}).items():
                gravity[n] = gravity.get(n, 0.0) + factor * y
        loads = notional(gravity, fraction)
        for d in directions:
            axis = d.lstrip("+-")
            for sense in [d[0]] if d[0] in "+-" else ["+", "-"]:
                own = f"{c['name']} N{sense}{axis}"
                sign = 1.0 if sense == "+" else -1.0
                case["point_loads"] += [{"node": n, "direction": f"F{axis.upper()}",
                                         "P": sign * value, "case": own}
                                        for n, value in loads.items() if value]  # fmt: skip
                out.append({"name": f"{c['name']}, notional {sense}{axis}",
                            "factors": {**c["factors"], own: 1.0}})  # fmt: skip
    case["combos"] = out


def write_case(project: Project, study: Study, mesh: Mesh, workdir: Path, inputs=()) -> Path:
    from puzzle.adapters.base import refuse_inputs

    refuse_inputs(inputs, "PyNite")
    steps = study.solve_steps()
    if len(steps) != 1 or steps[0].type not in ("stationary", "modal"):
        raise InputError("PyNite runs one static or modal step here so far",
                         code="unsupported")  # fmt: skip
    modal = steps[0].type == "modal"
    spectrum = modal and bool(steps[0].settings.get("response_spectrum"))
    if spectrum and study.seismic is None:
        raise InputError("a response spectrum needs the study's seismic parameters "
                         "(seismic_design)", code="invalid")  # fmt: skip
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
        "point_loads": [],
    }  # fmt: skip
    features = [f for ph in comp.physics if ph.type == "solid_mechanics" for f in ph.features
                if f.active(1)]  # fmt: skip
    combos = study.combinations
    settings = steps[0].settings
    dam = bool(settings.get("direct_analysis"))
    stainless = False
    if dam:  # AISC 360 C2.3: 0.8 of the stiffness (tau_b = 1); AISC 370 C2.3: 0.70
        from uuid import UUID

        for key, (e, g, nu, rho) in list(materials.items()):
            m = project.objects.get(UUID(key))
            steel = (getattr(m, "info", None) or {}).get("group") == "Stainless Steel"
            stainless |= steel
            tau = 0.70 if steel else 0.8
            materials[key] = [tau * e, tau * g, nu, rho]
    if spectrum:  # the spectrum is the seismic load: generated ELF loads stay out
        from puzzle.frames.seismic_frame import GENERATED

        features = [f for f in features if f.extensions != GENERATED]
    for f in globalize(project, features):
        load_case = _case_of(f, combos)
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
                for a in "xyz":
                    if v.get(a):
                        load = {"node": str(n), "direction": f"F{a.upper()}",
                                "P": v[a] / len(nodes), "case": load_case}  # fmt: skip
                        case["point_loads"].append(load)
        elif f.type == "line_load":
            within = set(_elements(mesh, f.selection))
            for name, _n1, _n2, eid, *_ in members:
                if eid in within:
                    case["line_loads"] += [{"member": name, "direction": f"F{a.upper()}",
                                            "w": v[a], "case": load_case}
                                           for a in "xyz" if v.get(a)]  # fmt: skip
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
                # in a modal run self weight is its own case, so the mass (from the
                # density) doesn't count it twice; combinations take it with its case
                own = f"{load_case}{SELF}" if modal else load_case
                case["line_loads"] += [{"member": name, "direction": f"F{a.upper()}",
                                        "w": rho * area * float(c), "case": own}
                                       for a, c in zip("xyz", g, strict=True) if c]  # fmt: skip
    if not case["supports"] and not case["springs"]:
        raise InputError("the frame has no support", code="invalid")
    case["combos"] = [{"name": c.name, "factors": dict(c.factors)} for c in combos]
    if modal:
        for c in case["combos"]:
            c["factors"].update({f"{k}{SELF}": v for k, v in list(c["factors"].items())})
        _modal(case, project, comp, study, mesh, steps[0].settings, spectrum)
    used = {load["case"].removesuffix(SELF) for load in case["point_loads"] + case["line_loads"]}
    used |= SEISMIC if spectrum else set()
    for c in combos:
        if not set(c.factors) & used:
            raise InputError(f"combination '{c.name}' factors no load case the frame has "
                             f"({', '.join(sorted(used)) or 'none'})", code="invalid")  # fmt: skip
    case["pdelta"] = bool(settings.get("second_order")) or dam
    if dam:  # 370 C2.3(c): τ_b = 1 with a further 0.002 Y_i, so 0.004 in all
        _notional(case, settings, 0.004 if stainless else 0.002)
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / CASE).write_text(json.dumps(case), encoding="utf-8")
    shutil.copyfile(RUNNER, workdir / RUNNER.name)
    return workdir / RUNNER.name


def read_results(workdir: Path):
    """results.json onto the mesh: displacement and rotation (node, global), reaction
    force and moment at held nodes, `beam_forces` (node: N, Vy, Vz, T, My, Mz in each
    member's axes, averaged over the members meeting there, as Code_Aster's EFGE_NOEU,
    with its signs), `member_forces` (element: those six at its start, then at its
    end) and `member_stations` (element: the six at STATIONS points evenly along it,
    component `<force>_<k>`, for the member checks' diagrams)."""
    from puzzle.results.model import Field, Results, StepResult

    data = json.loads((workdir / RESULTS).read_text(encoding="utf-8"))
    case = json.loads((workdir / CASE).read_text(encoding="utf-8"))
    out = Results(solver="pynite")
    combos = data.get("combos")
    if "modal" in data:
        return _modal_results(out, data, case)
    if combos is None:  # no combinations: the one load case as a static step
        out.steps.append(StepResult(1, "stationary", 1.0, fields=_fields(data, case)))
        return out
    for k, (name, part) in enumerate(combos.items(), start=1):
        fields = _fields(part, case)
        fields["combination"] = Field("combination", "global", [name], np.array([0]),
                                      np.array([[float(k)]]))  # fmt: skip
        out.steps.append(StepResult(1, "combination", float(k), fields=fields))
    return out


def _fields(data: dict, case: dict) -> dict:
    """One combination's (or the single case's) results as fields."""
    from puzzle.results.model import Field

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
    pieces: dict[int, list] = {}
    for m in case["members"]:
        ends = np.asarray(data["members"][m["name"]], float) * SIGNS  # (2, 6)
        if "stations" in data:
            pieces.setdefault(int(m["name"][1:].rstrip("abc")), []).append(
                np.asarray(data["stations"][m["name"]], float) * SIGNS)  # fmt: skip
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
    if pieces:
        fields["member_stations"] = _stations(eids, pieces, names)
    return fields


STATIONS = 9  # frame.py's
# the fields a spectrum combines and adds to combinations (forces scaled to the ELF shear)
FORCES = ("reaction_force", "reaction_moment", "beam_forces", "member_forces", "member_stations")
MOTIONS = ("displacement", "rotation")


def _global(name, components, values):
    from puzzle.results.model import Field

    return Field(name, "global", list(components), np.array([0]),
                 np.array([list(map(float, values))]))  # fmt: skip


def _modal_results(out, data, case):
    """A modal step: one step per mode (its shape and participation), with a response
    spectrum a step per direction (CQC of the modes, forces scaled up to the ELF base
    shear, 12.9.1.4) and each combination with Ex or Ey as two steps, ± the spectrum."""
    from puzzle.results.model import StepResult

    modal = data["modal"]
    total = modal["mass"]
    for i, m in enumerate(modal["modes"], start=1):
        ids = np.array([int(n) for n in m["nodes"]])
        values = np.array(list(m["nodes"].values()), float)
        from puzzle.results.model import Field

        fields = {
            "displacement": Field("displacement", "node", ["x", "y", "z"], ids, values[:, :3]),
            "rotation": Field("rotation", "node", ["x", "y", "z"], ids, values[:, 3:]),
            "participation_factor": _global("participation_factor", "xyz", m["gamma"]),
            "effective_mass": _global("effective_mass", "xyz", m["meff"]),
            "mass_fraction": _global("mass_fraction", "xyz",
                                     [e / t if t else 0.0 for e, t in zip(m["meff"], total,
                                                                          strict=True)]),
        }  # fmt: skip
        out.steps.append(StepResult(1, "modal", float(m["frequency"]), mode=i, fields=fields))
    envelopes = {}
    if "spectrum" in modal:
        envelopes = _spectra(out, modal, case)
    for k, (name, part) in enumerate((data.get("combos") or {}).items(), start=1):
        factors = next(c["factors"] for c in case["combos"] if c["name"] == name)
        seismic = {d: f for d, f in factors.items() if d in SEISMIC and d[-1] in envelopes}
        if seismic and all(f < 0 for f in seismic.values()):
            continue  # the spectrum acts both ways: its "+E" twin gives both senses
        static = _fields(part, case)
        if not seismic:
            static["combination"] = _global("combination", [name], [k])
            out.steps.append(StepResult(1, "combination", float(k), fields=static))
            continue
        for sense in (1.0, -1.0):
            fields = {}
            for key, f in static.items():
                add = np.zeros_like(f.values)
                for d, factor in seismic.items():
                    env = envelopes[d[-1]].get(key)
                    if env is not None:
                        add = add + abs(factor) * _aligned(env, f)
                fields[key] = type(f)(f.name, f.location, f.components, f.ids,
                                      f.values + sense * add)  # fmt: skip
            label = f"{name} ({'+' if sense > 0 else '−'}E)"
            fields["combination"] = _global("combination", [label], [k])
            out.steps.append(StepResult(1, "combination", float(k), fields=fields))
    return out


def _aligned(env, f):
    """An envelope field's values on another field's ids (zero where it has none)."""
    order = np.argsort(env.ids)
    at = np.searchsorted(env.ids, f.ids, sorter=order)
    at = np.clip(at, 0, len(order) - 1)
    hit = env.ids[order[at]] == f.ids
    values = np.zeros_like(f.values)
    values[hit] = env.values[order[at[hit]]]
    return values


def _spectra(out, modal, case) -> dict:
    """Each direction's CQC response (12.9.1.3), its base shear V_t against the ELF base
    shear V at the modal period capped at C_u T_a (12.9.1.4.1: forces × scale V / V_t
    where V_t is less), the drift scale of 12.9.1.4.2. Returns {direction: {field:
    envelope}} for the combinations."""
    from puzzle.frames import seismic
    from puzzle.model.schema import SeismicDesign
    from puzzle.results.model import StepResult

    design = SeismicDesign.model_validate(case["seismic"]["design"])
    levels = case["seismic"]["levels"]
    height = max(lv["height"] for lv in levels)
    omegas = [2 * np.pi * m["frequency"] for m in modal["modes"]]
    total = modal["mass"]
    envelopes = {}
    for direction, per_mode in modal["spectrum"].items():
        axis = "xyz".index(direction)
        fields = [_fields(p, case) for p in per_mode]
        shears = [float(np.sum(f["reaction_force"].values[:, axis])) if "reaction_force" in f
                  else 0.0 for f in fields]  # fmt: skip
        vt = float(seismic.cqc(np.array(shears), omegas, design.damping))
        fractions = [m["meff"][axis] / total[axis] if total[axis] else 0 for m in modal["modes"]]
        fundamental = 1.0 / modal["modes"][int(np.argmax(fractions))]["frequency"]
        elf_levels = [{"height": lv["height"], "weight": lv["weight"]} for lv in levels]
        elf = seismic.elf(design, elf_levels, height, fundamental)
        scale = max(1.0, design.scale_to_elf * elf.base_shear / vt) if vt else 1.0
        drift_scale = 1.0
        if elf.cs_clause == "12.8-6" and vt < elf.cs * elf.weight:
            drift_scale = elf.cs * elf.weight / vt
        combined = {}
        for key in fields[0]:
            stack = np.array([f[key].values for f in fields])
            env = seismic.cqc(stack, omegas, design.damping)
            if key in FORCES:
                env = env * scale
            f0 = fields[0][key]
            combined[key] = type(f0)(f0.name, f0.location, f0.components, f0.ids, env)
        envelopes[direction] = combined
        label = f"E{direction} response spectrum (CQC)"
        step_fields = dict(combined)
        step_fields["combination"] = _global("combination", [label], [0])
        step_fields["spectrum_base_shear"] = _global(
            "spectrum_base_shear",
            ["modal_V", "elf_V", "scale", "period", "cs", "drift_scale", "mass_participation"],
            [vt, elf.base_shear, scale, elf.period, elf.cs, drift_scale, sum(fractions)],
        )
        out.steps.append(StepResult(1, "spectrum", float(axis), fields=step_fields))
    return envelopes


def _stations(eids, pieces, names):
    """Each element's forces at STATIONS points evenly along it: a quadratic element is
    two PyNite members, each sampled at STATIONS points, so every other one of each."""
    from puzzle.results.model import Field

    rows = []
    for e in eids:
        parts = pieces[e]
        if len(parts) == 1:
            rows.append(parts[0])
        else:
            half = (STATIONS + 1) // 2
            take = np.linspace(0, STATIONS - 1, half).round().astype(int)
            rows.append(np.vstack([parts[0][take], parts[1][take][1:]]))
    values = np.array([r.reshape(-1) for r in rows])  # station-major: k's six together
    comps = [f"{c}_{k}" for k in range(STATIONS) for c in names]
    return Field("member_stations", "element", comps, np.asarray(eids), values)
