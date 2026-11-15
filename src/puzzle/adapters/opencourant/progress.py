"""OpenCourant's progress (ADR 0085 decision 6): the Engine's cycle lines on stdout.

`NC= 1200 T= 1.2E-04 DT= 9.9E-08 ERR= 0.3% DM/M= 0.0E+00` every printed cycle: the
cycle, the time, the time step, the energy error and the mass added over the mass. The
step's fraction is the time over the end time. The Starter's and Engine's error blocks
(`ERROR ID : n` then its description) become messages; a run ending in `ERROR
TERMINATION` is a Diverged.
"""

from __future__ import annotations

import re

from puzzle.progress import Converged, Diverged, Event, Increment, Message, Parser, Step

_CYCLE = re.compile(r"NC=\s*(\d+)\s+T=\s*(\S+)\s+DT=\s*(\S+)\s+ERR=\s*(\S+)%\s+DM/M=\s*(\S+)")
_ERROR = re.compile(r"\bERROR ID\s*:\s*(\d+)")


class OpenCourantProgress(Parser):
    def __init__(self, end_time: float, step: int = 1, of: int = 1) -> None:
        super().__init__(step, of)
        self.end_time = end_time
        self.started = False
        self.pending: list[str] | None = None  # an error block being read
        self.code = ""
        self.cycle, self.time, self.error = 0, 0.0, 0.0

    def feed(self, line: str) -> list[Event]:
        out: list[Event] = []
        text = line.rstrip()
        if not self.started and ("STARTER" in text or "NC=" in text):
            self.started = True
            out.append(Step(number=self.step, of=self.of, type="explicit", end_time=self.end_time))
        m = _CYCLE.search(text)
        if m:
            self.cycle, self.time = int(m.group(1)), float(m.group(2))
            self.error = float(m.group(4))
            out.append(Increment(step=self.step, number=self.cycle, size=float(m.group(3)),
                                 time=self.time, end_time=self.end_time))  # fmt: skip
            return out
        e = _ERROR.search(text)
        if e:
            self.pending, self.code = [], e.group(1)
            return out
        if self.pending is not None:
            if text.strip() and not text.strip().startswith(("**", "--", "DESCRIPTION")):
                self.pending.append(text.strip())
            if len(self.pending) >= 2 or (not text.strip() and self.pending):
                out.append(Message(level="error", text=" ".join(self.pending), code=self.code))
                self.pending = None
        if "ERROR TERMINATION" in text:
            out.append(
                Diverged(
                    step=self.step,
                    increment=self.cycle,
                    reason="OpenCourant stopped with an error",
                    final=True,
                )
            )
        return out

    def finish(self, ok: bool) -> list[Event]:
        if ok:
            return [
                Converged(
                    step=self.step, increment=self.cycle, iterations=self.cycle, time=self.time
                )
            ]
        return []
