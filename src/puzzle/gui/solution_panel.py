"""The Solution panel (ADR 0054), a tab beside Output: how a solve is going and how it
went. A progress bar with the time left, the stages with their times, the convergence
plot (each residual against its criterion on a log scale, increments that converged,
cut-backs and new steps marked), time against increment, result trackers, the solver's
messages in English, what the solve uses, a stall warning and a stop button.

It shows the events of the run in progress as they come, or a stored run's history
(`show_history`), drawn by the same code."""

from __future__ import annotations

import math
import threading
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from puzzle import progress
from puzzle.gui.theme import current as tokens
from puzzle.gui.theme import role

STAGES = {"mesh": "Mesh", "transfers": "Transfers", "write": "Write", "solve": "Solve",
          "read": "Read", "store": "Store"}  # fmt: skip
NAMES = {  # residual names as people read them
    "force": "Force",
    "displacement": "Displacement",
    "flux": "Heat flux",
    "temperature": "Temperature",
    "relative": "Relative residual",
    "absolute": "Largest residual",
    "reference": "Reference residual",
    "component": "Component residual",
    "contact pressure": "Contact pressure",
    "contact geometry": "Contact geometry",
    "friction": "Friction",
}


def _series_colours() -> list[QColor]:
    t = tokens()
    return [QColor(c) for c in (t.accent, t.done, t.warning, *t.levels, t.error)]


class Plot(QWidget):
    """A small line plot drawn with the theme's colours: series of (x, y), dashed
    horizontal lines (criteria), and vertical marks (increments, cut-backs, steps)."""

    def __init__(self, x_label: str, y_label: str, log: bool = False) -> None:
        super().__init__()
        self.x_label, self.y_label, self.log = x_label, y_label, log
        self.series: list[tuple[str, list[tuple[float, float]], QColor]] = []
        self.lines: list[tuple[str, float, QColor]] = []  # name, y, colour (dashed)
        self.marks: list[tuple[float, str]] = []  # x, kind: converged, cutback, step
        self.stairs = False
        self.empty = "Nothing to plot yet"
        self.setMinimumSize(160, 110)

    def set_data(self, series, lines=(), marks=(), empty: str | None = None) -> None:
        def keep(y):
            return math.isfinite(y) and (not self.log or y > 0)

        self.series = [(n, [(x, y) for x, y in pts if keep(y)], c) for n, pts, c in series]
        self.lines = [(n, y, c) for n, y, c in lines if keep(y)]
        self.marks = list(marks)
        if empty is not None:
            self.empty = empty
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt's name
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(t.sunken))
        points = [pt for _, pts, _ in self.series for pt in pts]
        if not points:
            p.setPen(QColor(t.muted))
            p.drawText(
                QRectF(self.rect()).adjusted(12, 0, -12, 0),
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                self.empty,
            )
            return
        ty = math.log10 if self.log else (lambda v: v)
        xs = [x for x, _ in points]
        ys = [ty(y) for _, y in points] + [ty(y) for _, y, _ in self.lines]
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        if self.log:
            y0, y1 = math.floor(y0), math.ceil(y1)
        elif y0 > 0:
            y0 = 0.0
        x1 = x1 if x1 > x0 else x0 + 1
        y1 = y1 if y1 > y0 else y0 + 1
        left, top, right, bottom = 72, 10, 10, 32
        w, h = max(self.width() - left - right, 10), max(self.height() - top - bottom, 10)

        def at(x, y):
            return QPointF(left + (x - x0) / (x1 - x0) * w, top + h - (ty(y) - y0) / (y1 - y0) * h)

        def shown(v):
            return f"1e{int(v)}" if self.log else f"{v:.3g}"

        p.setPen(QPen(QColor(t.border), 1))
        p.drawRect(QRectF(left, top, w, h))
        if self.log:
            every = max(1, int(y1 - y0) // 6)  # a label every few decades
            for d in range(int(y0) + 1, int(y1)):
                gy = top + h - (d - y0) / (y1 - y0) * h
                p.setPen(QPen(QColor(t.border), 1))
                p.drawLine(QPointF(left, gy), QPointF(left + w, gy))
                if (d - int(y0)) % every == 0 and gy > top + 16 and gy < top + h - 12:
                    p.setPen(QColor(t.muted))
                    p.drawText(22, int(gy) + 4, f"1e{d}")
        kinds = {"converged": t.done, "cutback": t.error, "step": t.accent, "failed": t.error}
        for x, kind in self.marks:
            pen = QPen(QColor(kinds.get(kind, t.muted)), 1)
            pen.setStyle(Qt.PenStyle.DashLine if kind == "cutback" else Qt.PenStyle.DotLine)
            if kind == "step":
                pen.setWidth(2)
            p.setPen(pen)
            gx = left + (x - x0) / (x1 - x0) * w
            p.drawLine(QPointF(gx, top), QPointF(gx, top + h))
        p.setPen(QColor(t.muted))
        p.drawText(22, top + 10, shown(y1))
        p.drawText(22, top + h, shown(y0))
        p.drawText(left, top + h + 14, f"{x0:.4g}")
        p.drawText(left + w - 40, top + h + 14, f"{x1:.4g}")
        p.drawText(left + w // 2 - 50, top + h + 28, self.x_label)
        p.save()
        p.translate(14, top + h // 2 + 50)
        p.rotate(-90)
        p.drawText(0, 0, self.y_label)
        p.restore()
        for _name, y, colour in self.lines:
            pen = QPen(colour, 1)
            pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            gy = at(x0, y).y()
            p.drawLine(QPointF(left, gy), QPointF(left + w, gy))
        for _name, pts, colour in self.series:
            p.setPen(QPen(colour, 2))
            q = [at(x, y) for x, y in pts]
            for a, b in zip(q, q[1:], strict=False):
                if self.stairs:
                    p.drawLine(a, QPointF(b.x(), a.y()))
                    p.drawLine(QPointF(b.x(), a.y()), b)
                else:
                    p.drawLine(a, b)
            if len(q) <= 60:
                for c in q:
                    p.drawEllipse(c, 2.2, 2.2)
        named = [(n, c) for n, _, c in self.series if n]
        if named:  # the legend, top right, on its own ground
            fm = p.fontMetrics()
            lw = max(fm.horizontalAdvance(n) for n, _ in named) + 26
            box = QRectF(left + w - lw - 6, top + 6, lw, 16 * len(named) + 6)
            p.setPen(QPen(QColor(t.border), 1))
            p.setBrush(QColor(t.sunken))
            p.drawRect(box)
            p.setBrush(Qt.BrushStyle.NoBrush)
            for k, (n, c) in enumerate(named):
                y = box.top() + 14 + 16 * k
                p.setPen(QPen(c, 2))
                p.drawLine(QPointF(box.left() + 5, y - 4), QPointF(box.left() + 17, y - 4))
                p.setPen(QColor(t.text))
                p.drawText(QPointF(box.left() + 21, y), n)


class SolutionPanel(QWidget):
    def __init__(self, window) -> None:
        super().__init__()
        self.window = window
        self.events: list[progress.Event] = []
        self.cancel: threading.Event | None = None
        self.running = False
        self.started = 0.0
        self.last_event = 0.0
        self.title = QLabel("No solve yet: run a study to see its progress here")
        role(self.title, "title")
        self.stages = QLabel("")
        role(self.stages, "muted")
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(True)
        self.bar.setFormat("")
        self.left = QLabel("")
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.stall = QLabel("")
        role(self.stall, "error")
        self.stall.hide()
        self.stop = QPushButton("Stop")
        self.stop.setToolTip("Stop the solver; what it solved so far is not kept")
        self.stop.setEnabled(False)
        self.stop.clicked.connect(self.stop_run)
        self.convergence = Plot("Iteration (all increments)", "Residual / criterion", log=True)
        self.convergence.empty = "No iterations: a linear solve converges in one"
        self.times = Plot("Increment", "Step time")
        self.times.stairs = True
        self.times.empty = "No increments yet"
        self.trackers = Plot("Step time", "Tracked value")
        self.trackers.empty = "No result trackers: tick Track on a probe (Results) to follow it"
        self.messages = QListWidget()
        self.messages.setWordWrap(True)  # long messages wrap rather than widen the panel
        self.messages.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.messages.setMinimumWidth(200)
        self.convergence.setMinimumWidth(260)
        self.resources = QLabel("")
        role(self.resources, "muted")

        top = QHBoxLayout()
        top.addWidget(self.title, 1)
        top.addWidget(self.left)
        top.addWidget(self.stop)
        side = QSplitter(Qt.Orientation.Vertical)
        side.addWidget(self.times)
        side.addWidget(self.trackers)
        plots = QSplitter(Qt.Orientation.Horizontal)
        plots.addWidget(self.convergence)
        plots.addWidget(side)
        plots.setStretchFactor(0, 3)
        plots.setStretchFactor(1, 2)
        body = QSplitter(Qt.Orientation.Horizontal)
        body.addWidget(plots)
        right = QWidget()
        column = QVBoxLayout(right)
        column.setContentsMargins(0, 0, 0, 0)
        column.addWidget(QLabel("Messages"))
        column.addWidget(self.messages, 1)
        column.addWidget(self.resources)
        right.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        body.addWidget(right)
        body.setStretchFactor(0, 3)
        body.setStretchFactor(1, 1)
        self.body, self.plots_split = body, plots
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.bar)
        layout.addWidget(self.stages)
        layout.addWidget(self.status)
        layout.addWidget(self.stall)
        layout.addWidget(body, 1)
        self.messages.itemDoubleClicked.connect(self._to_log)
        self.timer = QTimer(self)
        self.timer.setInterval(5000)
        self.timer.timeout.connect(self._tick)
        self._redraw_pending = False

    # --- a run in progress --------------------------------------------------------

    def start(self, title: str, cancel: threading.Event | None = None) -> None:
        self.events = []
        self.cancel = cancel
        self.running = True
        self.started = self.last_event = time.monotonic()
        self.title.setText(title)
        self.stop.setEnabled(cancel is not None)
        self.stall.hide()
        self.messages.clear()
        self._redraw()
        self.timer.start()

    def add(self, event: progress.Event) -> None:
        """One event from the running solve (on the GUI thread)."""
        self.events.append(event)
        self.last_event = time.monotonic()
        self.stall.hide()
        if isinstance(event, progress.Message) and event.level in ("warning", "error"):
            self._message(event)
        if not self._redraw_pending:  # many iterations a second: draw at most 5 times
            self._redraw_pending = True
            QTimer.singleShot(200, self._redraw)

    def finish(self, ok: bool, note: str = "") -> None:
        self.running = False
        self.timer.stop()
        self.stop.setEnabled(False)
        self.stall.hide()
        self._redraw()
        if note:
            self.status.setText(note)
        if ok:
            self.bar.setValue(1000)
            self.left.setText("Done")

    def stop_run(self) -> None:
        if self.cancel is not None:
            self.cancel.set()
            self.stop.setEnabled(False)
            self.status.setText("Stopping…")

    def _tick(self) -> None:
        from puzzle.settings import current

        self._redraw()
        minutes = current().get("solvers.stall_minutes") or 0
        quiet = time.monotonic() - self.last_event
        if self.running and minutes and quiet > minutes * 60:
            self.stall.setText(
                f"Nothing reported for {quiet / 60:.0f} min: the solver may be stuck "
                "(it keeps running unless you stop it)"
            )
            self.stall.show()

    # --- a stored run ----------------------------------------------------------------

    def show_history(self, title: str, events: list[progress.Event]) -> None:
        self.running = False
        self.timer.stop()
        self.cancel = None
        self.stop.setEnabled(False)
        self.events = list(events)
        self.title.setText(title)
        self.messages.clear()
        for e in self.events:
            if isinstance(e, progress.Message) and e.level in ("warning", "error"):
                self._message(e)
        self._redraw()
        if not self.events:
            self.status.setText("This run was kept without its progress (an older Puzzle)")

    # --- drawing ----------------------------------------------------------------------

    def _message(self, e: progress.Message) -> None:
        item = QListWidgetItem(("⚠ " if e.level == "warning" else "✖ ") + e.text)
        tip = e.original if e.original and e.original != e.text else ""
        if e.line:
            tip = (
                tip + "\n" if tip else ""
            ) + f"Log line {e.line} (double-click to find it in Output)"
        item.setToolTip(tip)
        item.setData(Qt.ItemDataRole.UserRole, e.original or e.text)
        self.messages.addItem(item)
        if self.messages.count() > 500:
            self.messages.takeItem(0)

    def _to_log(self, item) -> None:
        text = item.data(Qt.ItemDataRole.UserRole)
        log = getattr(self.window, "log", None)
        if log is None or not text:
            return
        self.window.output_tabs.setCurrentWidget(log)
        doc = log.document()
        found = doc.find(text.split("  ")[0][:60])
        if not found.isNull():
            log.setTextCursor(found)
            log.centerCursor()

    def _redraw(self) -> None:
        self._redraw_pending = False
        events = self.events
        s = progress.state(events)
        frac = s.fraction()
        if frac is None:
            self.bar.setRange(0, 0 if self.running else 1000)  # busy until it can tell
            self.bar.setFormat("")
        else:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(frac * 1000))
            self.bar.setFormat(f"{frac * 100:.0f} %")
        if self.running and frac:
            spent = time.monotonic() - self.started
            solving = [e.t for e in events if isinstance(e, progress.Stage) and e.name == "solve"]
            since = spent - (solving[0] if solving else 0.0)
            rest = since / frac * (1 - frac) if frac > 0.01 else None
            self.left.setText(f"about {_duration(rest)} left" if rest else "")
        elif self.running:
            self.left.setText(f"{_duration(time.monotonic() - self.started)} so far")
        done = [
            (e.name, e.seconds)
            for e in events
            if isinstance(e, progress.Stage) and e.state == "end"
        ]
        started = [e.name for e in events if isinstance(e, progress.Stage) and e.state == "start"]
        now = next((n for n in reversed(started) if n not in dict(done)), None)
        parts = [f"{STAGES.get(n, n)} {v:.1f} s" for n, v in done if v is not None]
        if now and self.running:
            parts.append(f"{STAGES.get(now, now)}…")
        self.stages.setText("  ·  ".join(parts))
        self.status.setText(s.line() if (s.steps or s.failed) else "")
        bits = []
        if s.equations:
            bits.append(f"{s.equations:,} equations")
        if s.memory_mb:
            bits.append(f"{s.memory_mb:,.0f} MB")
        if s.cores:
            bits.append(f"{s.cores} cores")
        if s.warnings or s.errors:
            bits.append(f"{s.warnings} warnings, {s.errors} errors")
        self.resources.setText(" · ".join(bits))
        self._plots(events)

    def _plots(self, events) -> None:
        colours = _series_colours()
        by_name: dict[str, list[tuple[float, float]]] = {}
        criteria: dict[str, float] = {}
        marks: list[tuple[float, str]] = []
        n = 0
        steps_seen: set[int] = set()
        incs: list[tuple[float, float]] = []
        cut_incs: list[tuple[float, float]] = []
        count = 0
        for e in events:
            if isinstance(e, progress.Iteration):
                n += 1
                for name, value in e.residuals.items():
                    crit = e.criteria.get(name)
                    if crit:  # against its criterion: below 1 has converged
                        by_name.setdefault(name, []).append((n, value / crit))
                        criteria[name] = crit
            elif isinstance(e, progress.Increment):
                count += 1
                if e.cutback or e.attempt > 1:
                    marks.append((n + 0.5, "cutback"))
                if e.time is not None:
                    (cut_incs if e.cutback or e.attempt > 1 else incs).append((count, e.time))
            elif isinstance(e, progress.Converged):
                marks.append((n + 0.5, "converged"))
            elif isinstance(e, progress.Step):
                if steps_seen and e.number not in steps_seen:
                    marks.append((n + 0.5, "step"))
                steps_seen.add(e.number)
        series = [
            (NAMES.get(k, k.capitalize()), pts, colours[i % len(colours)])
            for i, (k, pts) in enumerate(by_name.items())
        ]
        line = [("criterion", 1.0, QColor(tokens().muted))] if series else []
        self.convergence.set_data(series, line, marks if len(marks) < 2000 else [])
        times = [("Step time", sorted(incs + cut_incs), colours[0])] if incs or cut_incs else []
        if cut_incs:
            times.append(("Cut back", cut_incs, QColor(tokens().error)))
        self.times.set_data(times)
        tracked: dict[str, list[tuple[float, float]]] = {}
        for e in events:
            if isinstance(e, progress.Tracked) and e.time is not None:
                tracked.setdefault(f"{e.name}" + (f" ({e.unit})" if e.unit else ""), []).append(
                    (e.time, e.value)
                )
        self.trackers.set_data(
            [(k, v, colours[i % len(colours)]) for i, (k, v) in enumerate(tracked.items())]
        )


def _duration(s: float | None) -> str:
    if s is None:
        return ""
    if s < 90:
        return f"{s:.0f} s"
    if s < 5400:
        return f"{s / 60:.0f} min"
    return f"{s / 3600:.1f} h"


def save_png(events: list[progress.Event], title: str, path) -> None:
    """The Solution panel for a run's events, saved as a PNG (`puzzle progress --plot`);
    works without a display (Qt's offscreen platform)."""
    import os

    from PySide6.QtWidgets import QApplication

    if QApplication.instance() is None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])  # noqa: F841 - kept alive
    panel = SolutionPanel(None)
    panel.stop.hide()
    panel.resize(1200, 640)
    panel.body.setSizes([850, 350])
    panel.plots_split.setSizes([520, 330])
    panel.show_history(title, events)
    panel.finish(not progress.state(events).failed)
    if not panel.grab().save(str(path)):
        raise OSError(f"could not write {path}")
    panel.deleteLater()
