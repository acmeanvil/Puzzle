"""How imported fields connect to the model, in words and on the mesh (ADR 0044
decision 8, the tree, Properties and the 3D view; the schematic is ADR 0058's).

- `mapped` maps a field's column (or a feature's field use) onto a mesh selection's
  nodes, as a run would, and marks the nodes past the tolerance: what the view colours.
- `placement_words`, `use_words`: a placement and a use's scaling, mapping and limits as
  a sentence, in the display units.
- `used_by`: where an imported field is used (an imported field's Properties).
- `chain`: a field-fed feature's values from the file to the solver (its Properties).

Nothing here needs Qt: the GUI's `gui/field_view.py` draws and lists what these give.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

MAPPINGS = {
    "linear": "linear between the points",
    "nearest": "the nearest point's value",
    "inverse_distance": "inverse distance between the nearest points",
}
BEYOND = {
    "nearest": "the nearest value",
    "zero": "zero",
    "refuse": "refused",
}


@dataclass
class Mapped:
    """A column's values on a selection's nodes (SI), each node's distance to the
    nearest data point, and which are farther than `tolerance` (extrapolated)."""

    node_ids: np.ndarray
    values: np.ndarray
    distance: np.ndarray
    tolerance: float
    unit: str  # the values' SI unit, as pint names it ("" when dimensionless)
    label: str  # "Film coefficients: h"
    points: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))  # the data, placed
    point_values: np.ndarray = field(default_factory=lambda: np.zeros(0))

    @property
    def beyond(self) -> np.ndarray:
        return self.distance > self.tolerance

    @property
    def extrapolated(self) -> int:
        return int(self.beyond.sum())


def default_tolerance(points: np.ndarray, targets: np.ndarray) -> float:
    """As `fields.check` takes it: 2 % of the target's size, or 1.5 times the data's
    median spacing when that is larger (a coarse grid that covers the target)."""
    from scipy.spatial import cKDTree

    from puzzle.fields import _model_size

    size = _model_size(np.asarray(targets))
    if len(points) > 1:
        spacing = cKDTree(points).query(points, k=2)[0][:, 1]
        return max(0.02 * size, 1.5 * float(np.median(spacing)))
    return 0.02 * size


def selection_nodes(mesh, selection: str | None) -> tuple[np.ndarray, np.ndarray]:
    """A selection's node ids and positions in a mesh; the whole mesh for None."""
    if not selection:
        return np.asarray(mesh.node_ids), np.asarray(mesh.coords)
    group = mesh.groups.get(selection)
    if group is None:
        from puzzle.fields import FieldError

        raise FieldError(f"no selection '{selection}' in the mesh", code="not_found")
    return group.node_ids, mesh.node_coords(group.node_ids)


def mapped(project, root: Path, f, column: str, mesh, selection: str | None, use=None,
           tolerance: float | None = None) -> Mapped:  # fmt: skip
    """`f`'s `column` on `selection`'s nodes, through a feature's `use` when given (its
    scale, offset, placement, mapping and limits, as `fields.use_values` applies them),
    else as the field reads (linear, nothing past a limit). Extrapolated: past the use's
    max distance when it sets one, else past `tolerance` or the default tolerance."""
    from puzzle.fields import cloud, map_values, use_values

    ids, xyz = selection_nodes(mesh, selection)
    c = cloud(project, root, f, use)
    if column not in c.values:
        from puzzle.fields import FieldError

        raise FieldError(f"field '{f.name}' has no column '{column}'", code="not_found")
    if use is not None:
        values, dist = use_values(project, root, use, xyz)
        if tolerance is None and use.max_distance is not None:
            tolerance = use.max_distance.to_si()
    else:
        values, dist = map_values(c.points, c.values[column], xyz)
    if tolerance is None:
        tolerance = default_tolerance(c.points, xyz)
    point_values = c.values[column]
    if use is not None:
        point_values = use.scale * point_values  # the offset aside: a shade, not a value
    return Mapped(np.asarray(ids), np.asarray(values, float), np.asarray(dist, float),
                  float(tolerance), c.units.get(column, ""), f"{f.name}: {column}",
                  c.points, np.asarray(point_values, float))  # fmt: skip


# --- in words ------------------------------------------------------------------------------


def _length(value: float) -> str:
    """A length in the display unit ("2 mm", "0.002 m")."""
    from puzzle.model.units import pretty, shown
    from puzzle.settings import current

    v, unit = shown(float(value), "m", current().display_units())
    return f"{v:.4g} {pretty(unit)}"


def _point(xyz) -> str:
    from puzzle.model.units import pretty, shown
    from puzzle.settings import current

    units = current().display_units()
    parts = [shown(float(v), "m", units) for v in xyz]
    return "(" + ", ".join(f"{v:.4g}" for v, _ in parts) + f") {pretty(parts[0][1])}"


def placement_words(placement, project=None) -> str:
    """ "in Hub axes, turned 90° about z about (0, 0, 0) mm, then moved by (1, 0, 0) mm",
    or "as in the file"."""
    words = []
    if placement.axes is not None:
        cs = project.objects.get(placement.axes) if project is not None else None
        words.append(f"in {getattr(cs, 'name', 'a missing coordinate system')}'s axes")
    turns = [f"{a:g}° about {axis}" for a, axis in zip(placement.rotation, "xyz", strict=True)
             if a]  # fmt: skip
    if turns:
        about = f" about {_point(placement.about)}" if any(placement.about) else ""
        words.append("turned " + ", then ".join(turns) + about)
    if any(placement.translation):
        words.append(f"moved by {_point(placement.translation)}")
    return ", then ".join(words) if words else "as in the file"


def _quantity(q) -> str:
    from puzzle.model.units import pretty

    return f"{q.value:g} {pretty(q.unit)}".strip() if q.unit != "dimensionless" else f"{q.value:g}"


def scaling_words(use) -> str:
    """ "×1.2, then +10 K", or "as read"."""
    words = []
    if use.scale != 1:
        words.append(f"×{use.scale:g}")
    if use.offset is not None and use.offset.value:
        sign = "+" if use.offset.value > 0 else "−"
        q = use.offset.model_copy(update={"value": abs(use.offset.value)})
        words.append(f"{sign}{_quantity(q)}")
    return ", then ".join(words) if words else "as read"


def mapping_words(use) -> str:
    """ "linear between the points; past 2 mm, the nearest value"."""
    text = MAPPINGS.get(use.mapping, use.mapping)
    if use.max_distance is not None:
        limit = use.max_distance.to_si()
        text += f"; past {_length(limit)}, {BEYOND.get(use.beyond, use.beyond)}"
    return text


def use_words(use, project=None) -> str:
    """A use in one line: column, scaling, placement on top of the field's, mapping."""
    parts = [f"column {use.column}", scaling_words(use)]
    if use.placement.moved():
        parts.append(placement_words(use.placement, project))
    parts.append(mapping_words(use))
    return ", ".join(parts)


def feature_title(physics_type: str, feature) -> str:
    """ "Convection on P/Face", in the tree's words."""
    from puzzle.model.catalog import CATALOG

    spec = next((s for s in CATALOG.get(physics_type, []) if s.type == feature.type), None)
    name = spec.label if spec else feature.type.replace("_", " ").capitalize()
    return f"{name} on {feature.selection}"


def used_by(project, f) -> list[tuple[str, str]]:
    """Each use of the imported field `f`: (where: component, feature and value; how)."""
    from puzzle.model.schema import Component

    out = []
    for comp in project.of_type(Component):
        for ph in comp.physics:
            for feature in ph.features:
                for value, use in feature.fields.items():
                    if use.field != f.id:
                        continue
                    where = f"{comp.name}: {feature_title(ph.type, feature)} ({value})"
                    out.append((where, use_words(use, project)))
    return out


def solvers_taking(physics_type: str, feature, value: str) -> tuple[list[str], list[str]]:
    """The solvers that take this feature's value from a field, and those that solve the
    physics but refuse it (`Capabilities.field_features`), as solver keys."""
    from puzzle.adapters import available_adapters

    take, refuse = [], []
    for key, adapter in sorted(available_adapters().items()):
        caps = adapter.capabilities
        if physics_type not in caps.physics:
            continue
        if f"{feature.type}:{value}" in caps.field_features:
            take.append(key)
        else:
            refuse.append(key)
    return take, refuse


def _use_placement(use, project) -> str:
    if not use.placement.moved():
        return "as the field is"
    return placement_words(use.placement, project)


def chain(project, comp, physics_type: str, feature, value: str, solver_name=None
          ) -> list[tuple[str, str]]:  # fmt: skip
    """A field-fed value from the file to the solver: (step, words) rows. `solver_name`
    turns a solver key into its name (the GUI's labels)."""
    from puzzle.model.schema import ImportedField

    name = solver_name or (lambda k: k)
    use = feature.fields[value]
    f = project.objects.get(use.field)
    if not isinstance(f, ImportedField):
        return [("Imported field", "missing: the feature takes its value from a deleted field")]
    columns = ", ".join(f.value_columns()) or "no value columns"
    take, refuse = solvers_taking(physics_type, feature, value)
    solvers = ", ".join(name(k) for k in take) or "none yet"
    if refuse:
        solvers += f" (refused by {', '.join(name(k) for k in refuse)})"
    return [
        ("File", f"{f.file} ({f.format.upper()}, points in {f.length_unit})"),
        ("Imported field", f"{f.name}: {columns}; placed {placement_words(f.placement, project)}"),
        ("Column", use.column),
        ("Scaling", scaling_words(use)),
        ("Placed on the model", _use_placement(use, project)),
        ("Mapping", mapping_words(use)),
        ("Feature", f"{comp.name}: {feature_title(physics_type, feature)} ({value})"),
        ("Solvers", solvers),
    ]
