"""What the whole-run formats (Exodus II, VTKHDF, UNV) share: the elements they write,
each part's blocks, the time value of every step, and every field over the steps."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from puzzle.errors import PuzzleError

# Elements the whole-run writers carry (Gmsh names); others are refused, never dropped.
WRITTEN = {"tet4", "tet10", "hex8", "wedge6", "pyramid5", "tri3", "tri6", "quad4", "quad8",
           "line2", "line3"}  # fmt: skip


@dataclass
class Block:
    """The elements of one part of one type."""

    part: str
    element: str
    ids: np.ndarray  # (n,) element ids
    connectivity: np.ndarray  # (n, k) node ids, Gmsh order
    kind: str  # "solid", "shell", "beam" or "region" (a 2D model's faces)


def blocks(mesh) -> list[Block]:
    """The model's elements by part and type: volume elements, shells and beams, or a
    2D model's faces. Faces and edges meshed only to carry selections are left out."""
    owner = {e: name for name, g in mesh.groups.items() if "/" not in name for e in g.entities}
    kinds = [(b, "solid") for b in mesh.blocks if b.dim == 3]
    kinds += [(b, "beam") for b in mesh.beam_blocks()]
    kinds += [(b, "shell") for b in mesh.shell_blocks()]
    kinds += [(b, "region") for b in mesh.region_blocks()]
    grouped: dict[tuple[str, str], list] = {}
    for b, kind in kinds:
        if b.element not in WRITTEN:
            raise PuzzleError(
                f"{b.element} elements can't be exported in this format yet", code="unsupported"
            )
        part = owner.get(b.entity, f"block_{b.entity[1]}")
        grouped.setdefault((part, b.element, kind), []).append(b)
    out = []
    for (part, element, kind), bs in grouped.items():
        ids = np.concatenate([b.ids for b in bs])
        conn = np.concatenate([b.connectivity for b in bs])
        out.append(Block(part, element, ids, conn, kind))
    if not out:
        raise PuzzleError("the mesh has no elements to export", code="not_found")
    return out


def block_names(found: list[Block]) -> list[str]:
    """A part's name for its block; with its element type added when a part has more."""
    parts = [b.part for b in found]
    return [b.part if parts.count(b.part) == 1 else f"{b.part} {b.element}" for b in found]


def step_times(steps) -> tuple[list[float], str]:
    """Each step's time value for a time series, and what it is. The steps' own values
    (time, frequency in Hz, load factor) when they increase strictly, as readers need;
    else the step numbers 1, 2, ... (each step's own value then goes in the notes)."""
    values = [float(s.value) for s in steps]
    kinds = {s.kind for s in steps}
    if len(values) > 1 and np.any(np.diff(values) <= 0):
        return [float(i + 1) for i in range(len(values))], "step number (1, 2, ...)"
    if kinds == {"modal"}:
        return values, "frequency (Hz)"
    if kinds == {"buckling"}:
        return values, "load factor"
    if kinds == {"frequency_domain"}:
        return values, "frequency (Hz)"
    return values, "time (s)"


@dataclass(frozen=True)
class Kind:
    """A field as it appears over the steps."""

    name: str
    location: str  # node, element or global
    components: tuple[str, ...]


def fields(steps) -> list[Kind]:
    """Every field of any step, in first-seen order: a format with one variable list for
    the whole file writes each at every step (NaN where a step lacks it)."""
    seen: dict[tuple[str, str], Kind] = {}
    for s in steps:
        for f in s.fields.values():
            key = (f.name, f.location)
            if key not in seen:
                seen[key] = Kind(f.name, f.location, tuple(f.components))
    return list(seen.values())


def values_at(field, ids: np.ndarray, width: int) -> np.ndarray:
    """A field's values at `ids` (n, width), NaN where it has none (or no field)."""
    out = np.full((len(ids), width), np.nan)
    if field is None or not len(field.ids):
        return out
    order = np.argsort(field.ids)
    pos = np.clip(np.searchsorted(field.ids, ids, sorter=order), 0, len(field.ids) - 1)
    found = field.ids[order[pos]] == ids
    out[found] = np.asarray(field.values, float)[order[pos[found]]]
    return out


def component_names(kind: Kind) -> list[str]:
    """One variable name per component: `displacement_x`, `stress_xx`, `temperature`."""
    return [f"{kind.name}_{c}" if c else kind.name for c in kind.components]
