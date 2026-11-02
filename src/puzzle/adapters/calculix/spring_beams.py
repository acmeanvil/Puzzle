"""Open table sections on CalculiX (ADR 0055): each beam element as its exact
Timoshenko equivalent, the construction of ADR 0027's beam bolts.

CalculiX's beams expand into solids of closed outlines only, and its general beam (U1)
gives torsion G (I11 + I22) / L, not G J: some 1,700 times too stiff for a W12X26. So a
line part whose sections are open (I, channel, tee, angles) is written without beam
elements. Each element (a second-order one as two segments, so every mesh node keeps
its results) is a straight uniform segment between mesh nodes N1 and N2:

- every mesh node N of such a part gets a rotation node R_N (a helper past the mesh's
  ids, as rigid bodies have; its displacements are N's rotations);
- each segment end has an arm node at the segment's middle, tied to its end by the
  linearised rigid arm u_arm = u_N + θ_R × (middle − N) (*EQUATION; no *RIGID BODY,
  whose nonlinear MPCs a linear step needs nothing of);
- six SPRING2 springs in the section's frame join the two arm nodes (axial EA/L, shear
  along y and z) and the two rotation nodes (torsion GJ/L, bending EI1/L and EI2/L),
  `common.timoshenko_springs`: force and moment uncouple at the middle (the elastic
  centre), so this is the beam's 12 x 12 stiffness term for term.

A zero-stiffness spring joins N to R_N: CalculiX writes results only at element nodes.
Mass is lumped: ρAL/2 at each end of a segment as MASS elements (translations only;
rotary inertia is left out, which CalculiX's MASS element doesn't take). Gravity and
acceleration go on as each segment's consistent nodal loads (Q/2 at each end and
± r × Q / 12 on the rotation nodes, the fixed-end moments). The section forces
(`beam_forces`: N, Vy, Vz, T, My, Mz in the member's axes, as Code_Aster's EFGE_NOEU)
come back from the springs' stretch, exactly for loads at nodes; within a segment a
body load's own share (of order qL²/12) is left out.

Refused, naming Code_Aster: heat, thermal loads (springs don't expand), buckling and
geometric nonlinearity (no geometric stiffness), prestress, plasticity, Rayleigh
damping (springs carry no material), centrifugal loads, and nodes shared with solids,
shells or other beams (whose rotations these nodes can't join).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from puzzle.adapters.common import InputError, timoshenko_springs
from puzzle.mesh.mesh import Mesh
from puzzle.model.schema import Component, Material, Project, Study

INFO_SUFFIX = ".beams.json"
COMPONENTS = ["N", "Vy", "Vz", "T", "My", "Mz"]
_EPS = {(0, 1, 2): 1, (1, 2, 0): 1, (2, 0, 1): 1, (0, 2, 1): -1, (2, 1, 0): -1, (1, 0, 2): -1}


def is_open(b) -> bool:
    """A table section CalculiX can't expand into solids (not a rectangular or round
    hollow section): written as springs."""
    from puzzle import sections as tables

    return b.shape == "library" and tables.get(b.standard, b.designation).shape not in (
        "RHS",
        "CHS",
    )


def spring_parts(comp: Component) -> set[str]:
    """Line parts whose sections are open table sections. A part mixing them with
    sections CalculiX expands is refused: its nodes' rotations would not be joined."""
    by_part: dict[str, list[bool]] = {}
    for b in comp.beam_sections:
        by_part.setdefault(b.selection.split("/", 1)[0], []).append(is_open(b))
    mixed = [p for p, kinds in by_part.items() if any(kinds) and not all(kinds)]
    if mixed:
        raise InputError(
            f"line part '{mixed[0]}' mixes open table sections with sections CalculiX "
            "expands into solids, whose rotations it can't join; give it one kind, or run "
            "the study on Code_Aster",
            code="unsupported",
        )
    return {p for p, kinds in by_part.items() if all(kinds)}


@dataclass
class Segment:
    nodes: tuple[int, int]
    frame: np.ndarray  # rows: x along the segment, y the section's first axis, z
    length: float
    translation: tuple[float, float, float]
    rotation: tuple[float, float, float]
    mass: float  # ρAL; 0 without a density
    arms: tuple[int, int] = (0, 0)  # at the middle, following each end rigidly
    turns: tuple[int, int] = (0, 0)  # each end's rotation, carried to its own springs


@dataclass
class SpringBeams:
    parts: set[str]
    segments: list[Segment]
    rotation_of: dict[int, int] = field(default_factory=dict)  # mesh node -> R_N
    first_node: int = 0
    first_elem: int = 0
    next_node: int = 0
    next_elem: int = 0

    @property
    def nodes(self) -> set[int]:
        return set(self.rotation_of)

    def info(self) -> list[dict]:
        """What the reader needs for section forces and the ends' reactions."""
        return [
            {
                "nodes": list(s.nodes),
                "arms": list(s.arms),
                "turns": list(s.turns),
                "frame": s.frame.tolist(),
                "length": s.length,
            }
            for s in self.segments
        ]


def _material(project: Project, comp: Component, part: str, mesh: Mesh, nodes) -> Material:
    mine = [a for a in comp.materials if a.selection.split("/", 1)[0] == part]
    for a in mine:
        group = mesh.groups.get(a.selection)
        if group is not None and set(nodes) <= {int(n) for n in group.node_ids}:
            return project.get(a.material, Material)
    if not mine:
        raise InputError(f"the line body '{part}' has no material", code="invalid")
    return project.get(mine[0].material, Material)


def build(project: Project, comp: Component, mesh: Mesh) -> SpringBeams | None:
    """The segments of every open-section part, unnumbered (`number` gives the ids)."""
    from puzzle import sections as tables

    parts = spring_parts(comp)
    if not parts:
        return None
    segments: list[Segment] = []
    covered: dict[int, str] = {}
    for b in comp.beam_sections:
        part = b.selection.split("/", 1)[0]
        if part not in parts:
            continue
        group = mesh.groups.get(b.selection)
        if group is None:
            raise InputError(f"a beam section names '{b.selection}', which is not meshed")
        sec = tables.get(b.standard, b.designation)
        for blk in group.blocks(mesh):
            if blk.dim != 1:
                continue
            if blk.element not in ("line2", "line3"):
                raise InputError(f"CalculiX beams of {blk.element} elements aren't written")
            for eid, conn in zip(blk.ids, blk.connectivity, strict=True):
                if int(eid) in covered:
                    raise InputError(
                        f"beam sections on '{covered[int(eid)]}' and '{b.selection}' both "
                        "cover the same elements",
                        code="invalid",
                    )
                covered[int(eid)] = b.selection
                # Gmsh's line3 lists its ends, then its middle: two segments through it.
                if len(conn) == 2:
                    pairs = [(conn[0], conn[1])]
                else:
                    pairs = [(conn[0], conn[2]), (conn[2], conn[1])]
                for n1, n2 in pairs:
                    n1, n2 = int(n1), int(n2)
                    p1, p2 = mesh.node_coords(np.array([n1, n2]))
                    length = float(np.linalg.norm(p2 - p1))
                    x = (p2 - p1) / length
                    y = np.asarray(b.orientation, float)
                    y = y - (y @ x) * x
                    if np.linalg.norm(y) < 1e-9:
                        raise InputError(
                            f"beam section on '{b.selection}': its first axis runs along "
                            "the member; orient it across",
                            code="invalid",
                        )
                    y /= np.linalg.norm(y)
                    m = _material(project, comp, part, mesh, (n1, n2))
                    props = {k: q.to_si() for k, q in m.properties.items()}
                    for key in ("youngs_modulus", "poissons_ratio"):
                        if key not in props:
                            raise InputError(f"material '{m.name}' is missing {key}")
                    e, nu = props["youngs_modulus"], props["poissons_ratio"]
                    g = e / (2 * (1 + nu))
                    springs = timoshenko_springs(
                        e, g, sec.A, sec.I1, sec.I2, sec.J, sec.A1, sec.A2, length
                    )
                    segments.append(Segment(
                        (n1, n2), np.vstack([x, y, np.cross(x, y)]), length,
                        springs["translation"], springs["rotation"],
                        props.get("density", 0.0) * sec.A * length,
                    ))  # fmt: skip
    for part in sorted(parts):
        group = mesh.groups.get(part)
        ids = [int(e) for blk in (group.blocks(mesh) if group else []) if blk.dim == 1
               for e in blk.ids]  # fmt: skip
        if any(e not in covered for e in ids):
            raise InputError(
                f"some members of the line body '{part}' have no beam section: give the "
                "part one, or a section on each of its edges",
                code="invalid",
            )
    _check_shared(comp, mesh, parts)
    return SpringBeams(parts, segments)


def _check_shared(comp: Component, mesh: Mesh, parts: set[str]) -> None:
    mine: set[int] = set()
    for part in parts:
        for blk in mesh.groups[part].blocks(mesh):
            if blk.dim == 1:
                mine |= {int(n) for n in blk.connectivity.ravel()}
    others: set[int] = set()
    for p in comp.parts:
        if p.name in parts or p.name not in mesh.groups:
            continue
        own = mesh.groups[p.name]
        for blk in own.blocks(mesh):
            if blk.dim == own.dim:
                others |= {int(n) for n in blk.connectivity.ravel()}
    if mine & others:
        raise InputError(
            "beams of open table sections share nodes with other elements, which CalculiX's "
            "spring beams can't join; run this study on Code_Aster",
            code="unsupported",
        )


def number(beams: SpringBeams, mesh: Mesh, first_node: int, first_elem: int) -> None:
    nodes = sorted({n for s in beams.segments for n in s.nodes})
    beams.rotation_of = {n: first_node + k for k, n in enumerate(nodes)}
    node = first_node + len(nodes)
    for s in beams.segments:
        s.arms, s.turns = (node, node + 1), (node + 2, node + 3)
        node += 4
    beams.first_node, beams.next_node = first_node, node
    beams.first_elem = first_elem
    # six springs per segment, a link per node, a mass per node (when there is one)
    masses = len(_masses(beams))
    beams.next_elem = first_elem + 6 * len(beams.segments) + len(nodes) + masses


def _masses(beams: SpringBeams) -> dict[int, float]:
    out: dict[int, float] = {}
    for s in beams.segments:
        for n in s.nodes:
            out[n] = out.get(n, 0.0) + s.mass / 2
    return {n: m for n, m in out.items() if m > 0}


def _row(n: int, p) -> str:
    return f"{n}, " + ", ".join(f"{c:.13g}" for c in p)


def lines(beams: SpringBeams, mesh: Mesh) -> list[str]:
    """The model cards: rotation and arm nodes, orientations, springs, links, masses,
    the arms' equations."""
    rot_nodes = sorted(beams.rotation_of)
    out = ["*NODE"]
    at = mesh.node_coords(np.array(rot_nodes))
    out += [_row(beams.rotation_of[n], p) for n, p in zip(rot_nodes, at, strict=True)]
    equations: list[str] = []
    orients: dict[tuple, str] = {}
    sets: dict[tuple, list[str]] = {}
    elem = beams.first_elem
    for s in beams.segments:
        ends = mesh.node_coords(np.array(s.nodes))
        middle = ends.mean(axis=0)
        out += [_row(a, middle) for a in (*s.arms, *s.turns)]
        for node, arm, turn, at in zip(s.nodes, s.arms, s.turns, ends, strict=True):
            r = middle - at  # u_arm − u_N − θ × r = 0
            rot = beams.rotation_of[node]
            for d in range(3):
                terms = [f"{arm}, {d + 1}, 1.", f"{node}, {d + 1}, -1."]
                for (i, j, k), sign in _EPS.items():
                    if i == d and abs(r[k]) > 1e-12 * s.length:
                        terms.append(f"{rot}, {j + 1}, {-sign * r[k]:.13g}")
                equations += [str(len(terms)), ", ".join(terms)]
                # θ_turn = θ_R: the segment's own node for its rotation springs, so its
                # reaction is this segment's moment alone.
                equations += ["2", f"{turn}, {d + 1}, 1., {rot}, {d + 1}, -1."]
        key = tuple(np.round(s.frame[:2].ravel(), 12))
        name = orients.setdefault(key, f"OB{len(orients) + 1}")
        for (n1, n2), tag, values in ((s.arms, "T", s.translation),
                                      (s.turns, "R", s.rotation)):  # fmt: skip
            for dof, k in enumerate(values, start=1):
                sets.setdefault((name, tag, dof, f"{k:.13e}"), []).append(f"{elem}, {n1}, {n2}")
                elem += 1
    for key, name in orients.items():
        out += [f"*ORIENTATION, NAME={name}", ", ".join(f"{c:.13g}" for c in key)]
    for i, ((orient, _tag, dof, k), rows) in enumerate(sets.items(), start=1):
        # A stiffness is always written with an exponent: a bare integer reads as a dof.
        out += [f"*ELEMENT, TYPE=SPRING2, ELSET=E_PZBEAM{i}", *rows]
        out += [f"*SPRING, ELSET=E_PZBEAM{i}, ORIENTATION={orient}", f"{dof}, {dof}", k]
    # CalculiX writes results at element nodes only: a spring of no stiffness to R_N.
    out.append("*ELEMENT, TYPE=SPRING2, ELSET=E_PZBEAMLINK")
    for n in rot_nodes:
        out.append(f"{elem}, {n}, {beams.rotation_of[n]}")
        elem += 1
    out += ["*SPRING, ELSET=E_PZBEAMLINK", "1, 1", "0."]
    by_mass: dict[str, list[str]] = {}
    for n, m in sorted(_masses(beams).items()):
        by_mass.setdefault(f"{m:.10e}", []).append(f"{elem}, {n}")
        elem += 1
    for i, (m, rows) in enumerate(by_mass.items(), start=1):
        out += [f"*ELEMENT, TYPE=MASS, ELSET=E_PZBEAMMASS{i}", *rows,
                f"*MASS, ELSET=E_PZBEAMMASS{i}", m]  # fmt: skip
    out += ["*EQUATION", *equations]
    return out


def fixed_rows(beams: SpringBeams | None, group) -> list[str]:
    """A clamp on spring-beam nodes holds their rotations too: R_N's dofs."""
    if beams is None:
        return []
    held = sorted(beams.nodes & {int(n) for n in group.node_ids})
    return [f"{beams.rotation_of[n]}, 1, 3, 0." for n in held]


def in_part(beams: SpringBeams | None, selection: str) -> bool:
    return beams is not None and selection.split("/", 1)[0] in beams.parts


def body_rows(beams: SpringBeams, f, group, mesh: Mesh) -> list[str]:
    """Gravity or acceleration on spring-beam segments, as consistent nodal loads: Q/2 at
    each end (Q = ρAL a), and the fixed-end moments ± r × Q / 12 (r = N2 − N1) on the
    ends' rotation nodes."""
    from puzzle.adapters.common import body_load

    load = body_load(f)
    if load.uniform is None:
        raise InputError(
            f"{f.type} (centrifugal) loads on beams of open table sections aren't written "
            "on CalculiX yet; run this study on Code_Aster",
            code="unsupported",
        )
    a = np.asarray(load.uniform, float)
    inside = {int(n) for n in group.node_ids}
    loads: dict[int, np.ndarray] = {}
    for s in beams.segments:
        if not set(s.nodes) <= inside:
            continue
        if s.mass <= 0:
            raise InputError(f"{f.type} on '{f.selection}' needs the material's density")
        q = s.mass * a
        p1, p2 = mesh.node_coords(np.array(s.nodes))
        m = np.cross(p2 - p1, q) / 12
        (n1, n2), (r1, r2) = s.nodes, (beams.rotation_of[n] for n in s.nodes)
        for node, value in ((n1, q / 2), (n2, q / 2), (r1, m), (r2, -m)):
            loads[node] = loads.get(node, np.zeros(3)) + value
    return [
        f"{node}, {d + 1}, {v[d]:.10g}"
        for node, v in sorted(loads.items())
        for d in range(3)
        if v[d]
    ]


def check(project: Project, comp: Component, study: Study, beams: SpringBeams, inputs,
          damping) -> None:  # fmt: skip
    """Refuse what spring beams don't carry, naming Code_Aster."""

    def refuse(what: str) -> None:
        raise InputError(
            f"{what} on beams of open table sections, which CalculiX takes as exact "
            "springs (no material, no geometric stiffness); run this study on Code_Aster",
            code="unsupported",
        )

    for s in study.solve_steps():
        solved = set(s.physics) or {p.type for p in comp.physics}
        if "heat_transfer" in solved:
            refuse("heat transfer isn't written")
        if s.type == "buckling":
            refuse("buckling isn't written")
        if s.settings.get("geometric_nonlinearity"):
            refuse("geometric nonlinearity isn't written")
    if any(i.field.name == "temperature" for i in inputs):
        refuse("a thermal load isn't written")
    if any(i.role == "prestress" for i in inputs):
        refuse("prestress isn't written")
    if damping is not None and any(damping):
        refuse("Rayleigh damping isn't written")
    for a in comp.materials:
        if a.selection.split("/", 1)[0] in beams.parts:
            m = project.get(a.material, Material)
            if m.models.plasticity is not None:
                refuse(f"plasticity ('{m.name}') isn't written")
    for c in comp.contacts:
        for sel in (c.source, c.target):
            if sel and sel.split("/", 1)[0] in beams.parts:
                refuse(f"contact ('{sel}') isn't written")


def section_forces(info: list[dict], ids: np.ndarray, rf: np.ndarray):
    """(node ids, values (n, 6)): each segment's section forces (N, Vy, Vz, T, My, Mz in
    its axes; the force and moment the part ahead puts on the part behind) at its two
    ends, averaged at each node as Code_Aster's EFGE_NOEU. They are the springs' forces,
    read as the reactions CalculiX gives the first end's arm and turn nodes (each one
    spring end alone): exact, and good to the print's six digits of each force, where
    differences of printed displacements would lose most of them."""
    row = {int(n): i for i, n in enumerate(ids)}
    sums: dict[int, np.ndarray] = {}
    counts: dict[int, int] = {}
    for s in info:
        (n1, n2), arm, turn = s["nodes"], s["arms"][0], s["turns"][0]
        if arm not in row or turn not in row:
            continue
        frame = np.asarray(s["frame"])
        # The springs pull their first ends by +K Δ, which CalculiX's reaction gives as −.
        n, vy, vz = -(frame @ rf[row[arm], :3])
        torsion, my, mz = -(frame @ rf[row[turn], :3])
        ell = s["length"] / 2  # dMy/dx = Vz, dMz/dx = −Vy along the member
        for node, sign in ((n1, -1.0), (n2, 1.0)):
            end = np.array([n, vy, vz, torsion, my + sign * vz * ell, mz - sign * vy * ell])
            sums[node] = sums.get(node, np.zeros(6)) + end
            counts[node] = counts.get(node, 0) + 1
    nodes = np.array(sorted(sums), dtype=np.int64)
    return nodes, np.array([sums[n] / counts[n] for n in nodes]).reshape(-1, 6)


def reactions(info: list[dict], ids: np.ndarray, rf: np.ndarray) -> dict[int, np.ndarray]:
    """Each spring-beam node's reaction force: what its arms carry to it (CalculiX's own
    reaction there misses the forces passed through equations)."""
    row = {int(n): i for i, n in enumerate(ids)}
    out: dict[int, np.ndarray] = {}
    for s in info:
        for node, arm in zip(s["nodes"], s["arms"], strict=True):
            if arm in row:
                out[node] = out.get(node, np.zeros(3)) + rf[row[arm], :3]
    return out
