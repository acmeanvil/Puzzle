"""Code_Saturne's progress (ADR 0054): its listing (`CASE/RESU/puzzle/run_solver.log`),
read as it grows, since the run's stdout carries only `code_saturne run`'s stages.

Each time step ("INSTANT t TIME STEP NUMBER n") is one iteration of the steady
algorithm (or of the pseudo-time march), towards the step's iteration limit. Its
"Information on convergence" table gives, per variable, the right-hand side's norm, the
linear solver's iterations, its normalised residual, the drift and the time residual;
the time residual (how much the variable changed over the step, normalised) is the
convergence measure, plotted per variable. The steady run has no stopping criterion
here: it runs its iterations, and counts as converged when every residual fell
`progress.STEADY_DECADES` decades from its largest (else a warning). The listing is
written every iteration (`listing_every` in case.json; Code_Saturne's own default is
every 50th). Fatal errors and intercepted signals become messages and a final
Diverged."""

from __future__ import annotations

import contextlib
import json
import math
import re
from pathlib import Path

from puzzle.progress import (
    STEADY_DECADES,
    Converged,
    Diverged,
    Event,
    Increment,
    Iteration,
    Message,
    Parser,
    Step,
    Tail,
    worst_residual,
)

LISTING = Path("CASE") / "RESU" / "puzzle" / "run_solver.log"
_STEP = re.compile(r"^\s*INSTANT\s+(\S+)\s+TIME STEP NUMBER\s+(\d+)")
_ROW = re.compile(r"^c\s+(.+?)\s+(\S+)\s+(\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s*$")
# Variables as people read them (Code_Saturne's names on the left).
NAMES = {
    "velocity": "velocity",
    "pressure": "pressure",
    "k": "k",
    "epsilon": "epsilon",
    "omega": "omega",
    "tempk": "temperature",
    "temperature": "temperature",
}
# `code_saturne run`'s stages on stdout, as messages.
STAGES = {
    "Preparing calculation data": "Preparing the calculation",
    "Preprocessing calculation": "Reading the mesh (preprocessor)",
    "Starting calculation": "Solving",
    "Post-calculation operations": "Writing the results",
}
_ERRORS = (
    re.compile(r"\bFatal error\b", re.IGNORECASE),
    re.compile(r"\bsignal .*intercepted", re.IGNORECASE),
    re.compile(r"^\s*Error\b"),
    re.compile(r"^\s*SIG\w+"),
)


def _iterations(case: dict) -> int | None:
    limit = case.get("iterations")
    for key, value in case.get("xml", {}).items():  # an expert override (ADR 0023)
        if key.rstrip("/").endswith("time_parameters/iterations"):
            limit = value
    try:
        return int(limit) if limit is not None else None
    except (TypeError, ValueError):
        return None


class CodeSaturneProgress(Parser):
    def __init__(self, input_file: Path, step: int = 1, of: int = 1) -> None:
        super().__init__(step, of)
        try:
            case = json.loads(input_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            case = {}
        self.limit = _iterations(case)
        self.listing = Tail(input_file.parent / LISTING)
        self.started = False
        self.number = 0  # the time step being read
        self.residuals: dict[str, float] = {}
        self.table = False
        self.gave_up = False
        self.error: list[str] = []
        self.its: list[Iteration] = []
        self.last_error = ""

    def _start(self) -> list[Event]:
        if self.started:
            return []
        self.started = True
        return [
            Step(number=self.step, of=self.of, type="stationary", iterations=self.limit),
            Increment(step=self.step, number=1),
        ]

    # --- stdout: `code_saturne run`'s stages and errors ---------------------------------

    def feed(self, line: str) -> list[Event]:
        stripped = line.strip()
        for words, english in STAGES.items():
            if stripped == words:
                return [Message(level="info", text=english, original=stripped, line=self.lines)]
        if stripped.startswith("Error"):  # "Error running the calculation."
            return [Message(level="error", text=stripped, original=stripped, line=self.lines)]
        return []

    # --- the listing ---------------------------------------------------------------------

    def poll(self) -> list[Event]:
        return self._lines(self.listing.lines())

    def finish(self, ok: bool) -> list[Event]:
        out = self._lines(self.listing.flush()) + self._iteration() + self._error()
        worst = worst_residual(self.its)
        if self.number and ok and worst is not None and worst[1] <= 1.0:
            out.append(Converged(step=self.step, increment=1, iterations=self.number))
        elif self.number and ok and worst is not None:
            fell = STEADY_DECADES - math.log10(worst[1])
            out.append(Message(
                level="warning",
                text=f"Code_Saturne ran its {self.number} iterations but its {worst[0]} "
                f"residual fell only {fell:.1f} decades: the result may not have converged",
            ))  # fmt: skip
            out.append(Diverged(step=self.step, increment=1,
                                reason="residuals still high at the iteration limit"))  # fmt: skip
        elif not ok and not self.gave_up:
            self.gave_up = True
            out.append(Diverged(step=self.step, increment=1, final=True,
                                reason="Code_Saturne stopped with an error"))  # fmt: skip
        return out

    def _lines(self, lines: list[str]) -> list[Event]:
        out: list[Event] = []
        for line in lines:
            out += self._line(line)
        return out

    def _line(self, line: str) -> list[Event]:
        out = self._start()
        if self.error:  # an error's text, up to its call stack or advice
            text = line.strip()
            if text.startswith(("Call stack", "Read error messages")) or len(self.error) >= 12:
                return out + self._error()
            if text and not re.fullmatch(r"-+", text) and not text.endswith("Fatal error."):
                self.error.append(text)
            return out
        if (m := _STEP.match(line)) is not None:
            out += self._iteration()
            self.number = int(m[2])
            return out
        if "Rhs norm" in line and "Variable" in line:
            self.table = True
            return out
        if self.table:
            if (m := _ROW.match(line)) is not None:
                name = m[1].strip()
                with contextlib.suppress(ValueError):
                    self.residuals[NAMES.get(name.lower(), name)] = float(m[6])
            elif line.startswith("---") and self.residuals:
                self.table = False
            return out
        if any(p.search(line) for p in _ERRORS):
            self.error = [line.strip()]
        return out

    def _iteration(self) -> list[Event]:
        """The time step just read, once its table is complete (the next step began)."""
        if not self.number or not self.residuals:
            return []
        event = Iteration(step=self.step, increment=1, number=self.number,
                          type="nonlinear", residuals=self.residuals)  # fmt: skip
        self.residuals, self.table = {}, False
        self.its.append(event)
        return [event]

    def _error(self) -> list[Event]:
        if not self.error:
            return []
        text = re.sub(r"\s+([.,:;])", r"\1", " ".join(" ".join(self.error).split()))
        self.error = []
        if text == self.last_error:  # the listing repeats it for each rank's stop
            return []
        self.last_error = text
        # Its parameter checks ("... while its value must be greater than 0") refuse a
        # setting before solving; other fatal errors stop the solve itself.
        code = "cs_parameters" if "parameter error" in text else "cs_error"
        out: list[Event] = [Message(level="error", text=text, original=text, code=code)]
        if not self.gave_up:
            self.gave_up = True
            out.append(Diverged(step=self.step, increment=1, reason=text, final=True))
        return out
