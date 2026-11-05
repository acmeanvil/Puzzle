"""Save as New Project: a full copy of a project in a new folder, with nothing tying it
to the original afterwards.

Everything in the project's folder is copied: its objects, geometry files, imported
fields, results and meshes (the results store), versions and history. Files a geometry
names outside the folder (an absolute path, or `../parts/bracket.step`) are copied into
the new project's `imported/` folder and the geometry is pointed at the copy, so
editing or moving the original's files never reaches the copy. Solver working folders
under `.puzzle/runs` are left behind (they are disposable), except the meshes the GUI
and the agent keep there, and so is the original's lock.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from puzzle.errors import PuzzleError
from puzzle.project.layout import LOCK, WORK_DIR

IMPORTED = "imported"  # outside files, copied in
KEPT_WORK = ("gui-mesh", "mcp-mesh")  # meshes kept under .puzzle/runs


@dataclass
class Copied:
    """What a Save as did: where, and which outside files were brought in."""

    root: Path
    name: str
    brought_in: dict[str, str] = field(default_factory=dict)  # original path -> new, relative

    def summary(self) -> str:
        extra = (
            f"; {len(self.brought_in)} file(s) from outside the project copied into '{IMPORTED}/'"
            if self.brought_in
            else ""
        )
        return f"Saved as new project '{self.name}' in {self.root}{extra}"


def save_as(root: Path, dest: Path, name: str | None = None, version: int | None = None) -> Copied:
    """Copy the project at `root` to `dest` (a new or empty folder) and name it `name`;
    with `version`, the copy starts from that version's files (ADR 0059)."""
    from puzzle.project.store import load_project, save_project

    root, dest = Path(root).resolve(), Path(dest).resolve()
    if not (root / "project.toml").exists():
        raise PuzzleError(f"'{root}' is not a Puzzle project", code="not_found")
    if dest == root or root in dest.parents or dest in root.parents:
        raise PuzzleError(
            "the new project's folder must be outside the open project's folder (and not hold it)",
            code="invalid",
            path=str(dest),
        )
    if dest.exists() and any(dest.iterdir()):
        raise PuzzleError(
            f"'{dest}' is not empty: choose a new or empty folder", code="exists", path=str(dest)
        )
    work = root / WORK_DIR

    def ignore(folder: str, entries: list[str]) -> set[str]:
        here = Path(folder)
        skip = {e for e in entries if here / e == root / LOCK}
        if here == work:  # solver working folders are disposable; meshes are kept
            skip |= {e for e in entries if e not in KEPT_WORK}
        return skip

    from puzzle.project import versions

    source_name = load_project(root).name
    start = versions.get(root, version) if version is not None else None
    shutil.copytree(root, dest, ignore=ignore, dirs_exist_ok=True, symlinks=False)
    if start is not None:  # the version's files in place of the current ones
        versions.restore_files(dest, start)

    from puzzle import trust

    if trust.is_trusted(root):  # a copy made here of a trusted project (ADR 0062)
        trust.trust(dest)
    project = load_project(dest)
    project.name = name or project.name
    out = Copied(dest, project.name)

    def bring_in(given: str | None) -> str | None:
        """An outside file copied into imported/ (once); the path to store instead."""
        if not given:
            return given
        path = Path(given)
        full = path if path.is_absolute() else (root / path)
        full = full.resolve()
        if root in full.parents:  # inside the project: copied with it already
            return given
        if not full.exists():
            raise PuzzleError(
                f"geometry file '{given}' is missing, so the copy can't take it along",
                code="not_found",
                path=given,
            )
        if str(full) not in out.brought_in:
            target = dest / IMPORTED / full.name
            k = 1
            while target.exists():  # two outside files of the same name
                target = dest / IMPORTED / f"{full.stem}-{k}{full.suffix}"
                k += 1
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(full, target)
            out.brought_in[str(full)] = target.relative_to(dest).as_posix()
        return out.brought_in[str(full)]

    from puzzle.model.schema import Geometry

    for geo in project.of_type(Geometry):
        geo.source = bring_in(geo.source)
        for step in geo.history:
            if step.kind == "import" and step.args.get("file"):
                step.args = {**step.args, "file": bring_in(step.args["file"])}
    save_project(project, dest)
    words = {"source": source_name + (f", version {start.number}" if start else "")}
    versions.save(dest, how="auto", trigger="copied", words=words, project=project)
    return out
