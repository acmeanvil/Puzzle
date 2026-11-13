"""Bolts (ADR 0050): the standard sizes, bolt types and what a bolt resolves to.

A bolt (`BeamFastener`) may name a `BoltType`, which holds a size, a grade (a
material), a preload rule, the formulation and the ends once for every bolt of that
kind; a value set on the bolt itself wins. `resolve` gives the bolt with every value
filled in, and `resolved(project)` a copy of the project whose bolts are all resolved,
which is what the solvers, the view and the outline read, so none of them needs to
know about types.

Sizes are the published ones: ISO metric coarse and fine pitch (ISO 261/262) M3 to
M64, UNC to 4 in and UNF to 1 1/2 in (ASME B1.1). Heads, nuts, washers and clearance
holes of the standard parts are in `puzzle.fasteners`; VDI 2230's bolt and joint
calculations in `puzzle.vdi2230`. Tensile stress areas follow each standard's formula,
As = π/4 (d − 0.9382 P)² for ISO and As = π/4 (d − 0.9743/n)² for UN (both in their
own units), which reproduce the tabulated areas (M10: 58.0 mm²; 1/2-13: 0.1419 in²).
The bearing diameter, the face under a head or nut, is ISO 4017/4032's minimum dw
for metric hex bolts and nuts and the width across flats for inch hex (ASME
B18.2.1/B18.2.2): the annulus a spider takes in joint styles that bear on a face.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass, field

INCH = 0.0254


@dataclass(frozen=True)
class Size:
    """One standard thread size. Lengths in metres, area in m²."""

    standard: str  # iso_coarse, iso_fine, unc, unf
    name: str  # "M10", "M10x1.25", "1/2-13 UNC"
    diameter: float
    pitch: float
    bearing: float  # outer diameter of the face under the head or nut
    stress_area: float


def _iso(standard: str, d_mm: float, p_mm: float, dw_mm: float, fine: bool) -> Size:
    name = f"M{d_mm:g}" + (f"x{p_mm:g}" if fine else "")
    area = math.pi / 4 * (d_mm - 0.9382 * p_mm) ** 2 * 1e-6
    return Size(standard, name, d_mm * 1e-3, p_mm * 1e-3, dw_mm * 1e-3, area)


def _un(standard: str, label: str, d_in: float, tpi: float, af_in: float) -> Size:
    series = "UNC" if standard == "unc" else "UNF"
    area = math.pi / 4 * (d_in - 0.9743 / tpi) ** 2 * INCH**2
    n = f"{int(tpi)} 1/2" if tpi % 1 else f"{int(tpi)}"  # 4.5 threads per inch: "2-4 1/2 UNC"
    return Size(standard, f"{label}-{n} {series}", d_in * INCH, INCH / tpi, af_in * INCH, area)


# d, coarse pitch, fine pitch, dw min (ISO 4017/4032), all mm.
_METRIC = [
    (3, 0.5, 0.35, 4.57), (4, 0.7, 0.5, 5.88), (5, 0.8, 0.5, 6.88), (6, 1.0, 0.75, 8.88),
    (8, 1.25, 1.0, 11.63), (10, 1.5, 1.25, 14.63), (12, 1.75, 1.5, 16.63),
    (14, 2.0, 1.5, 19.64), (16, 2.0, 1.5, 22.49), (18, 2.5, 1.5, 25.34),
    (20, 2.5, 1.5, 28.19), (22, 2.5, 1.5, 31.71), (24, 3.0, 2.0, 33.61),
    (27, 3.0, 2.0, 38.0), (30, 3.5, 2.0, 42.75), (33, 3.5, 2.0, 46.55),
    (36, 4.0, 3.0, 51.11),
]  # fmt: skip

# label, d (in), UNC threads per inch, UNF threads per inch, hex width across flats (in).
_INCH = [
    ("#4", 0.112, 40, 48, 0.25), ("#6", 0.138, 32, 40, 0.3125), ("#8", 0.164, 32, 36, 0.34375),
    ("#10", 0.190, 24, 32, 0.375), ("1/4", 0.25, 20, 28, 0.4375), ("5/16", 0.3125, 18, 24, 0.5),
    ("3/8", 0.375, 16, 24, 0.5625), ("7/16", 0.4375, 14, 20, 0.625), ("1/2", 0.5, 13, 20, 0.75),
    ("9/16", 0.5625, 12, 18, 0.8125), ("5/8", 0.625, 11, 18, 0.9375), ("3/4", 0.75, 10, 16, 1.125),
    ("7/8", 0.875, 9, 14, 1.3125), ("1", 1.0, 8, 12, 1.5), ("1-1/8", 1.125, 7, 12, 1.6875),
    ("1-1/4", 1.25, 7, 12, 1.875), ("1-3/8", 1.375, 6, 12, 2.0625), ("1-1/2", 1.5, 6, 12, 2.25),
]  # fmt: skip

# Sizes from Lacewing Lab's fastener tables (`puzzle.fasteners`, checked against them in
# tests/test_fasteners.py). M39-M64 with ISO 4032's coarse and fine pitches; their dw is
# ISO 4014's product grade B dw min = 0.95 s min (s min from the ISO 4014/4017 table),
# the rule the M27-M36 values above already follow.
_METRIC_LARGE = [
    (39, 4.0, 3.0, 55.86), (42, 4.5, 3.0, 59.95), (45, 4.5, 3.0, 64.70), (48, 5.0, 3.0, 69.45),
    (52, 5.0, 3.0, 74.20), (56, 5.5, 4.0, 78.66), (60, 5.5, 4.0, 83.41), (64, 6.0, 4.0, 88.16),
]  # fmt: skip
# Further ISO 262 fine pitches (ISO 4032's fine column and its note's extra-fine ones):
# d, pitch, the size's dw as above.
_METRIC_FINE_MORE = [(10, 1.0, 14.63), (12, 1.25, 16.63), (18, 2.0, 25.34), (20, 2.0, 28.19),
                     (22, 2.0, 31.71)]  # fmt: skip
# UNC over 1 1/2 in (ASME B1.1 as ASME B18.2.1 Table 2's hex bolts list them, with their
# width across flats F): label, d (in), threads per inch, F (in). UNF stops at 1 1/2 in.
_INCH_LARGE = [
    ("1-3/4", 1.75, 5, 2.625), ("2", 2.0, 4.5, 3.0), ("2-1/4", 2.25, 4.5, 3.375),
    ("2-1/2", 2.5, 4, 3.75), ("2-3/4", 2.75, 4, 4.125), ("3", 3.0, 4, 4.5),
    ("3-1/4", 3.25, 4, 4.875), ("3-1/2", 3.5, 4, 5.25), ("3-3/4", 3.75, 4, 5.625),
    ("4", 4.0, 4, 6.0),
]  # fmt: skip

_ORDER = ("iso_coarse", "iso_fine", "unc", "unf")
# Each standard's sizes by diameter; the sizes listed first above stay first among sizes
# of one diameter (a stable sort), so a bolt type's nearest size is unchanged.
SIZES: list[Size] = sorted(
    [_iso("iso_coarse", d, pc, dw, False) for d, pc, _pf, dw in _METRIC]
    + [_iso("iso_fine", d, pf, dw, True) for d, _pc, pf, dw in _METRIC]
    + [_un("unc", label, d, nc, af) for label, d, nc, _nf, af in _INCH]
    + [_un("unf", label, d, nf, af) for label, d, _nc, nf, af in _INCH]
    + [_iso("iso_coarse", d, pc, dw, False) for d, pc, _pf, dw in _METRIC_LARGE]
    + [_iso("iso_fine", d, pf, dw, True) for d, _pc, pf, dw in _METRIC_LARGE]
    + [_iso("iso_fine", d, p, dw, True) for d, p, dw in _METRIC_FINE_MORE]
    + [_un("unc", label, d, n, af) for label, d, n, af in _INCH_LARGE],
    key=lambda s: (_ORDER.index(s.standard), s.diameter),
)

STANDARDS = {
    "iso_coarse": "ISO metric, coarse pitch",
    "iso_fine": "ISO metric, fine pitch",
    "unc": "Unified coarse (UNC)",
    "unf": "Unified fine (UNF)",
    "custom": "Custom",
}


def sizes(standard: str) -> list[Size]:
    return [s for s in SIZES if s.standard == standard]


def size(standard: str, name: str) -> Size | None:
    return next((s for s in SIZES if s.standard == standard and s.name == name), None)


def stress_area(diameter: float, pitch: float, standard: str = "iso_coarse") -> float:
    """The tensile stress area (m²) from the diameter and pitch (m), by the standard's
    own formula (ISO's for anything not UN)."""
    if standard in ("unc", "unf"):
        d_in, n = diameter / INCH, INCH / pitch
        return math.pi / 4 * (d_in - 0.9743 / n) ** 2 * INCH**2
    d, p = diameter * 1e3, pitch * 1e3
    return math.pi / 4 * (d - 0.9382 * p) ** 2 * 1e-6


def preload_from_torque(torque: float, nut_factor: float, diameter: float) -> float:
    """The short-form torque equation F = T / (K d) (Shigley 8-27)."""
    return torque / (nut_factor * diameter)


def preload_from_yield(percent: float, yield_strength: float, area: float) -> float:
    """A share of the bolt's yield load σy As."""
    return percent / 100 * yield_strength * area


# Resolution ----------------------------------------------------------------------


def _yield_strength(material) -> float | None:
    q = material.properties.get("yield_strength")
    return q.to_si() if q is not None else None


def type_values(project, btype) -> dict:
    """The values a bolt of this type takes: diameter (m), stress area (m²), the grade
    material's id, ends, formulation and the preload force (N, or None)."""
    from puzzle.errors import PuzzleError
    from puzzle.model.schema import Material

    d = btype.diameter.to_si()
    area = btype.stress_area.to_si() if btype.stress_area is not None else None
    if area is None and btype.pitch is not None:
        area = stress_area(d, btype.pitch.to_si(), btype.standard)
    if area is None:
        area = math.pi / 4 * d**2
    force = None
    if btype.preload == "force" and btype.preload_force is not None:
        force = btype.preload_force.to_si()
    elif btype.preload == "torque" and btype.torque is not None:
        force = preload_from_torque(btype.torque.to_si(), btype.nut_factor, d)
    elif btype.preload == "yield_percent":
        material = project.objects.get(btype.material)
        sy = _yield_strength(material) if isinstance(material, Material) else None
        if sy is None:
            raise PuzzleError(
                f"bolt type '{btype.name}': its grade has no yield strength, so "
                f"{btype.preload_percent:g} % of yield can't be worked out; give the "
                "material a yield strength or set the preload as a force",
                code="incomplete_material",
                path=btype.name,
            )
        force = preload_from_yield(btype.preload_percent, sy, area)
    return {"diameter": d, "stress_area": area, "material": btype.material,
            "ends": btype.ends, "formulation": btype.formulation, "preload": force}  # fmt: skip


def resolve(project, fastener):
    """The bolt with every value filled in: its own where set, else its type's, else
    the defaults (rigid ends, a beam)."""
    from puzzle.model.schema import BoltType, Quantity

    btype = project.objects.get(fastener.type) if fastener.type is not None else None
    if not isinstance(btype, BoltType):
        return fastener
    values = type_values(project, btype)
    update = {}
    if fastener.diameter is None:
        update["diameter"] = btype.diameter
    if fastener.material is None:
        update["material"] = values["material"]
    if fastener.ends is None:
        update["ends"] = values["ends"]
    if fastener.formulation is None:
        # A type's "modelled" makes a beam until the bolt is drawn (`solid_bolts`): a
        # bolt marked modelled with no parts would reach the solvers as nothing.
        formulation = values["formulation"]
        update["formulation"] = "beam" if formulation == "modelled" else formulation
    if fastener.joint is None:
        update["joint"] = btype.joint
    if fastener.pretension is None and values["preload"] is not None:
        update["pretension"] = Quantity(value=round(values["preload"], 6), unit="N")
    if fastener.thread is None:
        update["thread"] = type_thread(btype)
    if fastener.head is None:
        update["head"] = btype.head
    if fastener.washers is None:
        update["washers"] = btype.washers
    if fastener.thread_kf is None:
        update["thread_kf"] = btype.thread_kf
    return fastener.model_copy(update=update)


def type_thread(btype):
    """A bolt type's tapped-end thread (ADR 0076): its `thread_form`, else iso for ISO
    sizes and un for UNC/UNF; none for "none" or a custom size without a pitch."""
    from puzzle.model.schema import Thread

    form = btype.thread_form or ("un" if btype.standard in ("unc", "unf") else "iso")
    if form == "none":
        return None
    if btype.standard != "custom" and btype.size:
        return Thread(form=form, size=btype.size)
    if btype.diameter is None or btype.pitch is None:
        return None
    return Thread(form=form, major=btype.diameter, pitch=btype.pitch)


def resolved(project):
    """A copy of the project whose every bolt is resolved (what solvers read). A
    modelled bolt (ADR 0077) is left out: its parts, contacts and pretension carry it."""
    from puzzle.model.schema import Component

    comps = [o for o in project.objects.values() if isinstance(o, Component) and o.connectors]
    if not comps or not any(c.type is not None or c.formulation == "modelled"
                            for comp in comps for c in comp.connectors):  # fmt: skip
        return project
    objects = dict(project.objects)
    for comp in comps:
        kept = [resolve(project, c) for c in comp.connectors if c.formulation != "modelled"]
        objects[comp.id] = comp.model_copy(update={"connectors": kept})
    return dataclasses.replace(project, objects=objects)


# Results along a bolt and its use of its grade --------------------------------------

# A beam bolt carries no load between its ends, so its axial force, shear and torsion
# are the same all along it and its bending moment is linear: from the forces at its
# first end (the solvers' `fastener` field, ADR 0027) it follows by statics.
MOMENT_SIGN = -1.0  # M(s) = M_A + MOMENT_SIGN · s (axis × F_A); fixed by the shear test


@dataclass
class Along:
    """A bolt's loads along its length, from its first end (s = 0) to its second."""

    s: list[float]  # m
    axial: list[float]  # N, positive in tension
    shear: list[float]  # N
    torsion: list[float]  # N·m
    bending: list[float]  # N·m

    @property
    def largest_bending(self) -> float:
        return max(self.bending) if self.bending else 0.0


def along(values, a, b, points: int = 11) -> Along:
    """Loads along a bolt from its `fastener` field row (FASTENER_COMPONENTS: axial,
    shear, torsion, bending, Fx, Fy, Fz, Mx, My, Mz at the first end) and its ends'
    centres `a` and `b`."""
    import numpy as np

    a, b = np.asarray(a, float), np.asarray(b, float)
    length = float(np.linalg.norm(b - a))
    axis = (b - a) / length if length > 0 else np.array([0.0, 0.0, 1.0])
    force, moment = np.asarray(values[4:7], float), np.asarray(values[7:10], float)
    axial = float(force @ axis)
    shear = float(np.linalg.norm(force - axial * axis))
    s = np.linspace(0.0, length, max(2, points))
    out = Along([], [], [], [], [])
    for x in s:
        m = moment + MOMENT_SIGN * x * np.cross(axis, force)
        twist = float(m @ axis)
        out.s.append(float(x))
        out.axial.append(float(values[0]))
        out.shear.append(shear)
        out.torsion.append(twist)
        out.bending.append(float(np.linalg.norm(m - twist * axis)))
    return out


@dataclass
class Use:
    """A bolt's stress and how much of its grade's proof and yield it uses."""

    stress: float  # Pa: |N| / As + M / Z
    proof: float | None  # stress / proof strength (1 = at proof)
    yielding: float | None  # stress / yield strength


def use(axial: float, bending: float, area: float, proof: float | None,
        yield_strength: float | None) -> Use:  # fmt: skip
    """σ = |N|/As + M/Z with Z = π d³/32 on the stress area's diameter (ADR 0050)."""
    d = math.sqrt(4 * area / math.pi)
    stress = abs(axial) / area + bending / (math.pi * d**3 / 32)
    return Use(
        stress,
        stress / proof if proof else None,
        stress / yield_strength if yield_strength else None,
    )


def grade_strengths(project, bolt) -> tuple[float | None, float | None]:
    """(proof, yield) strengths in Pa of the bolt's resolved material."""
    from puzzle.model.schema import Material

    m = project.objects.get(bolt.material) if bolt.material is not None else None
    if not isinstance(m, Material):
        return None, None
    proof = m.properties.get("proof_strength")
    sy = m.properties.get("yield_strength")
    return (proof.to_si() if proof else None), (sy.to_si() if sy else None)


def bolt_area(project, bolt) -> float:
    """The bolt's tensile stress area (m²): its type's, or its diameter's full circle."""
    from puzzle.model.schema import BoltType

    btype = project.objects.get(bolt.type) if bolt.type is not None else None
    if isinstance(btype, BoltType) and bolt.diameter is None:
        return type_values(project, btype)["stress_area"]
    d = bolt.diameter.to_si() if bolt.diameter is not None else 0.01
    return math.pi / 4 * d**2


# Joint styles: what a bolt's ends bear on ------------------------------------------------

# For each style, what the first and second ends take: the hole's wall, the annulus
# under a head or nut on the plate's outer face, or a tapped hole's engaged thread.
JOINTS = {
    "hole_walls": ("wall", "wall"),
    "through_nut": ("head", "nut"),
    "tapped": ("head", "thread"),
    "stud_nuts": ("nut", "nut"),
    "stud_tapped": ("thread", "nut"),
}
WORDS = {"head": "under the head", "nut": "under the nut", "thread": "thread"}

# Gmsh tet10: corners 0-3, then the edge nodes (0,1) (1,2) (0,2) (0,3) (2,3) (1,3).
_TET_FACES = [(0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3)]
_TET_EDGES = {(0, 1): 4, (1, 2): 5, (0, 2): 6, (0, 3): 7, (2, 3): 8, (1, 3): 9}


def _skin(mesh, part: str):
    """The outer triangles of a part's tetrahedra: (corner node ids (n, 3), the six
    tri6 node ids (n, 6) or None for linear tets)."""
    import numpy as np

    group = mesh.groups.get(part)
    if group is None or group.dim != 3:
        return np.zeros((0, 3), int), None
    conn = np.vstack([b.connectivity for b in group.blocks(mesh) if b.element in ("tet4", "tet10")])
    quadratic = conn.shape[1] == 10
    faces = np.vstack([conn[:, list(f)] for f in _TET_FACES])
    keys = np.sort(faces, axis=1)
    _, first, counts = np.unique(keys, axis=0, return_index=True, return_counts=True)
    outer = first[counts == 1]
    corners = faces[outer]
    if not quadratic:
        return corners, None
    mids = []
    for f in _TET_FACES:
        a, b, c = f
        cols = [_TET_EDGES[tuple(sorted(e))] for e in ((a, b), (b, c), (a, c))]
        mids.append(conn[:, cols])
    mids = np.vstack(mids)[outer]
    return corners, np.hstack([corners, mids])


def _face_group(mesh, name: str, tris, quadratic_rows, entity: int):
    """A mesh face group of these skin triangles (a new block on a made-up entity, so it
    is never taken for shell elements)."""
    import dataclasses as dc

    import numpy as np

    from puzzle.mesh.mesh import CellBlock, Group

    rows = quadratic_rows if quadratic_rows is not None else tris
    element = "tri6" if rows.shape[1] == 6 else "tri3"
    start = int(max(b.ids.max() for b in mesh.blocks)) + 1
    block = CellBlock(element, 2, (2, entity), np.arange(start, start + len(rows)), rows)
    group = Group(name, 2, [(2, entity)], np.unique(rows))
    return dc.replace(mesh, blocks=[*mesh.blocks, block], groups={**mesh.groups, name: group})


def with_joints(project, component_id, mesh):
    """(the mesh with each joint's bearing faces as groups, the project whose bolts point
    at them). Bolts on hole walls are left alone; a project without such bolts comes
    back as it is."""
    import numpy as np

    from puzzle.adapters.common import InputError, fastener_ends
    from puzzle.model.schema import BoltType, Component

    comp = project.objects.get(component_id)
    if not isinstance(comp, Component) or not comp.connectors:
        return mesh, project
    bolts = [resolve(project, c) for c in comp.connectors]
    if all(JOINTS.get(b.joint or "hole_walls") == ("wall", "wall") for b in bolts):
        return mesh, project
    # New surfaces numbered past the mesh's own (Gmsh writes Code_Aster's MED file).
    made, out = {}, []
    entity = 1 + max((b.entity[1] for b in mesh.blocks if b.entity[0] == 2), default=0)
    for bolt in bolts:
        kinds = JOINTS.get(bolt.joint or "hole_walls", ("wall", "wall"))
        if kinds == ("wall", "wall"):
            out.append(bolt)
            continue
        what = f"bolt '{bolt.name}'"
        a, b = fastener_ends(bolt, mesh, what)
        axis = (b.centre - a.centre) / np.linalg.norm(b.centre - a.centre)
        btype = project.objects.get(bolt.type) if bolt.type is not None else None
        d = bolt.diameter.to_si()
        bearing = (btype.bearing_diameter.to_si() if isinstance(btype, BoltType)
                   and btype.bearing_diameter is not None else 1.6 * d)  # fmt: skip
        engaged = (btype.engagement.to_si() if isinstance(btype, BoltType)
                   and btype.engagement is not None else 1.5 * d)  # fmt: skip
        ends = []
        for which, (selection, kind) in enumerate(zip((bolt.source, bolt.target), kinds,
                                                      strict=True)):  # fmt: skip
            if kind == "wall" or selection is None:  # walls, or a bolt's ground end
                ends.append(selection)
                continue
            name = f"{selection} ({WORDS[kind]})"
            if name not in made:
                part = selection.split("/")[0]
                wall = mesh.groups[selection]
                s = (mesh.node_coords(wall.node_ids) - a.centre) @ axis
                # The first end's outer mouth is its lowest point along the bolt, the
                # second's its highest; a thread starts at the mouth facing the joint.
                outer = s.min() if which == 0 else s.max()
                inner = s.max() if which == 0 else s.min()
                off_axis = (mesh.node_coords(wall.node_ids) - a.centre) - np.outer(s, axis)
                radius = float(np.median(np.linalg.norm(off_axis, axis=1)))
                if kind == "thread":
                    tris = np.vstack([blk.connectivity for blk in wall.blocks(mesh)])
                    corners = tris[:, :3]
                    centroid = (mesh.node_coords(corners.ravel()).reshape(-1, 3, 3).mean(axis=1)
                                - a.centre) @ axis  # fmt: skip
                    depth = np.abs(centroid - inner)
                    keep = depth <= engaged + 1e-9
                    rows = tris[keep]
                    if not len(rows):
                        raise InputError(f"{what}: no thread within {engaged * 1e3:g} mm of the "
                                         f"mouth of '{selection}'", code="invalid")  # fmt: skip
                    quad = rows if rows.shape[1] == 6 else None
                    mesh = _face_group(mesh, name, rows[:, :3], quad, entity)
                elif len(bolt.bearing) > which and bolt.bearing[which]:
                    # A ring imprinted in the geometry (ADR 0050): its faces at this mouth.
                    ring = mesh.groups.get(bolt.bearing[which])
                    if ring is None:
                        raise InputError(f"{what}: no bearing ring '{bolt.bearing[which]}'")
                    tris = np.vstack([blk.connectivity for blk in ring.blocks(mesh)])
                    xyz = mesh.node_coords(tris[:, :3].ravel()).reshape(-1, 3, 3)
                    along = (xyz - a.centre) @ axis
                    keep = np.all(np.abs(along - outer) < 1e-3 * max(d, 1e-6), axis=1)
                    if not keep.any():
                        raise InputError(f"{what}: '{bolt.bearing[which]}' has no ring at "
                                         f"the outer mouth of '{selection}'")  # fmt: skip
                    rows = tris[keep]
                    quad = rows if rows.shape[1] == 6 else None
                    mesh = _face_group(mesh, name, rows[:, :3], quad, entity)
                else:
                    corners, quad = _skin(mesh, part)
                    xyz = mesh.node_coords(corners.ravel()).reshape(-1, 3, 3)
                    rel = xyz - a.centre
                    along = rel @ axis
                    tol = 1e-3 * max(d, 1e-6)
                    flat = np.all(np.abs(along - outer) < tol, axis=1)
                    mid = rel.mean(axis=1) - np.outer(along.mean(axis=1), axis)
                    r = np.linalg.norm(mid, axis=1)
                    keep = flat & (r >= radius - tol) & (r <= bearing / 2 + tol)
                    if not keep.any():
                        raise InputError(
                            f"{what}: nothing for the {kind} to bear on at the outer mouth of "
                            f"'{selection}' (a flat face around the hole, out to "
                            f"{bearing * 1e3:g} mm); refine the mesh near the hole, or bear on "
                            "the hole walls",
                            code="invalid",
                        )
                    picked = quad[keep] if quad is not None else None
                    mesh = _face_group(mesh, name, corners[keep], picked, entity)
                entity += 1
                made[name] = True
            ends.append(name)
        out.append(bolt.model_copy(update={"source": ends[0], "target": ends[1]}))
    objects = dict(project.objects)
    objects[comp.id] = comp.model_copy(update={"connectors": out})
    return mesh, dataclasses.replace(project, objects=objects)


# The bolt table's results (ADR 0050), for the GUI and the agent ------------------------


@dataclass
class BoltResult:
    name: str
    component: str
    loads: object  # bolts.Along
    stress: float
    proof: float | None
    yielding: float | None
    threads: list[float] = field(default_factory=list)  # N per thread, the first engaged first
    # A modelled bolt (ADR 0077): its loads are one section's, across its shank.
    modelled: bool = False

    @property
    def first_thread(self) -> float | None:
        """The first engaged thread's share of the threaded end's load (ADR 0076)."""
        total = sum(self.threads)
        return self.threads[0] / total if self.threads and total > 0 else None


def section_loads(mesh, stress, part: str) -> Along | None:
    """A modelled bolt's loads (ADR 0077) from its section across the shank: the stress
    traction integrated over the bolt part's own elements cut by the plane of its
    `<part>/Section` face (ADR 0042's section tool, `probes.section_force`), so the
    plates around it don't count. The plane is moved a fiftieth of the section's width
    toward the head, off the face the elements meet on. `s` is the section's distance
    from the bearing face. None when the part or its section isn't in the mesh."""
    import numpy as np

    from puzzle.mesh import Mesh
    from puzzle.post.probes import section_force
    from puzzle.results.model import StepResult

    body, cut = mesh.groups.get(part), mesh.groups.get(f"{part}/Section")
    if body is None or cut is None or not len(cut.node_ids):
        return None
    blocks = [b for b in body.blocks(mesh) if b.dim == 3]
    if not blocks:
        return None
    xyz = mesh.node_coords(np.asarray(cut.node_ids))
    centre = xyz.mean(axis=0)
    normal = np.linalg.svd(xyz - centre)[2][-1]  # the plane's normal: least spread
    bearing = mesh.groups.get(f"{part}/Bearing")
    head = mesh.node_coords(np.asarray(bearing.node_ids)).mean(axis=0) if (
        bearing is not None and len(bearing.node_ids)) else None  # fmt: skip
    if head is not None and float((centre - head) @ normal) < 0:
        normal = -normal  # from the head toward the bolt's end
    width = float(np.ptp(xyz, axis=0).max())
    origin = centre - normal * width / 50
    own = Mesh(mesh.node_ids, mesh.coords, blocks, {part: body})
    step = StepResult(0, "stationary", 0.0, None, {"stress": stress})
    found = section_force(own, step, origin, normal)
    force = np.array([found["components"][k] for k in "xyz"])
    moment = np.array([found["moment"][k] for k in "xyz"])
    axial = float(force @ normal)
    twist = float(moment @ normal)
    s = float((origin - head) @ normal) if head is not None else 0.0
    return Along([s], [axial], [float(np.linalg.norm(force - axial * normal))], [twist],
                 [float(np.linalg.norm(moment - twist * normal))])  # fmt: skip


def bolt_kf(project, comp, part: str) -> float | None:
    """The thread-root notch factor (ADR 0077) of the modelled bolt drawn as `part`, or
    None when no modelled bolt of `comp` is."""
    for bolt in comp.connectors:
        if bolt.formulation == "modelled" and bolt.parts and bolt.parts[0] == part:
            return resolve(project, bolt).thread_kf or 3.0
    return None


def modelled_threads(comp, bolt) -> list[str]:
    """The threaded contacts on a modelled bolt's thread (into the tapped hole or nut)."""
    if not bolt.parts:
        return []
    face = f"{bolt.parts[0]}/Thread"
    return [c.name for c in comp.contacts if c.thread is not None and face in (c.source, c.target)]


def _read(store, run_id: str, name: str, step):
    try:
        return store.read_field(run_id, name, step)
    except (KeyError, IndexError):  # not in this run (or not at this step)
        return None


def _per_thread(field, split) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    if field is not None:
        for name, value in zip(field.components, field.values[0], strict=True):
            out.setdefault(split(name), []).append(float(value))
    return out


def bolt_results(project, store, run_id: str, step: int | None = None) -> list[BoltResult]:
    """Each bolt's loads and use for one run's step (the last by default), worst first.

    Beam and solid bolts read the solvers' `fastener` field, one row per bolt in order
    with the modelled ones left out (as `resolved` leaves them out of the solve); a
    modelled bolt (ADR 0077) reads the stress across its shank (`section_loads`) and
    its threaded contact's `thread_load`. Stress and use are the beam's: |N|/As + M/Z
    on the type's stress area, so the two kinds compare like with like."""
    from puzzle.adapters.common import fastener_ends
    from puzzle.model.schema import Component

    record = store.get_run(run_id)
    study = next((s for s in project.objects.values() if str(s.id) == record.study_id), None)
    comp = project.objects.get(study.component) if study is not None else None
    if not isinstance(comp, Component) or not comp.connectors:
        return []
    beams = _read(store, run_id, "fastener", step)
    rows = iter(beams.values if beams is not None else ())
    modelled = any(b.formulation == "modelled" for b in comp.connectors)
    stress = _read(store, run_id, "stress", step) if modelled else None
    if stress is not None and stress.location != "node":
        stress = None
    if beams is None and stress is None:
        raise KeyError(f"run {run_id} has no bolt results (no 'fastener' or 'stress' field)")
    mesh = store.read_mesh(record.mesh_id)
    # ADR 0076: a beam bolt's threaded end's per-thread loads, "<bolt> <end>: <k>"; a
    # threaded contact's, "<contact>: <k>".
    thread_loads = _per_thread(_read(store, run_id, "bolt_thread_load", step),
                               lambda n: n.rsplit(" ", 2)[0])  # fmt: skip
    contact_loads = _per_thread(_read(store, run_id, "thread_load", step),
                                lambda n: n.rsplit(": ", 1)[0]) if modelled else {}  # fmt: skip
    out = []
    for bolt in comp.connectors:
        solved = resolve(project, bolt)
        if bolt.formulation == "modelled":
            loads = section_loads(mesh, stress, bolt.parts[0]) if (
                stress is not None and bolt.parts) else None  # fmt: skip
            if loads is None:
                continue
            threads = [v for c in modelled_threads(comp, bolt) for v in contact_loads.get(c, [])]
        else:
            values = next(rows, None)
            if values is None:
                continue
            a, b = (e.centre for e in fastener_ends(solved, mesh))
            loads = along(values, a, b)
            threads = thread_loads.get(bolt.name, [])
        proof, sy = grade_strengths(project, solved)
        u = use(loads.axial[0], loads.largest_bending, bolt_area(project, bolt), proof, sy)
        out.append(BoltResult(bolt.name, comp.name, loads, u.stress, u.proof, u.yielding,
                              threads, bolt.formulation == "modelled"))  # fmt: skip

    def worst(r: BoltResult):  # by its share of proof (or yield); unknown ones last
        ratio = r.proof if r.proof is not None else r.yielding
        return (ratio is None, -(ratio or 0.0), -r.stress)

    return sorted(out, key=worst)


def model_commands(project, comp, indices, root, *, bonded_heads: bool = False) -> list:
    """One batch modelling several bolts of `comp` (ADR 0077; `solid_bolts.make_commands`
    for each, from the same project: their edits append, so they compose). Bolts already
    modelled are skipped."""
    from puzzle.solid_bolts import make_commands

    out: list = []
    for i in indices:
        if comp.connectors[i].formulation == "modelled":
            continue
        out += make_commands(project, comp, i, root, bonded_heads=bonded_heads)
    return out
