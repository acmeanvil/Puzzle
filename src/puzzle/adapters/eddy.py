"""Magnetodynamics shared by the Elmer and FEniCSx adapters (ADR 0047).

A coil is one of two features on a body:
- `coil_current`, a **massive** conductor: the total current I (peak, phase in degrees)
  enters through its `coil_start` face and leaves through `coil_end`; eddy currents and
  skin effect develop inside it;
- `stranded_coil`, a **winding**: `turns` turns of current I spread evenly over its
  section (no eddy currents in it); `fill` is the copper's share of the section, which
  only sets its resistance. Without terminal faces it is a closed loop.

Results are the same on both solvers, computed here from element values:
- `loss_density` (W/m³, time-averaged: ½|J|²/σ; a winding's ½|J|²/(σ fill));
- `joule_loss` (W): its components are "total" and each body;
- `magnetic_energy` (J, time-averaged: ¼ ∫ B·H*);
- `coil_resistance` (Ω) and `coil_inductance` (H), one component per coil, from each
  coil's voltage over its current: a massive conductor's terminal potential, a
  winding's DC resistance plus jω times its flux linkage ∫ A·J / I.
Every value is a peak phasor; time averages carry the ½.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from puzzle.adapters.common import InputError, surface_blocks

MU0 = 4e-7 * math.pi
COILS = ("coil_current", "stranded_coil")
TERMINALS = ("coil_start", "coil_end")
META = "puzzle-magnetics.json"


@dataclass
class Coil:
    """One coil: `body` its body selection, `current` its peak current per turn as
    (real, imaginary) amperes."""

    body: str
    kind: str  # "massive" or "stranded"
    current: tuple[float, float]
    turns: float = 1.0
    fill: float = 1.0
    conductivity: float = 0.0  # S/m, the conductor's
    start: str | None = None
    end: str | None = None
    start_nodes: list[int] = field(default_factory=list)  # Puzzle node ids

    @property
    def phasor(self) -> complex:
        return complex(*self.current)

    @property
    def closed(self) -> bool:
        return self.start is None


def phasor(f) -> complex:
    """A coil feature's current I at its phase (degrees)."""
    current = f.values["I"].to_si()
    phase = f.values["phase"].to_si() if "phase" in f.values else 0.0  # radians
    return current * complex(math.cos(phase), math.sin(phase))


def _nodes(mesh, name: str) -> set[int]:
    group = mesh.groups.get(name)
    if group is None:
        raise InputError(f"no selection '{name}' in the mesh")
    if group.dim == (2 if mesh.planar else 3):
        return {int(n) for b in group.blocks(mesh) for n in b.connectivity.ravel()}
    return {int(n) for b in surface_blocks(group, mesh) for n in b.connectivity.ravel()}


def coils(project, comp, features, mesh, *, solver: str) -> list[Coil]:
    """The component's coils with their terminals, each terminal face matched to the
    coil body it lies on. A massive conductor needs one start and one end face; a
    winding has both or neither."""
    from puzzle.model.schema import Material

    material = {a.selection: project.get(a.material, Material) for a in comp.materials}
    terminals = [f for f in features if f.type in TERMINALS]
    out: list[Coil] = []
    for f in (f for f in features if f.type in COILS):
        if f.selection not in material:
            raise InputError(f"a coil must be on a body with a material, not '{f.selection}'")
        kind = "massive" if f.type == "coil_current" else "stranded"
        sigma = material[f.selection].properties.get("electrical_conductivity")
        body = _nodes(mesh, f.selection)
        mine = {t.type: t for t in terminals if _nodes(mesh, t.selection) <= body}
        coil = Coil(
            f.selection,
            kind,
            (phasor(f).real, phasor(f).imag),
            turns=f.values["turns"].to_si() if "turns" in f.values else 1.0,
            fill=f.values["fill"].to_si() if "fill" in f.values else 1.0,
            conductivity=sigma.to_si() if sigma is not None else 0.0,
            start=mine["coil_start"].selection if "coil_start" in mine else None,
            end=mine["coil_end"].selection if "coil_end" in mine else None,
        )
        if (coil.start is None) != (coil.end is None):
            raise InputError(
                f"coil '{f.selection}' needs both a 'current enters' and a 'current leaves' "
                "face on it, or neither (a closed winding)"
            )
        if kind == "massive" and coil.closed:
            raise InputError(
                f"the massive conductor '{f.selection}' needs a face where the current enters "
                "and one where it leaves"
            )
        if kind == "stranded" and not 0 < coil.fill <= 1:
            raise InputError(f"coil '{f.selection}': fill is a share of the section, 0 to 1")
        if coil.start:
            coil.start_nodes = sorted(_nodes(mesh, coil.start))
        out.append(coil)
    claimed = {c.start for c in out} | {c.end for c in out}
    loose = [t.selection for t in terminals if t.selection not in claimed]
    if loose:
        raise InputError(f"coil terminal faces {loose} are not on any coil's body")
    if not out:
        raise InputError(f"{solver} solves magnetic fields driven by a coil: add one")
    return out


def write_meta(path: Path, bodies: list[dict], coils_: list[Coil], frequencies) -> None:
    """What the result reader needs: each body's μr and σ, the coils, the frequencies."""
    path.write_text(
        json.dumps(
            {
                "bodies": bodies,
                "coils": [asdict(c) for c in coils_],
                "frequencies": list(frequencies),
            }
        )
    )


def read_meta(path: Path) -> dict:
    meta = json.loads(path.read_text())
    meta["coils"] = [Coil(**c) for c in meta["coils"]]
    return meta


def skin_depth(frequency: float, mu_r: float, sigma: float) -> float:
    """δ = √(2 / (ω μ σ)), m; infinite for a non-conductor or at 0 Hz."""
    if frequency <= 0 or sigma <= 0:
        return math.inf
    return math.sqrt(2 / (2 * math.pi * frequency * MU0 * mu_r * sigma))


def tet_volumes(coords: np.ndarray, corners: np.ndarray) -> np.ndarray:
    """Each tetrahedron's volume from its four corner nodes' coordinates."""
    p = coords[corners]
    return (
        np.abs(
            np.einsum("ij,ij->i", p[:, 1] - p[:, 0], np.cross(p[:, 2] - p[:, 0], p[:, 3] - p[:, 0]))
        )
        / 6
    )


def globals_(
    meta: dict,
    omega: float,
    body: np.ndarray,
    volume: np.ndarray,
    B,
    J,
    A,
    start_potential: dict[str, complex],
):
    """The loss density per element and the whole-model fields (see the module's
    docstring). `body` is each element's index into meta["bodies"]; B, J, A are complex
    (n, 3) element values; `start_potential` each massive coil's start-face potential.
    At ω = 0 (a static solve) nothing is averaged: loss |J|²/σ, energy ½ ∫ B·H, and a
    winding's inductance is its flux linkage over its current."""
    from puzzle.results.model import Field

    bodies = meta["bodies"]
    mu = np.array([MU0 * b["mu_r"] for b in bodies])[body]
    sigma = np.array([b["sigma"] for b in bodies], float)
    winding = {c.body: c for c in meta["coils"] if c.kind == "stranded"}
    names = [b["name"] for b in bodies]
    # A winding carries no eddy current (σ = 0 in the solve); its copper loses ½|J|²/(σ fill).
    effective = np.array(
        [
            winding[n].conductivity * winding[n].fill if n in winding else sigma[k]
            for k, n in enumerate(names)
        ]
    )[body]
    j2 = np.einsum("ij,ij->i", J, J.conj()).real
    half = 0.5 if omega else 1.0  # time averages of peak phasors carry ½
    density = np.where(effective > 0, half * j2 / np.where(effective > 0, effective, 1), 0.0)
    per_body = [float((density * volume)[body == k].sum()) for k in range(len(bodies))]
    energy = 0.5 * half * float((np.einsum("ij,ij->i", B, B.conj()).real / mu * volume).sum())
    out = {
        "joule_loss": Field(
            "joule_loss",
            "global",
            ["total", *names],
            np.array([0]),
            np.array([[sum(per_body), *per_body]]),
        ),
        "magnetic_energy": Field(
            "magnetic_energy", "global", [""], np.array([0]), np.array([[energy]])
        ),
    }
    resistance, inductance = [], []
    for c in meta["coils"]:
        current = c.phasor
        if c.kind == "massive":
            voltage = start_potential[c.body]
        else:
            inside = body == names.index(c.body)
            jc = J[inside]
            dc = (np.einsum("ij,ij->i", jc, jc.conj()).real * volume[inside]).sum()
            r_dc = dc / (c.conductivity * c.fill * abs(current) ** 2) if c.conductivity else 0.0
            linkage = (np.einsum("ij,ij->i", A[inside], jc) * volume[inside]).sum() / current
            voltage = r_dc * current + 1j * omega * linkage
        z = voltage / current
        resistance.append(z.real)
        if omega:
            inductance.append(z.imag / omega)
        else:  # static: L = Ψ / I for a winding
            inductance.append((linkage / current).real if c.kind == "stranded" else math.nan)
    coils_ = [c.body for c in meta["coils"]]
    out["coil_resistance"] = Field(
        "coil_resistance", "global", coils_, np.array([0]), np.array([resistance])
    )
    out["coil_inductance"] = Field(
        "coil_inductance", "global", coils_, np.array([0]), np.array([inductance])
    )
    return density, out


ELEMENTS = "puzzle-elements.npz"  # each volume element's id, body, centre and volume


def element_table(mesh, bodies: list[str], path: Path) -> None:
    """Each volume element's Puzzle id, body (index into `bodies`, the first holding
    it), corner-average centre and volume, in the mesh's block order."""
    body_of: dict[int, int] = {}
    for index, name in enumerate(bodies):
        for b in mesh.groups[name].blocks(mesh):
            for eid in b.ids:
                body_of.setdefault(int(eid), index)
    blocks = [b for b in mesh.blocks if b.dim == 3]
    ids = np.concatenate([b.ids for b in blocks]).astype(np.int64)
    corners = np.concatenate([b.connectivity[:, :4] for b in blocks])
    xyz = mesh.node_coords(corners.ravel()).reshape(-1, 4, 3)
    index = {int(n): i for i, n in enumerate(mesh.node_ids)}
    local = np.vectorize(index.__getitem__, otypes=[np.int64])(corners)
    np.savez(path, ids=ids, body=np.array([body_of[int(e)] for e in ids], np.int64),
             centres=xyz.mean(axis=1), volume=tet_volumes(mesh.coords, local))  # fmt: skip


def step_result(meta: dict, hz: float, table, B, J, A, start: dict[str, complex], node_ids,
                b_nodal):  # fmt: skip
    """One frequency's (or the static solve's) result step: B per node (amplitude and
    phase when harmonic), J and the loss density per element, and the whole-model
    values. B, J, A are complex element values in `table`'s order; `b_nodal` complex
    nodal B in `node_ids`' order."""
    from puzzle.adapters.common import harmonic_fields
    from puzzle.results.model import Field, StepResult

    ids, body, volume = table["ids"], table["body"], table["volume"]
    density, whole = globals_(meta, 2 * math.pi * hz, body, volume, B, J, A, start)
    by_id, order = np.argsort(ids), np.argsort(node_ids)
    harmonic = meta["frequencies"] != [0.0]
    xyz = ["x", "y", "z"]
    if harmonic:
        fields = [
            *harmonic_fields("magnetic_flux_density", node_ids[order], b_nodal.real[order],
                             b_nodal.imag[order], xyz),
            *harmonic_fields("current_density", ids[by_id], J.real[by_id], J.imag[by_id], xyz,
                             location="element"),
        ]  # fmt: skip
    else:
        fields = [
            Field("magnetic_flux_density", "node", xyz, node_ids[order], b_nodal.real[order]),
            Field("current_density", "element", xyz, ids[by_id], J.real[by_id]),
        ]
    fields.append(Field("loss_density", "element", [""], ids[by_id], density[by_id, None]))
    kind, value = ("frequency_domain", hz) if harmonic else ("", 1.0)
    return StepResult(1, kind, value, None, {f.name: f for f in fields} | whole)
