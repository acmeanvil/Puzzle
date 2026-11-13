"""Abaqus/CalculiX input (ADR 0043 step 6): the mesh and its sets as a `.inp` another
model includes, and the results it needs from this one as data cards of their own.

`<name>.inp` (`abaqus_inp`, `abaqus_displacement`) holds, in SI (m, kg, s, N, Pa, K):

- `*NODE` (metres, 13 significant digits: CalculiX reads 20 characters a field);
- `*ELEMENT` per part and element type, in the Abaqus/CalculiX node order: C3D4,
  C3D10, C3D8, C3D20, C3D6, C3D15; S3, S6 (Abaqus calls it STRI65), S4, S8R; B31, B32;
  a 2D model's CPS/CPE/CAX 3, 4, 6 and 8;
- `*NSET` for every selection and part (`N_<name>`), `*ELSET` for the parts' and the
  selections' elements (`E_<name>`), named as Puzzle's CalculiX decks name them;
- each material assignment's `*MATERIAL` (elastic, density, expansion with its zero,
  conductivity, specific heat: the room values) and `*SOLID SECTION`, `*SHELL SECTION`
  or `*BEAM SECTION`.

It has no steps, loads or supports: those are the receiving model's. The results go
in a file beside it, to `*INCLUDE` inside a step of the receiving model:

- `abaqus_inp`: `<name>_temperature.inp`, the chosen step's nodal temperatures (K) as
  `*TEMPERATURE` data, for a thermal stress model (set its initial temperature, the
  strain-free one, with `*INITIAL CONDITIONS, TYPE=TEMPERATURE`);
- `abaqus_displacement`: `<name>_displacement.inp`, the chosen step's nodal
  displacements (m) as `*BOUNDARY` data (dofs 1-3), of one selection's nodes if
  asked: a submodel's cut boundary.
"""

from __future__ import annotations

import numpy as np

from puzzle.adapters.calculix.inp import set_name
from puzzle.errors import PuzzleError
from puzzle.post.exports.decks import component, edge_order, expansion_reference, pieces, si

SOLIDS = {
    "tet4": ("C3D4", None),
    "tet10": ("C3D10", [(0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3)]),
    "hex8": ("C3D8", None),
    "hex20": ("C3D20", [(0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4),
                        (0, 4), (1, 5), (2, 6), (3, 7)]),
    "wedge6": ("C3D6", None),
    "wedge15": ("C3D15", [(0, 1), (1, 2), (2, 0), (3, 4), (4, 5), (5, 3), (0, 3), (1, 4),
                          (2, 5)]),
}  # fmt: skip
SHELLS = {"tri3": "S3", "tri6": "S6", "quad4": "S4", "quad8": "S8R"}
BEAMS = {"line2": ("B31", None), "line3": ("B32", [0, 2, 1])}  # B32 runs end-mid-end
PLANAR = {"plane_stress": "CPS", "plane_strain": "CPE", "axisymmetric": "CAX"}
CORNERS = {"tri3": 3, "tri6": 6, "quad4": 4, "quad8": 8}


def write(job):
    """The mesh deck, and the step's temperatures beside it when it has them."""
    files, notes = _mesh_deck(job)
    t = job.step.fields.get("temperature")
    if t is None or t.location != "node":
        return files, notes | {"temperatures": "none in this step (the mesh deck only)"}
    out = job.path.with_name(f"{job.path.stem}_temperature.inp")
    rows = [f"{int(n)}, {v:.10g}" for n, v in zip(t.ids, t.values[:, 0], strict=True)]
    out.write_text(
        "\n".join(
            [
                f"** Nodal temperatures (K) written by Puzzle: {job.step.label()}",
                "** *INCLUDE inside a step; the strain-free temperature is the model's "
                "*INITIAL CONDITIONS, TYPE=TEMPERATURE",
                "*TEMPERATURE",
                *rows,
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return [*files, out], notes | {"temperatures": f"{out.name}: {len(rows)} nodes, K"}


def write_displacement(job):
    """The mesh deck and the step's displacements as *BOUNDARY data."""
    files, notes = _mesh_deck(job)
    u = job.step.fields["displacement"]
    ids = u.ids
    if job.selection is not None:
        group = job.mesh.groups.get(job.selection)
        if group is None:
            raise PuzzleError(f"no selection '{job.selection}' in the mesh", code="not_found")
        ids = ids[np.isin(ids, group.node_ids)]
    values = u.at(ids)
    out = job.path.with_name(f"{job.path.stem}_displacement.inp")
    rows = [
        f"{int(n)}, {k + 1}, {k + 1}, {v[k]:.13g}"
        for n, v in zip(ids, values, strict=True)
        for k in range(3)
    ]
    out.write_text(
        "\n".join(
            [
                f"** Nodal displacements (m) written by Puzzle: {job.step.label()}",
                f"** on {job.selection or 'every node'}; *INCLUDE inside a step",
                "*BOUNDARY",
                *rows,
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return [*files, out], notes | {
        "displacements": f"{out.name}: {len(ids)} nodes on {job.selection or 'the whole model'}, m"
    }


def _mesh_deck(job):
    from puzzle.adapters.calculix.inp import _beam_sections, _chunks, _row
    from puzzle.adapters.common import InputError
    from puzzle.model.schema import Material

    study, comp = component(job)
    mesh = job.mesh
    planar = None
    if mesh.planar:
        planar = "axisymmetric" if mesh.axisymmetric else comp.planar_model
    parts = pieces(job.project, comp, mesh)
    out = [
        "** Abaqus/CalculiX mesh written by Puzzle (ADR 0043)",
        f"** study: {study.name}; units SI (m, kg, s, N, Pa, K); no steps, loads or supports",
        "*NODE, NSET=N_ALL",
        *(f"{n}, {x:.13g}, {y:.13g}, {z:.13g}" for n, (x, y, z) in
          zip(mesh.node_ids, mesh.coords, strict=True)),
    ]  # fmt: skip
    for p in parts:
        if p.dim == 3:
            if p.element not in SOLIDS:
                raise PuzzleError(f"no Abaqus element for {p.element}", code="unsupported")
            kind, edges = SOLIDS[p.element]
            order = edge_order(p.element, edges) if edges else None
        elif p.dim == 2 and planar is not None:
            kind, order = f"{PLANAR[planar]}{CORNERS[p.element]}", None
        elif p.dim == 2:
            kind, order = SHELLS[p.element], None
        else:
            kind, order = BEAMS[p.element]
        conn = p.connectivity[:, order] if order else p.connectivity
        out.append(f"*ELEMENT, TYPE={kind}, ELSET={set_name('E', p.part)}")
        out += [_row([eid, *nodes]) for eid, nodes in zip(p.ids, conn, strict=True)]
    model_ids = {int(e) for p in parts for e in p.ids}
    part_names = {p.name for p in comp.parts}
    for group in mesh.groups.values():
        out.append(f"*NSET, NSET={set_name('N', group.name)}")
        out += _chunks(group.node_ids)
        own = [b.ids for b in group.blocks(mesh)
               if b.dim == group.dim and b.ids.size and int(b.ids[0]) in model_ids]  # fmt: skip
        if own and group.name not in part_names:
            out.append(f"*ELSET, ELSET={set_name('E', group.name)}")
            out += _chunks(np.concatenate(own))
    for i, a in enumerate(comp.materials, start=1):
        material = job.project.get(a.material, Material)
        group = mesh.groups.get(a.selection)
        if group is None:
            continue
        name = f"M{i}"
        out += [f"** {name}: {material.name}", f"*MATERIAL, NAME={name}", *_material(material)]
        if group.dim == 3 or planar is not None:
            out.append(f"*SOLID SECTION, ELSET={set_name('E', a.selection)}, MATERIAL={name}")
            if planar == "plane_stress":
                from puzzle.adapters.common import plane_thickness

                out.append(f"{plane_thickness(comp, a.selection.split('/', 1)[0]):.10g}")
        elif group.dim == 2:
            out += _shells(comp, a.selection, name)
        else:
            try:
                out += _beam_sections(comp, mesh, a.selection, name)
            except InputError as exc:
                raise PuzzleError(str(exc), code="invalid") from None
    path = job.path.with_suffix(".inp")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return [path], {
        "deck": "mesh, sets, materials and sections; SI (m, kg, s, N, Pa, K)",
        "elements": ", ".join(sorted({p.element for p in parts})),
    }


def _material(m) -> list[str]:
    cards = []
    e, nu = si(m, "youngs_modulus"), si(m, "poissons_ratio")
    if e is not None and nu is not None:
        cards += ["*ELASTIC", f"{e:.10g}, {nu:.10g}"]
    for card, key in (("*DENSITY", "density"), ("*CONDUCTIVITY", "thermal_conductivity"),
                      ("*SPECIFIC HEAT", "specific_heat")):  # fmt: skip
        if (v := si(m, key)) is not None:
            cards += [card, f"{v:.10g}"]
    if (alpha := si(m, "thermal_expansion")) is not None:
        cards += [f"*EXPANSION, ZERO={expansion_reference(m):.10g}", f"{alpha:.10g}"]
    return cards


def _shells(comp, selection: str, material: str) -> list[str]:
    part = selection.split("/", 1)[0]
    mine = [s for s in comp.shell_sections if s.selection.split("/", 1)[0] == part]
    if not mine:
        raise PuzzleError(f"the sheet body '{selection}' has no thickness", code="invalid")
    out = []
    for s in mine:
        offset = f", OFFSET={s.offset:.6g}" if s.offset else ""
        out += [f"*SHELL SECTION, ELSET={set_name('E', s.selection)}, MATERIAL={material}"
                f"{offset}", f"{s.thickness.to_si():.10g}"]  # fmt: skip
    return out
