"""Writing your materials (ADR 0020): the builder's store, not a project.

Your materials live in `custom_library_path()` (JSON, `{"materials": {key: entry}}`),
read after the shipped generics and your imported library, so an entry there with a
library key overrides that material: editing a generic saves your copy, and removing
your copy brings the library's back. Writes are atomic; each change is logged with what
was there before (`custom-history.jsonl`), so `undo` restores it.

Agents do not write here directly (ADR 0020: they may only propose library changes).
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from puzzle.errors import PuzzleError
from puzzle.model.schema import Material


def entry_of(material: Material, parent: str | None = None) -> dict:
    """A project or builder material as a library entry (what `library_material` reads)."""
    data = material.model_dump(mode="json")
    entry = {
        "name": data["name"],
        "description": data["description"],
        "info": data["info"],
        "properties": data["properties"],
        "tables": {
            k: {
                "unit": t["unit"],
                "reference_temperature": t["reference_temperature"],
                "points": [[p["temperature"], p["value"], p["source"]] for p in t["points"]],
            }
            for k, t in data["tables"].items()
        },
        "sources": data["sources"],
        "models": {k: v for k, v in data["models"].items() if v is not None},
        "solver_keywords": data["solver_keywords"],
    }
    if parent:
        entry["parent"] = parent
    return entry


def key_for(name: str) -> str:
    """A library key from a name: "Al 6061-T6" -> "al_6061_t6"."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "material"


class LibraryStore:
    def __init__(self, path: Path | None = None) -> None:
        from puzzle.materials import custom_library_path

        self.path = Path(path) if path else custom_library_path()
        self.history = self.path.with_name(self.path.stem + "-history.jsonl")

    def entries(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text()).get("materials", {})

    def save(self, key: str, entry: dict) -> None:
        """Add or replace your material `key` (checked by building it first)."""
        from puzzle.materials import reload_library

        self._check(key, entry)
        entry = {**entry, "modified": datetime.now(UTC).isoformat(timespec="seconds")}
        self._write(key, entry)
        reload_library()

    def remove(self, key: str) -> None:
        """Your material `key` removed (a library material by that key shows again)."""
        from puzzle.materials import reload_library

        if key not in self.entries():
            raise PuzzleError(f"no material '{key}' of yours", code="not_found")
        self._write(key, None)
        reload_library()

    def undo(self) -> str | None:
        """The last change taken back; returns the key it touched (None: nothing)."""
        from puzzle.materials import reload_library

        if not self.history.exists():
            return None
        lines = self.history.read_text().splitlines()
        if not lines:
            return None
        last = json.loads(lines[-1])
        entries = self.entries()
        if last["before"] is None:
            entries.pop(last["key"], None)
        else:
            entries[last["key"]] = last["before"]
        self._dump(entries)
        self.history.write_text("".join(line + "\n" for line in lines[:-1]))
        reload_library()
        return last["key"]

    # --- inside --------------------------------------------------------------------------

    def _check(self, key: str, entry: dict) -> None:
        """Build the material as the library would, so a bad entry never lands."""
        from puzzle.materials import _library, material_from_entry, resolve

        if not re.fullmatch(r"[a-z0-9_]+", key):
            raise PuzzleError("a material key is lower-case letters, digits and _", code="invalid")
        lib = {**_library(), **self.entries(), key: entry}
        try:
            material_from_entry(resolve(lib, key))
        except KeyError as exc:
            raise PuzzleError(str(exc).strip("'\""), code="not_found") from None
        except Exception as exc:  # noqa: BLE001 - a schema error from the entry
            raise PuzzleError(f"material '{key}' is not valid: {exc}", code="invalid") from None

    def _write(self, key: str, entry: dict | None) -> None:
        entries = self.entries()
        before = entries.get(key)
        if entry is None:
            entries.pop(key, None)
        else:
            entries[key] = entry
        self._dump(entries)
        self.history.parent.mkdir(parents=True, exist_ok=True)
        with self.history.open("a") as f:
            f.write(json.dumps({"key": key, "before": before}) + "\n")

    def _dump(self, entries: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"materials": entries}, indent=1, ensure_ascii=False))
        tmp.replace(self.path)  # never a half-written file
