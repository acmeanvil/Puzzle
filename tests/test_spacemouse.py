"""6-DOF devices (ADR 0069): reports parsed, and counts turned into camera moves."""

from __future__ import annotations

import numpy as np
import pytest

from puzzle.gui.spacemouse import devices, motion
from puzzle.gui.spacemouse.motion import Camera, Settings


def _report(kind: int, *values: int) -> bytes:
    return bytes([kind]) + b"".join(v.to_bytes(2, "little", signed=True) for v in values)


def test_split_and_combined_reports_fill_the_six_axes():
    r = devices.Reading()
    assert devices.parse(_report(1, 10, -20, 30), r) == "axes"
    assert devices.parse(_report(2, -1, 2, -3), r) == "axes"
    assert r.axes == [10, -20, 30, -1, 2, -3]
    combined = devices.Reading()
    devices.parse(_report(1, 1, 2, 3, 4, 5, 6), combined)  # Compact, Wireless
    assert combined.axes == [1, 2, 3, 4, 5, 6]


def test_buttons_and_presses():
    r = devices.Reading()
    assert devices.parse(bytes([3, 0b101, 0]), r) == "buttons"
    assert r.buttons == 0b101
    assert devices.pressed(0b111, 0b101) == [1]
    assert devices.parse(bytes([9, 1, 2]), r) is None and devices.parse(b"", r) is None


def test_devices_are_known_or_read_as_multi_axis_controllers():
    assert devices.device_for(0x256F, 0xC635).name == "SpaceMouse Compact"
    other = devices.device_for(0x1234, 0x0001, *devices.MULTI_AXIS)
    assert other is not None and "1234:0001" in other.name
    assert devices.device_for(0x1234, 0x0001, 0x01, 0x02) is None  # a mouse


def test_device_frame_to_view_frame():
    # pushed down (device +z) is the view's -y; pulled toward you (device +y) is +z
    dev = devices.known(0x046D, 0xC626)
    assert devices.to_view(dev, [0, 0, 100, 0, 0, 0])[:3] == [0, -100, 0]
    assert devices.to_view(dev, [0, 50, 0, 0, 0, 0])[:3] == [0, 0, 50]


def test_dead_zone_curve_locks_dominant_and_invert():
    s = Settings()
    assert not np.any(motion.shaped([10, -15, 5, 0, 0, 0], s))  # inside the dead zone
    full = motion.shaped([350, 0, 0, 0, 0, -350], s)
    assert full[0] == pytest.approx(1.0) and full[5] == pytest.approx(-1.0)
    half = motion.shaped([175, 0, 0, 0, 0, 0], s)[0]
    assert 0 < half < 0.5  # gentler than linear near rest
    assert not np.any(motion.shaped([350] * 6, Settings(translate=False))[:3])
    one = motion.shaped([100, 300, 0, 0, 0, 50], Settings(dominant=True))
    assert np.count_nonzero(one) == 1 and one[1] > 0
    assert motion.shaped([350, 0, 0, 0, 0, 0], Settings(invert=(True,) + (False,) * 5))[0] < 0


CAM = Camera((0.0, 0.0, 10.0), (0.0, 0.0, 0.0), (0.0, 1.0, 0.0))


def test_object_mode_moves_the_camera_opposite_the_push():
    pushed = motion.move(CAM, [1, 0, 0, 0, 0, 0], 0.05)
    assert pushed.position[0] < 0 and pushed.focal_point[0] < 0  # model went right
    camera = motion.move(CAM, [1, 0, 0, 0, 0, 0], 0.05, settings=Settings(mode="camera"))
    assert camera.position[0] > 0


def test_pan_and_zoom_scale_with_the_distance():
    near = Camera((0, 0, 1.0), (0, 0, 0), (0, 1, 0))
    far = Camera((0, 0, 100.0), (0, 0, 0), (0, 1, 0))
    a = motion.move(near, [1, 0, 0, 0, 0, 0], 0.05).focal_point[0]
    b = motion.move(far, [1, 0, 0, 0, 0, 0], 0.05).focal_point[0]
    assert b == pytest.approx(100 * a)
    closer = motion.move(far, [0, 0, 1, 0, 0, 0], 0.05)  # pulled toward you
    assert 0 < closer.position[2] < 100 and closer.focal_point == far.focal_point


def test_rotation_turns_about_the_pivot_and_keeps_the_distance():
    pivot = (2.0, 0.0, 0.0)
    turned = motion.move(CAM, [0, 0, 0, 0, 1, 0], 0.05, pivot=pivot)  # twist about up
    p = np.asarray(turned.position)
    before = np.linalg.norm(np.subtract(CAM.position, pivot))
    assert np.linalg.norm(p - pivot) == pytest.approx(before)
    assert p[1] == pytest.approx(0.0, abs=1e-12)  # about up: stays level
    assert np.linalg.norm(turned.up) == pytest.approx(1.0)


def test_speed_follows_time_not_frames():
    one = motion.move(CAM, [0.5, 0, 0, 0, 0, 0], 0.04).focal_point[0]
    two = motion.move(motion.move(CAM, [0.5, 0, 0, 0, 0, 0], 0.02), [0.5, 0, 0, 0, 0, 0], 0.02)
    assert two.focal_point[0] == pytest.approx(one)
    stall = motion.move(CAM, [1, 0, 0, 0, 0, 0], 5.0).focal_point[0]  # a 5 s stall ...
    capped = motion.move(CAM, [1, 0, 0, 0, 0, 0], motion.MAX_STEP).focal_point[0]
    assert stall == pytest.approx(capped)  # ... moves as far as one longest step


def test_at_rest_the_camera_stays():
    assert motion.move(CAM, np.zeros(6), 0.05) == CAM


# --- the controller, with a fake device and window -------------------------------------


class _Action:
    def __init__(self):
        self.triggered = 0

    def trigger(self):
        self.triggered += 1


class _View:
    def __init__(self):
        self.triple = [(0.0, 0.0, 10.0), (0.0, 0.0, 0.0), (0.0, 1.0, 0.0)]
        self.view_bar = type("Bar", (), {"actions_by_name": {"fit": _Action(), "iso": _Action()}})()

    def camera(self):
        return self.triple

    def set_camera(self, triple):
        self.triple = triple

    def pivot(self):
        return np.zeros(3)


@pytest.fixture
def controller():
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication

    from puzzle.gui.spacemouse.controller import Controller

    QApplication.instance() or QApplication([])
    window = QObject()
    window.viewport = _View()
    return Controller(window)


def test_a_push_moves_the_view_and_rest_stops_it(controller):
    controller._got([350, 0, 0, 0, 0, 0], 0)
    assert controller.timer.isActive()
    controller._last -= 0.05  # 50 ms later
    controller._tick()
    assert controller.window.viewport.triple[1][0] < 0  # object mode: the model went right
    controller._got([0] * 6, 0)
    controller._tick()
    assert not controller.timer.isActive()


def test_buttons_run_the_chosen_actions(controller):
    from puzzle import settings

    actions = controller.window.viewport.view_bar.actions_by_name
    controller._got([0] * 6, 0b01)  # left: Fit all by default
    controller._got([0] * 6, 0b11)  # right too: Isometric
    assert actions["fit"].triggered == 1 and actions["iso"].triggered == 1
    settings.current().set("mouse3d.button_1", "")
    controller._got([0] * 6, 0)
    controller._got([0] * 6, 0b01)
    assert actions["fit"].triggered == 1  # Nothing


def test_switched_off_it_moves_nothing(controller):
    from puzzle import settings

    settings.current().set("mouse3d.enabled", False)
    controller._got([350, 0, 0, 0, 0, 0], 0)
    before = list(controller.window.viewport.triple)
    controller._tick()
    assert controller.window.viewport.triple == before and not controller.timer.isActive()


def test_the_reader_thread_reads_reports_until_unplugged(controller, monkeypatch):
    from puzzle.gui.spacemouse import hid

    reports = [_report(1, 100, 0, 0, 0, 0, 0)]

    class Fake:
        def __init__(self, path):
            pass

        def read(self, timeout_ms=0):
            if reports:
                return reports.pop()
            raise OSError("unplugged")

        def close(self):
            pass

    looks = iter([[hid.Found(devices.known(0x256F, 0xC635), b"p")]])  # found once, then none
    monkeypatch.setattr(hid, "find", lambda: next(looks, []))
    monkeypatch.setattr(hid, "Reader", Fake)
    got, names = [], []
    controller.reading.connect(lambda v, b: got.append(list(v)))
    controller.found.connect(names.append)
    controller._stop.clear()
    monkeypatch.setattr(controller._stop, "wait", lambda s: controller._stop.set())
    controller._run()  # this thread: reads, then the device goes, then it looks again
    from PySide6.QtWidgets import QApplication

    QApplication.processEvents()
    assert got[0][0] == 100 and got[-1] == [0] * 6  # released when it went
    assert names == ["SpaceMouse Compact", ""]
