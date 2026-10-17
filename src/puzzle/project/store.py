"""Project directory format: one TOML file per object, so projects diff and merge in git.

my-project/
    project.toml
    geometry/<uuid>.toml
    material/<uuid>.toml
    component/<uuid>.toml
    study/<uuid>.toml
    ...
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import tomli_w

from puzzle.model.schema import OBJECT_TYPES, SCHEMA_VERSION, Project, ProjectObject

_BY_KIND: dict[str, type[ProjectObject]] = {cls.kind: cls for cls in OBJECT_TYPES}


def save_project(project: Project, root: Path) -> None:
    """Write the project to `root`, removing files for objects that no longer exist."""
    root.mkdir(parents=True, exist_ok=True)
    header = {"name": project.name, "schema_version": project.schema_version}
    (root / "project.toml").write_text(tomli_w.dumps(header))

    keep: set[Path] = set()
    for obj in project.objects.values():
        path = root / obj.kind / f"{obj.id}.toml"
        path.parent.mkdir(exist_ok=True)
        path.write_text(tomli_w.dumps(obj.model_dump(mode="json", exclude_none=True)))
        keep.add(path)

    for kind in _BY_KIND:
        for path in (root / kind).glob("*.toml"):
            if path not in keep:
                path.unlink()


def load_project(root: Path) -> Project:
    header = tomllib.loads((root / "project.toml").read_text())
    if header.get("schema_version", 0) > SCHEMA_VERSION:
        raise ValueError(
            f"project schema {header['schema_version']} is newer than this Puzzle "
            f"({SCHEMA_VERSION})"
        )
    project = Project(name=header["name"], schema_version=header["schema_version"])
    for kind, cls in _BY_KIND.items():
        for path in sorted((root / kind).glob("*.toml")):
            project.add(cls.model_validate(tomllib.loads(path.read_text())))
    return project
