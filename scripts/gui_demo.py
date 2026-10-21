"""Open the GUI on a scratch copy of the cantilever example, filled in to show the layout.

    .pixi/envs/dev/bin/python scripts/gui_demo.py            # mesh, highlight, run, show stress
    .pixi/envs/dev/bin/python scripts/gui_demo.py --plain    # just open the project
    .pixi/envs/dev/bin/python scripts/gui_demo.py --project path/to/project

The example is copied to a temporary folder, so nothing in the repository changes.
With the default walk-through it meshes the component, highlights the Tip face,
runs Tip load (CalculiX, from .pixi/envs/calculix) and shows von Mises stress; the
agent panel opens with a short scripted exchange so its layout is visible without an
API key (`--agent` uses your real provider instead).
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", type=Path, help="open this project instead (not copied)")
    parser.add_argument("--plain", action="store_true", help="open only, no walk-through")
    parser.add_argument("--agent", action="store_true", help="use your real agent provider")
    args = parser.parse_args()

    os.environ.setdefault("PUZZLE_SOLVER_PATH", str(REPO / ".pixi" / "envs" / "calculix" / "bin"))
    if args.project is not None:
        root = args.project
    else:
        root = Path(tempfile.mkdtemp(prefix="puzzle-gui-")) / "cantilever"
        shutil.copytree(
            REPO / "examples" / "cantilever",
            root,
            ignore=shutil.ignore_patterns("results", ".puzzle"),
        )
        print(f"scratch copy: {root}")

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from puzzle.gui.tree import ROLE
    from puzzle.gui.window import MainWindow

    app = QApplication(sys.argv[:1])
    factory = None if args.agent or args.plain else _scripted_agent
    window = MainWindow(root, provider_factory=factory)
    window.resize(1500, 950)
    window.show()

    def select(kind: str, label: str) -> None:
        for item in window.tree._items():
            node = item.data(0, ROLE)
            if node.kind == kind and node.label == label:
                window.tree.setCurrentItem(item)
                return

    def after(task, then) -> None:
        """Run `then` once a background task has finished (or right away without one)."""
        if task is None:
            then()
        else:
            task.signals.done.connect(lambda _: QTimer.singleShot(300, then))

    def walk_through() -> None:
        select("object", "Cantilever")
        after(window.mesh_selected(), show_selection)

    def show_selection() -> None:
        select("selection", "Tip")
        QTimer.singleShot(1500, run)

    def run() -> None:
        select("object", "Tip load")
        # Wait past the window's own reload after a run (it resets the field).
        task = window.run_selected()
        task.signals.done.connect(lambda _: QTimer.singleShot(1500, show_stress))

    def show_stress() -> None:
        window.results_bar.field.setCurrentText("stress")
        if factory is not None:
            task = window.agent.send("What does the Tip load study do?")
        else:
            task = None
        shot = os.environ.get("PUZZLE_DEMO_SCREENSHOT")  # for checking the layout unattended
        if shot:
            after(task, lambda: (window.grab().save(shot), app.quit()))

    if not args.plain and args.project is None:
        QTimer.singleShot(500, walk_through)
    return app.exec()


def _scripted_agent():
    """A short canned exchange (no API key needed) so the agent panel shows its layout."""
    from puzzle.agent.providers import Reply, ScriptedProvider, ToolCall

    return ScriptedProvider(
        [
            Reply(tool_calls=[ToolCall("1", "outline", {})]),
            Reply(tool_calls=[ToolCall("2", "result_summary", {"study": "Tip load"})]),
            Reply(
                text="Tip load is a static CalculiX analysis of the bar: fixed at Bar/Root, "
                "with -100 N in y on Bar/Tip. The run just finished; the peak von Mises "
                "stress is at the fixed root. (Scripted demo reply: start with --agent to "
                "talk to your configured model.)"
            ),
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
