"""The 6-DOF devices Puzzle knows and how to read their HID reports (ADR 0069).

Every one is a HID multi-axis controller (usage page 0x01, usage 0x08). Its reports:

- report 1: translation x, y, z as little-endian int16 (older models), or all six axes
  (newer ones: Compact, Wireless, the universal receiver);
- report 2: rotation rx, ry, rz (older models only);
- report 3: the buttons, a little-endian bit mask.

The parser goes by each report's length, so a device not listed still reads; the table
names devices and their buttons. The axes come in the device's frame (x right, y toward
the user, z down) and `to_view` turns them into the view's (x right, y up, z out of the
screen) by `Device.signs`. IDs and layouts from spacenavd's and pyspacemouse's device
lists; signs to be confirmed on hardware, model by model.
"""

from __future__ import annotations

from dataclasses import dataclass, field

MULTI_AXIS = (0x01, 0x08)  # HID usage page, usage
VENDORS = (0x046D, 0x256F)  # made under Logitech, then 3Dconnexion's own


@dataclass(frozen=True)
class Device:
    vendor: int
    product: int
    name: str
    buttons: tuple[str, ...] = ("Left", "Right")
    # device axes (x, y, z, rx, ry, rz) to the view's: (which device axis, sign) each
    signs: tuple[tuple[int, int], ...] = ((0, 1), (2, -1), (1, 1), (3, 1), (5, -1), (4, 1))


_PRO = ("Menu", "Fit", "Top", "Right", "Front", "Roll view", "1", "2", "3", "4",
        "Esc", "Alt", "Shift", "Ctrl", "Rotation lock")  # fmt: skip

KNOWN = [
    Device(0x046D, 0xC626, "SpaceNavigator"),
    Device(0x046D, 0xC628, "SpaceNavigator for Notebooks"),
    Device(0x046D, 0xC627, "SpaceExplorer", tuple(f"Button {i + 1}" for i in range(15))),
    Device(0x046D, 0xC625, "SpacePilot", tuple(f"Button {i + 1}" for i in range(21))),
    Device(0x046D, 0xC629, "SpacePilot Pro", tuple(f"Button {i + 1}" for i in range(31))),
    Device(0x046D, 0xC62B, "SpaceMouse Pro", _PRO),
    Device(0x256F, 0xC62E, "SpaceMouse Wireless (cable)"),
    Device(0x256F, 0xC62F, "SpaceMouse Wireless"),
    Device(0x256F, 0xC631, "SpaceMouse Pro Wireless (cable)", _PRO),
    Device(0x256F, 0xC632, "SpaceMouse Pro Wireless", _PRO),
    Device(0x256F, 0xC633, "SpaceMouse Enterprise", tuple(f"Button {i + 1}" for i in range(31))),
    Device(0x256F, 0xC635, "SpaceMouse Compact"),
    Device(0x256F, 0xC652, "3Dconnexion Universal Receiver", _PRO),
]
_BY_ID = {(d.vendor, d.product): d for d in KNOWN}


def known(vendor: int, product: int) -> Device | None:
    return _BY_ID.get((vendor, product))


def device_for(vendor: int, product: int, usage_page: int = 0, usage: int = 0,
               name: str = "") -> Device | None:  # fmt: skip
    """The device, if Puzzle can read it: listed, or any multi-axis controller (named by
    what it reports, with the common layout)."""
    found = known(vendor, product)
    if found is not None:
        return found
    if (usage_page, usage) == MULTI_AXIS:
        return Device(vendor, product, name or f"3D mouse {vendor:04x}:{product:04x}")
    return None


@dataclass
class Reading:
    """The device's latest state: six axis counts (device frame) and the buttons down."""

    axes: list[int] = field(default_factory=lambda: [0] * 6)
    buttons: int = 0


def _int16s(data: bytes, n: int) -> list[int]:
    return [int.from_bytes(data[2 * i : 2 * i + 2], "little", signed=True) for i in range(n)]


def parse(report: bytes | list[int], reading: Reading) -> str | None:
    """Fold one report into `reading`; what changed: "axes", "buttons" or None."""
    data = bytes(report)
    if not data:
        return None
    kind, body = data[0], data[1:]
    if kind == 1 and len(body) >= 12:  # all six axes
        reading.axes = _int16s(body, 6)
        return "axes"
    if kind == 1 and len(body) >= 6:
        reading.axes[:3] = _int16s(body, 3)
        return "axes"
    if kind == 2 and len(body) >= 6:
        reading.axes[3:] = _int16s(body, 3)
        return "axes"
    if kind == 3:
        reading.buttons = int.from_bytes(body, "little")
        return "buttons"
    return None


def to_view(device: Device, axes: list[int]) -> list[int]:
    """Device-frame counts (x right, y toward you, z down) in the view's frame (x right,
    y up, z out of the screen), translations first."""
    return [sign * axes[i] for i, sign in device.signs]


def pressed(buttons: int, before: int) -> list[int]:
    """The buttons that went down since `before`, by number from 0."""
    went = buttons & ~before
    return [i for i in range(went.bit_length()) if went >> i & 1]
