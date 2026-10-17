"""The built-in default materials library (ADR 0009).

`library.json` is generated from Materials_Hunt_List.xlsx by
`scripts/import_materials.py`: room-temperature properties, temperature tables
with a source on every point, and material info. `supplement.toml` adds fluids
the workbook doesn't cover.

Adding a library material to a project copies it into the project as a static
`Material`: later changes to the library never reach the project, and editing
the project's copy never touches the library.
"""

from __future__ import annotations

import json
import tomllib
from functools import cache
from importlib.resources import files

from puzzle.model.schema import Material, Project, PropertyTable, Quantity, TablePoint


@cache
def _library() -> dict[str, dict]:
    here = files(__package__)
    entries = json.loads(here.joinpath("library.json").read_text())["materials"]
    for key, entry in tomllib.loads(here.joinpath("supplement.toml").read_text()).items():
        name, description = entry.pop("name"), entry.pop("description", "")
        entries[key] = {
            "name": name,
            "description": description,
            "info": {"type": "Fluid"},
            "properties": entry,
            "tables": {},
            "sources": [],
        }
    return entries


def library_keys() -> list[str]:
    return sorted(_library())


def search_library(text: str) -> list[str]:
    """Keys whose name, group or specification contains `text` (case-insensitive)."""
    needle = text.lower()
    return [
        key
        for key, e in sorted(_library().items())
        if needle in " ".join([e["name"], *e["info"].values()]).lower()
    ]


def library_material(key: str) -> Material:
    """A fresh Material built from the library entry `key`."""
    try:
        e = _library()[key]
    except KeyError:
        raise KeyError(f"no material '{key}' in the built-in library") from None
    tables = {
        prop: PropertyTable(
            unit=t["unit"],
            reference_temperature=t.get("reference_temperature"),
            points=[TablePoint(temperature=k, value=v, source=s) for k, v, s in t["points"]],
        )
        for prop, t in e["tables"].items()
    }
    return Material(
        name=e["name"],
        description=e["description"],
        info=dict(e["info"]),
        properties={prop: Quantity(**q) for prop, q in e["properties"].items()},
        tables=tables,
        sources=list(e["sources"]),
    )


def add_library_material(project: Project, key: str, name: str | None = None) -> Material:
    """Copy a library material into the project and return the project's copy."""
    material = library_material(key)
    if name:
        material.name = name
    return project.add(material)


__all__ = ["add_library_material", "library_keys", "library_material", "search_library"]
