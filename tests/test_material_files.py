"""Material library files and removals: a library exported from one install loads in
another, an import is checked and undone in one step, and a library material removed
stays removed when the library is imported again (Materials → Export/Import material
library…, the builder's Remove from library and Find duplicates…)."""

import json

import pytest

from puzzle.errors import PuzzleError
from puzzle.materials import (
    _library,
    custom_library_path,
    duplicates,
    exchange,
    library_material,
    reload_library,
    user_library_path,
)
from puzzle.materials.store import LibraryStore, entry_of


@pytest.fixture
def imported(monkeypatch):
    """An imported library in the user folder that the library reads (tests otherwise
    read the generics only): steel twice under two keys, and a variant of one."""
    path = user_library_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    steel = entry_of(library_material("structural_steel"))
    entries = {
        "s355": {**steel, "name": "S355"},
        "s355_copy": {**steel, "name": "S355 (copy)"},
        "s355_hot": {
            "name": "S355 hot",
            "parent": "s355",
            "properties": {"density": {"value": 7800, "unit": "kg/m^3"}},
        },  # fmt: skip
    }
    path.write_text(json.dumps({"materials": entries}), encoding="utf-8")
    monkeypatch.setenv("PUZZLE_MATERIALS", str(path))
    reload_library()
    yield path
    path.unlink(missing_ok=True)
    path.with_name(path.name + ".bak").unlink(missing_ok=True)
    reload_library()


def mine(name: str, e: float = 100e9) -> dict:
    entry = entry_of(library_material("structural_steel"))
    entry["name"] = name
    entry["properties"]["youngs_modulus"] = {"value": e, "unit": "Pa"}
    return entry


def test_an_exported_library_imports_elsewhere(imported, tmp_path):
    LibraryStore().save("my_steel", mine("My steel"))
    out = tmp_path / "materials.json"
    assert exchange.export(out, "yours") == 1
    assert exchange.export(out, "user") == 4  # three imported, one of yours
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["format"] == exchange.FORMAT and data["scope"] == "user"
    assert exchange.export(out, "all") == len(_library())

    # A variant exported on its own takes its family along.
    store = LibraryStore()
    store.save("hot_mine", {"name": "Hot", "parent": "s355", "properties": {}})
    exchange.export(out, "yours")
    assert set(json.loads(out.read_text(encoding="utf-8"))["materials"]) == {
        "my_steel", "hot_mine", "s355"}  # fmt: skip

    # "Another install": nothing of yours; the file goes into your materials.
    custom_library_path().unlink()
    reload_library()
    done = exchange.import_file(out)
    assert done.kept == ["s355"] and sorted(done.added) == ["hot_mine", "my_steel"]
    assert library_material("my_steel").properties["youngs_modulus"].value == 100e9
    assert LibraryStore().undo() == "hot_mine, my_steel"  # one undo takes the import back
    assert "my_steel" not in _library()


def test_an_import_skips_what_is_not_valid_and_replaces_only_when_asked(imported, tmp_path):
    LibraryStore().save("my_steel", mine("My steel"))
    out = tmp_path / "in.json"
    out.write_text(json.dumps({"materials": {
        "my_steel": mine("My steel", 50e9),
        "broken": {"name": "Broken", "properties": {"youngs_modulus": {"value": "x"}}},
        "orphan": {"name": "Orphan", "parent": "nowhere", "properties": {}},
    }}), encoding="utf-8")  # fmt: skip
    done = exchange.import_file(out)
    assert done.kept == ["my_steel"] and set(done.invalid) == {"broken", "orphan"}
    assert library_material("my_steel").properties["youngs_modulus"].value == 100e9
    done = exchange.import_file(out, conflicts="replace")
    assert done.replaced == ["my_steel"]
    assert library_material("my_steel").properties["youngs_modulus"].value == 50e9

    not_a_library = tmp_path / "other.json"
    not_a_library.write_text('{"format": "something else", "materials": {}}', encoding="utf-8")
    with pytest.raises(PuzzleError, match="not a material library"):
        exchange.import_file(not_a_library)


def test_a_file_can_become_the_imported_library(imported, tmp_path):
    out = tmp_path / "theirs.json"
    out.write_text(json.dumps({"materials": {"theirs": mine("Theirs")}}), encoding="utf-8")
    done = exchange.import_file(out, target="library")
    assert done.added == ["theirs"] and done.backup.is_file()
    assert "theirs" in _library() and "s355" not in _library()


def test_removed_materials_stay_removed_until_restored(imported):
    store = LibraryStore()
    assert ["s355", "s355_copy"] in duplicates()
    with pytest.raises(PuzzleError, match="s355_hot inherit"):
        store.remove_from_library(["s355"])  # its variant would lose its family
    assert store.remove_from_library(["s355_copy", "copper"]) == ["copper", "s355_copy"]
    assert "s355_copy" not in _library() and "copper" not in _library()  # generics too
    assert ["s355", "s355_copy"] not in duplicates()

    # Imported again (the importer rewrites library.json): still removed.
    imported.write_text(imported.read_text(encoding="utf-8"), encoding="utf-8")
    reload_library()
    assert "s355_copy" not in _library()
    assert store.removed() == ["copper", "s355_copy"] and store.mine() == {}

    assert store.restore(["copper"]) == ["copper"] and "copper" in _library()
    assert store.undo() == "copper" and "copper" not in _library()  # the restore undone
    assert store.undo() == "copper, s355_copy" and "s355_copy" in _library()

    # A material only you made is just deleted, no marker left behind.
    store.save("my_steel", mine("My steel"))
    store.remove_from_library(["my_steel"])
    assert "my_steel" not in store.entries()
