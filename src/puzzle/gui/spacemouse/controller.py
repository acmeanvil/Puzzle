"""The 3D mouse in the window (ADR 0069): a reader thread, the view moved on the GUI's.

`Controller.start()` looks for a device (and again every few seconds, so one plugged in
later is found), reads it on a daemon thread and posts each reading to the GUI thread
through a Qt signal. A timer there turns the latest reading into one camera move per
frame (`motion.move`, scaled by the time since the last), about the view's pivot, and
puts it in the view (`Viewport.set_camera`). Buttons run the window's actions by name
(Settings > 3D mouse). Settings are read afresh on each move, so a change applies at once.
"""

from __future__ import annotations

import logging
import threading
import time

from PySide6.QtCore import QObject, QTimer, Signal

from puzzle.gui.spacemouse import devices, hid, motion

log = logging.getLogger("puzzle.gui.spacemouse")
FRAME_MS = 16  # about 60 moves a second while the cap is off centre
LOOK_EVERY_S = 3.0  # for a device plugged in after Puzzle started


def settings_now() -> tuple[motion.Settings, bool, list[str]]:
    """The motion settings, whether the device is on, and the buttons' actions."""
    from puzzle.settings import current

    s = current()
    how = s.get("mouse3d.motion")
    invert = [False] * 6
    invert[2] = bool(s.get("mouse3d.invert_zoom"))
    found = motion.Settings(
        speed=float(s.get("mouse3d.speed")),
        mode=s.get("mouse3d.mode"),
        translate=how != "rotate",
        rotate=how != "translate",
        dominant=bool(s.get("mouse3d.dominant")),
        invert=tuple(invert),
    )
    return found, bool(s.get("mouse3d.enabled")), [s.get("mouse3d.button_1"),
                                                    s.get("mouse3d.button_2")]  # fmt: skip


class Controller(QObject):
    reading = Signal(object, int)  # view-frame counts, buttons
    found = Signal(str)  # the device's name, or "" when it went away

    def __init__(self, window) -> None:
        super().__init__(window)
        self.window = window
        self.device: devices.Device | None = None
        self.values = [0] * 6
        self.buttons = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last = None
        self._pivot = None
        self.filter = motion.Filter()  # smoothing and a sticky dead zone
        self.timer = QTimer(self, interval=FRAME_MS)
        self.timer.timeout.connect(self._tick)
        self.reading.connect(self._got)

    # --- the device ---------------------------------------------------------------------

    def start(self) -> bool:
        """Read the first device found, looking again every few seconds meanwhile."""
        if self._thread is not None and self._thread.is_alive():
            return True
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="puzzle-3d-mouse", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        self.timer.stop()
        # Wait out the reader's current read (100 ms at most), so nothing is emitted
        # into a window being closed ("Signal source has been deleted" in the log).
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.0)

    def _emit(self, signal, *args) -> None:
        """Emit from the reader thread, unless stopping or the Qt side is gone."""
        if self._stop.is_set():
            return
        try:
            signal.emit(*args)
        except RuntimeError:  # the window was deleted meanwhile
            self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            _, on, _ = settings_now()
            found = hid.find() if on else []
            if not found:
                self._stop.wait(LOOK_EVERY_S)
                continue
            first = found[0]
            try:
                reader = hid.Reader(first.path)
            except OSError:
                self._stop.wait(LOOK_EVERY_S)
                continue
            self.device = first.device
            self._emit(self.found, first.device.name)
            dev = first.device
            log.warning("3D mouse found: %s (%04x:%04x)", dev.name, dev.vendor, dev.product)
            state, unknown = devices.Reading(), set()
            try:
                while not self._stop.is_set():
                    report = reader.read()
                    if report and devices.parse(report, state):
                        self._emit(
                            self.reading, devices.to_view(first.device, state.axes), state.buttons
                        )
                    elif report and len(unknown) < 32 and (report[0], len(report)) not in unknown:
                        # A report Puzzle doesn't read yet (the Enterprise's extra keys?):
                        # into the log once, so it can be mapped (Help > Log).
                        unknown.add((report[0], len(report)))
                        log.warning("3D mouse: report %d not read (%d bytes): %s",
                                    report[0], len(report), report.hex(" "))  # fmt: skip
            except OSError:  # unplugged
                pass
            finally:
                reader.close()
                self.device = None
                self._emit(self.found, "")
                self._emit(self.reading, [0] * 6, 0)

    # --- the GUI thread -------------------------------------------------------------------

    def _got(self, values, buttons: int) -> None:
        before, self.buttons = self.buttons, buttons
        for button in devices.pressed(buttons, before):
            self.press(button)
        self.values = list(values)
        if any(self.values) and not self.timer.isActive():
            self._last = time.monotonic()
            self._pivot = self.window.viewport.pivot()  # held while the cap moves
            self.timer.start()

    def press(self, button: int) -> None:
        """A button went down: its action from Settings, or a Pro's named key."""
        _, on, actions = settings_now()
        if not on:
            return
        name = actions[button] if button < len(actions) else ""
        if self.device is not None and button < len(self.device.buttons):
            named = {"Fit": "fit", "Top": "+z", "Right": "+x", "Front": "-y"}
            name = named.get(self.device.buttons[button], name) if button >= 2 else name
        action = self.window.viewport.view_bar.actions_by_name.get(name) if name else None
        if action is not None:
            action.trigger()

    def _tick(self) -> None:
        settings, on, _ = settings_now()
        now = time.monotonic()
        dt, self._last = now - (self._last or now), now
        values = self.filter(self.values, dt, settings) if on else None
        if values is None or (not values.any() and self.filter.resting()):
            self.timer.stop()
            self.filter = motion.Filter()
            return
        view = self.window.viewport
        triple = view.camera()
        if triple is None or not values.any():
            return
        scale = view.parallel_scale() if hasattr(view, "parallel_scale") else None
        camera = motion.Camera.of(triple, scale)
        moved = motion.move(camera, values, dt, self._pivot, settings)
        view.set_camera(moved.triple(), moved.parallel_scale)
