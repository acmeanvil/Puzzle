"""Start the desktop GUI: `puzzle gui [PROJECT]`."""

from __future__ import annotations

import sys
from pathlib import Path

NAME = "Puzzle"


def mac_app_name(name: str = NAME) -> bool:
    """Name the app in the macOS menu bar and Dock, which otherwise say "Python".

    macOS takes the name from the running bundle's Info.plist, which is Python's, so
    the bundle's CFBundleName is replaced in memory before Qt starts; through the
    Objective-C runtime directly, so nothing beyond the standard library is needed."""
    if sys.platform != "darwin":
        return False
    import ctypes
    import ctypes.util

    try:
        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        ctypes.cdll.LoadLibrary(ctypes.util.find_library("Foundation"))
    except (OSError, TypeError):
        return False
    objc.objc_getClass.restype = ctypes.c_void_p
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    send = objc.objc_msgSend
    send.restype = ctypes.c_void_p

    def call(target, selector, *args, types=()):
        send.argtypes = [ctypes.c_void_p, ctypes.c_void_p, *types]
        return send(target, objc.sel_registerName(selector), *args)

    def text(value: str):
        cls = objc.objc_getClass(b"NSString")
        return call(cls, b"stringWithUTF8String:", value.encode(), types=(ctypes.c_char_p,))

    bundle = call(objc.objc_getClass(b"NSBundle"), b"mainBundle")
    info = call(bundle, b"localizedInfoDictionary") or call(bundle, b"infoDictionary")
    if not info:
        return False
    pair = (ctypes.c_void_p, ctypes.c_void_p)
    for key in (b"CFBundleName", b"CFBundleDisplayName"):
        call(info, b"setObject:forKey:", text(name), text(key.decode()), types=pair)
    return True


def main(project: Path | None = None) -> int:
    mac_app_name()  # before the QApplication: the menu bar reads it once
    from PySide6.QtWidgets import QApplication

    from puzzle.gui.start_page import app_icon
    from puzzle.gui.window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName(NAME)
    app.setWindowIcon(app_icon())  # the Dock's icon too, run from a plain Python
    archive = project if project is not None and Path(project).suffix == ".puzzle" else None
    window = MainWindow(None if archive is not None else project)
    window.show()
    if archive is not None:  # a .puzzle file opened from the system (ADR 0061)
        from PySide6.QtCore import QTimer

        from puzzle.gui.archive_dialog import open_archive

        QTimer.singleShot(0, lambda: open_archive(window, str(archive)))
    return app.exec()
