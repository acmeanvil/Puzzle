"""6-DOF devices through hidapi (ADR 0069): find them, open them shared, read reports.

hidapi's Python binding is conda-forge's `hidapi` (`import hid`). Windows reads through
its own HID class driver and opens devices shared; macOS through IOKit, without taking
the device; Linux through hidraw, which needs read access to /dev/hidraw* (a udev rule)
unless spacenavd is used instead (`spacenav`).
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass

from puzzle.gui.spacemouse.devices import VENDORS, Device, device_for

READ_BYTES = 64
READ_TIMEOUT_MS = 100  # the reader thread checks for a stop this often


def _hid():
    try:
        import hid
    except ImportError:
        return None
    return hid


def why_not() -> str | None:
    """Why no device can be read here, or None."""
    if _hid() is None:
        return "hidapi isn't installed (the hidapi package)"
    return None


@dataclass(frozen=True)
class Found:
    device: Device
    path: bytes


def find() -> list[Found]:
    """The 6-DOF devices plugged in (one per device: a multi-interface device lists its
    multi-axis interface, else its first)."""
    hid = _hid()
    if hid is None:
        return []
    found: dict[tuple[int, int, bytes], Found] = {}
    try:
        listed = hid.enumerate()
    except Exception:  # noqa: BLE001 - a backend that can't list devices lists none
        return []
    for info in listed:
        vendor, product = info.get("vendor_id", 0), info.get("product_id", 0)
        page, usage = info.get("usage_page", 0), info.get("usage", 0)
        if vendor not in VENDORS and (page, usage) != (0x01, 0x08):
            continue
        device = device_for(vendor, product, page, usage, info.get("product_string") or "")
        if device is None:
            continue
        serial = (info.get("serial_number") or "").encode()
        key = (vendor, product, serial)
        if key not in found or (page, usage) == (0x01, 0x08):
            found[key] = Found(device, info["path"])
    return list(found.values())


class Reader:
    """One open device; `read` returns a report, or b"" when none came in time."""

    def __init__(self, path: bytes) -> None:
        hid = _hid()
        if hid is None:
            raise OSError(why_not())
        self._dev = hid.device()
        self._dev.open_path(path)
        self._dev.set_nonblocking(0)

    def read(self, timeout_ms: int = READ_TIMEOUT_MS) -> bytes:
        return bytes(self._dev.read(READ_BYTES, timeout_ms))

    def close(self) -> None:
        with contextlib.suppress(Exception):  # unplugged already
            self._dev.close()
