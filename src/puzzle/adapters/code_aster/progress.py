"""Code_Aster's progress (ADR 0054): time steps ("Instant de calcul") with their cut-back
level, the Newton table of STAT_NON_LINE, DYNA_NON_LINE and THER_NON_LINE (columns
named from the table's own header, so contact and friction loops and SUIVI_DDL
trackers are read wherever they sit), and the boxed alarms and errors, in English."""

from __future__ import annotations

import re
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
    Tracked,
    translate,
)

_NUM = r"([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)"
_INSTANT = re.compile(r"Instant de calcul:\s*" + _NUM + r"\s*-\s*Niveau de découpe:\s*(\d+)")
_EQUATIONS = re.compile(r"La matrice est de taille\s+(\d+)\s+équations")
_MEMORY = re.compile(r"Mémoire \(Mo\)\s*:\s*" + _NUM)
_LEVELS = {"A": "warning", "E": "error", "F": "error", "S": "error", "I": "info"}
# Plain lines (outside boxes) worth a message, and how bad they are.
_NOTABLE = [
    (re.compile(r"<Échec>"), "error"),
    (re.compile(r"Degré de liberté physique associé au noeud"), "error"),
    (re.compile(r"la matrice est singulière", re.IGNORECASE), "error"),
    (re.compile(r"^<Erreur>"), "error"),
    (re.compile(r"^<Action>"), "info"),
]

# Newton-table columns by their header words: residuals and loop counters.
RESIDUALS = {
    "RESI_GLOB_RELA": "relative",
    "RESI_GLOB_MAXI": "absolute",
    "RESI_REFE_RELA": "reference",
    "RESI_COMP_RELA": "component",
    "PRESSURE ERROR": "contact pressure",
    "RESI_GEOM": "contact geometry",
    "RESI_FROT": "friction",
}
LOOPS = {"BCL. GEOM.": "geometry", "BCL. FROT.": "friction", "BCL. CONT.": "contact"}


def _numbers(text: str, keyword: str) -> list[float]:
    return [float(v) for v in re.findall(keyword + r"\s*=\s*" + _NUM, text)]


class CodeAsterProgress(Parser):
    def __init__(self, input_file: Path, step: int = 1, of: int = 1) -> None:
        super().__init__(step, of)
        comm = next(iter(sorted(input_file.parent.glob("*.comm"))), None)
        text = comm.read_text(errors="replace") if comm is not None else ""
        ends = _numbers(text, "JUSQU_A")
        self.end_time = max(ends) if ends else None
        nonlinear = any(k in text for k in ("STAT_NON_LINE", "DYNA_NON_LINE", "THER_NON_LINE"))
        self.criteria: dict[str, float] = {}
        rela, maxi = _numbers(text, "RESI_GLOB_RELA"), _numbers(text, "RESI_GLOB_MAXI")
        if rela or (nonlinear and not maxi):
            self.criteria["relative"] = rela[0] if rela else 1e-6
        if maxi:
            self.criteria["absolute"] = maxi[0]
        self.tracked_now: dict[str, float] = {}  # tracker -> its value this iteration
        self.started = False
        self.increment = 0
        self.level = 1
        self.time: float | None = None
        self.cut_next = False
        self.failed_here = False
        self.header: list[list[str]] = []
        self.columns: list[str] = []
        self.newton = 0
        self.memory = 0.0
        self.box: list[str] | None = None
        self.box_line = 0
        self.gave_up = False  # a final Diverged is sent once
        self.last_box = ""

    # --- the Newton table ----------------------------------------------------------

    def _columns(self) -> list[str]:
        width = max(len(r) for r in self.header)
        return [
            " ".join(r[i].strip() for r in self.header if i < len(r) and r[i].strip())
            for i in range(width)
        ]

    def _row(self, cells: list[str]) -> list[Event]:
        residuals: dict[str, float] = {}
        loops: dict[str, int] = {}
        tracked: list[Event] = []
        newton = None
        for name, cell in zip(self.columns, cells, strict=False):
            value = cell.strip().rstrip("X").strip()
            if not value:
                continue
            if "NEWTON" in name and "ITERATION" in name:
                newton = int(value)
                continue
            loop = next((v for k, v in LOOPS.items() if k in name), None)
            if loop is not None:
                loops[loop] = int(value)
                continue
            key = next((v for k, v in RESIDUALS.items() if k in name), None)
            try:
                number = float(value)
            except ValueError:
                continue
            if key is not None:
                residuals[key] = number
            else:
                titles = self._titles()
                title = next((t for t in titles if t in name), None)
                if title is not None:
                    self.tracked_now[titles[title]] = number
        if newton is None:
            return tracked
        self.newton = newton
        return [
            Iteration(
                step=self.step,
                increment=self.increment,
                number=newton + 1,  # Code_Aster counts Newton iterations from 0
                type="contact" if loops else "nonlinear",
                residuals=residuals,
                criteria={k: v for k, v in self.criteria.items() if k in residuals},
                loops=loops,
            ),
            *tracked,
        ]

    def _titles(self) -> dict[str, str]:
        """SUIVI_DDL column titles -> tracker names (the order comm.py wrote them in)."""
        from puzzle.adapters.code_aster.comm import TRACK_TITLE, followed

        return {
            TRACK_TITLE.format(i): t.name for i, t in enumerate(followed(self.trackers), start=1)
        }

    # --- lines -----------------------------------------------------------------------

    def feed(self, line: str) -> list[Event]:
        out: list[Event] = []
        if not self.started:
            self.started = True
            out.append(Step(number=self.step, of=self.of, end_time=self.end_time))
        stripped = line.strip()
        # A boxed message: ╔ ... ║ <A> <CODE> ║ ... ╚
        if stripped.startswith("╔"):
            self.box, self.box_line = [], self.lines
            return out
        if self.box is not None:
            if stripped.startswith("╚"):
                out += self._message_box(self.box)
                self.box = None
            else:
                self.box.append(stripped.strip("║").strip())
            return out
        if (m := _INSTANT.search(line)) is not None:
            self.increment += 1
            self.time, self.level = float(m[1]), int(m[2])
            self.header, self.columns, self.newton = [], [], 0
            out.append(
                Increment(
                    step=self.step,
                    number=self.increment,
                    time=self.time,
                    end_time=self.end_time,
                    cutback=self.cut_next,
                    level=self.level,
                )
            )
            self.cut_next = self.failed_here = False
            return out
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = stripped.strip("|").split("|")
            first = cells[0].strip().rstrip("X").strip()
            if first.isdigit() and self.columns:
                return out + self._row(cells)
            if not first.isdigit():
                self.header.append(cells)
                self.columns = self._columns()
            return out
        if (m := _EQUATIONS.search(line)) is not None:
            return out + [Resource(equations=int(m[1]))]
        if (m := _MEMORY.search(line)) is not None:
            peak = float(m[1])
            if peak > self.memory * 1.05:
                self.memory = peak
                out.append(Resource(memory_mb=peak))
            return out
        if stripped.startswith("Temps CPU consommé dans ce pas de temps"):
            if not self.failed_here and self.increment:
                out.append(
                    Converged(step=self.step, increment=self.increment,
                              iterations=self.newton + 1, time=self.time)
                )  # fmt: skip
                out += [  # the trackers at the converged iteration
                    Tracked(name=n, step=self.step, increment=self.increment, time=self.time,
                            value=v, unit="m")
                    for n, v in self.tracked_now.items()
                ]  # fmt: skip
            self.tracked_now = {}
            return out
        if "On découpe le pas de temps" in line and self.increment:
            self.cut_next = True
        if "<S>_" in stripped and ("DIAGNOSTIC JOB" in stripped or "execution ended" in stripped):
            if self.gave_up:
                return out
            self.gave_up = True
            reason = stripped.split("<S>_", 1)[1].split()[0].replace("_", " ").lower()
            return out + [
                Diverged(step=self.step, increment=self.increment, reason=reason, final=True)
            ]
        for pattern, level in _NOTABLE:
            if pattern.search(stripped):
                english = translate(stripped)
                out.append(Message(level=level, text=english, original=stripped, line=self.lines))
                if "<Échec>" in stripped and not self.gave_up:
                    self.gave_up = True
                    out.append(
                        Diverged(
                            step=self.step,
                            increment=self.increment,
                            reason=english.split("Error:")[-1].strip(" :"),
                            final=True,
                        )
                    )
                elif stripped.startswith("<Erreur>"):
                    self.failed_here = True
                    out.append(
                        Diverged(
                            step=self.step,
                            increment=self.increment,
                            reason=english.removeprefix("Error:").strip(),
                        )
                    )
                break
        return out

    def _message_box(self, lines: list[str]) -> list[Event]:
        body = [x for x in lines if x]
        if not body:
            return []
        m = re.match(r"<([^<>]+)>\s*<([^<>]+)>", body[0])
        # <A> alarm, <I> information; <E>, <F>, <S>, <EXCEPTION>, <SolverError>: errors
        level, code = (_LEVELS.get(m[1], "error"), m[2]) if m else ("info", "")
        text = " ".join(body[1:] if m else body)
        english = translate(text).strip()
        if english and english == self.last_box:  # <SolverError> then <EXCEPTION>, same text
            return []
        self.last_box = english
        events: list[Event] = [
            Message(level=level, text=english or text, original=text, code=code, line=self.box_line)
        ]
        if m and m[1] not in ("A", "I", "E") and not self.gave_up:
            self.gave_up = True
            events.append(
                Diverged(
                    step=self.step, increment=self.increment, reason=english or text, final=True
                )
            )
        return events
