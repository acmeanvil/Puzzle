"""CalculiX's progress (ADR 0054): steps, increments, attempts and iterations from its
log. The log carries what `.sta` and `.cvg` hold (step, increment, attempt, step time;
per iteration the largest residual against the time-averaged force, the largest
correction against the increment, the contact elements), and a log reaches us from a
container as well as from a native run, so it is the one source read."""

from __future__ import annotations

import contextlib
import re
from pathlib import Path

import numpy as np

from puzzle.progress import (
    Converged,
    Diverged,
    Event,
    Increment,
    Iteration,
    Message,
    Parser,
    Resource,
    Step,
    Tracked,
)

# CalculiX's defaults for *CONTROLS, PARAMETERS=FIELD: R_n (residual over the
# time-averaged force) and C_n (correction over the increment).
FORCE_CRITERION, CORRECTION_CRITERION = 0.005, 0.01

_NUM = r"([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)"
_STEP = re.compile(r"^\s*STEP\s+(\d+)\s*$")
_INC = re.compile(r"^\s*increment\s+(\d+)\s+attempt\s+(\d+)")
_SIZE = re.compile(r"^\s*increment size\s*=\s*" + _NUM)
_STEP_TIME = re.compile(r"^\s*actual step time\s*=\s*" + _NUM)
_ITER = re.compile(r"^\s*iteration\s+(\d+)\s*$")
_CONTACT = re.compile(r"Number of contact spring elements\s*=\s*(\d+)")
_AVG = re.compile(r"^\s*time avg\. (forc|flux)\w*\s*=\s*" + _NUM)
_RESID = re.compile(r"^\s*largest residual (force|flux)\s*=\s*" + _NUM)
_INCR = re.compile(r"^\s*largest increment of (disp|temp)\w*\s*=\s*" + _NUM)
_CORR = re.compile(r"^\s*largest correction to (disp|temp)\w*\s*=\s*" + _NUM)
_CPUS = re.compile(r"Using up to (\d+) cpu")
_FINAL = ("too many cutbacks", "smaller than minimum", "increment size smaller")


def steps_of(deck: str) -> list[tuple[int, str, float | None]]:
    """Each *STEP of a deck: (Puzzle's step, 0 for a helper; procedure; time period,
    None when it has none, as *FREQUENCY)."""
    out: list[tuple[int, str, float | None]] = []
    lines = deck.splitlines()
    tag = None
    for i, line in enumerate(lines):
        if line.startswith("** puzzle step"):
            tag = int(line.split()[-1])
        upper = line.strip().upper()
        if upper.startswith("*STEP"):
            out.append((tag if tag is not None else len(out) + 1, "", None))
            tag = None
        elif out and not out[-1][1] and upper.startswith(
            ("*STATIC", "*DYNAMIC", "*HEAT TRANSFER", "*MODAL DYNAMIC", "*FREQUENCY",
             "*BUCKLE", "*STEADY STATE DYNAMICS", "*COUPLED")
        ):  # fmt: skip
            name = upper.split(",")[0].lstrip("*").lower()
            period: float | None = None
            if name in ("static", "dynamic", "heat transfer", "modal dynamic", "coupled"):
                period = 1.0
                data = lines[i + 1].strip() if i + 1 < len(lines) else ""
                if data and not data.startswith("*"):
                    parts = [p.strip() for p in data.split(",")]
                    if len(parts) > 1 and parts[1]:
                        with contextlib.suppress(ValueError):
                            period = float(parts[1])
                if "STEADY STATE" in upper:
                    period = 1.0
            out[-1] = (out[-1][0], name, period)
    return out


def criteria_of(deck: str) -> tuple[float, float]:
    lines = deck.splitlines()
    for i, line in enumerate(lines):
        if line.upper().replace(" ", "").startswith("*CONTROLS,PARAMETERS=FIELD") and i + 1 < len(
            lines
        ):
            try:
                parts = [float(p) for p in lines[i + 1].split(",")[:2] if p.strip()]
            except ValueError:
                break
            if len(parts) == 2:
                return parts[0], parts[1]
    return FORCE_CRITERION, CORRECTION_CRITERION


# .frd blocks the trackers read (puzzle.trackers.FIELDS), by CalculiX's name.
FRD_FIELDS = {"DISP": "displacement", "NDTEMP": "temperature", "FORC": "reaction_force"}


class CalculixProgress(Parser):
    def __init__(self, input_file: Path, step: int = 1, of: int = 1) -> None:
        super().__init__(step, of)
        self.frd = input_file.with_suffix(".frd")
        self.frd_offset = 0
        self.frd_text = ""  # read but not yet a whole block
        self.frd_step = 0
        self.frd_increment = 0
        self.frd_time: float | None = None
        deck = input_file.read_text(errors="replace") if input_file.exists() else ""
        self.steps = steps_of(deck)
        self.force_crit, self.corr_crit = criteria_of(deck)
        own = [s for s, _, _ in self.steps if s]
        self.total = max(own) if own else max(len(self.steps), 1)
        self.ccx_step = 0
        self.increment = self.attempt = 0
        self.size = self.step_time = None
        self.iteration = 0
        self.contact = None
        self.avg = self.resid = self.incr = None
        self.unit = ("force", "displacement")
        self.equations_next = False
        self.cores_seen = False
        self.equations = 0

    def _puzzle_step(self) -> tuple[int, str, float | None]:
        if 1 <= self.ccx_step <= len(self.steps):
            return self.steps[self.ccx_step - 1]
        return (self.ccx_step, "", None)

    def feed(self, line: str) -> list[Event]:
        if self.equations_next:
            self.equations_next = False
            if line.strip().isdigit() and int(line.strip()) != self.equations:
                self.equations = int(line.strip())
                return [Resource(equations=self.equations)]
        if line.strip() == "number of equations":
            self.equations_next = True
            return []
        if (m := _STEP.match(line)) is not None:
            self.ccx_step = int(m[1])
            number, procedure, period = self._puzzle_step()
            self.increment = self.iteration = 0
            # A helper step (0: a stored *FREQUENCY, a replayed prestress) shows as the
            # step it serves.
            return [Step(number=max(number, 1), of=self.total, type=procedure, end_time=period)]
        if (m := _INC.match(line)) is not None:
            self.increment, self.attempt = int(m[1]), int(m[2])
            self.iteration, self.size, self.step_time = 0, None, None
            return []
        if (m := _SIZE.match(line)) is not None:
            self.size = float(m[1])
            return []
        if (m := _STEP_TIME.match(line)) is not None:
            self.step_time = float(m[1])
            number, _, period = self._puzzle_step()
            return [
                Increment(
                    step=max(number, 1),
                    number=self.increment,
                    attempt=self.attempt,
                    size=self.size,
                    time=self.step_time,
                    end_time=period,
                    cutback=self.attempt > 1,
                    level=self.attempt,
                )
            ]
        if (m := _ITER.match(line)) is not None:
            self.iteration = int(m[1])
            self.avg = self.resid = self.incr = None
            return []
        if (m := _CONTACT.search(line)) is not None:
            self.contact = int(m[1])
            return []
        if (m := _AVG.match(line)) is not None:
            self.avg = float(m[2])
            self.unit = ("force", "displacement") if m[1] == "forc" else ("flux", "temperature")
            return []
        if (m := _RESID.match(line)) is not None:
            self.resid = float(m[2])
            return []
        if (m := _INCR.match(line)) is not None:
            self.incr = float(m[2])
            return []
        if (m := _CORR.match(line)) is not None:
            corr = float(m[2])
            residuals: dict[str, float] = {}
            if self.resid is not None and self.avg:
                residuals[self.unit[0]] = abs(self.resid) / abs(self.avg)
            if self.incr:
                residuals[self.unit[1]] = abs(corr) / abs(self.incr)
            if not residuals:
                return []
            number, _, _ = self._puzzle_step()
            return [
                Iteration(
                    step=max(number, 1),
                    increment=self.increment,
                    number=self.iteration or 1,
                    type="contact" if self.contact is not None else "nonlinear",
                    residuals=residuals,
                    criteria={self.unit[0]: self.force_crit, self.unit[1]: self.corr_crit},
                    contact=self.contact,
                )
            ]
        stripped = line.strip()
        if stripped == "convergence" or stripped.startswith("convergence;"):
            number, _, _ = self._puzzle_step()
            return [
                Converged(
                    step=max(number, 1),
                    increment=self.increment,
                    iterations=self.iteration,
                    time=self.step_time,
                )
            ]
        if (m := _CPUS.search(line)) is not None and not self.cores_seen:
            self.cores_seen = True
            return [Resource(cores=int(m[1]))]
        low = stripped.lower()
        if stripped.startswith("*ERROR"):
            number, _, _ = self._puzzle_step()
            text = stripped.removeprefix("*ERROR").lstrip(": ").strip() or stripped
            out: list[Event] = [
                Message(level="error", text=text, original=stripped, line=self.lines)
            ]
            if any(k in low for k in _FINAL):
                out.append(
                    Diverged(step=max(number, 1), increment=self.increment, reason=text, final=True)
                )
            return out
        if stripped.startswith("*WARNING"):
            text = stripped.removeprefix("*WARNING").lstrip(": ").strip() or stripped
            return [Message(level="warning", text=text, original=stripped, line=self.lines)]
        if "increment size is decreased" in low or low.startswith(
            ("divergence", "too slow convergence", "too many iterations")
        ):
            number, _, _ = self._puzzle_step()
            return [
                Diverged(step=max(number, 1), increment=self.increment, reason=stripped),
                Message(level="warning", text=stripped, original=stripped, line=self.lines),
            ]
        return []

    # --- result trackers: the .frd as it grows (ADR 0054 3) ----------------------------

    def poll(self) -> list[Event]:
        """New whole result blocks in the .frd: each tracker's value at that step time.
        CalculiX writes the .frd every increment; a block still being written waits."""
        if not self.trackers or not self.frd.exists():
            return []
        with self.frd.open("rb") as f:
            f.seek(self.frd_offset)
            data = f.read()
        if not data:
            return []
        self.frd_offset += len(data)
        self.frd_text += data.decode(errors="replace")
        out: list[Event] = []
        wanted = {t.field for t in self.trackers}
        while True:
            start = self.frd_text.find("100CL")
            if start < 0:
                self.frd_text = self.frd_text[-200:]  # a 1PSTEP line may be cut in two
                break
            end = self.frd_text.find("\n -3", start)
            if end < 0:
                break
            block = self.frd_text[start : end + 4]
            self.frd_text = self.frd_text[end + 4 :]
            out += self._block(block, wanted)
        return out

    def _block(self, block: str, wanted: set[str]) -> list[Event]:
        lines = block.splitlines()
        try:
            time = float(lines[0].split()[2])
        except (IndexError, ValueError):
            return []
        header = next((x for x in lines[1:4] if x.startswith(" -4")), "")
        name = FRD_FIELDS.get(header.split()[1] if header.split()[1:] else "")
        if name not in wanted:
            return []
        if time != self.frd_time:  # a new increment's results
            self.frd_time = time
            self.frd_increment += 1
        from puzzle.adapters.calculix.frd import _values

        ids, rows = [], []
        for line in lines:
            if line.startswith(" -1"):
                ids.append(int(line[3:13]))
                rows.append(_values(line[13:]))
        if not ids:
            return []
        width = 1 if name == "temperature" else 3
        values = np.array([r[:width] for r in rows], dtype=float)
        ids_arr = np.asarray(ids)
        out: list[Event] = []
        number, _, _ = self._puzzle_step()
        for t in self.trackers:
            if t.field != name:
                continue
            v = t.value(ids_arr, values)
            if v is not None:
                out.append(Tracked(name=t.name, step=max(number, 1), increment=self.frd_increment,
                                   time=time, value=v, unit=t.unit))  # fmt: skip
        return out
