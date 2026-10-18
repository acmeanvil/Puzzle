"""A Gmsh session. Gmsh keeps global state, so only one session may be open at a time."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

import gmsh

_lock = threading.Lock()
_resident: threading.Thread | None = None


def _keep_resident() -> None:
    """Initialise Gmsh once on a thread that never exits.

    Gmsh/OpenCASCADE keep per-thread state from the first thread that uses them.
    If that thread exits (a worker thread: MCP tool calls, a GUI job) and another
    thread then uses Gmsh, the process segfaults. A resident first user avoids it.
    """
    global _resident
    if _resident is not None:
        return
    ready = threading.Event()

    def resident() -> None:
        gmsh.initialize(interruptible=False)
        gmsh.finalize()
        ready.set()
        threading.Event().wait()

    _resident = threading.Thread(target=resident, name="gmsh-resident", daemon=True)
    _resident.start()
    ready.wait()


@contextmanager
def gmsh_session(name: str = "puzzle", *, verbose: bool = False) -> Iterator[None]:
    """Initialise Gmsh with SI units (OpenCASCADE imports converted to metres)."""
    with _lock:
        _keep_resident()
        gmsh.initialize(interruptible=False)
        try:
            gmsh.option.setNumber("General.Terminal", 1 if verbose else 0)
            gmsh.option.setString("Geometry.OCCTargetUnit", "M")
            gmsh.model.add(name)
            yield
        finally:
            gmsh.finalize()
