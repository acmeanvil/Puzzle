"""Save as New Project: a full copy in a new folder that shares nothing with the
original afterwards (its files, results and history are its own)."""

import os
import shutil
from pathlib import Path

import pytest

from puzzle.errors import PuzzleError
from puzzle.model.schema import Geometry, HistoryStep, Project
from puzzle.project import load_project, save_project
from puzzle.project.copy import IMPORTED, save_as

EXAMPLE = Path(__file__).parents[1] / "examples" / "cantilever"


def _example(tmp_path) -> Path:
    root = tmp_path / "original"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns(".puzzle"))
    (root / ".puzzle" / "runs" / "study-1").mkdir(parents=True)  # a solver's working files
    (root / ".puzzle" / "runs" / "gui-mesh").mkdir(parents=True)  # a kept mesh
    (root / ".puzzle" / "runs" / "gui-mesh" / "mesh.msh").write_text("mesh")
    (root / ".puzzle" / "lock").write_text("held")
    return root


def test_a_full_copy_with_its_own_name_and_nothing_shared(tmp_path):
    root = _example(tmp_path)
    done = save_as(root, tmp_path / "copy", "Cantilever, steel")
    copy = load_project(done.root)
    original = load_project(root)
    assert copy.name == "Cantilever, steel" and original.name != copy.name
    assert set(copy.objects) == set(original.objects)  # every object, ids kept
    for rel in ("bar.step", "history.jsonl", "results/store.json",
                ".puzzle/runs/gui-mesh/mesh.msh"):  # fmt: skip
        assert (done.root / rel).exists(), rel
    assert not (done.root / ".puzzle" / "lock").exists()  # the original's lock stays
    assert not (done.root / ".puzzle" / "runs" / "study-1").exists()  # disposable
    # Independent: changing the copy leaves the original alone.
    (done.root / "bar.step").write_text("changed")
    copy.name = "renamed"
    save_project(copy, done.root)
    assert (root / "bar.step").read_text() != "changed"
    assert load_project(root).name == original.name


def test_outside_files_are_brought_in_and_repointed(tmp_path):
    parts = tmp_path / "parts"
    parts.mkdir()
    shutil.copy(EXAMPLE / "bar.step", parts / "bar.step")
    other = tmp_path / "elsewhere" / "bar.step"  # the same name, another file
    other.parent.mkdir()
    shutil.copy(EXAMPLE / "bar.step", other)
    root = tmp_path / "original"
    p = Project(name="outside")
    p.add(Geometry(name="Relative", source="../parts/bar.step"))
    p.add(Geometry(name="Absolute", source=str(other)))
    p.add(Geometry(name="Imported", history=[
        HistoryStep(kind="import", name="F", args={"file": str(parts / "bar.step")})]))  # fmt: skip
    save_project(p, root)
    done = save_as(root, tmp_path / "copy")
    copy = load_project(done.root)
    by = {g.name: g for g in copy.of_type(Geometry)}
    # Two outside files of one name: two copies, neither over the other.
    assert {by["Relative"].source, by["Absolute"].source} == {
        f"{IMPORTED}/bar.step", f"{IMPORTED}/bar-1.step"}  # fmt: skip
    # The same outside file named twice is copied once.
    assert by["Imported"].history[0].args["file"] == by["Relative"].source
    for g in ("Relative", "Absolute"):
        assert (done.root / by[g].source).exists()
    os.remove(parts / "bar.step")  # the original's file gone: the copy still has its own
    assert (done.root / by["Relative"].source).exists()
    assert len(done.brought_in) == 2


def test_it_refuses_a_folder_inside_the_project_or_one_in_use(tmp_path):
    root = _example(tmp_path)
    with pytest.raises(PuzzleError, match="outside"):
        save_as(root, root / "copy")
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "notes.txt").write_text("mine")
    with pytest.raises(PuzzleError) as refused:
        save_as(root, busy)
    assert refused.value.code == "exists"
    assert (busy / "notes.txt").read_text() == "mine"


def test_a_missing_outside_file_is_named(tmp_path):
    root = tmp_path / "original"
    p = Project(name="lost")
    p.add(Geometry(name="Gone", source=str(tmp_path / "nowhere.step")))
    save_project(p, root)
    with pytest.raises(PuzzleError, match="nowhere.step"):
        save_as(root, tmp_path / "copy")
