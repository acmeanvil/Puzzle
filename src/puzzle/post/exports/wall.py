"""Wall quantities as point clouds (ADR 0043, fluid): on each wall selection, at its
faces' centres, the wall pressure, the wall shear stress, y+ and the heat transfer
coefficient with its reference temperature, as CSV for a structure or a conjugate heat
transfer model elsewhere (Ansys External Data, COMSOL interpolation, ...).

The CFD adapters store cell values only (velocity, pressure, temperature, k, ...), no
wall fields, so these are computed here from the cells next to the wall:

- wall pressure: the adjacent cell's pressure (the wall-normal pressure gradient is
  negligible in an attached boundary layer);
- wall shear stress (laminar, or a RANS cell inside the viscous sublayer):
  tau = mu u_t / d, the adjacent cell's tangential velocity over its centre's distance
  from the wall: the wall gradient the finite-volume solvers use themselves
  (OpenFOAM's snGrad, Code_Saturne's laminar wall), so the wall forces balance the
  run's pressure drop. (A quadratic through the next cell out was tried: the cell next
  to the wall is off the parabola by about 2 % in both solvers' Poiseuille runs, and it
  came out 2.7 % high where this is 0.45 % low, as low as the runs' dp/dx.)
- wall shear stress (RANS, the adjacent cell in the log layer): the standard wall
  function, u_k = C_mu^(1/4) sqrt(k), tau = rho u_k kappa u_t / ln(E y*), with
  C_mu = 0.09, kappa = 0.41, E = 9.8 (OpenFOAM's; Code_Saturne's differ slightly, so
  expect a few per cent from what the solver used);
- y+ of the adjacent cell's centre: rho u_tau d / mu, u_tau = sqrt(|tau| / rho);
- heat transfer coefficient h = q / (T_wall - T_ref), q the heat flux into the fluid.
  The wall temperature and flux come from the wall's thermal condition (a fixed
  temperature, a flux, or convection) and the same one-cell gradient,
  q = k (T_wall - T_cell) / d. T_ref is the `reference_temperature` asked (K), else
  the inlet's fixed temperature, else the mean of the fluid cells' temperatures; it is
  written as a column. An adiabatic wall has no h.

Fluid properties (density, dynamic viscosity, thermal conductivity) come from the
component's material. The last column is each face's area, the weight a conservative
import (ADR 0044 stage 2) integrates the wall loads with.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from puzzle.errors import PuzzleError
from puzzle.post.exports import fluid

QUANTITIES = {
    "wall_pressure": "Wall pressure",
    "wall_shear_stress": "Wall shear stress",
    "y_plus": "y+",
    "heat_transfer_coefficient": "Heat transfer coefficient",
}
C_MU, KAPPA, E = 0.09, 0.41, 9.8


def _y_laminar() -> float:
    """Where the log law meets u+ = y+ (11.53 for kappa 0.41, E 9.8)."""
    y = 11.0
    for _ in range(50):
        y = np.log(max(E * y, 1.0)) / KAPPA
    return float(y)


Y_LAMINAR = _y_laminar()


@dataclass
class Wall:
    """A wall selection's faces and the cells next to them."""

    centres: np.ndarray  # (n, 3)
    normals: np.ndarray  # (n, 3) unit, into the fluid
    cell1: np.ndarray  # (n,) adjacent cell index
    d1: np.ndarray  # (n,) its centre's distance from the wall
    areas: np.ndarray | None = None  # (n,) the faces' areas (ADR 0044: conservative imports)


def wall_faces(mesh, vol: fluid.Volume, faces: fluid.Faces, blocks) -> Wall:
    corners = []
    for b in blocks:
        k = fluid.CORNERS[fluid.family(b.element)]
        rows = np.full((len(b.ids), 4), -1, dtype=np.int64)
        rows[:, :k] = b.connectivity[:, :k]
        corners.append(rows)
    corners = np.vstack(corners)
    tri = corners[:, 3] < 0
    filled = np.where(tri[:, None], np.column_stack([corners[:, :3], corners[:, 2]]), corners)
    xyz = mesh.node_coords(filled.ravel()).reshape(-1, 4, 3)
    centres = np.where(tri[:, None], xyz[:, :3].mean(1), xyz.mean(1))
    normal = np.where(
        tri[:, None],
        np.cross(xyz[:, 1] - xyz[:, 0], xyz[:, 2] - xyz[:, 0]),
        np.cross(xyz[:, 2] - xyz[:, 0], xyz[:, 3] - xyz[:, 1]),
    )
    areas = 0.5 * np.linalg.norm(normal, axis=1)  # a triangle's, or a quad's by its diagonals
    normal /= np.linalg.norm(normal, axis=1)[:, None]
    cell1 = fluid.adjacent(faces, blocks)
    if (cell1 < 0).any():
        raise PuzzleError("some faces of the selection border no volume cell", code="inconsistent")
    offset = vol.centroids[cell1] - centres
    flip = np.einsum("ij,ij->i", offset, normal) < 0
    normal[flip] *= -1
    d1 = np.einsum("ij,ij->i", offset, normal)
    return Wall(centres, normal, cell1, d1, areas)


def _cells(step, name: str, vol: fluid.Volume, mesh) -> np.ndarray | None:
    """A field per volume cell: its own value, or the mean of a nodal field's corners."""
    field = step.fields.get(name)
    if field is None:
        return None
    if field.location == "element":
        return fluid.lookup(field, vol.ids)
    if field.location == "node":
        corners = vol.corners
        values = fluid.lookup(field, np.where(corners < 0, corners[:, :1], corners).ravel())
        values = values.reshape(*corners.shape, -1)
        mask = (corners >= 0)[..., None]
        return (values * mask).sum(1) / mask.sum(1)
    return None


def shear(w: Wall, velocity, props, k=None) -> np.ndarray:
    """The wall shear stress vector the fluid exerts (n, 3), along the flow."""
    n = w.normals

    def tangential(u):
        return u - np.einsum("ij,ij->i", u, n)[:, None] * n

    u1 = tangential(velocity[w.cell1])
    tau = props["mu"] * u1 / w.d1[:, None]
    if k is not None:
        rho, mu = props["rho"], props["mu"]
        uk = C_MU**0.25 * np.sqrt(np.maximum(k[w.cell1, 0], 0.0))
        ystar = rho * uk * w.d1 / mu
        log = ystar > Y_LAMINAR
        law = (rho * uk * KAPPA / np.log(E * np.maximum(ystar, 1.0)))[:, None] * u1
        tau = np.where(log[:, None], law, tau)
    return tau


def thermal(w: Wall, temperature, condition, conductivity):
    """Wall temperature and heat flux into the fluid per face, from the wall's
    condition: ("temperature", T), ("heat_flux", q) or ("convection", h, T_inf), with
    q = k (T_w - T_cell) / d."""
    k, d = conductivity, w.d1
    t1 = temperature[w.cell1, 0]
    kind = condition[0]
    if kind == "temperature":
        t_wall = np.full(len(t1), float(condition[1]))
    elif kind == "heat_flux":
        t_wall = t1 + condition[1] * d / k
    elif kind == "convection":  # h (T_inf - T_w) = k (T_w - T1) / d
        h, t_inf = condition[1], condition[2]
        t_wall = (h * t_inf + k * t1 / d) / (h + k / d)
    else:
        raise ValueError(f"no wall temperature from a '{kind}' condition")
    return t_wall, k * (t_wall - t1) / d


def write(job):
    from puzzle.model.schema import Material

    mesh, step = job.mesh, job.step
    comp = fluid.component_of(job)
    parts = fluid.boundaries(mesh)
    walls = (
        [f.selection for p in comp.physics if p.type == "fluid_flow"
         for f in p.features if f.type == "wall"]
        if comp else []
    )  # fmt: skip
    if job.selection:
        if job.selection not in parts:
            raise PuzzleError(
                f"no face selection '{job.selection}' in the mesh",
                code="not_found", details={"selections": sorted(parts)},
            )  # fmt: skip
        chosen = [job.selection]
    else:
        chosen = [w for w in dict.fromkeys(walls) if w in parts]
        if not chosen:
            raise PuzzleError("the model has no wall to export; name a selection",
                              code="not_found", details={"selections": sorted(parts)})  # fmt: skip
    wanted = [job.quantity] if job.quantity else list(QUANTITIES)
    unknown = [q for q in wanted if q not in QUANTITIES]
    if unknown:
        raise PuzzleError(f"no wall quantity '{unknown[0]}'", code="not_found",
                          details={"quantities": list(QUANTITIES)})  # fmt: skip

    props = {}
    if comp is not None and comp.materials:
        material = job.project.get(comp.materials[0].material, Material)
        si = {k: q.to_si() for k, q in material.properties.items()}
        props = {"rho": si.get("density"), "mu": si.get("dynamic_viscosity"),
                 "k": si.get("thermal_conductivity")}  # fmt: skip
    props |= {k: v for k, v in job.extra.get("properties", {}).items()}  # tests, scripts
    flow = next((p for p in comp.physics if p.type == "fluid_flow"), None) if comp else None
    turbulent = flow is not None and flow.settings.get("turbulence_model", "laminar") not in (
        "laminar",
        None,
    )

    vol = fluid.volume(mesh)
    faces = fluid.cell_faces(vol)
    velocity = _cells(step, "velocity", vol, mesh)
    pressure = _cells(step, "pressure", vol, mesh)
    temperature = _cells(step, "temperature", vol, mesh)
    k_field = _cells(step, "turbulent_kinetic_energy", vol, mesh) if turbulent else None
    conditions = _thermal_conditions(comp)
    t_ref, ref_from = _reference(job, comp, temperature)

    files, skipped = [], {}
    many = len(chosen) > 1
    for name in chosen:
        w = wall_faces(mesh, vol, faces, parts[name])
        columns: list[tuple[str, str | None, np.ndarray]] = []
        tau = None
        for q in wanted:
            if q == "wall_pressure":
                if pressure is None:
                    skipped[q] = "no pressure in this step"
                    continue
                columns.append(("wall pressure", "pressure", pressure[w.cell1, 0]))
            elif q in ("wall_shear_stress", "y_plus"):
                if velocity is None or not props.get("mu"):
                    skipped[q] = "needs the velocity and the fluid's dynamic viscosity"
                    continue
                if turbulent and k_field is None:
                    skipped[q] = "a RANS run without k stored"
                    continue
                if tau is None:
                    tau = shear(w, velocity, props, k_field)
                if q == "wall_shear_stress":
                    for c, axis in enumerate("xyz"):
                        columns.append((f"wall shear stress {axis}", "pressure", tau[:, c]))
                    columns.append(("wall shear stress magnitude", "pressure",
                                    np.linalg.norm(tau, axis=1)))  # fmt: skip
                else:
                    if not props.get("rho"):
                        skipped[q] = "needs the fluid's density"
                        continue
                    u_tau = np.sqrt(np.linalg.norm(tau, axis=1) / props["rho"])
                    columns.append(("y+", None, props["rho"] * u_tau * w.d1 / props["mu"]))
            elif q == "heat_transfer_coefficient":
                condition = conditions.get(name)
                if temperature is None or not props.get("k"):
                    skipped[q] = "needs the temperature and the fluid's thermal conductivity"
                    continue
                if condition is None:
                    skipped[q] = f"'{name}' is adiabatic: no heat transfer coefficient"
                    continue
                t_wall, flux = thermal(w, temperature, condition, props["k"])
                columns += [
                    ("wall temperature", "temperature", t_wall),
                    ("wall heat flux", "heat_flux", flux),
                    ("heat transfer coefficient", "film", flux / (t_wall - t_ref)),
                    ("reference temperature", "temperature", np.full(len(flux), t_ref)),
                ]
        if columns:  # each face's area: a conservative import's weights (ADR 0044)
            columns.append(("area", "area", w.areas))
        if not columns:
            raise PuzzleError(
                f"no wall quantity could be computed on '{name}'",
                code="not_found", details={"why": skipped},
            )  # fmt: skip
        path = job.path
        if many:
            path = path.with_name(f"{path.stem}_{name.replace('/', '_').replace(' ', '_')}.csv")
        files.append(_csv(path, w.centres, columns, job.units))
    notes = {
        "on": ", ".join(chosen) + " (face centres)",
        "faces": str(sum(len(np.concatenate([b.ids for b in parts[n]])) for n in chosen)),
        "units": "display units" if job.units == "display" else "SI",
        "method": "from the cells next to the wall (see puzzle.post.exports.wall)"
        + ("; RANS: standard wall function" if turbulent else ""),
    }
    if any(q == "heat_transfer_coefficient" for q in wanted) and temperature is not None:
        notes["reference temperature"] = f"{t_ref:.6g} K ({ref_from})"
    notes |= {f"not written: {QUANTITIES[q]}": why for q, why in skipped.items()}
    return files, notes


def _thermal_conditions(comp) -> dict[str, tuple]:
    out = {}
    if comp is None:
        return out
    for p in comp.physics:
        if p.type != "heat_transfer":
            continue
        for f in p.features:
            v = {k: q.to_si() for k, q in f.values.items()}
            if f.type == "temperature":
                out[f.selection] = ("temperature", v["T"])
            elif f.type == "heat_flux":
                out[f.selection] = ("heat_flux", v["q"])
            elif f.type == "convection":
                out[f.selection] = ("convection", v["h"], v["T_inf"])
    return out


def _reference(job, comp, temperature) -> tuple[float, str]:
    asked = job.extra.get("reference_temperature")
    if asked is not None:
        value = asked.to_si() if hasattr(asked, "to_si") else float(asked)
        return float(value), "as asked"
    if comp is not None:
        kinds = ("velocity_inlet", "mass_flow_inlet")
        inlets = {f.selection for p in comp.physics if p.type == "fluid_flow"
                  for f in p.features if f.type in kinds}  # fmt: skip
        for selection, condition in _thermal_conditions(comp).items():
            if selection in inlets and condition[0] == "temperature":
                return float(condition[1]), f"the inlet temperature on {selection}"
    if temperature is not None:
        return float(np.nanmean(temperature)), "the mean of the fluid cells' temperatures"
    return float("nan"), "no temperature"


def _csv(path: Path, centres, columns, units: str) -> Path:
    length, xyz = "m", centres
    shown = []
    if units == "display":
        from puzzle.gui.viewport import _display
        from puzzle.settings import length_unit

        length, per_metre = length_unit()
        xyz = centres * per_metre
        for name, kind, values in columns:
            values, unit = _display(values, kind)
            shown.append((name, unit, values))
    else:
        from puzzle.model.units import DIMENSIONS

        shown = [(n, DIMENSIONS[k][1] if k else None, v) for n, k, v in columns]
    header = [f"x [{length}]", f"y [{length}]", f"z [{length}]"]
    header += [f"{n} [{u}]" if u else n for n, u, _ in shown]
    table = np.column_stack([xyz, *(v for _, _, v in shown)])
    path.write_text(
        ",".join(header) + "\n"
        + "\n".join(",".join(f"{v:.9g}" for v in row) for row in table) + "\n"
    )  # fmt: skip
    return path
