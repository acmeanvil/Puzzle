"""The built-in default materials library (ADR 0009).

Adding a library material to a project copies it into the project as a static
`Material`: later changes to the library never reach the project, and editing
the project's copy never touches the library.
"""

from __future__ import annotations

import tomllib
from functools import cache
from importlib.resources import files

from puzzle.model.schema import Material, Project, Quantity


@cache
def _library() -> dict[str, dict]:
    return tomllib.loads(files(__package__).joinpath("library.toml").read_text())


def library_keys() -> list[str]:
    return sorted(_library())


def library_material(key: str) -> Material:
    """A fresh Material built from the library entry `key`."""
    try:
        entry = dict(_library()[key])
    except KeyError:
        raise KeyError(f"no material '{key}' in the built-in library") from None
    name = entry.pop("name")
    description = entry.pop("description", "")
    properties = {prop: Quantity(**value) for prop, value in entry.items()}
    return Material(name=name, description=description, properties=properties)


def add_library_material(project: Project, key: str, name: str | None = None) -> Material:
    """Copy a library material into the project and return the project's copy."""
    material = library_material(key)
    if name:
        material.name = name
    return project.add(material)


__all__ = ["add_library_material", "library_keys", "library_material"]
