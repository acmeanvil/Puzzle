"""References between objects as wires that can be drawn and removed (ADR 0071 decision 8).

The tree and the schematic are equal ways to build a project, so every reference the
tree's Properties can set (a part's material, a component's mesh recipe, a study's
component, a load's coordinate system, a result tool's study ...) can be made by drawing
a wire, or dropping one node on another, and cleared by removing the wire.

`LINKS` lists them: the kind that holds the reference, where (a dotted path with `[]` for
each list on the way), the kinds that can be wired in, and a readable name. `choices`
says what a wire from one object to another can mean (several when the target holds a
list: which part, which load); `commands` turns a choice into the Edit command the tree
would make; `existing` finds the references a drawn wire stands for, to remove them.
Nothing here imports Qt: the GUI and MCP share it.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class Link:
    target: str  # the kind holding the reference
    path: str  # where: "mesh", "materials[].material", "physics[].features[].axes"
    sources: tuple[str, ...]  # the kinds that can be wired in
    name: str  # what it is, read by the user: "Mesh recipe"
    required: bool = False  # a reference the object can't be without: replaced, not cleared
    types: tuple[str, ...] = ()  # an operator's: only these types of it


LINKS: tuple[Link, ...] = (
    Link("component", "mesh", ("mesh",), "Mesh recipe", required=True),
    Link("component", "parts[].geometry", ("geometry",), "Geometry of a part", required=True),
    Link("component", "materials[].material", ("material",), "Material", required=True),
    Link("component", "materials[].axes", ("axes",), "Material directions"),
    Link("component", "physics[].features[].axes", ("axes",), "Coordinate system"),
    Link("component", "physics[].features[].amplitude", ("amplitude",), "Amplitude"),
    Link("component", "beam_sections[].axes", ("axes",), "Beam orientation"),
    Link("component", "connectors[].type", ("bolt_type",), "Bolt type"),
    Link("component", "connectors[].material", ("material",), "Bolt material"),
    Link("component", "bolt_rules[].type", ("bolt_type",), "Bolt type of a rule"),
    Link("component", "contacts[].thread.cs", ("axes",), "Thread axis"),  # ADR 0076
    Link("component", "connectors[].thread.cs", ("axes",), "Thread axis of a bolt"),
    Link("study", "component", ("component",), "Component", required=True),
    Link(
        "study",
        "convergence.quantities[].source",
        ("operator", "probe"),
        "Quantity mesh convergence judges",
    ),
    Link(
        "study",
        "adapt.quantities[].source",
        ("operator", "probe"),
        "Quantity adaptive refinement follows",
    ),
    Link("mesh", "controls[].axes", ("axes",), "Coordinate system of a control"),
    Link("mesh", "controls[].table", ("operator",), "Size table of a control"),
    Link("probe", "source", ("study", "result"), "Results it reads", required=True),
    Link("probe", "construction", ("construction",), "Path or plane"),
    Link("result", "source", ("study", "result"), "Results it reads"),
    Link("result", "cases[].study", ("study",), "Study of a case", required=True),
    # An equation's values from operators and result tools (B, C...: EquationInput).
    Link("result", "inputs[].source", ("operator", "probe"), "Value", required=True),
    Link("optimisation", "study", ("study",), "Study it runs", required=True),
    Link("mesh_study", "study", ("study",), "Study it refines", required=True),
    Link("construction", "axes", ("axes",), "Coordinate system"),
    Link("axes", "relative_to", ("axes",), "Relative to"),
    Link("bolt_type", "material", ("material",), "Grade material"),
    Link("field", "source", ("operator",), "Table it reads"),
    Link("operator", "target", ("component", "geometry"), "What it measures", types=("measure",)),
    Link("operator", "target", ("construction",), "Plane it cuts on", types=("slice", "clip")),
    Link("operator", "target", ("construction",), "Line its seeds lie on", types=("stream",)),
    Link(
        "operator",
        "target",
        (
            "material",
            "component",
            "mesh",
            "parameters",
            "study",
            "geometry",
            "axes",
            "amplitude",
            "bolt_type",
            "construction",
        ),
        "What it reads",
        types=("model_value",),
    ),
    Link("operator", "axes", ("axes",), "Coordinate system"),
)


@dataclass(frozen=True)
class Choice:
    """One thing a wire can mean: set `path` (or append to it) on the target."""

    link: Link
    path: str  # a concrete dotted path: "materials.2.material"; for an append, the list
    label: str  # "Material of Bracket", "Coordinate system of Pressure on Inner"
    op: str = "set"  # "set"; "append" (a new list item, `value` its fields); "add_part"
    # (the tree's Add part, which asks the part's name and placement)
    value: object = None


def _item_label(kind: str, item) -> str:
    """How a list item is named to the user: a part by name, a material assignment by
    what it covers, a load by its type and where."""
    get = item.get if isinstance(item, dict) else lambda k, d=None: getattr(item, k, d)
    for key in ("name", "selection"):
        if get(key):
            if kind == "features":
                return f"{_words(get('type', ''))} on {get('selection', '')}".strip()
            return str(get(key))
    return _words(str(get("type", ""))) or "an item"


def _words(text: str) -> str:
    return text.replace("_", " ").capitalize()


def _items(data, parts: list[str], prefix: str = "", label: str = ""):
    """(dotted path, label) of every place `parts` (a LINKS path split on '.') reaches in
    `data` (an object's JSON form)."""
    head, rest = parts[0], parts[1:]
    if head.endswith("[]"):
        name = head[:-2]
        for i, item in enumerate(data.get(name) or []):
            here = f"{prefix}{name}.{i}"
            # a component's physics is no part of a load's name: "Pressure on Inner"
            word = "" if name == "physics" else _item_label(name, item)
            joined = ", ".join(w for w in (label, word) if w)
            yield from _items(item, rest, here + ".", joined)
        return
    if rest:
        if isinstance(data.get(head), dict):
            yield from _items(data[head], rest, f"{prefix}{head}.", label)
        return
    yield f"{prefix}{head}", label


def choices(project, source, target, within: str = "") -> list[Choice]:
    """What a wire from `source` to `target` (project objects) can set, in the tree's
    words; empty when it fits nowhere. `within` (a feature's path in its component, as
    "physics.0.features.1") keeps to that feature: a wire onto a load's own node."""
    if within:
        return _feature_choices(project, source, target, within)
    found: list[Choice] = []
    data = target.model_dump(mode="json")
    if source.kind == "geometry" and target.kind == "component":  # mostly: a new part
        link = next(k for k in LINKS if k.path == "parts[].geometry")
        found.append(Choice(link, "parts", f"New part from {source.name}", "add_part"))
    for link in LINKS:
        if link.target != target.kind or source.kind not in link.sources:
            continue
        if link.types and getattr(target, "type", None) not in link.types:
            continue
        for path, where in _items(data, link.path.split(".")):
            label = f"{link.name} of {where}" if where else link.name
            found.append(Choice(link, path, label))
        if link.path == "inputs[].source" and getattr(target, "method", "") == "equation":
            used = {i.name for i in target.inputs}  # a new value, by the next free letter
            name = next(c for c in "BCDEFGHIJKLMNOPQRSTUVWXYZ" if c not in used)
            value = {"name": name, "source": str(source.id), "port": "out"}
            found.append(Choice(link, "inputs", f"Value {name} of the equation (new)",
                                "append", value))  # fmt: skip
        if link.path == "materials[].material":  # a part without a material yet
            covered = {a.selection for a in target.materials}
            for part in target.parts:
                if part.name not in covered:
                    found.append(Choice(link, "materials", f"Material of {part.name} (new)",
                                        "append", {"material": str(source.id),
                                                   "selection": part.name}))  # fmt: skip
    return found


def _feature(target, within: str):
    i, j = int(within.split(".")[1]), int(within.split(".")[3])
    return target.physics[i].features[j]


def _feature_choices(project, source, target, within: str) -> list[Choice]:
    """A wire onto one load or support: its coordinate system, amplitude, or (a
    geometry) the selection it acts on."""
    found = [c for c in choices(project, source, target) if c.path.startswith(within + ".")]
    found += _value_choices(project, source, _feature(target, within), within)
    if source.kind == "geometry":  # where it acts: one of the geometry's selections
        link = Link("component", "physics[].features[].selection", ("geometry",), "Where")
        for part in (p for p in target.parts if p.geometry == source.id):
            for sel in source.selections:
                name = f"{part.name}/{sel.name}"
                found.append(Choice(link, f"{within}.selection", f"Acts on {name}", "set", name))
    return found


VALUE_LINK = Link("component", "physics[].features[].values", ("operator", "parameters"), "Value")


def _parameter_names(project, source) -> list[str]:
    """The parameters `source` gives the model by name: a parameter set's values, a
    pre-solve Number, Slider or Expression's own name (ADR 0062)."""
    if source.kind == "parameters":
        return list(source.values)
    if source.kind == "operator":
        from puzzle.operators import first_type, pre_solve

        if any(o.id == source.id for o in pre_solve(project)) and (
            first_type(source, project) == "number"
        ):
            return [source.name]
    return []


def _value_choices(project, source, feature, within: str) -> list[Choice]:
    """A number wired onto a load's node (ADR 0071): each of its values made to follow
    that parameter ("Sets p, following t")."""
    found = []
    for name in _parameter_names(project, source):
        for key, q in feature.values.items():
            value = {"value": q.value, "unit": q.unit, "parameter": name}
            found.append(Choice(VALUE_LINK, f"{within}.values.{key}",
                                f"Sets {key}, following {name}", "set", value))  # fmt: skip
    return found


def why_not(project, source, target) -> str:
    """Why a wire from `source` to `target` makes nothing: what the target takes."""
    from puzzle.schematic import KIND_WORDS  # the names the nodes show

    def name(kind: str) -> str:
        return KIND_WORDS.get(kind, _words(kind))

    takes = sorted({name(k) for link in LINKS if link.target == target.kind
                    for k in link.sources})  # fmt: skip
    if not takes:
        return f"Nothing can be wired into {target.name}"
    return f"{target.name} takes {', '.join(takes)}, not {name(source.kind)}"


def commands(choice: Choice, source, target) -> list[dict]:
    """The Edit command a choice makes: what the tree's Properties would."""
    if choice.op == "append":
        op = {"op": "append", "path": choice.path, "value": choice.value}
        return [{"command": "edit", "object": str(target.id), "ops": [op]}]
    value = choice.value if choice.value is not None else str(source.id)  # a name, or the id
    return [{"command": "edit", "object": str(target.id),
             "ops": [{"op": "set", "path": choice.path, "value": value}]}]  # fmt: skip


def existing(project, source_id, target, within: str = "") -> list[Choice]:
    """The references in `target` to `source_id`: what a drawn wire between them stands
    for, to remove (a required one can only be replaced, never cleared); `within` keeps
    to one feature's."""
    if within:
        found = [c for c in existing(project, source_id, target)
                 if c.path.startswith(within + ".")]  # fmt: skip
        source = project.objects.get(source_id)
        names = set(_parameter_names(project, source)) if source is not None else set()
        for key, q in _feature(target, within).values.items():
            if q.parameter and q.parameter in names:  # a value bound to it: unbound
                found.append(Choice(VALUE_LINK, f"{within}.values.{key}",
                                    f"{key} follows {q.parameter}", "unbind",
                                    {"value": q.value, "unit": q.unit}))  # fmt: skip
        return found
    found: list[Choice] = []
    data = target.model_dump(mode="json")
    for link in LINKS:
        if link.target != target.kind:
            continue
        for path, where in _items(data, link.path.split(".")):
            value = data
            for part in path.split("."):
                value = value[int(part)] if isinstance(value, list) else (value or {}).get(part)
            if value == str(source_id):
                label = f"{link.name} of {where}" if where else link.name
                found.append(Choice(link, path, label))
    return found


def clear_commands(choice: Choice, target) -> list[dict]:
    """Removing a wire: an optional reference cleared; a required one in a list item (a
    material assignment) removes that item; a required single one can't be removed."""
    if choice.op == "unbind":  # the value stays as it is now, following nothing
        return [{"command": "edit", "object": str(target.id),
                 "ops": [{"op": "set", "path": choice.path, "value": choice.value}]}]  # fmt: skip
    if not choice.link.required:
        return [{"command": "edit", "object": str(target.id),
                 "ops": [{"op": "set", "path": choice.path, "value": None}]}]  # fmt: skip
    if "." in choice.path and choice.link.path.count("[]") == 1:
        item = choice.path.rsplit(".", 1)[0]  # "materials.2"
        return [{"command": "edit", "object": str(target.id),
                 "ops": [{"op": "remove", "path": item}]}]  # fmt: skip
    return []


def source_kinds() -> set[str]:
    """Every kind that can be wired into something."""
    return {k for link in LINKS for k in link.sources}


def parse(object_id) -> UUID:
    return object_id if isinstance(object_id, UUID) else UUID(str(object_id))
