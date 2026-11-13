"""OpenFOAM's progress (ADR 0054): the `Allrun` steps and simpleFoam's log.

`Allrun` sends each program's output to its own log (`log.gmshToFoam`,
`log.simpleFoam`, `log.postProcess`), so they are read as they grow; stdout carries
only what goes wrong outside them (foamDictionary's errors). Each `Time = N` of the
steady solver is one SIMPLE iteration towards controlDict's `endTime`; per field the
first solve's initial residual is the residual (what `residualControl` compares), and
its criterion is that field's `residualControl` entry in the written fvSolution (U's
for Ux, Uy, Uz; regular-expression keys such as "(k|epsilon|omega)" match by name).
"SIMPLE solution converged in N iterations" is convergence; reaching `endTime` without
it is a warning (the residuals had not met their criteria). FOAM FATAL ERROR blocks
and floating point exceptions become errors and a final Diverged."""

from __future__ import annotations

import re
import shlex
from pathlib import Path

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
    Tail,
)

_NUM = r"([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)"
_TIME = re.compile(r"^Time = " + _NUM + r"\s*$")
_SOLVE = re.compile(
    r"^(?:\w+:\s+)?Solving for (\w+), Initial residual = " + _NUM
    + r", Final residual = " + _NUM + r", No Iterations (\d+)"
)  # fmt: skip
_CONTINUITY = re.compile(r"time step continuity errors : sum local = " + _NUM)
_CONVERGED = re.compile(r"SIMPLE solution converged in (\d+) iterations")
_FIELD = re.compile(r"^field\s+(\S+)\s+tolerance\s+" + _NUM)
_PROCS = re.compile(r"^nProcs\s*:\s*(\d+)")
# The programs Allrun runs, in order, with their logs.
PROGRAMS = (
    ("log.gmshToFoam", "Converting the mesh (gmshToFoam)"),
    ("log.simpleFoam", "Solving (simpleFoam)"),
    ("log.postProcess", "Writing cell centres (postProcess)"),
)


def _block(text: str, name: str) -> str:
    """The body of sub-dictionary `name` (the first one) in an OpenFOAM dictionary."""
    m = re.search(r"(?:^|\s)" + re.escape(name) + r"\s*\{", text)
    if m is None:
        return ""
    depth, start = 1, m.end()
    for i in range(start, len(text)):
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        if depth == 0:
            return text[start:i]
    return ""


def residual_control(case: Path) -> dict[str, float]:
    """fvSolution's SIMPLE residualControl (keys as written: names or quoted regular
    expressions), with Allrun's foamDictionary overrides (ADR 0023) applied."""
    fv = case / "system" / "fvSolution"
    text = fv.read_text(errors="replace", encoding="utf-8") if fv.exists() else ""
    body = _block(_block(text, "SIMPLE"), "residualControl")
    out = {k.strip('"'): float(v) for k, v in re.findall(r'("[^"]+"|\S+)\s+' + _NUM + ";", body)}
    prefix = "system/fvSolution/SIMPLE/residualControl/"
    for key, value in _overrides(case).items():
        if key.startswith(prefix) and re.fullmatch(_NUM, value.strip()):
            out[key.removeprefix(prefix).strip('"')] = float(value)
    return out


def _overrides(case: Path) -> dict[str, str]:
    """`foamDictionary <file> -entry <path> -set <value>` lines of Allrun, as
    "<file>/<path>" -> value."""
    allrun = case / "Allrun"
    out: dict[str, str] = {}
    if not allrun.exists():
        return out
    for line in allrun.read_text(errors="replace", encoding="utf-8").splitlines():
        if not line.startswith("foamDictionary ") or " -set " not in line:
            continue
        try:
            words = shlex.split(line.split(">")[0])
        except ValueError:
            continue
        if "-entry" in words and "-set" in words:
            entry = words[words.index("-entry") + 1]
            out[f"{words[1]}/{entry}"] = words[words.index("-set") + 1]
    return out


def end_time(case: Path) -> int | None:
    control = case / "system" / "controlDict"
    text = control.read_text(errors="replace", encoding="utf-8") if control.exists() else ""
    value = _overrides(case).get("system/controlDict/endTime")
    if value is None and (m := re.search(r"^endTime\s+" + _NUM + r"\s*;", text, re.M)):
        value = m[1]
    try:
        return int(float(value)) if value is not None else None
    except ValueError:
        return None


def criterion(field: str, control: dict[str, float]) -> float | None:
    """A solved field's residualControl entry: by name, by its vector's name (Ux -> U),
    or by a regular-expression key."""
    base = field[:-1] if field[-1:] in "xyz" and len(field) > 1 else field
    for name in (field, base):
        if name in control:
            return control[name]
    for key, value in control.items():
        try:
            if re.fullmatch(key, field) or re.fullmatch(key, base):
                return value
        except re.error:
            continue
    return None


class OpenFoamProgress(Parser):
    def __init__(self, input_file: Path, step: int = 1, of: int = 1) -> None:
        super().__init__(step, of)
        case = input_file.parent
        self.control = residual_control(case)
        self.limit = end_time(case)
        self.tails = [(Tail(case / log), text) for log, text in PROGRAMS]
        self.announced: set[int] = set()
        self.started = False
        self.number = 0  # the SIMPLE iteration being read
        self.residuals: dict[str, float] = {}
        self.converged = 0  # the iteration simpleFoam said it converged in
        self.said_converged = False
        self.ended = False
        self.gave_up = False
        self.error: list[str] | None = None
        self.error_line: int | None = None
        self.warning: list[str] | None = None
        self.warning_line: int | None = None
        self.procs = False
        self.fpe = False

    def _start(self) -> list[Event]:
        if self.started:
            return []
        self.started = True
        return [Step(number=self.step, of=self.of, type="stationary", iterations=self.limit)]

    # --- stdout ------------------------------------------------------------------------

    def feed(self, line: str) -> list[Event]:
        return self._line(line, self.lines)

    # --- the logs ----------------------------------------------------------------------

    def poll(self) -> list[Event]:
        out: list[Event] = []
        for k, (tail, text) in enumerate(self.tails):
            lines = tail.lines()
            if (lines or tail.path.exists()) and k not in self.announced:
                self.announced.add(k)
                out += self._start() + [Message(level="info", text=text, original=tail.path.name)]
                if k == 1:
                    out.append(Increment(step=self.step, number=1))
            for line in lines:
                out += self._line(line)
        return out

    def finish(self, ok: bool) -> list[Event]:
        out: list[Event] = []
        for tail, _ in self.tails:
            for line in tail.flush():
                out += self._line(line)
        out += self._iteration() + self._converged() + self._error()
        if self.number and not self.converged and self.ended and ok:
            out.append(Message(
                level="warning",
                text=f"simpleFoam stopped at its iteration limit ({self.limit or self.number}) "
                "before the residuals met their criteria: the result may not have converged",
            ))  # fmt: skip
            out.append(Diverged(step=self.step, increment=1,
                                reason="the iteration limit came before convergence"))  # fmt: skip
        if not ok and not self.gave_up:
            self.gave_up = True
            out.append(Diverged(step=self.step, increment=1, final=True,
                                reason="OpenFOAM stopped with an error"))  # fmt: skip
        return out

    def _line(self, line: str, number: int | None = None) -> list[Event]:
        out: list[Event] = []
        stripped = line.strip()
        if self.warning is not None:  # "--> FOAM Warning :", From, in file, the message
            if stripped.startswith(("From ", "in file ")) and len(self.warning) < 2:
                return out
            if stripped and not stripped.startswith(("From ", "in file ")):
                self.warning.append(stripped)
            if self.warning:
                out += self._warning()
            return out
        if self.error is not None:  # a FOAM FATAL ERROR block runs to "FOAM exiting"
            if stripped.startswith(("FOAM exiting", "FOAM aborting")) or len(self.error) > 30:
                return out + self._error()
            if stripped and not stripped.startswith(("From ", "in file ", "#")):
                self.error.append(stripped)
            return out
        if (m := _TIME.match(stripped)) is not None:
            out += self._iteration()
            self.number = int(float(m[1]))
            return out
        if (m := _SOLVE.match(stripped)) is not None:
            name = m[1]
            if name not in self.residuals:  # the first solve's initial residual counts
                self.residuals[name] = float(m[2])
            return out
        if (m := _CONTINUITY.match(stripped)) is not None:
            self.residuals.setdefault("continuity", float(m[1]))  # no criterion
            return out
        if (m := _CONVERGED.search(stripped)) is not None:
            # Function objects (scalarTransport's T) still solve this iteration after it.
            self.converged = int(m[1])
            return out
        if stripped == "End" and self.number:
            self.ended = True
            return out + self._iteration() + self._converged()
        if (m := _FIELD.match(stripped)) is not None:  # "SIMPLE: convergence criteria"
            self.control[m[1].strip('"')] = float(m[2])  # what simpleFoam itself read
            return out
        if (m := _PROCS.match(stripped)) is not None and self.number == 0 and not self.procs:
            self.procs = True
            return out + [Resource(processes=int(m[1]))]
        if "FOAM FATAL" in stripped:
            self.error = [stripped.lstrip("-> ").strip()]
            self.error_line = number
            return out
        if "FOAM Warning" in stripped:
            self.warning, self.warning_line = [], number
            rest = stripped.split("FOAM Warning", 1)[1].strip(" :")
            if rest:
                self.warning.append(rest)
            return out
        if ("Floating point exception" in stripped or "sigFpe" in stripped) and not self.fpe:
            self.fpe = True  # the stack trace's sigFpe line, then the shell's own words
            reason = "The solution blew up (a floating point exception)"
            out.append(Message(level="error", code="sigFpe", original=stripped, line=number,
                               text=f"{reason}: check the boundary conditions and the mesh, "
                               "or lower the relaxation factors"))  # fmt: skip
            if not self.gave_up:
                self.gave_up = True
                out.append(Diverged(step=self.step, increment=1, reason=reason, final=True))
        return out

    def _iteration(self) -> list[Event]:
        if not self.number or not self.residuals:
            return []
        criteria = {n: c for n in self.residuals if (c := criterion(n, self.control)) is not None}
        event = Iteration(step=self.step, increment=1, number=self.number, type="nonlinear",
                          residuals=self.residuals, criteria=criteria)  # fmt: skip
        self.residuals = {}
        return [event]

    def _converged(self) -> list[Event]:
        if not self.converged or self.said_converged:
            return []
        self.said_converged = True
        return [Converged(step=self.step, increment=1, iterations=self.converged)]

    def _warning(self) -> list[Event]:
        text, self.warning = " ".join(self.warning or []), None
        if "undefined faces in mesh" in text:  # Allrun makes them walls, as documented
            return [Message(level="info", text="Faces outside every selection become walls",
                            original=text, line=self.warning_line)]  # fmt: skip
        return [Message(level="warning", text=text, original=text, line=self.warning_line)]

    def _error(self) -> list[Event]:
        if not self.error:
            self.error = None
            return []
        original = "\n".join(self.error)
        # "FOAM FATAL IO ERROR: (openfoam-2412 ...)" then the message and its details
        head, *body = self.error
        what = "OpenFOAM input error" if "IO ERROR" in head else "OpenFOAM error"
        text = f"{what}: {' '.join(body)}" if body else head
        line = self.error_line
        self.error = None
        code = "FOAM FATAL IO ERROR" if "IO ERROR" in head else "FOAM FATAL ERROR"
        out: list[Event] = [
            Message(level="error", text=text, original=original, line=line, code=code)
        ]
        if not self.gave_up:
            self.gave_up = True
            out.append(Diverged(step=self.step, increment=self.number and 1,
                                reason=body[0] if body else head, final=True))  # fmt: skip
        return out
