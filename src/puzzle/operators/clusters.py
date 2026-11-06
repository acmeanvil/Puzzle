"""Clusters and recipes (ADR 0064 item 9, phase 4): a chain of operators kept as a
template, with named input ports, and put into a project as copies, wired to what the
user picks.

A template is JSON: its name, help, `ports` (each an input from outside: a name, a type,
and the operator inputs it feeds) and `operators` (each a key, a type, its settings,
and inputs wired by key to the template's other operators, or typed values).

- A **recipe** ships with Puzzle (`RECIPES`): the 20 worst nodes labelled in the view,
  peak stress per part as a bar chart, two solvers' difference by node, a stress
  margin, reactions per part in kN, a mass from the geometry.
- A **cluster** is the user's own: operators picked in the schematic, saved with
  `save` to the user's library (`<user data dir>/clusters/<name>.json`), and so
  available in every project. Putting it in copies it (new ids), never links it, so a
  project stays self-contained.

`commands` gives the Create commands that put one in (one batch, one undo): each
operator named after the template ("Worst nodes: Top N"), its ports' inputs wired to
the given sources, and a tree group holding them, folded together in the schematic.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from puzzle.errors import PuzzleError


def _op(key, type_, inputs=None, **settings) -> dict:
    return {"key": key, "type": type_, "settings": settings, "inputs": inputs or {}}


def _from(key: str, port: str = "out") -> dict:
    return {"from": key, "port": port}


RESULT = {"name": "result", "type": "result", "label": "Result"}
RECIPES: dict[str, dict] = {
    "worst_nodes": {
        "name": "Worst nodes", "help": "The 20 highest von Mises nodes, labelled in the "
        "3D view and exported to Excel.",
        "ports": [{**RESULT, "into": [["table", "result"]]}],
        "operators": [
            _op("table", "result_table", quantities=["stress:von_mises"]),
            _op("top", "top_n", {"table": _from("table"),
                                 "count": {"value": {"value": 20, "unit": "dimensionless"}}},
                column="von_mises"),
            _op("show", "preview", {"table": _from("top")}, column="von_mises"),
            _op("save", "export", {"value": _from("top")}, file="exports/worst_nodes.xlsx"),
        ],
    },
    "peak_per_part": {
        "name": "Peak per part", "help": "The peak von Mises stress of each part, in MPa, "
        "as a bar chart.",
        "ports": [{**RESULT, "into": [["table", "result"]]}],
        "operators": [
            _op("table", "result_table", quantities=["stress:von_mises"]),
            _op("peaks", "group", {"table": _from("table")}, by=["part"],
                aggregations=["max(von_mises) as peak"]),
            _op("mpa", "convert_units", {"table": _from("peaks")}, column="peak", unit="MPa"),
            _op("chart", "plot", {"value": _from("mpa")}, by=["part"], column="peak",
                chart="bar"),
        ],
    },
    "solver_difference": {
        "name": "Solver difference", "help": "Two runs' total deformation joined by node, "
        "their difference and the 10 nodes where they differ most.",
        "ports": [{**RESULT, "into": [["a", "result"]]},
                  {"name": "other", "type": "result", "label": "Second result",
                   "into": [["b", "result"]]}],
        "operators": [
            _op("a", "result_table", quantities=["displacement:total"]),
            _op("b", "result_table", quantities=["displacement:total"]),
            _op("both", "join", {"table": _from("a"), "other": _from("b")}, by=["node"],
                suffix="_2"),
            _op("diff", "new_column", {"table": _from("both")}, new_name="difference",
                expression="abs(deformation_2 - deformation)"),
            _op("most", "top_n", {"table": _from("diff")}, column="difference"),
        ],
    },
    "stress_margin": {
        "name": "Stress margin", "help": "The peak von Mises stress against an allowable "
        "(250 MPa: change it on the check): a verdict with its margin.",
        "ports": [{**RESULT, "into": [["peak", "result"]]}],
        "operators": [
            _op("peak", "reduce", quantity="stress:von_mises", reduction="max"),
            _op("check", "check", {"value": _from("peak"),
                                   "limit": {"value": {"value": 250, "unit": "MPa"}}}),
        ],
    },
    "reactions_kn": {
        "name": "Reactions in kN", "help": "The reaction force summed per part and step, "
        "in kN.",
        "ports": [{**RESULT, "into": [["table", "result"]]}],
        "operators": [
            _op("table", "result_table", quantities=["reaction_force:total"], steps="all"),
            _op("sum", "group", {"table": _from("table")}, by=["part", "step"],
                aggregations=["sum(reaction_force) as reaction"]),
            _op("kn", "convert_units", {"table": _from("sum")}, column="reaction", unit="kN"),
        ],
    },
    "mass_from_geometry": {
        "name": "Mass from geometry", "help": "A part's mass from its exact geometry and "
        "material, with no solve: pick the component and part on the Measure.",
        "ports": [],
        "operators": [
            _op("mass", "measure", measurement="mass"),
            _op("show", "panel", {"value": _from("mass")}),
        ],
    },
}  # fmt: skip


def library_dir() -> Path:
    from puzzle.materials import user_data_dir

    return user_data_dir() / "clusters"


def _slug(name: str) -> str:
    return re.sub(r"[^\w-]+", "_", name.strip()).strip("_").lower() or "cluster"


def templates() -> dict[str, dict]:
    """Every template by key: the recipes, then the user's clusters (`mine:<slug>`)."""
    out = dict(RECIPES)
    folder = library_dir()
    if folder.exists():
        for path in sorted(folder.glob("*.json")):
            try:
                out[f"mine:{path.stem}"] = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                continue  # a damaged file is left out, never fatal
    return out


def save(project, operator_ids: list[UUID], name: str, help_: str = "") -> Path:
    """The operators as the user's cluster: their settings and the wires among them;
    every input wired from outside the set becomes a port, named after the input."""
    from puzzle.model.schema import Operator
    from puzzle.operators import TYPES

    picked = [project.objects[i] for i in operator_ids]
    if not picked or not all(isinstance(o, Operator) for o in picked):
        raise PuzzleError("a cluster is made of operators", code="invalid")
    keys = {o.id: f"op{i}" for i, o in enumerate(picked, 1)}
    ports: dict[str, dict] = {}
    operators = []
    for o in picked:
        data = o.model_dump(mode="json", exclude={"id", "name", "type", "inputs", "tags"})
        inputs = {}
        for k, inp in o.inputs.items():
            if inp.source is not None and inp.source in keys:
                inputs[k] = _from(keys[inp.source], inp.port)
            elif inp.source is not None:  # from outside: a port
                port = k if k not in ports else f"{k}_{len(ports) + 1}"
                spec = TYPES[o.type].input(k)
                ports[port] = {"name": port, "type": spec.types[0] if spec else "number",
                               "label": spec.label if spec else port,
                               "into": [[keys[o.id], k]]}  # fmt: skip
            elif inp.value is not None:
                inputs[k] = {"value": inp.value.model_dump(mode="json")}
        defaults = {k: f.get_default(call_default_factory=True)
                    for k, f in Operator.model_fields.items() if not f.is_required()}  # fmt: skip
        settings = {k: v for k, v in data.items() if k in defaults and v != defaults[k]}
        operators.append({"key": keys[o.id], "type": o.type, "settings": settings,
                          "inputs": inputs, "name": o.name})  # fmt: skip
    template = {"name": name, "help": help_ or f"{len(picked)} operators",
                "ports": list(ports.values()), "operators": operators}  # fmt: skip
    folder = library_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{_slug(name)}.json"
    path.write_text(json.dumps(template, indent=1), encoding="utf-8")
    return path


def commands(project, key: str, bindings: dict[str, tuple[str, str]] | None = None,
             name: str | None = None) -> list[dict]:  # fmt: skip
    """The Create commands that put template `key` into the project, its ports wired
    to `bindings` (port name -> (source id, port)); one batch, so one undo."""
    from puzzle.operators import TYPES, default_name

    found = templates().get(key)
    if found is None:
        raise PuzzleError(f"no recipe or cluster '{key}'", code="not_found",
                          details={"known": sorted(templates())})  # fmt: skip
    title = name or found["name"]
    ids = {op["key"]: str(uuid4()) for op in found["operators"]}
    wired: dict[str, dict[str, dict]] = {}
    for port in found.get("ports", []):
        source = (bindings or {}).get(port["name"])
        if source is None:
            continue
        for target, input_key in port["into"]:
            wired.setdefault(target, {})[input_key] = {"source": source[0], "port": source[1]}
    out, taken = [], set()
    for op in found["operators"]:
        if op["type"] not in TYPES:
            raise PuzzleError(f"'{title}': no operator type '{op['type']}'", code="invalid")
        inputs = {}
        for k, v in op.get("inputs", {}).items():
            if "from" in v:
                inputs[k] = {"source": ids[v["from"]], "port": v.get("port", "out")}
            else:
                inputs[k] = v
        inputs.update(wired.get(op["key"], {}))
        label = f"{title}: {op.get('name') or TYPES[op['type']].label}"
        while label in taken or any(o.name == label for o in project.objects.values()):
            label = default_name(project, op["type"]) if label in taken else f"{label} 2"
        taken.add(label)
        out.append({"command": "create", "kind": "operator",
                    "data": {"id": ids[op["key"]], "name": label, "type": op["type"],
                             "inputs": inputs, **op.get("settings", {})}})  # fmt: skip
    out.append({"command": "create", "kind": "group",
                "data": {"name": title, "section": "Operators",
                         "members": list(ids.values())}})  # fmt: skip
    return out


def summary() -> list[dict[str, Any]]:
    """Every template's key, name, help and ports (MCP, the menus)."""
    return [{"key": k, "name": t["name"], "help": t.get("help", ""),
             "ports": [{"name": p["name"], "type": p["type"]} for p in t.get("ports", [])],
             "mine": k.startswith("mine:")} for k, t in templates().items()]  # fmt: skip
