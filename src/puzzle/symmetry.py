"""Symmetry planes (ADR 0081 step 1): a model cut where it mirrors itself.

A `symmetry` feature on a flat face (a straight edge in a 2D model) says the model
continues mirrored across it: in solid mechanics nothing moves through the plane, an
`antisymmetry` feature nothing along it. Both become plain `displacement` supports on the
face's normal here, after meshing, so every FE solver that holds components writes them
with its own supports:

- a plane across x, y or z holds that global component (or, antisymmetric, the others);
- any other plane holds the component along its normal in a Cartesian system made for
  it, which only adapters taking `displacement:cartesian:partial` write (CalculiX); the
  others refuse it by name.

In heat transfer and electrostatics symmetry is the natural condition (no flux across
the plane), so the feature is dropped: it only records the intent and draws the plane.
Fluid and magnetic symmetry are the adapters' own and pass through untouched.
"""

from __future__ import annotations

import dataclasses

import numpy as np

SOLID = ("symmetry", "antisymmetry")
NATURAL = {"heat_transfer", "electrostatics"}  # symmetry is no flux: nothing to write
FLAT = 1e-6  # a face's out-of-plane spread, relative to its size, still called flat
ALIGNED = 1e-6  # 1 - |n·axis| below this: the plane is across that axis
# An adapter holding any plane: components in local Cartesian axes (CalculiX, Code_Aster),
# or a plane by its own normal (Elmer's Normal-Tangential Displacement).
OBLIQUE = {"displacement:cartesian:partial", "symmetry:oblique"}


# The feature each physics takes for a symmetry plane (ADR 0081).
SYMMETRY = {
    "solid_mechanics": "symmetry",
    "heat_transfer": "symmetry",
    "electrostatics": "symmetry",
    "fluid_flow": "symmetry",
    "magnetic_fields": "magnetic_symmetry",
}


def plane_features(comp, kind: str) -> list[tuple[int, str]]:
    """(physics index, feature type) for each physics a `kind` plane goes on."""
    if kind == "antisymmetry":
        return [(i, "antisymmetry") for i, p in enumerate(comp.physics)
                if p.type == "solid_mechanics"]  # fmt: skip
    return [(i, SYMMETRY[p.type]) for i, p in enumerate(comp.physics) if p.type in SYMMETRY]


def plane_ops(comp, kind: str, selection: str) -> list:
    """The Edit ops putting a `kind` plane on `selection` in every physics that takes it."""
    from puzzle.commands.model import EditOp

    return [
        EditOp(
            op="append", path=f"physics.{i}.features", value={"type": t, "selection": selection}
        )  # fmt: skip
        for i, t in plane_features(comp, kind)
    ]


def plane(points: np.ndarray, planar: bool = False) -> tuple[np.ndarray, np.ndarray, bool]:
    """(unit normal, centre, flat?) of a face's nodes; in a 2D model (`planar`) the normal
    of the straight edge they lie on, in the xy plane."""
    centre = points.mean(axis=0)
    rel = points - centre
    size = float(np.linalg.norm(rel, axis=1).max()) or 1.0
    if planar:
        _, _, vt = np.linalg.svd(rel[:, :2], full_matrices=False)
        along = vt[0]
        normal = np.array([-along[1], along[0], 0.0])
    else:
        _, _, vt = np.linalg.svd(rel, full_matrices=False)
        normal = vt[-1]
    spread = float(np.abs(rel @ normal).max())
    return normal / np.linalg.norm(normal), centre, spread <= FLAT * size


def _axis(normal: np.ndarray) -> int | None:
    k = int(np.argmax(np.abs(normal)))
    return k if 1.0 - abs(float(normal[k])) < ALIGNED else None


def held(kind: str, axis: int, planar: bool) -> list[str]:
    """The global components a plane across `axis` holds."""
    names = "xy" if planar else "xyz"
    if kind == "symmetry":
        return ["xyz"[axis]]
    return [a for a in names if a != "xyz"[axis]]


def applied(project, component_id, mesh, caps=None):
    """The project whose component has its symmetry planes as displacement supports (or
    dropped, where symmetry is natural). `caps`, the adapter's capabilities, refuses a
    plane across no axis where the adapter can't hold a component in local axes."""
    from puzzle.adapters.common import InputError
    from puzzle.model.schema import Component, CoordinateSystem, Feature, Quantity

    comp = project.objects.get(component_id)
    if not isinstance(comp, Component):
        return project
    if not any(f.type in SOLID for p in comp.physics for f in p.features):
        return project
    planar = comp.dimension != "3d"
    objects = dict(project.objects)
    physics = []
    for ph in comp.physics:
        features = []
        for f in ph.features:
            if f.type not in SOLID or ph.type in ("fluid_flow", "magnetic_fields"):
                features.append(f)
                continue
            if ph.type in NATURAL and f.type == "symmetry":
                continue  # no flux across the plane: the solver's natural condition
            if ph.type != "solid_mechanics":
                raise InputError(f"{f.type} on '{f.selection}' is not written in {ph.type} "
                                 "(antisymmetry is structural only)",
                                 code="unsupported")  # fmt: skip
            group = mesh.groups.get(f.selection)
            if group is None:
                raise InputError(f"no selection '{f.selection}' in the mesh")
            normal, centre, flat = plane(mesh.node_coords(group.node_ids), planar)
            if not flat:
                shape = "a straight edge" if planar else "a flat face"
                raise InputError(
                    f"a {f.type} plane needs {shape}: '{f.selection}' is curved or bent",
                    code="invalid",
                )
            axis = _axis(normal)
            keep = {"steps": f.steps, "when": f.when}
            if axis is not None:
                values = {a: Quantity(value=0.0, unit="m") for a in held(f.type, axis, planar)}
                features.append(Feature(type="displacement", selection=f.selection,
                                        values=values, **keep))  # fmt: skip
                continue
            if caps is not None and not OBLIQUE & set(caps.axes_features):
                raise InputError(
                    f"the {f.type} plane '{f.selection}' is across no global axis; this "
                    "solver holds symmetry only on planes across x, y or z so far "
                    "(CalculiX, Code_Aster and Elmer take any plane)",
                    code="unsupported",
                )
            other = np.eye(3)[int(np.argmin(np.abs(normal)))]
            cs = CoordinateSystem(name=f"{f.type}: {f.selection}", type="cartesian",
                                  origin=tuple(map(float, centre)),
                                  x_axis=tuple(map(float, normal)),
                                  xy_direction=tuple(map(float, other)))  # fmt: skip
            objects[cs.id] = cs
            local = ["x"] if f.type == "symmetry" else (["y"] if planar else ["y", "z"])
            values = {a: Quantity(value=0.0, unit="m") for a in local}
            # Elmer holds it by its own normal and tangents (Normal-Tangential Displacement),
            # the same frame for a flat face; CalculiX as n · u = 0 equations, which meet
            # other planes and supports at shared edges (a *TRANSFORM can't).
            features.append(Feature(type="displacement", selection=f.selection,
                                    values=values, axes=cs.id,
                                    extensions={"elmer": {"normal_tangential": True},
                                                "calculix": {"equations": True}},
                                    **keep))  # fmt: skip
        physics.append(ph.model_copy(update={"features": features}))
    objects[comp.id] = comp.model_copy(update={"physics": physics})
    return dataclasses.replace(project, objects=objects)
