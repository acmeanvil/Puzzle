"""Schema migrations (ADR 0006): old project files brought up to this Puzzle's schema.

`SCHEMA_VERSION` (model/schema.py) is the shape of the files this build writes. A
project saved at an older version is read through `MIGRATIONS`, in order, on the raw
TOML data before any object is validated; it is written back at the new version on
its next save, and the session saves a version (ADR 0059) of the old files first. A
project saved at a newer version is refused with `newer_schema` before anything is
read, since this build cannot know what the newer fields mean.

A migration is `MIGRATIONS[n]`, taking version n to n + 1: a function of the header
(project.toml's table) and the objects, each `{"kind": ..., "data": ...}`, changed in
place (a renamed kind changes "kind"; a removed object is dropped from the list).
History entries and version blobs keep the shape they were written in; undo and
restore read them through the same functions (`upgrade_object`).

Bump SCHEMA_VERSION, and add a migration, when the stored shape changes: a field
renamed, removed or given another meaning, or a kind renamed. A new optional field
with a default needs a bump too if older builds must refuse such files plainly
rather than name the unknown field (`unknown_field`): the maintainer's call per
release.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from puzzle.model.schema import SCHEMA_VERSION

Migration = Callable[[dict[str, Any], list[dict[str, Any]]], None]
MIGRATIONS: dict[int, Migration] = {}  # none yet: the schema is still at version 1


def upgrade(header: dict[str, Any], objects: list[dict[str, Any]], found: int,
            to: int | None = None) -> int:  # fmt: skip
    """Run the migrations from `found` to `to` (this build's version) in place; the
    version reached."""
    to = SCHEMA_VERSION if to is None else to
    version = found
    while version < to:
        step = MIGRATIONS.get(version)
        if step is not None:
            step(header, objects)
        version += 1
    header["schema_version"] = version
    return version


def upgrade_object(kind: str, data: dict[str, Any], found: int) -> tuple[str, dict[str, Any]]:
    """One object's (kind, data) as this build reads it: an undo snapshot or a version's
    file written at schema `found`."""
    if found >= SCHEMA_VERSION:
        return kind, data
    objects = [{"kind": kind, "data": data}]
    upgrade({}, objects, found)
    if not objects:
        return kind, {}
    return objects[0]["kind"], objects[0]["data"]
