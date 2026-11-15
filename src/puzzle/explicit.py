"""Explicit dynamics' time step and cost, before a run (ADR 0085 decision 4).

Central differences are stable while the time step stays under the time a wave takes
to cross the smallest element: Δt ≤ L / c, with c the dilatational wave speed
√(E (1 − ν) / (ρ (1 + ν)(1 − 2ν))) and L the element's characteristic length (a
tetrahedron's smallest height 3 V / A_max, a brick's V / A_max; a ten-node tetrahedron
about half its corner tetrahedron's, its mid-edge nodes halving the spacing). The
solver takes a share of it (the time-step scale, 0.9 by default). These are estimates
for the user and the agent: the solver's own step decides.

Mass scaling raises the step of the elements under a target by adding nodal mass, so
the mass a target would add is said with it: m (Δt_target / Δt_e)² − m per element.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from puzzle.mesh.mesh import Mesh
from puzzle.model.schema import Component, Material, Project

CYCLES_WARN = 1e7  # a run longer than this is warned (ADR 0085 decision 4)
CYCLES_AIM = 1e6  # the mass scaling suggested brings a run to about this


@dataclass
class Estimate:
    time_step: float  # the smallest stable step, s (before the scale)
    element: int  # the element setting it
    where: tuple[float, float, float]  # its centre
    cycles: float  # end time / (scale × step)
    scale: float
    mass: float  # kg, the model's
    suggestion: float | None = None  # a mass scaling time step for about CYCLES_AIM cycles
    added_mass: float = 0.0  # kg that suggestion would add
    smallest: list[tuple[int, float]] = None  # the ten smallest (element, step)

    def summary(self) -> dict:
        out = {"stable_time_step_s": self.time_step, "element": self.element,
               "where_m": list(self.where), "cycles": round(self.cycles),
               "time_step_scale": self.scale, "mass_kg": self.mass}  # fmt: skip
        if self.suggestion is not None:
            out |= {
                "mass_scaling_time_step_s": self.suggestion,
                "added_mass_kg": self.added_mass,
                "added_mass_share": self.added_mass / self.mass if self.mass else 0.0,
            }
        return out


def wave_speed(e: float, nu: float, rho: float) -> float:
    return float(np.sqrt(e * (1 - nu) / (rho * (1 + nu) * (1 - 2 * nu))))


def _tri_area(a, b, c):
    return 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=-1)


def element_lengths(mesh: Mesh) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Every solid element's (ids, characteristic lengths, volumes, centres)."""
    ids, lengths, volumes, centres = [], [], [], []
    for b in mesh.blocks:
        if b.dim != 3 or b.element not in ("tet4", "tet10", "hex8"):
            continue
        n = mesh.node_coords(b.connectivity.ravel()).reshape(len(b.ids), -1, 3)
        if b.element.startswith("tet"):
            p0, p1, p2, p3 = (n[:, k] for k in range(4))
            v = np.abs(np.einsum("ij,ij->i", np.cross(p1 - p0, p2 - p0), p3 - p0)) / 6
            areas = np.stack([_tri_area(p0, p1, p2), _tri_area(p0, p1, p3),
                              _tri_area(p0, p2, p3), _tri_area(p1, p2, p3)], axis=1)  # fmt: skip
            length = 3 * v / areas.max(axis=1)
            if b.element == "tet10":
                length = length / 2
            centre = n[:, :4].mean(axis=1)
        else:
            centre = n.mean(axis=1)
            faces = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6),
                     (3, 0, 4, 7)]  # fmt: skip
            quads = [_tri_area(n[:, a], n[:, b_], n[:, c]) + _tri_area(n[:, a], n[:, c], n[:, d])
                     for a, b_, c, d in faces]  # fmt: skip
            areas = np.stack(quads, axis=1)
            # the volume as six tetrahedra about the centre's faces
            v = np.zeros(len(b.ids))
            for a, b_, c, d in faces:
                for t in ((a, b_, c), (a, c, d)):
                    q = [n[:, k] for k in t]
                    v += np.abs(np.einsum("ij,ij->i", np.cross(q[1] - q[0], q[2] - q[0]),
                                          centre - q[0])) / 6  # fmt: skip
            length = v / areas.max(axis=1)
        ids.append(b.ids)
        lengths.append(length)
        volumes.append(v)
        centres.append(centre)
    if not ids:
        return (np.zeros(0, int), np.zeros(0), np.zeros(0), np.zeros((0, 3)))
    return (np.concatenate(ids), np.concatenate(lengths), np.concatenate(volumes),
            np.concatenate(centres))  # fmt: skip


def _materials(
    project: Project, comp: Component, mesh: Mesh
) -> dict[int, tuple[float, float, float]]:
    """Each solid element's (E, ν, ρ), later assignments winning."""
    out = {}
    for a in comp.materials:
        m = project.objects.get(a.material)
        group = mesh.groups.get(a.selection)
        if not isinstance(m, Material) or group is None:
            continue
        p = {k: q.to_si() for k, q in m.properties.items()}
        if not {"youngs_modulus", "poissons_ratio", "density"} <= set(p):
            continue
        props = (p["youngs_modulus"], p["poissons_ratio"], p["density"])
        for b in group.blocks(mesh):
            if b.dim == 3:
                out.update(dict.fromkeys(b.ids.tolist(), props))
    return out


def estimate(project: Project, comp: Component, mesh: Mesh, end_time: float,
             scale: float = 0.9) -> Estimate | None:  # fmt: skip
    """The stable time step, the cycles to `end_time`, and when a run would pass
    CYCLES_WARN cycles the mass scaling for about CYCLES_AIM with the mass it adds.
    None when no element has a material with E, ν and ρ."""
    ids, lengths, volumes, centres = element_lengths(mesh)
    props = _materials(project, comp, mesh)
    keep = np.array([int(e) in props for e in ids], bool)
    if not keep.any():
        return None
    ids, lengths, volumes, centres = ids[keep], lengths[keep], volumes[keep], centres[keep]
    e, nu, rho = (np.array([props[int(i)][k] for i in ids]) for k in range(3))
    c = np.sqrt(e * (1 - nu) / (rho * (1 + nu) * (1 - 2 * nu)))
    steps = lengths / c
    k = int(np.argmin(steps))
    dt = float(steps[k])
    mass = rho * volumes
    cycles = end_time / (scale * dt)
    order = np.argsort(steps)[:10]
    out = Estimate(
        dt,
        int(ids[k]),
        tuple(float(x) for x in centres[k]),
        float(cycles),
        scale,
        float(mass.sum()),
        smallest=[(int(ids[i]), float(steps[i])) for i in order],
    )
    if cycles > CYCLES_WARN:
        target = end_time / (scale * CYCLES_AIM)
        under = steps < target
        out.suggestion = float(target)
        out.added_mass = float((mass[under] * ((target / steps[under]) ** 2 - 1)).sum())
    return out


def warnings(est: Estimate | None) -> list[str]:
    """What the estimate warns of (ADR 0085 decision 4), in words."""
    if est is None:
        return []
    out = []
    if est.cycles > CYCLES_WARN:
        out.append(f"about {est.cycles:.3g} cycles at a stable step of {est.time_step:.3g} s "
                   f"(element {est.element}); mass scaling at {est.suggestion:.3g} s would "
                   f"bring it to about {CYCLES_AIM:.0e}, adding {est.added_mass:.3g} kg "
                   f"({est.added_mass / est.mass:.1%} of the mass)")  # fmt: skip
    if est.smallest and len(est.smallest) > 1:
        tenth = est.smallest[-1][1]
        if est.time_step < 0.2 * tenth:
            out.append(f"element {est.element} sets the time step at {est.time_step:.3g} s, "
                       f"under a fifth of the tenth smallest's ({tenth:.3g} s): a sliver or a "
                       "small feature; remeshing it would speed the run")  # fmt: skip
    return out


ENERGY_ERROR = 0.05  # ADR 0085 decision 5
HOURGLASS_SHARE = 0.10
ADDED_MASS_SHARE = 0.05


def _series(results, name) -> np.ndarray:
    out = []
    for s in results.steps:
        f = s.fields.get(name)
        out.append(float(f.values[0, 0]) if f is not None and f.values.size else 0.0)
    return np.array(out)


def run_checks(results) -> list[tuple[str, str]]:
    """After an explicit run, from its frames' global energies (ADR 0085 decision 5):
    (code, sentence) for an energy balance off by more than 5 %, hourglass energy over
    10 % of the internal energy, or added mass over 5 % of the mass."""
    if not results.steps or "kinetic_energy" not in results.steps[0].fields:
        return []
    ke, ie = _series(results, "kinetic_energy"), _series(results, "internal_energy")
    held = ke + ie + _series(results, "rotational_energy") + _series(results, "contact_energy") \
        + _series(results, "hourglass_energy")  # fmt: skip
    work = _series(results, "external_work")
    expected = held[0] + work
    scale = max(float(np.max(np.abs(held))), float(np.max(np.abs(expected))), 1e-300)
    error = float(np.max(np.abs(held - expected))) / scale
    out = []
    if error > ENERGY_ERROR:
        out.append(("energy_error", f"the energy balance is off by {error:.1%} (kinetic, internal, "
                                    "contact and hourglass against the start plus the external "
                                    "work): a time step scale too large, or a contact or wall "
                                    "making or losing energy"))  # fmt: skip
    hg = _series(results, "hourglass_energy")
    if ie[-1] > 0 and hg[-1] > HOURGLASS_SHARE * ie[-1]:
        out.append(("hourglass", f"hourglass energy is {hg[-1] / ie[-1]:.1%} of the internal "
                                 "energy: the bricks' zero-energy modes are taking part; refine "
                                 "there or use tetrahedra"))  # fmt: skip
    added, mass = _series(results, "added_mass"), _series(results, "mass")
    if mass[-1] > 0 and added[-1] > ADDED_MASS_SHARE * mass[-1]:
        out.append(("added_mass", f"mass scaling added {added[-1] / mass[-1]:.1%} of the mass: "
                                  "inertial results are changed; lower the mass scaling time "
                                  "step or remesh the small elements"))  # fmt: skip
    return out
