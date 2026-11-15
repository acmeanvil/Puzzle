"""OpenCourant's input (ADR 0085): the Starter's model deck, the Engine's run deck and
the script that runs both and converts the output.

Radioss's block format: a card is a `/KEY/.../id` line, a title line, then fixed-width
fields, integers 10 characters wide and reals 20 (`_i`, `_r`). Node and element ids
are Gmsh's, so results map straight back. Each material assignment's elements of one
type are a Radioss part (its material and property); loads and supports act on node
groups (`/GRNOD/NODE`), pressures on segment surfaces (`/SURF/SEG`), and anything
varying in time on a function (`/FUNCT`) from the feature's amplitude.

Element node orders: hex8 and tet4 as Gmsh's; tet10's mid-edge nodes are Gmsh's
(0-1, 1-2, 2-0, 0-3, 2-3, 1-3) with the last two swapped, Radioss taking (2-4, 3-4)
after (1-4).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from puzzle.adapters.common import InputError, body_load, nodal_traction, surface_area
from puzzle.mesh.mesh import Mesh
from puzzle.model.schema import Amplitude, Component, Material, Project, Study

NAME = "puzzle"  # the run's name: puzzle_0000.rad, puzzle_0001.rad, puzzleA001, puzzleT01
SOLIDS = {"tet4": "/TETRA4", "tet10": "/TETRA10", "hex8": "/BRICK"}
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
    return "".join(c if c.isprintable() else " " for c in text)[:100] or "-"


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
    strain, the first curve; ADR 0085 decision 3)."""
    props = {k: q.to_si() for k, q in m.properties.items()}
    for key in ("density", "youngs_modulus", "poissons_ratio"):
        if key not in props:
            raise InputError(f"material '{m.name}' has no {key}", code="incomplete_material")
    rho, e, nu = props["density"], props["youngs_modulus"], props["poissons_ratio"]
    mid = deck.ids.next("mat")
    plastic = m.models.plasticity if m.models is not None else None
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


def _property(deck: Deck, element: str) -> int:
    """A solid property: HEPH bricks (Isolid 24, physical hourglass); tets as they are
    (Radioss's tetrahedra need no hourglass control)."""
    pid = deck.ids.next("prop")
    isolid = 24 if element == "hex8" else 0
    deck.card(f"/PROP/SOLID/{pid}", f"{element} solid",
              _i(isolid, 0) + f"{0:>20}{0:>20}" + _i(0, 0) + _r(0.0),
              _r(0.0, 0.0, 0.0, 0.0, 0.0), _r(0.0) + _i(0, 0))  # fmt: skip
    return pid


def _elements(project: Project, comp: Component, mesh: Mesh) -> dict[int, tuple[int, str]]:
    """Each solid element's material assignment index and element type; later
    assignments win, as elsewhere."""
    owner: dict[int, tuple[int, str]] = {}
    for k, a in enumerate(comp.materials):
        group = mesh.groups.get(a.selection)
        if group is None:
            raise InputError(f"material assignment on '{a.selection}': no such selection")
        for b in group.blocks(mesh):
            if b.dim != 3:
                continue
            if b.element not in SOLIDS:
                raise InputError(f"{b.element} elements aren't written for OpenCourant (tet4, "
                                 "tet10 and hex8 are)", code="unsupported")  # fmt: skip
            for e in b.ids.tolist():
                owner[int(e)] = (k, b.element)
    solids = [b for b in mesh.blocks if b.dim == 3]
    missing = [int(e) for b in solids for e in b.ids if int(e) not in owner]
    if missing:
        raise InputError(f"{len(missing)} elements have no material", code="incomplete_material")
    return owner


def _connectivity(mesh: Mesh) -> dict[int, np.ndarray]:
    out = {}
    for b in mesh.blocks:
        if b.dim == 3:
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
            if f.type == "fixed":
                gid = deck.nodes_group(title, nodes)
                deck.card(f"/BCS/{deck.ids.next('bcs')}", title,
                          f"{'111':>6}{'000':>4}" + _i(0, gid))  # fmt: skip
            elif f.type == "displacement":
                held = "".join("1" if v.get(a) == 0.0 else "0" for a in "xyz")
                moved = [a for a in "xyz" if v.get(a)]
                gid = deck.nodes_group(title, nodes)
                if "1" in held:
                    deck.card(f"/BCS/{deck.ids.next('bcs')}", title,
                              f"{held:>6}{'000':>4}" + _i(0, gid))  # fmt: skip
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


def _force(deck, project, f, group, mesh, v, title) -> None:
    """A total force spread as a uniform traction, consistently over the faces (a node
    selection: shared equally), one /CLOAD per node and direction."""
    total = np.array([v.get(a, 0.0) for a in "xyz"])
    if group.dim == 2:
        area = surface_area(group, mesh)
        forces = nodal_traction(group, mesh, lambda x: np.tile(total / area, (len(x), 1)))
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


def _pressure(deck, project, f, group, mesh, v, title) -> None:
    """A pressure on segments facing out of the solid each face bounds (/PLOAD on
    /SURF/SEG); tri6 faces (tet10 meshes) are refused for now."""
    from puzzle.adapters.common import surface_blocks

    p = v["p"]
    centres = {}
    for b in mesh.blocks:
        if b.dim == 3:
            corners = mesh.node_coords(b.connectivity[:, :4].ravel()).reshape(len(b.ids), 4, 3)
            for nodes, c in zip(b.connectivity, corners.mean(axis=1), strict=True):
                for n in nodes[:4] if b.element.startswith("tet") else nodes:
                    centres.setdefault(int(n), []).append(c)
    rows, sid = [], deck.ids.next("surf")
    for b in surface_blocks(group, mesh):
        if b.element not in ("tri3", "quad4"):
            raise InputError(f"pressure on {b.element} faces isn't written for OpenCourant yet "
                             "(first-order meshes only)", code="unsupported")  # fmt: skip
        for nodes in b.connectivity:
            xyz = mesh.node_coords(nodes[:3])
            normal = np.cross(xyz[1] - xyz[0], xyz[2] - xyz[0])
            inside = np.mean([np.mean(centres[int(n)], axis=0) for n in nodes], axis=0)
            seg = [int(n) for n in nodes]
            if normal @ (xyz.mean(axis=0) - inside) < 0:  # the segment's normal outward
                seg = seg[::-1]
            rows.append(_i(len(rows) + 1, *seg))
    deck.card(f"/SURF/SEG/{sid}", title, *rows)
    fid = _amplitude(deck, project, f)
    deck.card(f"/PLOAD/{deck.ids.next('pload')}", title, _i(sid, fid) + " " * 20 + _r(1.0, p))


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


def write(project: Project, study: Study, mesh: Mesh, workdir: Path, step, threads: int = 1,
          models: set[str] = frozenset()) -> Path:  # fmt: skip
    """Write puzzle_0000.rad, puzzle_0001.rad and run.sh; return run.sh."""
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
    parts: dict[tuple[int, str], list[int]] = {}
    for e, key in owner.items():
        parts.setdefault(key, []).append(e)
    for (k, element), elements in sorted(parts.items()):
        pid = deck.ids.next("part")
        prop = _property(deck, element)
        a = comp.materials[k]
        deck.card(f"/PART/{pid}", f"{a.selection} {element}", _i(prop, mats[k], 0))
        deck.lines.append(f"{SOLIDS[element]}/{pid}")
        deck.lines += [_i(e, *conn[e]) for e in sorted(elements)]
    _features(deck, project, comp, mesh)
    name = NAME
    (workdir / f"{name}_0000.rad").write_text(deck.text(), encoding="utf-8", newline="\n")
    (workdir / f"{name}_0001.rad").write_text(engine(s, mesh, project, comp), encoding="utf-8",
                                              newline="\n")  # fmt: skip
    sp = "_sp" if s.precision == "single" else ""
    script = "\n".join([
        "#!/bin/sh",
        "# Written by Puzzle (ADR 0085): the Starter, the Engine, then the output converted.",
        "set -e",
        f"starter{sp} -i {name}_0000.rad -np 1",
        f"engine{sp} -i {name}_0001.rad",
        f"for f in {name}A[0-9][0-9][0-9]; do [ -e \"$f\" ] && anim_to_vtk_linux64_gf \"$f\" "
        "> \"$f.vtk\"; done",
        f"[ -e {name}T01 ] && th_to_csv_linux64_gf {name}T01 > /dev/null",
        "exit 0",
    ])  # fmt: skip
    run = workdir / "run.sh"
    run.write_text(script + "\n", encoding="utf-8", newline="\n")
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
             f"/PRINT/-{every}"]  # fmt: skip
    if s.mass_scaling_time_step:
        lines += ["/DT/NODA/CST/0", _r(s.time_step_scale, s.mass_scaling_time_step)]
    elif s.time_step_scale != 0.9:
        lines += ["/DT", _r(s.time_step_scale, 0.0)]
    return "\n".join([*lines, "/END"]) + "\n"
