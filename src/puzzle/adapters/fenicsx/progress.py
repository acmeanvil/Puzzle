"""FEniCSx's progress (ADR 0054): `study.py` prints its events itself, as tagged JSON
lines (`PUZZLE-EVENT {...}`), so this parser only reads them back, numbers them by
the job's step, and turns the script's own refusals (SystemExit text, tracebacks) into
messages. Tracked probes are handed to the script in `trackers.json`, written when
the trackers are set (before the job starts)."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from puzzle.progress import Diverged, Event, Message, Parser, Step, from_dict

TAG = "PUZZLE-EVENT "
# What study.py can follow while solving (it computes no reactions as it goes).
FOLLOWED = {"displacement", "temperature"}


class FenicsxProgress(Parser):
    def __init__(self, input_file: Path, step: int = 1, of: int = 1) -> None:
        self.folder = input_file.parent  # before the base sets the trackers
        self.notes: list[str] = []
        super().__init__(step, of)
        self.errors: list[str] = []
        self.last = ""
        self.traceback = False

    # Setting the trackers writes them for study.py (run_study sets them before the job).
    @property
    def trackers(self) -> list:
        return self._trackers

    @trackers.setter
    def trackers(self, value: list) -> None:
        self._trackers = list(value)
        followed = [t for t in self._trackers if t.field in FOLLOWED and t.points is not None]
        self.notes = [
            f"{t.name}: FEniCSx follows displacement and temperature only while solving"
            for t in self._trackers
            if not any(t is f for f in followed)
        ]
        path = self.folder / "trackers.json"
        if followed:
            path.write_text(json.dumps([
                {"name": t.name, "field": t.field, "what": t.what, "reduce": t.reduce,
                 "unit": t.unit, "points": t.points.tolist()}
                for t in followed
            ]), encoding="utf-8")  # fmt: skip
        elif path.exists():
            path.unlink()

    def feed(self, line: str) -> list[Event]:
        out: list[Event] = [Message(level="info", text=f"Not tracked: {n}") for n in self.notes]
        self.notes = []
        text = line.rstrip("\n")
        if text.startswith(TAG):
            e = from_dict(json.loads(text[len(TAG) :]))
            if e is None:
                return out
            if isinstance(e, Step):
                e = dataclasses.replace(e, number=self.step, of=self.of)
            elif hasattr(e, "step"):
                e = dataclasses.replace(e, step=self.step)
            return [*out, e]
        stripped = text.strip()
        if stripped.startswith("Traceback"):
            self.traceback = True
        if stripped:
            self.last = stripped
        if any(w in stripped for w in ("did not converge", "Error:", "error:")):
            self.errors.append(stripped)
            out.append(Message(level="error", text=stripped, original=stripped, line=self.lines))
        return out

    def finish(self, ok: bool) -> list[Event]:
        if ok:
            return []
        out: list[Event] = []
        reason = self.errors[-1] if self.errors else self.last
        if reason and not self.errors:  # SystemExit's text, or a traceback's last line
            out.append(Message(level="error", text=reason, original=reason, line=self.lines))
        out.append(Diverged(step=self.step, reason=reason or "study.py failed", final=True))
        return out
