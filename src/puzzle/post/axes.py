"""Results in a coordinate system (ADR 0049): vectors and tensors rotated into a
system's axes at each point, after the solve and for every solver alike.

`in_axes(mesh, results, system)` gives a copy of the results whose vector fields (x, y,
z) and tensor fields (xx ... zx) hold components along the system's axes at each node
(or element centre): x, y, z of a Cartesian system; r, θ, z of a cylindrical one; r, θ,
φ of a spherical one. The component slots keep their names (x, xx, ...) so every result
tool reads them unchanged; `quantities.catalog(..., axes=type)` names them for the
system ("Normal stress θ (hoop)"). Scalars and quantities that don't depend on the
axes (temperature, magnitudes, von Mises, principal stresses) come out the same.

The deformed shape still needs global displacements: they are kept as
`displacement_global`, which the view warps by when present.
"""

from __future__ import annotations

import numpy as np

from puzzle.results.model import Field, Results, StepResult

VECTOR = ["x", "y", "z"]
TENSOR = ["xx", "yy", "zz", "xy", "yz", "zx"]


def rotate_vectors(frames: np.ndarray, v: np.ndarray) -> np.ndarray:
    """(n, 3) global vectors in the local axes `frames` (n, 3, 3: columns the axes)."""
    return np.einsum("nji,nj->ni", frames, np.asarray(v, float))


def rotate_tensors(frames: np.ndarray, t: np.ndarray) -> np.ndarray:
    """(n, 6) global tensors (xx, yy, zz, xy, yz, zx) as Rᵀ σ R, the same order."""
    t = np.asarray(t, float)
    xx, yy, zz, xy, yz, zx = (t[:, i] for i in range(6))
    s = np.stack([np.stack([xx, xy, zx], -1), np.stack([xy, yy, yz], -1),
                  np.stack([zx, yz, zz], -1)], -2)  # fmt: skip
    m = np.einsum("nji,njk,nkl->nil", frames, s, frames)
    return np.stack([m[:, 0, 0], m[:, 1, 1], m[:, 2, 2], m[:, 0, 1], m[:, 1, 2], m[:, 2, 0]], 1)


def _points(mesh, f: Field) -> np.ndarray:
    """Where a field's values sit: its nodes, or its elements' centres."""
    if f.location == "node":
        return mesh.node_coords(f.ids)
    centres = {}
    for block in mesh.blocks:
        corners = block.connectivity[:, : min(4, block.connectivity.shape[1])]
        xyz = mesh.node_coords(corners.ravel()).reshape(len(block.ids), -1, 3).mean(axis=1)
        centres.update(zip(block.ids.tolist(), xyz, strict=True))
    return np.array([centres[i] for i in np.asarray(f.ids).tolist()])


def in_axes(mesh, results: Results, system) -> Results:
    """`results` with vectors and tensors in `system`'s axes (see the module notes)."""
    steps = []
    for step in results.steps:
        fields = dict(step.fields)
        for name, f in step.fields.items():
            if f.location == "global":
                continue
            if list(f.components) == VECTOR:
                frames = system.frames(_points(mesh, f))
                fields[name] = _with(f, rotate_vectors(frames, f.values))
                if name == "displacement":
                    fields["displacement_global"] = _renamed(f, "displacement_global")
            elif list(f.components) == TENSOR:
                frames = system.frames(_points(mesh, f))
                fields[name] = _with(f, rotate_tensors(frames, f.values))
        steps.append(_step(step, fields))
    return Results(results.solver, steps)


def _with(f: Field, values) -> Field:
    import dataclasses

    return dataclasses.replace(f, values=np.asarray(values))


def _renamed(f: Field, name: str) -> Field:
    import dataclasses

    return dataclasses.replace(f, name=name)


def _step(step: StepResult, fields: dict) -> StepResult:
    import dataclasses

    return dataclasses.replace(step, fields=fields)
