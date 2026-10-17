"""A Gmsh session. Gmsh keeps global state, so only one session may be open at a time."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

import gmsh

_lock = threading.Lock()


@contextmanager
def gmsh_session(name: str = "puzzle", *, verbose: bool = False) -> Iterator[None]:
    """Initialise Gmsh with SI units (OpenCASCADE imports converted to metres)."""
    with _lock:
        gmsh.initialize(interruptible=False)
        try:
            gmsh.option.setNumber("General.Terminal", 1 if verbose else 0)
            gmsh.option.setString("Geometry.OCCTargetUnit", "M")
            gmsh.model.add(name)
            yield
        finally:
            gmsh.finalize()
