"""Nastran bulk data and punch results (ADR 0043 step 6).

`<name>.bdf` is bulk data only (no executive or case control: INCLUDE it after BEGIN
BULK), in **large-field** format: a card's name with `*`, four 16-character fields a
line, continuation lines starting `*`. Reals keep 10 significant digits. It holds:

- GRID: every node, in metres (Puzzle's SI units: m, kg, s, N, Pa, K; give Nastran
  consistent loads);
- the elements: CTETRA (4, 10), CHEXA (8, 20), CPENTA (6, 15), CTRIA3/6, CQUAD4/8 for
  sheet bodies, CBAR for line bodies (two-node: a quadratic beam's middle node is left
  out, and the orientation is the section's `orientation` vector);
- a PSOLID, PSHELL (its thickness) or PBAR (A, I1, I2, J) and a MAT1 (E, ν, ρ, α and
  its reference temperature, SI) per material assignment;
- each selection as a SET1 of its grids, and the selections with elements of the model
  (bodies, sheets, line bodies) as a SET3 ELEM too; Nastran numbers sets, so the file
  starts with a table of set number -> selection name.

`<name>.pch` holds the results (SORT1, real, 80-column records with a sequence number):
for each step a SUBCASE with DISPLACEMENTS (EIGENVECTOR and its EIGENVALUE for modes:
(2π f)² rad²/s², or the load factor of a buckling mode), and ELEMENT STRESSES for the
solids where stress is stored. Values are E18.6: seven significant digits.

Stress records follow the OES1 order for CTETRA (type 39), CHEXA (67) and CPENTA (68):
per element the centre (grid 0) and each corner grid, 21 words each (grid, σx, τxy, σ1,
its direction cosines, mean pressure, von Mises, σy, τyz, σ2, cosines, σz, τzx, σ3,
cosines), three values a line after the first. Puzzle stores stress at nodes, so a
corner's value is its node's and the centre's the corners' mean. That layout was not
checked against Nastran itself (no Nastran nor punch reader here).
"""

from __future__ import annotations

import numpy as np

from puzzle.errors import PuzzleError
from puzzle.post.exports.decks import component, edge_order, expansion_reference, pieces, si

SOLIDS = {
    "tet4": ("CTETRA", None),
    "tet10": ("CTETRA", [(0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3)]),
    "hex8": ("CHEXA", None),
    "hex20": ("CHEXA", [(0, 1), (1, 2), (2, 3), (3, 0), (0, 4), (1, 5), (2, 6), (3, 7),
                        (4, 5), (5, 6), (6, 7), (7, 4)]),
    "wedge6": ("CPENTA", None),
    "wedge15": ("CPENTA", [(0, 1), (1, 2), (2, 0), (0, 3), (1, 4), (2, 5), (3, 4), (4, 5),
                           (5, 3)]),
}  # fmt: skip
SHELLS = {"tri3": "CTRIA3", "tri6": "CTRIA6", "quad4": "CQUAD4", "quad8": "CQUAD8"}
STRESS_TYPES = {"CTETRA": (39, "TETRA", 4), "CHEXA": (67, "HEXA", 8), "CPENTA": (68, "PENTA", 6)}
LIMIT = 99_999_999  # Nastran ids have at most eight digits


def write(job):
    study, comp = component(job)
    if job.mesh.planar:
        raise PuzzleError(
            "2D models are not written to Nastran (its plane elements differ); export "
            "a 3D model, or use the Abaqus/CalculiX deck",
            code="unsupported",
        )
    bdf, sets, written = bulk(job.project, comp, job.mesh, study.name)
    path = job.path.with_suffix(".bdf")
    path.write_text(bdf, encoding="utf-8")
    pch = path.with_suffix(".pch")
    subcases = punch(job.results, job.mesh, written, pch, study.name, job.record.solver)
    return [path, pch], {
        "bulk data": "large-field format; GRID in metres; SI throughout (m, kg, s, N, Pa, K)",
        "punch": f"{subcases} subcase(s): displacements (m), solid element stresses (Pa)",
        "sets": "; ".join(f"{sid} {name}" for sid, name in sets) or "none",
    }


def _real(v: float) -> str:
    """A real in at most 16 characters, 10 significant digits."""
    v = float(v)
    if v == 0.0:
        return "0.0"
    for digits in (9, 8, 7, 6):
        text = f"{v:.{digits}E}"
        if len(text) <= 16:
            return text
    raise PuzzleError(f"the value {v} does not fit a Nastran field", code="export_failed")


def _field(v) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, (int, np.integer)):
        return str(int(v))
    return _real(v)


def card(name: str, fields: list) -> list[str]:
    """One card in large-field format: `NAME*` and four 16-character fields a line."""
    texts = [_field(f) for f in fields]
    lines = []
    for k in range(0, max(len(texts), 1), 4):
        head = f"{name + '*':<8}" if k == 0 else f"{'*':<8}"
        lines.append((head + "".join(f"{t:>16}" for t in texts[k : k + 4])).rstrip())
    return lines


def _ids(ids) -> list:
    """A set's members, runs of consecutive ids written `a THRU b`."""
    ids = np.unique(np.asarray(ids, np.int64))
    out: list = []
    start = 0
    for k in range(1, len(ids) + 1):
        if k == len(ids) or ids[k] != ids[k - 1] + 1:
            run = ids[start:k]
            out += [int(run[0]), "THRU", int(run[-1])] if len(run) > 2 else [int(i) for i in run]
            start = k
    return out


def bulk(project, comp, mesh, title: str) -> tuple[str, list[tuple[int, str]], dict]:
    """The bulk data, the sets (number, selection) and each written element's card
    name by id (for the punch's stresses)."""
    from puzzle.model.schema import Material

    if mesh.node_ids.max() > LIMIT:
        raise PuzzleError("node ids past eight digits don't fit Nastran", code="unsupported")
    parts = pieces(project, comp, mesh)
    missing = sorted({p.part for p in parts if p.material is None})
    if missing:
        raise PuzzleError(
            f"no material is assigned to {', '.join(missing)}: Nastran's properties need one",
            code="invalid",
        )
    props: list[str] = []
    elements: list[str] = []
    written: dict[int, tuple[str, np.ndarray]] = {}
    pids: dict = {}
    for i, a in enumerate(comp.materials, start=1):
        m = project.get(a.material, Material)
        e, nu = si(m, "youngs_modulus"), si(m, "poissons_ratio")
        if e is None or nu is None:
            raise PuzzleError(f"material '{m.name}' needs E and ν for MAT1", code="invalid")
        alpha = si(m, "thermal_expansion")
        props += [f"$ MAT1 {i}: {m.name}"]
        props += card("MAT1", [i, e, None, nu, si(m, "density"), alpha,
                               expansion_reference(m) if alpha is not None else None])  # fmt: skip
    next_pid = 1
    for p in parts:
        mid = p.assignment + 1
        if p.dim == 3:
            if p.element not in SOLIDS:
                raise PuzzleError(f"Nastran export has no {p.element} elements", code="unsupported")
            name, edges = SOLIDS[p.element]
            key = ("solid", mid)
            if key not in pids:
                pids[key] = next_pid
                props += [f"$ PSOLID {next_pid}: {p.part}"] + card("PSOLID", [next_pid, mid])
                next_pid += 1
            order = edge_order(p.element, edges) if edges else None
            rows = [(eid, pids[key], conn) for eid, conn in zip(p.ids, p.connectivity, strict=True)]
        elif p.dim == 2:
            name, order = SHELLS[p.element], None
            thickness = _shell_thickness(comp, p.part)
            key = ("shell", mid, thickness)
            if key not in pids:
                pids[key] = next_pid
                props += [f"$ PSHELL {next_pid}: {p.part}"]
                props += card("PSHELL", [next_pid, mid, thickness, mid, None, mid])
                next_pid += 1
            rows = [(eid, pids[key], conn) for eid, conn in zip(p.ids, p.connectivity, strict=True)]
        else:
            rows, beam_props, next_pid = _beams(comp, mesh, p, mid, next_pid, pids)
            props += beam_props
            name, order = "CBAR", None
        if any(eid > LIMIT for eid, _, _ in rows):
            raise PuzzleError("element ids past eight digits don't fit Nastran", code="unsupported")
        for eid, pid, conn in rows:
            if name == "CBAR":  # GA, GB, then the orientation vector X1, X2, X3
                a, b, v = conn
                elements += card("CBAR", [int(eid), pid, int(a), int(b), *(float(c) for c in v)])
                continue
            nodes = conn[order] if order else conn
            elements += card(name, [int(eid), pid, *(int(n) for n in nodes)])
            if name in STRESS_TYPES:
                written[int(eid)] = (name, np.asarray(nodes[: STRESS_TYPES[name][2]]))
    sets, set_cards = [], []
    model_ids = {int(e) for p in parts for e in p.ids}
    for group in mesh.groups.values():
        sid = len(sets) + 1
        sets.append((sid, f"{group.name} (grids)"))
        set_cards += [f"$ SET1 {sid}: {group.name}"] + card("SET1", [sid, *_ids(group.node_ids)])
        own = [
            b.ids
            for b in group.blocks(mesh)
            if b.dim == group.dim and b.ids.size and int(b.ids[0]) in model_ids
        ]
        if own:
            sid = len(sets) + 1
            sets.append((sid, f"{group.name} (elements)"))
            set_cards += [f"$ SET3 {sid}: {group.name}"]
            set_cards += card("SET3", [sid, "ELEM", *_ids(np.concatenate(own))])
    head = [
        "$ Nastran bulk data written by Puzzle (ADR 0043), large-field format",
        f"$ study: {title}",
        "$ units: SI (m, kg, s, N, Pa, K); GRID coordinates in metres",
        "$ INCLUDE this after BEGIN BULK; it has no loads or constraints",
        "$",
        "$ sets (Nastran numbers them):",
        *(f"$   {sid:>6}  {name}" for sid, name in sets),
        "$",
    ]
    grids = []
    for n, (x, y, z) in zip(mesh.node_ids, mesh.coords, strict=True):
        grids += card("GRID", [int(n), None, x, y, z])
    text = "\n".join(head + props + ["$ grids (m)"] + grids + ["$ elements"] + elements
                     + ["$ sets"] + set_cards)  # fmt: skip
    return text + "\n", sets, written


def _shell_thickness(comp, part: str) -> float:
    mine = [s for s in comp.shell_sections if s.selection.split("/", 1)[0] == part]
    if not mine:
        raise PuzzleError(f"the sheet body '{part}' has no thickness", code="invalid")
    if len({s.thickness.to_si() for s in mine}) > 1:
        raise PuzzleError(
            f"the sheet body '{part}' has several thicknesses; Nastran export takes one a part",
            code="unsupported",
        )
    return float(mine[0].thickness.to_si())


def _beams(comp, mesh, p, mid: int, next_pid: int, pids: dict):
    """CBAR rows (eid, pid, (end a, end b, x1, x2, x3)) and PBAR cards for a line body:
    Nastran's plane 1 holds the orientation vector, so its I1 is Puzzle's i2."""
    mine = [b for b in comp.beam_sections if b.selection.split("/", 1)[0] == p.part]
    if not mine:
        raise PuzzleError(f"the line body '{p.part}' has no beam section", code="invalid")
    props, rows = [], []
    by_element = {}
    for b in mine:
        if b.orient != "vector":
            raise PuzzleError(
                f"the beam section on '{b.selection}' is oriented by '{b.orient}'; the "
                "Nastran export takes a vector orientation only",
                code="unsupported",
            )
        group = mesh.groups.get(b.selection)
        ids = [] if group is None else [e for blk in group.blocks(mesh) if blk.dim == 1
                                        for e in blk.ids.tolist()]  # fmt: skip
        key = ("bar", mid, b.selection)
        if key not in pids:
            pids[key] = next_pid
            q = b.properties()
            props += [f"$ PBAR {next_pid}: {b.selection} ({b.describe()})"]
            props += card("PBAR", [next_pid, mid, q["area"], q["i2"], q["i1"], q["j"]])
            next_pid += 1
        for e in ids:
            by_element[int(e)] = (pids[key], b.orientation)
    for eid, conn in zip(p.ids, p.connectivity, strict=True):
        if int(eid) not in by_element:
            raise PuzzleError(f"beam element {eid} of '{p.part}' has no section", code="invalid")
        pid, v = by_element[int(eid)]
        rows.append((eid, pid, (conn[0], conn[1], v)))
    return rows, props, next_pid


def _record(text: str, seq: int) -> str:
    return f"{text:<72.72}{seq:8d}"


def _e(v: float) -> str:
    return f"{float(v):18.6E}"


def punch(results, mesh, written: dict, path, title: str, solver: str) -> int:
    """The punch file: one SUBCASE per step (modes share their solve step's subcase)."""
    lines: list[str] = []
    seq = 0

    def add(text: str) -> None:
        nonlocal seq
        seq += 1
        lines.append(_record(text, seq))

    subcases = 0
    for k, step in enumerate(results.steps, start=1):
        subcase = step.index if step.mode is not None else k
        subcases += 1

        def header(kind: str, extra: list[str] = (), step=step, subcase=subcase) -> None:
            add(f"$TITLE   = {title}")
            add(f"$SUBTITLE= Puzzle, {solver}")
            add(f"$LABEL   = {step.label()}")
            add(kind)
            add("$REAL OUTPUT")
            add(f"$SUBCASE ID = {subcase:11d}")
            for line in extra:
                add(line)

        modal = []
        if step.mode is not None:
            value = step.value if step.kind == "buckling" else (2 * np.pi * step.value) ** 2
            modal = [f"$EIGENVALUE = {value:15.7E}  MODE = {step.mode:5d}"]
        u = step.fields.get("displacement")
        if u is not None and u.location == "node":
            header("$EIGENVECTOR" if step.mode is not None else "$DISPLACEMENTS", modal)
            for n, (x, y, z) in zip(u.ids, u.values, strict=True):
                add(f"{int(n):10d}{'G':>8}{_e(x)}{_e(y)}{_e(z)}")
                add(f"{'-CONT-':<18}{_e(0)}{_e(0)}{_e(0)}")  # rotations: none on solids
        s = step.fields.get("stress")
        if s is not None and s.location == "node" and written:
            by_type: dict = {}
            for eid, (name, corners) in written.items():
                by_type.setdefault(name, []).append((eid, corners))
            node_rows = _stress_words(s)
            for name, elems in by_type.items():
                code, word, count = STRESS_TYPES[name]
                header("$ELEMENT STRESSES", [*modal, f"$ELEMENT TYPE = {code:12d}  {word}"])
                for eid, corners in elems:
                    tensors = s.at(corners)
                    centre = _stress_words_of(tensors.mean(axis=0, keepdims=True))[0]
                    add(f"{eid:10d}{'0GRID':>8}{count:18d}")
                    rows = [(0, centre), *zip(corners, node_rows(corners), strict=True)]
                    for grid, words in rows:
                        first = "".join(_e(w) for w in words[:3])
                        add(f"{'-CONT-':<10}{int(grid):8d}{first}")
                        for j in range(3, 20, 3):
                            add(f"{'-CONT-':<18}" + "".join(_e(w) for w in words[j : j + 3]))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return subcases


def _stress_words(field):
    """A function: node ids -> their 20 OES words each (see the module docstring)."""
    words = _stress_words_of(field.values)
    order = np.argsort(field.ids)

    def at(ids):
        return words[order[np.searchsorted(field.ids, ids, sorter=order)]]

    return at


def _stress_words_of(values: np.ndarray) -> np.ndarray:
    """(n, 6) xx, yy, zz, xy, yz, zx -> (n, 20): σx, τxy, σ1, a(3), p, vm, σy, τyz, σ2,
    b(3), σz, τzx, σ3, c(3); principal stresses largest first."""
    xx, yy, zz, xy, yz, zx = np.asarray(values, float).T
    t = np.stack([np.stack([xx, xy, zx], -1), np.stack([xy, yy, yz], -1),
                  np.stack([zx, yz, zz], -1)], -2)  # fmt: skip
    w, v = np.linalg.eigh(t)
    w, v = w[:, ::-1], v[:, :, ::-1]
    p = -(xx + yy + zz) / 3
    vm = np.sqrt(0.5 * ((xx - yy) ** 2 + (yy - zz) ** 2 + (zz - xx) ** 2)
                 + 3 * (xy**2 + yz**2 + zx**2))  # fmt: skip
    cols = [xx, xy, w[:, 0], *v[:, :, 0].T, p, vm, yy, yz, w[:, 1], *v[:, :, 1].T,
            zz, zx, w[:, 2], *v[:, :, 2].T]  # fmt: skip
    return np.column_stack(cols)
