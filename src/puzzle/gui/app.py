"""Start the desktop GUI: `puzzle gui [PROJECT]`."""

from __future__ import annotations

import sys
from pathlib import Path


def main(project: Path | None = None) -> int:
    from PySide6.QtWidgets import QApplication

    from puzzle.gui.window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("Puzzle")
    window = MainWindow(project)
    window.show()
    return app.exec()
