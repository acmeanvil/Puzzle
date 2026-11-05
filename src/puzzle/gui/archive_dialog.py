"""Project → Export project archive… and Open project archive… (ADR 0061).

Export asks everything in one form: which results go in, versions, history, each
with its size now, and where to write the file. Open reads the archive's manifest
first (its name, who wrote it, what it holds), asks for a new or empty folder, checks
and unpacks it, and opens the project there, saying what it noted (code it holds, a
dropped results store).
"""

from __future__ import annotations

from pathlib import Path

from puzzle.project import archive


def _mb(n: int) -> str:
    return f"{n / 1e6:.1f} MB" if n >= 1e5 else f"{n / 1e3:.0f} kB"


def export_archive(window) -> archive.Written | None:
    from puzzle.gui.quick_form import Ask

    if window.session is None:
        return None
    root = window.session.root
    size = archive.sizes(root)
    got = window.ask_form("Export project archive", [
        Ask("results", "Results", choices=[
            ("None: the model only", "none"),
            (f"The latest run of each study (up to {_mb(size['results'])})", "latest"),
            (f"Every run ({_mb(size['results'])})", "all")], default="latest"),
        Ask("versions", "Versions", choices=[(f"All ({_mb(size['versions'])})", True),
                                             ("None", False)]),
        Ask("history", "History", choices=[(f"All ({_mb(size['history'])})", True),
                                           ("None", False)]),
    ], intro=f"One .puzzle file to send. The model ({_mb(size['model'])}: objects, geometry "
             "and its files, imported fields, materials) always goes in.")  # fmt: skip
    if got is None:
        return None
    name = f"{window.session.project.name}.puzzle"
    path = window.ask_save_path("Export project archive", str(root.parent / name),
                                "Puzzle archives (*.puzzle)")  # fmt: skip
    if not path:
        return None
    include = archive.Include(results=got["results"], versions=bool(got["versions"]),
                              history=bool(got["history"]))  # fmt: skip
    try:
        done = archive.write(root, Path(path), include)
    except Exception as exc:  # noqa: BLE001 - said in the window
        window.report(exc)
        return None
    window.statusBar().showMessage(done.summary(), 10000)
    from puzzle.gui.export_dialog import _open

    window.toasts.show(done.summary(), ("Open folder", lambda: _open(done.path.parent)))
    return done


def open_archive(window, path: str | None = None) -> archive.Opened | None:
    from puzzle.gui.quick_form import Ask

    path = path or window.ask_open_path("Open project archive", "Puzzle archives (*.puzzle)")
    if not path:
        return None
    try:
        info = archive.manifest(Path(path))
    except Exception as exc:  # noqa: BLE001 - said in the window
        window.report(exc)
        return None
    summary = info.get("summary") or {}
    counts = summary.get("counts") or {}
    held = ", ".join(f"{v} {k}" for k, v in counts.items()) or "a project"
    words = {"none": "no results", "latest": "the latest results", "all": "every run's results"}
    results = words.get(info.get("include", {}).get("results"), "")
    got = window.ask_form("Open project archive", [
        Ask("name", "Name", kind="text", default=info.get("name", Path(path).stem)),
        Ask("folder", "Folder", kind="folder", help="A new or empty folder for the project"),
    ], intro=f"{Path(path).name}: '{info.get('name')}', written {info.get('written_at', '')} "
             f"by {info.get('by', '?')} with Puzzle {info.get('puzzle', '?')}. It holds {held}"
             + (f", {results}" if results else "") + ". It is checked before anything is "
             "written.", check=lambda v: None if v["folder"] else "Choose a folder")  # fmt: skip
    if got is None:
        return None
    dest = Path(got["folder"])
    if dest.exists() and any(dest.iterdir()):
        dest = dest / (got["name"] or Path(path).stem)  # a folder for it inside the chosen one
    try:
        opened = archive.read(Path(path), dest, name=got["name"] or None)
    except Exception as exc:  # noqa: BLE001 - said in the window
        window.report(exc)
        return None
    window.open(opened.root)
    window.statusBar().showMessage(opened.summary(), 15000)
    if opened.notes:
        window.toasts.show(opened.summary(), state="info")
    return opened
