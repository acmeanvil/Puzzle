"""Materials → Export material library… and Import material library… (`materials.exchange`).

Export asks what goes in, then where; import asks for the file, then where its
materials go and what to do with ones already in your library, all in one form each.
Neither needs a project: the library is yours, not the project's.
"""

from __future__ import annotations

from pathlib import Path

from puzzle.materials import exchange

FILTER = "Material libraries (*.json)"


def export_library(window) -> int | None:
    from puzzle.gui.quick_form import Ask

    counts = {scope: len(exchange.entries_for(scope)) for scope in exchange.SCOPES}
    choices = [(f"{text} ({counts[k]})", k) for k, text in exchange.SCOPES.items() if counts[k]]
    if not any(counts[k] for k in ("yours", "user")):
        choices = [(f"{exchange.SCOPES['all']} ({counts['all']})", "all")]
    got = window.ask_form("Export material library", [
        Ask("scope", "Materials", choices=choices, default=choices[0][1]),
    ], intro="One file of materials to load into another Puzzle with Import material "
             "library…. The families a material inherits from go with it.")  # fmt: skip
    if got is None:
        return None
    path = window.ask_save_path(
        "Export material library", str(Path.home() / "puzzle-materials.json"), FILTER
    )
    if not path:
        return None
    try:
        n = exchange.export(Path(path), got["scope"])
    except Exception as exc:  # noqa: BLE001 - said in the window
        window.report(exc)
        return None
    from puzzle.gui.export_dialog import _open

    text = f"Exported {n} materials to {Path(path).name}"
    window.statusBar().showMessage(text, 10000)
    window.toasts.show(text, ("Open folder", lambda: _open(Path(path).parent)))
    return n


def import_library(window) -> exchange.Imported | None:
    from puzzle.gui.quick_form import Ask

    path = window.ask_open_path("Import material library", FILTER)
    if not path:
        return None
    try:
        count = len(exchange._read(Path(path)))
    except Exception as exc:  # noqa: BLE001 - said in the window
        window.report(exc)
        return None
    got = window.ask_form("Import material library", [
        Ask("target", "Put them", choices=[(t, k) for k, t in exchange.TARGETS.items()]),
        Ask("conflicts", "Materials you already have",
            choices=[(t, k) for k, t in exchange.CONFLICTS.items()],
            shown=lambda v: v.get("target") == "yours"),
    ], intro=f"{Path(path).name} holds {count} materials. Added to your materials, each is "
             "checked first and Undo in the Material builder takes the import back; as your "
             "imported library, it takes the place of the one you have (kept as "
             "library.json.bak).")  # fmt: skip
    if got is None:
        return None
    try:
        done = exchange.import_file(Path(path), got["target"], got.get("conflicts", "keep"))
    except Exception as exc:  # noqa: BLE001 - said in the window
        window.report(exc)
        return None
    text = f"Imported {Path(path).name}: {done.summary()}"
    window.statusBar().showMessage(text, 10000)
    window.toasts.show(text, state="info" if done.invalid else "done")
    if done.invalid:
        window.log("\n".join(f"{k}: {why}" for k, why in sorted(done.invalid.items())))
    return done
