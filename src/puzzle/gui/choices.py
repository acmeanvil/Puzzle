"""What a property may be set to, with names people read (the properties panel).

`choices(project, obj, path)` gives [(label, value), ...] for the field at a dotted
path of an object, or None for free text. Choices come from, in order:

- references to other objects (a part's geometry, a component's mesh, a study's
  component, a transfer's studies): the objects of that kind, by name;
- selections ("Bar/Tip"): the component's own and its parts' selections;
- the solver of a study or a shoot-out: the installed adapters;
- the physics a solve step solves: the component's physics;
- a feature's type: its physics' catalog (labels such as "Fixed support");
- settings with a fixed set of values (time_scheme, turbulence_model);
- a geometry step's axis, mode, plane or pattern feature;
- any field the schema limits to a set of values (Literal).

Values are stored as they are (ids, keys); only the labels are for people.
"""

from __future__ import annotations

import types
import typing
from typing import Any, Literal, Union

from pydantic import BaseModel

from puzzle.model.schema import (
    Component,
    Geometry,
    Material,
    MeshRecipe,
    Project,
    Study,
    Transfer,
)

# Field name -> the kind of object it names (by id).
REFERENCES = {
    "geometry": Geometry,
    "material": Material,
    "mesh": MeshRecipe,
    "component": Component,
    "owner": Component,
}
NONE = "(none)"
# Words kept as written when a value is turned into a label.
WORDS = {"bdf2": "BDF2", "sst": "SST", "mcp": "MCP", "3d": "3D", "2d": "2D", "dg": "DG"}
SETTINGS = {
    "time_scheme": ("backward_euler", "crank_nicolson", "bdf2", "theta"),
    "turbulence_model": ("laminar", "k-epsilon", "k-omega-SST"),
}
STEP_CHOICES = {
    "axis": ["x", "y", "z"],
    "mode": ["add", "cut", "intersect", "new"],
    "plane": ["xy", "yz", "xz"],
}


def label(value: Any) -> str:
    """A key as words: "solid_mechanics" -> "Solid mechanics", "bdf2" -> "BDF2"."""
    if value is None:
        return NONE
    if not isinstance(value, str):
        return str(value)
    words = value.replace("_", " ").split(" ")
    out = [WORDS.get(w.lower(), w) for w in words]
    text = " ".join(out)
    return text[:1].upper() + text[1:] if text and text[:1].islower() else text


def choices(project: Project, obj, path: str) -> list[tuple[str, Any]] | None:
    """The values the field at `path` of `obj` may take, labelled; None for free text."""
    keys = [k for k in path.split(".") if k]
    if not keys:
        return None
    owner, field, node, trail = obj, None, obj, []
    for key in keys[:-1]:
        if isinstance(node, BaseModel):
            owner, field = node, key
            node = getattr(node, key)
        elif isinstance(node, list):
            node = node[int(key)]
        elif isinstance(node, dict):
            node = node[key]
        else:
            return None
        trail.append(key)
    last = keys[-1]
    if isinstance(node, BaseModel):
        owner, field, in_list = node, last, False
    elif isinstance(node, list):
        in_list = True  # an item of the list field `field`
    elif isinstance(node, dict):
        return _dict_entry(project, obj, owner, field, last, keys)
    else:
        return None
    return _field(project, obj, owner, field, in_list)


def _field(project, obj, owner, field, in_list):
    component = _component(project, obj)
    # References by id, labelled by name.
    kind = REFERENCES.get(field)
    if isinstance(owner, Transfer) and field in ("source", "target"):
        kind = Study
    if kind is not None and not in_list:
        found = sorted(project.of_type(kind), key=lambda o: o.name)
        out = [(o.name, str(o.id)) for o in found]
        return ([(NONE, None)] + out) if _optional(owner, field) else out
    if field in ("selection", "target_selection") or (
        field in ("source", "target") and type(owner).__name__ == "Contact"
    ):
        comp = component
        if isinstance(owner, Transfer):
            target = project.objects.get(owner.target)
            comp = project.objects.get(target.component) if isinstance(target, Study) else None
        names = _selections(project, comp)
        if field == "target_selection":
            return [(NONE, None)] + [(n, n) for n in names]
        return [(n, n) for n in names] if names else None
    if field == "solver" or (field == "solvers" and in_list):
        from puzzle.adapters import available_adapters

        return [(solver_label(k), k) for k in sorted(available_adapters())]
    if field == "physics" and in_list and component is not None:
        kinds = dict.fromkeys(p.type for p in component.physics)
        return [(label(k), k) for k in kinds]
    if field == "type" and type(owner).__name__ == "Feature":
        from puzzle.model.catalog import CATALOG

        physics = _physics_of_feature(obj, owner)
        specs = CATALOG.get(physics, [])
        return [(s.label, s.type) for s in specs] or None
    return _literal(owner, field, in_list)


def _dict_entry(project, obj, owner, field, key, keys):
    """A value in a dict field: physics or step settings, a geometry step's args."""
    if field == "settings" and key in SETTINGS:
        return [(label(v), v) for v in SETTINGS[key]]
    if field == "args" and isinstance(obj, Geometry):
        if key in STEP_CHOICES:
            step = obj.history[int(keys[1])]
            from puzzle.geometry.steps import STEPS

            kinds = {f.name: f.kind for f in STEPS.get(step.kind, [])}
            if kinds.get(key) == "uv":
                return [("u", "u"), ("v", "v")]
            return [(v, v) for v in STEP_CHOICES[key]]
        if key == "feature":
            index = int(keys[1])
            tools = [s.name for s in obj.history[:index] if s.kind in _TOOLS]
            return [(n, n) for n in tools] or None
    return None


_TOOLS = {"box", "cylinder", "cone", "sphere", "extrude", "revolve", "hole"}


def _literal(owner, field, in_list):
    info = type(owner).model_fields.get(field)
    if info is None:
        return None
    annotation = info.annotation
    if in_list and typing.get_origin(annotation) in (list, tuple):
        annotation = typing.get_args(annotation)[0]
    values, optional = _literal_values(annotation)
    if not values:
        return None
    out = [(label(v), v) for v in values]
    return [(NONE if field != "meshing" else "Automatic", None), *out] if optional else out


def _literal_values(annotation) -> tuple[list, bool]:
    origin = typing.get_origin(annotation)
    if origin is Literal:
        return list(typing.get_args(annotation)), False
    if origin in (Union, types.UnionType):
        values, optional = [], False
        for a in typing.get_args(annotation):
            if a is type(None):
                optional = True
            else:
                v, _ = _literal_values(a)
                values += v
        return values, optional
    return [], False


def _optional(owner, field) -> bool:
    info = type(owner).model_fields.get(field)
    return info is not None and type(None) in typing.get_args(info.annotation)


def _component(project, obj):
    if isinstance(obj, Component):
        return obj
    if isinstance(obj, Study):
        return project.objects.get(obj.component)
    return None


def _selections(project, comp) -> list[str]:
    if comp is None:
        return []
    names = []
    for part in comp.parts:
        geo = project.objects.get(part.geometry)
        if isinstance(geo, Geometry):
            names += [f"{part.name}/{s.name}" for s in geo.selections]
    return names + [s.name for s in comp.selections]


def _physics_of_feature(obj, feature) -> str | None:
    if isinstance(obj, Component):
        for p in obj.physics:
            if any(f is feature for f in p.features):
                return p.type
    return None


SOLVERS = {
    "calculix": "CalculiX",
    "code_aster": "Code_Aster",
    "code_saturne": "Code_Saturne",
    "elmer": "Elmer",
    "fenicsx": "FEniCSx",
    "openfoam": "OpenFOAM",
}


def solver_label(key: str) -> str:
    return SOLVERS.get(key, label(key))
