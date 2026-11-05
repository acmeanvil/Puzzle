"""Render Puzzle's main screens in each theme to PNGs, for looking over after a change to
the look (ADR 0045 decision 10). Not a test: nothing is compared pixel by pixel.

    .pixi/envs/dev/bin/python scripts/theme_screens.py OUT_DIR [--themes light dark high_contrast]
        [--density comfortable] [--no-run]

It opens a scratch copy of the cantilever example (nothing in the repository changes),
meshes it and runs Tip load on CalculiX (from .pixi/envs/calculix, unless --no-run),
then for each theme saves:

- model.png: the tree and Properties on the study, the Advisor beside;
- geometry.png, mesh.png: the Geometry and Mesh tabs with their ribbons;
- results.png: von Mises stress with the legend and the Results ribbon;
- agent.png: the agent panel;
- settings.png: the Settings dialog on Interface;
- form.png: the new-study form;
- toast.png: a finished note over the window.

Run it on the native platform (not QT_QPA_PLATFORM=offscreen), so the 3D view draws.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", type=Path)
    parser.add_argument("--themes", nargs="+", default=["light", "dark", "high_contrast"])
    parser.add_argument("--density", default="comfortable", choices=["comfortable", "compact"])
    parser.add_argument("--no-run", action="store_true", help="skip meshing and solving")
    args = parser.parse_args()
    os.environ.setdefault("PUZZLE_SOLVER_PATH", str(REPO / ".pixi" / "envs" / "calculix" / "bin"))
    # A user folder of its own, so the user's settings are neither read nor changed.
    os.environ["PUZZLE_USER_DIR"] = tempfile.mkdtemp(prefix="puzzle-screens-user-")
    root = Path(tempfile.mkdtemp(prefix="puzzle-screens-")) / "cantilever"
    shutil.copytree(REPO / "examples" / "cantilever", root,
                    ignore=shutil.ignore_patterns("results", ".puzzle", "versions"))  # fmt: skip

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from puzzle import settings
    from puzzle.gui.tree import ROLE
    from puzzle.gui.window import MainWindow

    app = QApplication(sys.argv[:1])
    settings.current().set("interface.density", args.density)
    settings.current().save()  # the window's Settings reload reads the file
    window = MainWindow(root)
    window.resize(1500, 950)
    window.show()

    def spin(seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.02)

    def wait(task) -> None:
        if task is None:
            return
        done = []
        task.signals.done.connect(lambda _r: done.append(1))
        task.signals.failed.connect(lambda _e: done.append(0))
        while not done:
            spin(0.1)
        spin(1.5)  # past the window's own reload after a run

    def select(kind: str, label: str) -> None:
        for item in window.tree._items():
            node = item.data(0, ROLE)
            if node.kind == kind and node.label == label:
                window.tree.setCurrentItem(item)
                return

    spin(1.0)
    ran = False
    if not args.no_run:
        select("object", "Cantilever")
        wait(window.mesh_selected())
        select("object", "Tip load")
        wait(window.run_selected())
        ran = True

    args.out.mkdir(parents=True, exist_ok=True)
    saved = []
    for name in args.themes:
        settings.current().set("interface.theme", name)
        settings.current().save()
        window.settings_changed()
        window.refresh()
        folder = args.out / name
        folder.mkdir(exist_ok=True)

        def shot(what: str, widget=None, folder=folder) -> None:
            spin(0.6)
            path = folder / f"{what}.png"
            (widget or window).grab().save(str(path))
            saved.append(path)

        window.left_tabs.setCurrentIndex(0)
        select("object", "Tip load")
        window.docks.show("advisor")
        shot("model")
        window.left_tabs.setCurrentWidget(window.workbench)
        shot("geometry")
        window.left_tabs.setCurrentWidget(window.mesh_bench)
        shot("mesh")
        window.left_tabs.setCurrentIndex(0)
        if ran:
            for item in window.tree._items():
                if item.data(0, ROLE).kind == "run":
                    window.tree.setCurrentItem(item)
                    break
            spin(1.0)
            window.results_bar.field.setCurrentText("stress")
            shot("results")
        window.docks.show("agent")
        shot("agent")
        from puzzle.gui.settings_dialog import SettingsDialog

        dialog = SettingsDialog(parent=window)
        found = dialog.groups.findItems("Interface", Qt.MatchFlag.MatchExactly)
        if found:
            dialog.groups.setCurrentItem(found[0])
        dialog.show()
        shot("settings", dialog)
        dialog.close()
        from puzzle.gui.quick_form import Ask, QuickForm

        form = QuickForm("New study", [
            Ask("name", "Name", "text", default="Static 1"),
            Ask("solver", "Solver", choices=[("CalculiX", "calculix"),
                                             ("Code_Aster", "code_aster")]),
        ], intro="A study solves a component's physics.", parent=window)  # fmt: skip
        form.show()
        shot("form", form)
        form.close()
        window.toasts.show("Tip load finished", ("Show results", lambda: None))
        shot("toast")
        for t in list(window.toasts.live):
            t.close_now()
    for path in saved:
        print(path)
    window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
