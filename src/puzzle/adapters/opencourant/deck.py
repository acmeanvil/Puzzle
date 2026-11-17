"""OpenCourant's input (ADR 0085): the Starter's model deck, the Engine's run deck and
the script that runs both and converts the output.

Radioss's block format: a card is a `/KEY/.../id` line, a title line, then fixed-width
fields, integers 10 characters wide and reals 20 (`_i`, `_r`). Node and element ids
are Gmsh's, so results map straight back. Each material assignment's elements of one
type are a Radioss part (its material and property); loads and supports act on node
groups (`/GRNOD/NODE`), pressures on segment surfaces (`/SURF/SEG`), and anything
varying in time on a function (`/FUNCT`) from the feature's amplitude.

Beams (step 5): two-node /BEAM elements, each with its section's first axis as the
orientation vector (local y; Radioss makes it square to the member), on /PROP/BEAM
with A, Iyy = I1 (about the first axis), Izz = I2 and Ixx = J, shear included.

Element node orders: hex8 and tet4 as Gmsh's; tet10's mid-edge nodes are Gmsh's
(0-1, 1-2, 2-0, 0-3, 2-3, 1-3) with the last two swapped, Radioss taking (2-4, 3-4)
after (1-4).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from puzzle.adapters.common import InputError, body_load, nodal_traction, surface_area
from puzzle.mesh.mesh import Mesh
from puzzle.model.schema import Amplitude, Component, Material, Project, Study

NAME = "puzzle"  # the run's name: puzzle_0000.rad, puzzle_0001.rad, puzzleA001, puzzleT01
SOLIDS = {"tet4": "/TETRA4", "tet10": "/TETRA10", "hex8": "/BRICK"}
SHELLS = {"quad4": "/SHELL", "tri3": "/SH3N"}  # ADR 0085 step 2
BEAMS = {"line2": "/BEAM"}  # ADR 0085 step 5
TET10 = [0, 1, 2, 3, 4, 5, 6, 7, 9, 8]  # Gmsh tet10 -> Radioss TETRA10
HUGE = 1e30  # "never stops"
FRAMES = 20


def _i(*values) -> str:
    return "".join(f"{int(v):>10d}" for v in values)


def _r(*values) -> str:
    out = []
    for v in values:
        text = f"{float(v):.12g}"
        if len(text) > 20:
            text = f"{float(v):.10e}"
        out.append(f"{text:>20}")
    return "".join(out)


def _title(text: str) -> str:
    """A card's title line from a user's name. Radioss skips a line starting with `#` or
    `$` (a comment) and reads one starting with `/` as a keyword, so such a name shifted
    every field after it: an amplitude `#1 ramp` lost its first point, a material
    `#1 Steel` read E as its density (ADR 0090 OC-1). Those starts get a `_` before them."""
    text = "".join(c if c.isprintable() else " " for c in text).lstrip()
    if text[:1] in ("#", "$", "/"):
        text = "_" + text
    return text[:100] or "-"


@dataclass
class Settings:
    """An explicit step's settings (ADR 0085 decision 2), in SI."""

    end_time: float
    frames: int = FRAMES
    frame_interval: float | None = None
    history_interval: float | None = None
    time_step_scale: float = 0.9
    mass_scaling_time_step: float | None = None
    precision: str = "double"

    @property
    def frame_dt(self) -> float:
        return self.frame_interval or self.end_time / max(1, self.frames)

    @property
    def history_dt(self) -> float:
        return self.history_interval or self.end_time / 1000.0


def settings_of(step) -> Settings:
    s = step.settings

    def si(key):
        v = s.get(key)
        if v is None:
            return None
        return float(v.to_si()) if hasattr(v, "to_si") else float(v)

    end = si("end_time")
    if not end or end <= 0:
        raise InputError("an explicit step needs a positive end_time", code="missing_input")
    precision = str(s.get("precision", "double"))
    if precision not in ("double", "single"):
        raise InputError(f"precision is double or single, not '{precision}'")
    scale = si("time_step_scale") or 0.9
    if not 0 < scale <= 1:
        raise InputError(f"time_step_scale must be in (0, 1], not {scale:g}")
    return Settings(
        end,
        int(s.get("frames", FRAMES) or FRAMES),
        si("frame_interval"),
        si("history_interval"),
        scale,
        si("mass_scaling_time_step"),
        precision,
    )


@dataclass
class _Ids:
    """Radioss ids, one counter per kind of card."""

    counters: dict[str, int] = field(default_factory=dict)

    def next(self, kind: str) -> int:
        self.counters[kind] = self.counters.get(kind, 0) + 1
        return self.counters[kind]


class Deck:
    """The Starter deck, card by card."""

    def __init__(self) -> None:
        self.lines: list[str] = ["#RADIOSS STARTER"]
        self.ids = _Ids()
        self.functions: dict[object, int] = {}

    def card(self, key: str, title: str, *rows: str) -> None:
        self.lines += [key, _title(title), *rows]

    def nodes_group(self, title: str, nodes) -> int:
        gid = self.ids.next("grnod")
        nodes = [int(n) for n in nodes]
        rows = [_i(*nodes[k : k + 10]) for k in range(0, len(nodes), 10)]
        self.card(f"/GRNOD/NODE/{gid}", title, *rows)
        return gid

    def function(self, points: list[tuple[float, float]], title: str, key=None) -> int:
        """A /FUNCT of (time, factor), held flat past its ends."""
        if key is not None and key in self.functions:
            return self.functions[key]
        pts = sorted(points)
        if pts[0][0] > 0:
            pts.insert(0, (0.0, pts[0][1]))
        pts.append((max(HUGE, pts[-1][0] * 10), pts[-1][1]))
        fid = self.ids.next("funct")
        self.card(f"/FUNCT/{fid}", title, *[_r(t, y) for t, y in pts])
        if key is not None:
            self.functions[key] = fid
        return fid

    def text(self) -> str:
        return "\n".join([*self.lines, "/END"]) + "\n"


def _material(deck: Deck, m: Material, solver_models: set[str]) -> int:
    """A material card: elastic, or with its plasticity table (true stress against plastic
    strain, the first curve; ADR 0085 decision 3), and its failure model after it."""
    mid = _law(deck, m, solver_models)
    failure = m.models.failure if m.models is not None else None
    if failure is not None:
        plastic = m.models.plasticity is not None or m.models.johnson_cook is not None
        if not plastic:
            raise InputError(f"material '{m.name}': failure needs plasticity (a hardening "
                             "table or Johnson–Cook)", code="incomplete_material")  # fmt: skip
        # hm_cfg_files radioss2018 FAIL/fail_johnson.cfg: D1-D5 (no temperature term:
        # D5 0); ε̇0, Ifail_sh 1 and Ifail_so 1 (an element goes when one point fails),
        # Dadv, Ixfem 0
        deck.lines += [f"/FAIL/JOHNSON/{mid}",
                       _r(failure.strain, failure.d2, failure.d3, failure.d4, 0.0),
                       _r(failure.reference_strain_rate) + _i(1, 1) + " " * 20 + _r(0.0)
                       + " " * 10 + _i(0)]  # fmt: skip
    return mid


def _law(deck: Deck, m: Material, solver_models: set[str]) -> int:
    """The material law's card; its id."""
    props = {k: q.to_si() for k, q in m.properties.items()}
    for key in ("density", "youngs_modulus", "poissons_ratio"):
        if key not in props:
            raise InputError(f"material '{m.name}' has no {key}", code="incomplete_material")
    rho, e, nu = props["density"], props["youngs_modulus"], props["poissons_ratio"]
    mid = deck.ids.next("mat")
    plastic = m.models.plasticity if m.models is not None else None
    jc = m.models.johnson_cook if m.models is not None else None
    if jc is not None and plastic is not None:
        raise InputError(f"material '{m.name}' has both a plasticity table and Johnson–Cook: "
                         "keep one")  # fmt: skip
    if jc is not None:  # (a + b εpⁿ)(1 + c ln(ε̇/ε̇0)), ADR 0085 decision 3
        deck.card(f"/MAT/PLAS_JOHNS/{mid}", m.name, _r(rho, 0.0), _r(e, nu),
                  _r(jc.a, jc.b, jc.n, 0.0, jc.max_stress),
                  _r(jc.c, jc.reference_strain_rate) + _i(1, 1 if jc.rate_cutoff else 0)
                  + _r(jc.rate_cutoff),
                  _r(0.0, 0.0, 0.0, 0.0))  # fmt: skip
        return mid
    if plastic is None:
        deck.card(f"/MAT/ELAST/{mid}", m.name, _r(rho, 0.0), _r(e, nu))
        return mid
    if "plasticity" not in solver_models:
        raise InputError(f"material '{m.name}': plasticity isn't written on OpenCourant")
    curve = next((c for c in plastic.curves if c.temperature is None), plastic.curves[0])
    fid = deck.function([(float(eps), float(sig)) for eps, sig in curve.points],
                        f"{m.name} hardening")  # fmt: skip
    deck.card(f"/MAT/PLAS_TAB/{mid}", m.name, _r(rho, 0.0), _r(e, nu, 0.0, 0.0, 0.0),
              _i(1, 0) + _r(0.0, 0.0, 0.0), _i(0) + _r(0.0), _i(fid), _r(1.0), _r(0.0))  # fmt: skip
    return mid


def _property(deck: Deck, element: str, thickness=None, comp: Component | None = None) -> int:
    """A solid property: HEPH bricks (Isolid 24, physical hourglass); tets as they are
    (Radioss's tetrahedra need no hourglass control). A shell's: QEPH quadrilaterals
    (Ishell 24), five points through the thickness, plasticity iterated. A beam's
    (`thickness` is then its section's index): /PROP/BEAM from `frames.constants`."""
    pid = deck.ids.next("prop")
    if element in BEAMS:
        from puzzle.frames import constants

        section = comp.beam_sections[thickness]
        a, i1, i2, j = constants(section)
        # hm_cfg_files radioss110 PROP/prop_p3_beam.cfg (radioss51 format): Ismstr;
        # Dm, Df (no damping); A, Iyy, Izz, Ixx; the rotations held at neither end,
        # Ishear 0 (shear deformation included)
        deck.card(f"/PROP/BEAM/{pid}", f"beam {section.selection} {section.shape}",
                  " " * 10 + _i(0), _r(0.0, 0.0), _r(a, i1, i2, j),
                  f"{'000':>6} {'000':>3}" + _i(0))  # fmt: skip
        return pid
    if element in SHELLS:
        deck.card(f"/PROP/SHELL/{pid}", f"{element} shell {thickness:g} m",
                  _i(24 if element == "quad4" else 0, 0, 0), _r(0.0, 0.0, 0.0, 0.0, 0.0),
                  _i(5, 1) + _r(thickness, 0.0) + " " * 10 + _i(1, 1))  # fmt: skip
        return pid
    isolid = 24 if element == "hex8" else 0
    deck.card(f"/PROP/SOLID/{pid}", f"{element} solid",
              _i(isolid, 0) + f"{0:>20}{0:>20}" + _i(0, 0) + _r(0.0),
              _r(0.0, 0.0, 0.0, 0.0, 0.0), _r(0.0) + _i(0, 0))  # fmt: skip
    return pid


def _thicknesses(comp: Component, mesh: Mesh) -> dict[int, float]:
    """Each shell element's thickness from the component's shell sections (a face
    selection's after its part's)."""
    out: dict[int, float] = {}
    sections = sorted(comp.shell_sections, key=lambda s: "/" in s.selection)
    for s in sections:
        if s.offset:
            raise InputError(f"shell section on '{s.selection}': offsets aren't written for "
                             "OpenCourant yet", code="unsupported")  # fmt: skip
        if s.fields:
            raise InputError(f"shell section on '{s.selection}': a thickness from a field "
                             "isn't written for OpenCourant", code="unsupported")  # fmt: skip
        group = mesh.groups.get(s.selection)
        if group is None:
            raise InputError(f"a shell thickness names '{s.selection}', which is not meshed")
        for b in group.blocks(mesh):
            if b.dim == 2:
                out.update(dict.fromkeys(b.ids.tolist(), s.thickness.to_si()))
    return out


def _beam_sections(comp: Component, mesh: Mesh) -> dict[int, int]:
    """Each beam element's section (its index in `comp.beam_sections`), an edge
    selection's after its part's."""
    out: dict[int, int] = {}
    order = sorted(range(len(comp.beam_sections)),
                   key=lambda k: "/" in comp.beam_sections[k].selection)  # fmt: skip
    beams = {id(b) for b in mesh.beam_blocks()}
    for k in order:
        s = comp.beam_sections[k]
        group = mesh.groups.get(s.selection)
        if group is None:
            raise InputError(f"a beam section names '{s.selection}', which is not meshed")
        for b in group.blocks(mesh):
            if id(b) in beams:
                out.update(dict.fromkeys(b.ids.tolist(), k))
    return out


def _elements(project: Project, comp: Component, mesh: Mesh):
    """Each element's (material assignment index, element type, shell thickness, a
    beam's section index or None); later assignments win, as elsewhere."""
    owner: dict[int, tuple] = {}
    shells = {id(b) for b in mesh.shell_blocks()}
    beams = {id(b) for b in mesh.beam_blocks()}
    thick = _thicknesses(comp, mesh) if shells else {}
    sections = _beam_sections(comp, mesh) if beams else {}
    for k, a in enumerate(comp.materials):
        group = mesh.groups.get(a.selection)
        if group is None:
            raise InputError(f"material assignment on '{a.selection}': no such selection")
        for b in group.blocks(mesh):
            if b.dim == 2 and id(b) in shells:
                if b.element not in SHELLS:
                    raise InputError(
                        f"{b.element} shells aren't written for OpenCourant (tri3 "
                        "and quad4 are: element order 1)",
                        code="unsupported",
                    )
                for e in b.ids.tolist():
                    if int(e) not in thick:
                        raise InputError(f"the sheet body '{a.selection}' has no thickness: "
                                         "add a shell section", code="invalid")  # fmt: skip
                    owner[int(e)] = (k, b.element, thick[int(e)])
                continue
            if b.dim == 1 and id(b) in beams:
                if b.element not in BEAMS:
                    raise InputError(
                        f"{b.element} beams aren't written for OpenCourant "
                        "(line2 are: element order 1)",
                        code="unsupported",
                    )
                for e in b.ids.tolist():
                    if int(e) not in sections:
                        raise InputError(f"the line body '{a.selection}' has no section: add "
                                         "a beam section", code="invalid")  # fmt: skip
                    owner[int(e)] = (k, b.element, sections[int(e)])
                continue
            if b.dim != 3:
                continue
            if b.element not in SOLIDS:
                raise InputError(f"{b.element} elements aren't written for OpenCourant (tet4, "
                                 "tet10 and hex8 are)", code="unsupported")  # fmt: skip
            for e in b.ids.tolist():
                owner[int(e)] = (k, b.element, None)
    elements = [b for b in mesh.blocks if b.dim == 3 or id(b) in shells or id(b) in beams]
    missing = [int(e) for b in elements for e in b.ids if int(e) not in owner]
    if missing:
        raise InputError(f"{len(missing)} elements have no material", code="incomplete_material")
    return owner


def _connectivity(mesh: Mesh) -> dict[int, np.ndarray]:
    out = {}
    shells = {id(b) for b in mesh.shell_blocks()} | {id(b) for b in mesh.beam_blocks()}
    for b in mesh.blocks:
        if b.dim == 3 or id(b) in shells:
            conn = b.connectivity[:, TET10] if b.element == "tet10" else b.connectivity
            out.update(zip(b.ids.tolist(), conn, strict=True))
    return out


def _amplitude(deck: Deck, project: Project, f) -> int:
    """The feature's time function: its amplitude's points, else 1 throughout."""
    if f.amplitude is None:
        return deck.function([(0.0, 1.0)], "constant", key="constant")
    amp = project.objects.get(f.amplitude)
    if not isinstance(amp, Amplitude):
        raise InputError(f"{f.type} on '{f.selection}': its amplitude doesn't exist")
    return deck.function([tuple(p) for p in amp.points], amp.name, key=amp.id)


def _starts_at_rest(project: Project, f) -> None:
    """A nonzero prescribed displacement must start from zero. Without an amplitude (the
    normal input in a static step) the nodes jump at t = 0, an infinite velocity: a 20 mm
    block moved 0.01 mm so rang at 2.2 times the static stress and the energy check
    blamed the time step (ADR 0090 OC-2). So it is refused, saying what to do."""
    amp = project.objects.get(f.amplitude) if f.amplitude is not None else None
    start = amp.at(0.0) if isinstance(amp, Amplitude) else None
    if f.amplitude is None or (start is not None and start != 0.0):
        said = ("has no amplitude, so it would be applied at once"
                if f.amplitude is None else f"has an amplitude starting at {start:g}, so it "
                "would jump at t = 0")  # fmt: skip
        raise InputError(
            f"displacement on '{f.selection}' {said}: in an explicit step that is an "
            "impact. Give it an amplitude rising from 0 (a ramp over the step for a slow "
            "pull), or prescribe a velocity instead",
            code="invalid_input",
            details={"selection": f.selection},
        )


def _group(mesh: Mesh, f):
    group = mesh.groups.get(f.selection)
    if group is None:
        raise InputError(f"{f.type} on '{f.selection}': no such selection in the mesh")
    return group


def _features(deck: Deck, project: Project, comp: Component, mesh: Mesh) -> None:
    """Supports, loads, velocities and walls (ADR 0085 decision 3)."""
    for physics in comp.physics:
        if physics.type != "solid_mechanics":
            continue
        for f in physics.features:
            v = {k: q.to_si() for k, q in f.values.items()}
            group = _group(mesh, f)
            nodes = group.node_ids
            title = f"{f.type} {f.selection}"
            if f.type == "fixed":  # on shell and beam nodes their rotations too
                gid = deck.nodes_group(title, nodes)
                turns = "111" if set(map(int, nodes)) & _turning(mesh) else "000"
                deck.card(f"/BCS/{deck.ids.next('bcs')}", title,
                          f"{'111':>6}{turns:>4}" + _i(0, gid))  # fmt: skip
            elif f.type == "displacement":
                held = "".join("1" if v.get(a) == 0.0 else "0" for a in "xyz")
                moved = [a for a in "xyz" if v.get(a)]
                gid = deck.nodes_group(title, nodes)
                if "1" in held:
                    deck.card(f"/BCS/{deck.ids.next('bcs')}", title,
                              f"{held:>6}{'000':>4}" + _i(0, gid))  # fmt: skip
                if moved:
                    _starts_at_rest(project, f)
                fid = _amplitude(deck, project, f) if moved else 0
                for a in moved:
                    deck.card(f"/IMPDISP/{deck.ids.next('impdisp')}", title,
                              _i(fid) + f"{a.upper():>10}" + _i(0, 0, gid),
                              _r(1.0, v[a], 0.0, HUGE))  # fmt: skip
            elif f.type == "velocity":
                gid = deck.nodes_group(title, nodes)
                fid = _amplitude(deck, project, f)
                for a in "xyz":
                    if a in v:
                        deck.card(f"/IMPVEL/{deck.ids.next('impvel')}", title,
                                  _i(fid) + f"{a.upper():>10}" + _i(0, 0, gid, 0, 0),
                                  _r(1.0, v[a], 0.0, HUGE))  # fmt: skip
            elif f.type == "initial_velocity":
                gid = deck.nodes_group(title, nodes)
                deck.card(
                    f"/INIVEL/TRA/{deck.ids.next('inivel')}",
                    title,
                    _r(v.get("vx", 0.0), v.get("vy", 0.0), v.get("vz", 0.0)) + _i(gid, 0),
                )
            elif f.type == "force":
                _force(deck, project, f, group, mesh, v, title)
            elif f.type == "pressure":
                _pressure(deck, project, f, group, mesh, v, title)
            elif f.type in ("gravity", "acceleration"):
                load = np.asarray(body_load(f).uniform, float)  # gravity: g; inertia: −a
                gid = deck.nodes_group(title, nodes)
                fid = _amplitude(deck, project, f)
                for k, a in enumerate("XYZ"):
                    if load[k]:
                        deck.card(f"/GRAV/{deck.ids.next('grav')}", title,
                                  _i(fid) + f"{a:>10}" + _i(0, 0, gid) + " " * 10
                                  + _r(1.0, load[k]))  # fmt: skip
            elif f.type == "rigid_wall":
                _wall(deck, f, nodes, v, title)
            else:
                raise InputError(f"'{f.type}' isn't written for OpenCourant", code="unsupported")


def _turning(mesh: Mesh) -> set[int]:
    """Nodes with rotations: shells' and beams'."""
    return set(mesh.shell_nodes()) | mesh.beam_nodes()


def _force(deck, project, f, group, mesh, v, title) -> None:
    """A total force spread as a uniform traction, consistently over the faces or along
    the edges (a point selection: shared equally), one /CLOAD per node and direction."""
    total = np.array([v.get(a, 0.0) for a in "xyz"])
    if group.dim == 2:
        area = surface_area(group, mesh)
        forces = nodal_traction(group, mesh, lambda x: np.tile(total / area, (len(x), 1)))
    elif group.dim == 1 and (shares := _edge_shares(group, mesh)):
        forces = {n: share * total for n, share in shares.items()}
    else:
        forces = {int(n): total / len(group.node_ids) for n in group.node_ids}
    fid = _amplitude(deck, project, f)
    for node, force in sorted(forces.items()):
        gid = None
        for k, a in enumerate("XYZ"):
            if abs(force[k]) > 0:
                gid = gid or deck.nodes_group(f"{title} node {node}", [node])
                deck.card(
                    f"/CLOAD/{deck.ids.next('cload')}",
                    title,
                    _i(fid) + f"{a:>10}" + _i(0, 0, gid) + " " * 10 + _r(1.0, force[k]),
                )


def _edge_shares(group, mesh: Mesh) -> dict[int, float]:
    """Each node's share of a uniform load along an edge selection's line elements: its
    tributary length over the whole (half of each two-node element; a three-node one's
    1/6, 1/6, 2/3). Shared equally, an end node took an interior one's share and uneven
    spacing was ignored (ADR 0090 OC-6)."""
    shares: dict[int, float] = {}
    for b in group.blocks(mesh):
        if b.dim != 1 or b.element not in ("line2", "line3"):
            continue
        xyz = mesh.node_coords(b.connectivity[:, :2].ravel()).reshape(len(b.ids), 2, 3)
        length = np.linalg.norm(xyz[:, 1] - xyz[:, 0], axis=1)
        weights = (0.5, 0.5) if b.element == "line2" else (1 / 6, 1 / 6, 2 / 3)
        for k, w in enumerate(weights):
            for n, dl in zip(b.connectivity[:, k], length, strict=True):
                shares[int(n)] = shares.get(int(n), 0.0) + w * float(dl)
    whole = sum(shares.values())
    return {n: s / whole for n, s in shares.items()} if whole > 0 else {}


def _pressure(deck, project, f, group, mesh, v, title) -> None:
    """A pressure on segments facing out of the solid each face bounds (/PLOAD on
    /SURF/SEG); tri6 faces (tet10 meshes) are refused for now."""
    sid = _segments(deck, group, mesh, title, corners_only=False)
    fid = _amplitude(deck, project, f)
    # hm_cfg_files radioss51 (decks to 2019): surf, funct, sensor, 30 blanks, the scales.
    # Radioss's pressure pushes along the segment's normal (out of the solid, along a
    # shell's face normal); Puzzle's pushes against it: so −p (measured on a plate).
    deck.card(f"/PLOAD/{deck.ids.next('pload')}", title,
              _i(sid, fid, 0) + " " * 30 + _r(1.0, -v["p"]))  # fmt: skip


def _segments(deck, group, mesh, title, corners_only=True) -> int:
    """A face selection as /SURF/SEG, each segment's normal out of the solid it bounds
    (a shell's own face as it is). Six-node faces as their corners when `corners_only`
    (a contact's master surface), else refused (a pressure's)."""
    from puzzle.adapters.common import surface_blocks

    centres = {}
    for b in mesh.blocks:
        if b.dim == 3:  # each element's centre from all its corners (a hex8's first four
            k = 4 if b.element.startswith("tet") else 8  # are one face, not the middle)
            corners = mesh.node_coords(b.connectivity[:, :k].ravel()).reshape(len(b.ids), k, 3)
            for nodes, c in zip(b.connectivity, corners.mean(axis=1), strict=True):
                for n in nodes[:k]:
                    centres.setdefault(int(n), []).append(c)
    rows, sid = [], deck.ids.next("surf")
    blocks = surface_blocks(group, mesh)
    if not blocks:
        raise InputError(f"'{group.name}' has no faces to load or touch", code="invalid_input")
    for b in blocks:
        corners = {"tri3": 3, "quad4": 4, "tri6": 3, "quad8": 4, "quad9": 4}.get(b.element)
        if corners is None or (b.element not in ("tri3", "quad4") and not corners_only):
            raise InputError(f"pressure on {b.element} faces isn't written for OpenCourant yet "
                             "(first-order meshes only)", code="unsupported")  # fmt: skip
        for nodes in b.connectivity[:, :corners]:
            xyz = mesh.node_coords(nodes[:3])
            normal = np.cross(xyz[1] - xyz[0], xyz[2] - xyz[0])
            seg = [int(n) for n in nodes]
            near = [centres[int(n)] for n in nodes if int(n) in centres]
            if near:  # a solid's face: the segment's normal outward
                inside = np.mean([np.mean(c, axis=0) for c in near], axis=0)
                if normal @ (xyz.mean(axis=0) - inside) < 0:
                    seg = seg[::-1]
            rows.append(_i(len(rows) + 1, *seg))
    deck.card(f"/SURF/SEG/{sid}", title, *rows)
    return sid


def _contacts(deck, comp: Component, mesh: Mesh) -> None:
    """ADR 0085 step 2: a bonded contact across meshes that don't match as /INTER/TYPE2
    (the source's nodes tied to the target's faces; a conformal one shares nodes and
    needs nothing); frictionless and frictional as /INTER/TYPE7 both ways (penalty,
    Coulomb friction), and a selection's contact with itself as one /INTER/TYPE7 of its
    nodes on its own faces. Others are refused."""
    for c in comp.contacts:
        if c.thread is not None:
            raise InputError(f"contact '{c.name}': threads aren't written for OpenCourant",
                             code="unsupported")  # fmt: skip
        if c.is_self and c.type not in ("frictionless", "frictional"):
            raise InputError(f"contact '{c.name}': a self-contact slides (frictionless or "
                             "frictional), it can't be bonded", code="invalid")  # fmt: skip
        if c.type == "bonded" and c.effective_meshing == "conformal":
            continue
        groups = []
        for sel in (c.source, c.target):
            g = mesh.groups.get(sel)
            if g is None:
                raise InputError(f"contact '{c.name}': '{sel}' is not in the mesh")
            groups.append(g)
        source, target = groups
        if c.type == "bonded":
            gid = deck.nodes_group(f"{c.name} tied nodes", source.node_ids)
            sid = _segments(deck, target, mesh, f"{c.name} target")
            deck.card(f"/INTER/TYPE2/{deck.ids.next('inter')}", c.name,
                      _i(gid, sid) + f"{0:>20}" + _i(0, 0, 0, 0) + _r(0.0), _r(1.0))  # fmt: skip
        elif c.type in ("frictionless", "frictional"):
            mu = float(c.friction or 0.0) if c.type == "frictional" else 0.0
            # a self-contact (ADR 0085 step 5): the faces' own nodes against them, once;
            # Radioss leaves out each node's own segments
            pairs = [(source, target)] if c.is_self else [(source, target), (target, source)]
            gap = self_gap(source, mesh) if c.is_self else 0.0  # 0: Radioss's own
            for a, b in pairs:
                gid = deck.nodes_group(f"{c.name} nodes", a.node_ids)
                sid = _segments(deck, b, mesh, f"{c.name} faces")
                deck.card(f"/INTER/TYPE7/{deck.ids.next('inter')}", c.name,
                          _i(gid, sid, 0, 0, 0, 0, 0, 0, 0, 0), _r(0.0, 0.0), _r(0.0, 0.0),
                          _r(0.0, mu, gap, 0.0, 0.0),
                          # INACTI 6: nodes inside the gap at the start (touching
                          # parts) aren't pushed out: the gap shrinks for them instead
                          f"{'000':>10}" + " " * 20 + _i(6) + _r(0.0, 0.0, 0.0),
                          _i(0, 0) + _r(0.0) + _i(0))  # fmt: skip
        else:
            raise InputError(f"contact '{c.name}': {c.type} contact isn't written for "
                             "OpenCourant (bonded, frictionless and frictional are)",
                             code="unsupported")  # fmt: skip


def self_gap(group, mesh: Mesh) -> float:
    """A self-contact's gap: Radioss's own (a tenth of the smallest face edge, 0 here)
    unless faces that share no element start closer than that, as touching bodies or
    coplanar sides across a slit do; then half the nearest such distance. Starting
    inside the gap, INACTI 6 shrinks it to about nothing for those nodes and the
    penalty blows up (measured: bars 0.1 mm apart under a 0.33 mm gap reached 600 m/s);
    two-part contacts don't see their sides, so they keep the default."""
    from scipy.spatial import cKDTree

    from puzzle.adapters.common import surface_blocks

    nodes = np.asarray(sorted(int(n) for n in group.node_ids))
    if len(nodes) < 2:
        return 0.0
    edges = []
    for b in surface_blocks(group, mesh):
        k = 3 if b.element.startswith("tri") else 4
        xyz = mesh.node_coords(b.connectivity[:, :k].ravel()).reshape(len(b.ids), k, 3)
        edges.append(np.linalg.norm(xyz - np.roll(xyz, 1, axis=1), axis=2).min())
    if not edges:
        return 0.0
    default = 0.1 * float(min(edges))
    xyz = mesh.node_coords(nodes)
    close = cKDTree(xyz).query_pairs(default, output_type="ndarray")
    if not len(close):
        return 0.0
    owners: dict[int, set] = {}  # node -> the elements (solid or shell) it belongs to
    wanted = set(nodes.tolist())
    for b in mesh.blocks:
        if b.dim >= 2:
            for e, row in zip(b.ids.tolist(), b.connectivity, strict=True):
                for n in row:
                    if int(n) in wanted:
                        owners.setdefault(int(n), set()).add((b.dim, e))
    apart = [
        np.linalg.norm(xyz[i] - xyz[j])
        for i, j in close
        if not owners.get(int(nodes[i]), set()) & owners.get(int(nodes[j]), set())
    ]
    return 0.5 * float(min(apart)) if apart else 0.0


def _wall(deck, f, nodes, v, title) -> None:
    """An infinite rigid wall through a point, its normal toward the model; mu > 0 is
    Coulomb friction, else it slides."""
    n = np.array([v.get("nx", 0.0), v.get("ny", 0.0), v.get("nz", 0.0)])
    if not np.linalg.norm(n):
        raise InputError(f"rigid wall on '{f.selection}': its normal has no direction")
    n = n / np.linalg.norm(n)
    p = np.array([v.get("x0", 0.0), v.get("y0", 0.0), v.get("z0", 0.0)])
    mu = float(v.get("mu", 0.0))
    gid = deck.nodes_group(title, nodes)
    deck.card(f"/RWALL/PLANE/{deck.ids.next('rwall')}", title,
              " " * 10 + _i(2 if mu else 0, gid, 0),
              _r(0.0, mu, 0.0, 0.0) + _i(0), _r(*p), _r(*(p + n)))  # fmt: skip


def write(project: Project, study: Study, mesh: Mesh, workdir: Path, step, processes: int = 1,
          models: set[str] = frozenset()) -> Path:  # fmt: skip
    """Write puzzle_0000.rad, puzzle_0001.rad and run.sh; return run.sh. With
    `processes` > 1 the Starter splits the model into that many domains and the Engine
    runs under MPI (the image's OpenMPI), the cores per run shared among them."""
    comp = project.objects[study.component]
    s = settings_of(step)
    deck = Deck()
    deck.lines += ["/BEGIN", _title(study.name), _i(2019, 0),
                   f"{'kg':>20}{'m':>20}{'s':>20}", f"{'kg':>20}{'m':>20}{'s':>20}"]  # fmt: skip
    owner = _elements(project, comp, mesh)
    conn = _connectivity(mesh)
    mats = {}
    for k, a in enumerate(comp.materials):
        m = project.objects.get(a.material)
        if not isinstance(m, Material):
            raise InputError(f"material assignment on '{a.selection}': no such material")
        if k in {o[0] for o in owner.values()}:
            mats[k] = _material(deck, m, models)
    used = coords = None
    used = sorted({int(n) for c in conn.values() for n in c})
    coords = mesh.node_coords(np.asarray(used))
    deck.lines.append("/NODE")
    deck.lines += [f"{n:>10d}" + _r(*xyz) for n, xyz in zip(used, coords, strict=True)]
    parts: dict[tuple, list[int]] = {}
    for e, key in owner.items():
        parts.setdefault(key, []).append(e)
    for (k, element, thickness), elements in sorted(parts.items(), key=lambda kv: (
            kv[0][0], kv[0][1], kv[0][2] or 0.0)):  # fmt: skip
        pid = deck.ids.next("part")
        prop = _property(deck, element, thickness, comp)
        a = comp.materials[k]
        deck.card(f"/PART/{pid}", f"{a.selection} {element}", _i(prop, mats[k], 0))
        deck.lines.append(f"{SOLIDS.get(element) or SHELLS.get(element) or BEAMS[element]}/{pid}")
        if element in BEAMS:  # id, the two nodes, no third node: the first axis as Vx Vy Vz
            v = np.asarray(comp.beam_sections[thickness].orientation, float)
            deck.lines += [_i(e, *conn[e], 0) + _r(*v) for e in sorted(elements)]
        elif element == "tet10":  # the id on its own line, then the ten nodes
            for e in sorted(elements):  # (hm_cfg_files ELEM/tetra10.cfg)
                deck.lines += [_i(e), _i(*conn[e])]
        else:
            deck.lines += [_i(e, *conn[e]) for e in sorted(elements)]
    _features(deck, project, comp, mesh)
    _contacts(deck, comp, mesh)
    name = NAME
    (workdir / f"{name}_0000.rad").write_text(deck.text(), encoding="utf-8", newline="\n")
    (workdir / f"{name}_0001.rad").write_text(engine(s, mesh, project, comp), encoding="utf-8",
                                              newline="\n")  # fmt: skip
    sp = "_sp" if s.precision == "single" else ""
    script = "\n".join([
        "#!/bin/sh",
        "# Written by Puzzle (ADR 0085): the Starter, the Engine, then the output converted.",
        "set -e",
        f"starter{sp} -i {name}_0000.rad -np {processes}",
        (f"mpiexec -np {processes} engine_ompi{sp} -i {name}_0001.rad" if processes > 1
         else f"engine{sp} -i {name}_0001.rad"),
        # every frame, A001 to A999 and on past it (ADR 0090 OC-5), not the .vtk made here
        f"for f in {name}A[0-9][0-9][0-9]*; do case \"$f\" in *.*) continue;; esac; "
        "[ -e \"$f\" ] && anim_to_vtk_linux64_gf \"$f\" > \"$f.vtk\"; done",
        f"[ -e {name}T01 ] && th_to_csv_linux64_gf {name}T01 > /dev/null",
        "exit 0",
    ])  # fmt: skip
    run = workdir / "run.sh"
    run.write_text(script + "\n", encoding="utf-8", newline="\n")
    # what the native driver (native.py) needs to do the same without a shell
    job = {"name": name, "processes": processes, "precision": s.precision}
    (workdir / "run.json").write_text(json.dumps(job), encoding="utf-8")
    return run


def engine(s: Settings, mesh: Mesh, project: Project, comp: Component) -> str:
    """The Engine's deck: the end time, output and time-step control. The run goes two
    time steps past the end: the Engine stops at the end before writing a frame there."""
    from puzzle import explicit

    est = explicit.estimate(project, comp, mesh, s.end_time, s.time_step_scale)
    cycles = est.cycles if est is not None else 1e4
    every = max(1, int(math.ceil(cycles / 200)))  # about 200 progress lines
    past = 2 * est.time_step * s.time_step_scale if est is not None else 1e-6 * s.end_time
    if s.mass_scaling_time_step:
        past = max(past, 2 * s.mass_scaling_time_step)
    lines = ["#RADIOSS ENGINE", f"/RUN/{NAME}/1", _r(s.end_time + past),
             "/TFILE/0", _r(s.history_dt), "/ANIM/DT", _r(0.0, s.frame_dt),
             "/ANIM/VECT/DISP", "/ANIM/VECT/VEL", "/ANIM/VECT/ACC",
             "/ANIM/BRICK/TENS/STRESS", "/ANIM/ELEM/VONM", "/ANIM/ELEM/EPSP",
             *(["/ANIM/SHELL/TENS/STRESS/MEMB"] if mesh.shell_blocks() else []),
             f"/PRINT/-{every}"]  # fmt: skip
    if s.mass_scaling_time_step:
        lines += ["/DT/NODA/CST/0", _r(s.time_step_scale, s.mass_scaling_time_step)]
    elif s.time_step_scale != 0.9:
        lines += ["/DT", _r(s.time_step_scale, 0.0)]
    return "\n".join([*lines, "/END"]) + "\n"
