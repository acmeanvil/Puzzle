"""A 3D mouse through spacenavd on Linux (ADR 0069 step 4).

spacenavd, the open-source daemon (BSD licence) Linux distributions ship, reads the
device with the permissions already in place and serves its events over a Unix socket,
`/var/run/spnav.sock` (`SPNAV_SOCKET` overrides it), in libspnav's X11-free protocol:
each event is eight native 32-bit integers,

- motion: 0, x, y, z, rx, ry, rz, period (ms);
- a button pressed: 1, its number, then padding; released: 2, its number.

Its axes are x right, y up, z into the screen (rotations about the same), so the view's
frame (x right, y up, z out of the screen) is z and rz negated. Where it runs, the
controller reads it in place of hidapi, which on Linux needs a udev rule for hidraw.
"""

from __future__ import annotations

import contextlib
import os
import socket
import struct
from dataclasses import dataclass

DEFAULT_SOCKET = "/var/run/spnav.sock"
EVENT = struct.Struct("=8i")  # native byte order, as libspnav writes
MOTION, PRESS, RELEASE = 0, 1, 2
NAME = "3D mouse (through spacenavd)"
VIEW_SIGNS = (1, 1, -1, 1, 1, -1)


def socket_path() -> str:
    return os.environ.get("SPNAV_SOCKET", DEFAULT_SOCKET)


def available() -> bool:
    """spacenavd's socket is there and this Python can speak to it (Unix only)."""
    return hasattr(socket, "AF_UNIX") and os.path.exists(socket_path())


@dataclass
class Event:
    kind: str  # "motion" or "button"
    axes: list[int] | None = None  # view-frame counts, translations first
    button: int = 0
    down: bool = False


def to_event(values: tuple[int, ...]) -> Event | None:
    """One event's eight integers as an Event, or None for a kind not read."""
    kind = values[0]
    if kind == MOTION:
        return Event("motion", [s * v for s, v in zip(VIEW_SIGNS, values[1:7], strict=True)])
    if kind in (PRESS, RELEASE):
        return Event("button", button=values[1], down=kind == PRESS)
    return None


class Reader:
    """The daemon's socket; `read` returns the next event, or None when none came in
    time (so the reader thread can look for a stop)."""

    def __init__(self, path: str | None = None, timeout_s: float = 0.1) -> None:
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.settimeout(timeout_s)
        try:
            self._sock.connect(path or socket_path())
        except OSError:
            self._sock.close()
            raise
        self._pending = b""

    def read(self) -> Event | None:
        while len(self._pending) < EVENT.size:
            try:
                chunk = self._sock.recv(EVENT.size * 16)
            except TimeoutError:
                return None
            if not chunk:
                raise OSError("spacenavd closed the connection")
            self._pending += chunk
        values = EVENT.unpack(self._pending[: EVENT.size])
        self._pending = self._pending[EVENT.size :]
        return to_event(values)

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self._sock.close()
