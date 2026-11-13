"""GUI errors are written down and shown, and launcher placeholders open no project
(ADR 0068's first Windows tests)."""

from __future__ import annotations

import faulthandler
import sys
import threading
from pathlib import Path

import pytest

from puzzle.gui import errors
from puzzle.gui.app import project_argument


@pytest.mark.parametrize("arg", ["%1", '"%1"', "%f", "%U", "", None])
def test_a_launchers_empty_placeholder_opens_no_project(arg):
    assert project_argument(arg) is None


def test_a_real_path_is_opened():
    assert project_argument("C:\\Users\\me\\bracket") == Path("C:\\Users\\me\\bracket")
    assert project_argument(Path("plate.puzzle")) == Path("plate.puzzle")


@pytest.fixture
def installed(tmp_path, monkeypatch):
    told = []
    monkeypatch.setattr(errors, "_tell", told.append)
    hooks = sys.excepthook, threading.excepthook, sys.stdout, sys.stderr
    path = errors.install(tmp_path / "logs" / "gui.log")
    yield path, told
    sys.excepthook, threading.excepthook, sys.stdout, sys.stderr = hooks
    _uninstall()


def _uninstall() -> None:
    import logging

    faulthandler.disable()
    logging.getLogger().removeHandler(errors._state.pop("handler"))
    logging.captureWarnings(False)
    errors._state.pop("log").close()


def test_an_uncaught_error_is_logged_and_told(installed):
    path, told = installed
    try:
        raise ValueError("no study named 'Tip load'")
    except ValueError:
        sys.excepthook(*sys.exc_info())
    text = path.read_text(encoding="utf-8")
    assert "=== Puzzle started" in text
    assert "ValueError: no study named 'Tip load'" in text and "Traceback" in text
    assert told == ["ValueError: no study named 'Tip load'"]


def test_a_long_log_is_started_afresh(tmp_path, monkeypatch):
    path = tmp_path / "gui.log"
    path.write_text("x" * (errors.MAX_LOG_BYTES + 1), encoding="utf-8")
    monkeypatch.setattr(errors, "_tell", lambda m: None)
    hooks = sys.excepthook, threading.excepthook
    try:
        errors.install(path)
        assert path.stat().st_size < 1000
        assert (tmp_path / "gui.old.log").exists()
    finally:
        sys.excepthook, threading.excepthook = hooks
        _uninstall()


def test_warnings_reach_the_log_and_the_viewer_can_show_errors_only(installed):
    import logging

    from puzzle.gui import log_viewer

    path, _ = installed
    logging.getLogger("puzzle.mesh").warning("element quality 0.04")
    try:
        raise RuntimeError("the mesh has inverted elements")
    except RuntimeError:
        sys.excepthook(*sys.exc_info())
    everything = log_viewer.read(path=path)
    assert "WARNING puzzle.mesh: element quality 0.04" in everything
    only = log_viewer.read(errors_only=True, path=path)
    assert "RuntimeError: the mesh has inverted elements" in only
    assert "element quality" not in only and "=== Puzzle started" not in only
