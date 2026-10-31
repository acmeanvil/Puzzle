"""Solve progress and convergence as typed, solver-neutral events (ADR 0054).

Each adapter's parser turns its solver's log lines (and side files) into these events;
`run_study` stamps them with the time since the run started, hands them to `on_event`
and keeps them with the run (`progress.jsonl` in the run's folder, and the results
store's `progress` table), so a finished, failed or stopped run still shows how it
converged. A parser never blocks or changes a run: a line it can't read is only a log
line.
"""

from __future__ import annotations

import contextlib
import json
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
from typing import Any, ClassVar

FILE = "progress.jsonl"


# --- events ------------------------------------------------------------------------


@dataclass(frozen=True)
class Event:
    kind: ClassVar[str] = "event"
    t: float = 0.0  # seconds since the run started, set by the recorder

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, **asdict(self)}


@dataclass(frozen=True)
class Stage(Event):
    """A stage of the run around the solver: mesh, transfers, write, solve, read, store."""

    kind: ClassVar[str] = "stage"
    name: str = ""
    state: str = "start"  # start or end
    seconds: float | None = None  # how long it took, on "end"


@dataclass(frozen=True)
class Step(Event):
    """Solve step `number` of `of` starts; `end_time` is its time period (None for
    modal, buckling and the like, which have no time)."""

    kind: ClassVar[str] = "step"
    number: int = 1
    of: int = 1
    type: str = ""
    end_time: float | None = None


@dataclass(frozen=True)
class Increment(Event):
    """An increment (time or load step) starts, at step time `time` of `end_time`.
    `attempt` > 1 or `cutback` set: an earlier try at it failed and it was cut back."""

    kind: ClassVar[str] = "increment"
    step: int = 1
    number: int = 1
    attempt: int = 1
    size: float | None = None
    time: float | None = None
    end_time: float | None = None
    cutback: bool = False
    level: int = 1  # cut-back depth (Code_Aster's "niveau de découpe")


@dataclass(frozen=True)
class Iteration(Event):
    """One equilibrium iteration: each residual by name (force, displacement, relative,
    absolute, contact pressure, ...) with its criterion where the solver has one."""

    kind: ClassVar[str] = "iteration"
    step: int = 1
    increment: int = 1
    number: int = 1
    type: str = "nonlinear"  # nonlinear, linear or contact
    residuals: dict[str, float] = field(default_factory=dict)
    criteria: dict[str, float] = field(default_factory=dict)
    contact: int | None = None  # contact elements or points active
    loops: dict[str, int] = field(default_factory=dict)  # outer loops (contact, friction)


@dataclass(frozen=True)
class Converged(Event):
    kind: ClassVar[str] = "converged"
    step: int = 1
    increment: int = 1
    iterations: int = 0
    time: float | None = None


@dataclass(frozen=True)
class Diverged(Event):
    """An increment (or the run) failed to converge: `reason` in English."""

    kind: ClassVar[str] = "diverged"
    step: int = 1
    increment: int = 1
    reason: str = ""
    final: bool = False  # the run gave up (no more cut-backs)


@dataclass(frozen=True)
class Message(Event):
    """A warning or error from the log, in English (`original` keeps the solver's text)."""

    kind: ClassVar[str] = "message"
    level: str = "info"  # info, warning, error
    text: str = ""
    original: str = ""
    code: str = ""  # the solver's own message id (Code_Aster's MODELISA8_14)
    line: int | None = None  # the log line it came from, counted from 1


@dataclass(frozen=True)
class Resource(Event):
    """What the solve uses: equations, memory (MB), cores and processes."""

    kind: ClassVar[str] = "resource"
    equations: int | None = None
    memory_mb: float | None = None
    cores: int | None = None
    processes: int | None = None


@dataclass(frozen=True)
class Tracked(Event):
    """A result tracker's value (ADR 0054 3) at step time `time`."""

    kind: ClassVar[str] = "tracked"
    name: str = ""
    step: int = 1
    increment: int = 1
    time: float | None = None
    value: float = 0.0
    unit: str = ""


KINDS: dict[str, type[Event]] = {
    c.kind: c
    for c in (Stage, Step, Increment, Iteration, Converged, Diverged, Message, Resource, Tracked)
}


def from_dict(data: dict[str, Any]) -> Event | None:
    """The event a stored dict describes; None for an unknown kind (a newer Puzzle's)."""
    cls = KINDS.get(data.get("kind", ""))
    if cls is None:
        return None
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in names})


def dumps(events: Iterable[Event]) -> str:
    return "".join(json.dumps(e.to_dict(), separators=(",", ":")) + "\n" for e in events)


def loads(text: str) -> list[Event]:
    out = []
    for line in text.splitlines():
        if line.strip():
            try:
                e = from_dict(json.loads(line))
            except (ValueError, TypeError):
                continue
            if e is not None:
                out.append(e)
    return out


def read(path: Path) -> list[Event]:
    """A run's events from its folder (or the file itself); [] when there are none."""
    path = path / FILE if path.is_dir() else path
    return loads(path.read_text()) if path.exists() else []


# --- parsers -----------------------------------------------------------------------


class Parser:
    """Turns one job's log lines into events. `poll` reads side files as they grow
    (called every few tenths of a second while the job runs); `finish` adds what only
    the end tells. Subclasses keep their own state; one parser per job."""

    def __init__(self, step: int = 1, of: int = 1) -> None:
        self.step, self.of = step, of  # which of the study's solve steps this job is
        self.lines = 0
        self.trackers: list = []  # puzzle.trackers.Tracker, set before the job starts

    def feed(self, line: str) -> list[Event]:
        return []

    def poll(self) -> list[Event]:
        return []

    def finish(self, ok: bool) -> list[Event]:
        return []

    def safe_feed(self, line: str) -> list[Event]:
        """`feed`, never raising: a parser bug must not stop a solve."""
        self.lines += 1
        try:
            return self.feed(line)
        except Exception:  # noqa: BLE001 - an unreadable line is only a log line
            return []

    def safe(self, fn: Callable[[], list[Event]]) -> list[Event]:
        try:
            return fn()
        except Exception:  # noqa: BLE001
            return []


# --- recording ---------------------------------------------------------------------


class Recorder:
    """Stamps events with the time since the run started, keeps them, writes them to
    the run folder's `progress.jsonl` as they come (so a killed run keeps its history)
    and passes them on."""

    def __init__(self, on_event: Callable[[Event], None] | None = None) -> None:
        self.on_event = on_event
        self.events: list[Event] = []
        self.start = time.monotonic()
        self.files: list[Path] = []

    def write_to(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / FILE
        path.write_text(dumps(self.events))
        self.files.append(path)

    def __call__(self, event: Event) -> None:
        if not event.t:
            event = replace(event, t=round(time.monotonic() - self.start, 3))
        self.events.append(event)
        for path in self.files:
            try:
                with path.open("a") as f:
                    f.write(dumps([event]))
            except OSError:
                pass
        if self.on_event is not None:
            # A listener's bug must not stop a solve.
            with contextlib.suppress(Exception):
                self.on_event(event)


# --- reading the history -----------------------------------------------------------


@dataclass
class State:
    """Where a run is, folded from its events: for a progress bar and a status line."""

    stage: str = ""
    step: int = 0
    steps: int = 0
    step_type: str = ""
    end_time: float | None = None
    time: float | None = None
    increment: int = 0
    iteration: int = 0
    iterations: int = 0  # cumulative
    cutbacks: int = 0
    converged: int = 0
    failed: str = ""
    residuals: dict[str, float] = field(default_factory=dict)
    criteria: dict[str, float] = field(default_factory=dict)
    equations: int | None = None
    memory_mb: float | None = None
    cores: int | None = None
    warnings: int = 0
    errors: int = 0
    last_t: float = 0.0
    finished: bool = False
    steps_done: int = 0

    def add(self, e: Event) -> None:
        self.last_t = max(self.last_t, e.t)
        if isinstance(e, Stage):
            self.stage = e.name if e.state == "start" else self.stage
            if e.name == "store" and e.state == "end":
                self.finished = True
        elif isinstance(e, Step):
            if self.step and e.number != self.step:
                self.steps_done += 1
            self.step, self.steps, self.step_type = e.number, e.of, e.type
            self.end_time, self.time, self.increment = e.end_time, None, 0
        elif isinstance(e, Increment):
            self.increment, self.iteration = e.number, 0
            if e.cutback or e.attempt > 1:
                self.cutbacks += 1
            if e.end_time is not None:
                self.end_time = e.end_time
        elif isinstance(e, Iteration):
            self.iteration = e.number
            self.iterations += 1
            self.residuals, self.criteria = dict(e.residuals), dict(e.criteria)
        elif isinstance(e, Converged):
            self.converged += 1
            if e.time is not None:
                self.time = e.time
        elif isinstance(e, Diverged):
            if e.final:
                self.failed = e.reason or "did not converge"
        elif isinstance(e, Message):
            self.warnings += e.level == "warning"
            self.errors += e.level == "error"
        elif isinstance(e, Resource):
            self.equations = e.equations or self.equations
            if e.memory_mb is not None:
                self.memory_mb = max(self.memory_mb or 0.0, e.memory_mb)
            self.cores = e.cores or self.cores

    def fraction(self) -> float | None:
        """How far through the solve: steps done plus this step's time over its end
        time; None when nothing tells yet."""
        if not self.steps:
            return None
        within = 0.0
        if self.end_time and self.time is not None:
            within = min(max(self.time / self.end_time, 0.0), 1.0)
        return min((max(self.step - 1, 0) + within) / self.steps, 1.0)

    def line(self) -> str:
        """One status line: 'step 1 of 2, t 0.25 of 1, increment 3 (2 cut-backs), iteration 7'."""
        bits = []
        if self.steps:
            bits.append(f"step {self.step} of {self.steps}")
        if self.end_time and self.time is not None:
            bits.append(f"t {self.time:.4g} of {self.end_time:.4g}")
        if self.increment:
            cut = (
                f" ({self.cutbacks} cut-back{'s' * (self.cutbacks != 1)})" if self.cutbacks else ""
            )
            bits.append(f"increment {self.increment}{cut}")
        if self.iteration:
            bits.append(f"iteration {self.iteration}")
        if self.residuals:
            name, value = next(iter(self.residuals.items()))
            crit = self.criteria.get(name)
            bits.append(f"{name} {value:.2e}" + (f" (needs {crit:.0e})" if crit else ""))
        if self.failed:
            bits.append(f"failed: {self.failed}")
        return ", ".join(bits) or self.stage or "starting"


def state(events: Iterable[Event]) -> State:
    s = State()
    for e in events:
        s.add(e)
    return s


def summary(events: list[Event]) -> dict[str, Any]:
    """What a run's history comes to, for the CLI, MCP and the agent."""
    s = state(events)
    stages = {e.name: e.seconds for e in events if isinstance(e, Stage) and e.state == "end"}
    messages = [
        {"level": e.level, "text": e.text, **({"code": e.code} if e.code else {})}
        for e in events
        if isinstance(e, Message) and e.level in ("warning", "error")
    ]
    out: dict[str, Any] = {
        "steps": s.steps,
        "increments": sum(isinstance(e, Increment) for e in events),
        "cutbacks": s.cutbacks,
        "iterations": s.iterations,
        "converged_increments": s.converged,
        "stages_s": {k: round(v, 2) for k, v in stages.items() if v is not None},
    }
    if s.end_time is not None:
        out["time_reached"] = s.time
        out["end_time"] = s.end_time
    if s.residuals:
        out["last_residuals"] = s.residuals
    if s.criteria:
        out["criteria"] = s.criteria
    for key in ("equations", "memory_mb", "cores"):
        if getattr(s, key) is not None:
            out[key] = getattr(s, key)
    if s.failed:
        out["failed"] = s.failed
    tracked: dict[str, float] = {}
    for e in events:
        if isinstance(e, Tracked):
            tracked[e.name] = e.value
    if tracked:
        out["trackers_last"] = tracked
    if messages:
        out["messages"] = messages[-20:]
        out["messages_total"] = len(messages)
    return out


# --- translation (Code_Aster speaks French) ----------------------------------------

# Phrases as Code_Aster writes them, and what they say in English. Matched anywhere in
# a message; the groups carry over. Unknown text passes through unchanged.
TRANSLATIONS: list[tuple[str, str]] = [
    (
        r"Le nombre maximum d'itérations de Newton est atteint",
        "The maximum number of Newton iterations was reached",
    ),
    (r"On essaie de découper le pas de temps", "Trying to cut the time step"),
    (r"On découpe le pas de temps", "Cutting the time step"),
    (r"On utilise la découpe manuelle", "Using a manual cut-back"),
    (
        r"Découpe uniforme à partir de l'instant\s+(\S+) en (\d+) pas de temps",
        r"Uniform cut from time \1 into \2 steps",
    ),
    (r"\(soit un incrément constant de\s+(\S+)\)", r"(an increment of \1)"),
    (
        r"Le pas de temps ne peut plus être découpé|niveau maximal de découpe est atteint|Nombre maximal de niveau de subdivision atteint",  # noqa: E501 - the solver's own words
        "The time step can't be cut any further (the maximum cut-back level was reached)",
    ),
    (
        r"Degré de liberté physique associé au noeud (\S+) et à la composante (\S+?)\.?$",
        r"Degree of freedom at node \1, component \2",
    ),
    (r"La matrice du système est singulière", "The system's matrix is singular"),
    (r"Échec dans la tentative de découper le pas de temps", "Cutting the time step failed"),
    (r"<Action>\s*<Échec>\s*", "Error: "),
    (r"<Échec>\s*", "Error: "),
    (r"Arrêt pour cause de matrice non inversible", "Stopped: the matrix can't be inverted"),
    (
        r"La base globale est sauvegardée\. Elle contient les pas archivés avant l'arrêt",
        "The database is saved with the steps stored before the stop",
    ),
    (
        r"Liste des alarmes émises lors de l'exécution du calcul\.",
        "The warnings given during the run.",
    ),
    (
        r"la matrice est singulière ou presque singulière",
        "the matrix is singular or nearly singular",
    ),
    (
        r"Lors de la factorisation de la matrice, on a rencontré un problème",
        "Factorising the matrix met a problem",
    ),
    (
        r"\(pivot nul ou presque nul\) à la ligne (\d+) qui correspond au degré de liberté donné ci-dessus",  # noqa: E501 - the solver's own words
        r"(a zero or nearly zero pivot) at row \1, the degree of freedom given above",
    ),
    (
        r"Si la ligne correspond a un degré de liberté physique, il s'agit probablement d'un mouvement",  # noqa: E501 - the solver's own words
        "If the row is a physical degree of freedom, it is probably a rigid-body motion",
    ),
    (r"de corps rigide mal bloqué", "that is not held"),
    (r"Vérifiez (?:les|vos) conditions aux limites", "Check the boundary conditions"),
    (r"Conseils\s*:", "Advice:"),
    (r"Vérifiez votre modèle, la cohérence des unités", "Check the model and that its units agree"),
    (
        r"Si vous faites du contact, il ne faut pas que la structure ne \"tienne\" que par le contact",  # noqa: E501 - the solver's own words
        "With contact, the structure must not be held by contact alone",
    ),
    (
        r"Parfois, en parallèle, le critère de détection de singularité de MUMPS est trop pessimiste ! "  # noqa: E501 - the solver's own words
        r"Il reste néanmoins souvent possible de faire passer le calcul complet en relaxant ce critère "  # noqa: E501 - the solver's own words
        r"\(augmenter de 1 ou 2 la valeur du mot-clé NPREC\) ou en le débranchant \(valeur du mot-clé "  # noqa: E501 - the solver's own words
        r"NPREC=-1\) ou en relançant le calcul sur moins de processeurs",
        "In parallel, MUMPS's singularity check is sometimes too strict: relax it (raise NPREC by 1 or "  # noqa: E501 - the solver's own words
        "2), switch it off (NPREC=-1) or rerun on fewer processes",
    ),
    (
        r"Si la ligne correspond a un degré de liberté de Lagrange, il s'agit probablement de conditions",  # noqa: E501 - the solver's own words
        "If the row is a Lagrange degree of freedom, there are probably conditions",
    ),
    (r"aux limites redondantes", "that repeat one another (redundant constraints)"),
    (r"Problème\s*:", "Problem:"),
    (r"Risques et conseils\s*:", "Risks and advice:"),
    (r"La matrice est de taille (\d+) équations", r"The matrix has \1 equations"),
    (
        r"Il y a (\d+) points initialement en contact et (\d+) points exclus",
        r"\1 points start in contact, \2 are excluded",
    ),
    (r"Instant de calcul:\s*(\S+) - Niveau de découpe:\s*(\d+)", r"Time \1, cut-back level \2"),
    (r"Temps CPU consommé dans ce pas de temps\s*:\s*(.*)", r"CPU time in this time step: \1"),
    (r"Nombre d'itérations de Newton\s*:\s*(\d+)", r"Newton iterations: \1"),
    (r"Le calcul est arrêté|arrêt du calcul", "The calculation stops"),
    (
        r"Échec dans le traitement de l'événement|Echec dans le traitement de l'événement",
        "Handling the event failed",
    ),
    (r"pas de convergence|absence de convergence|Non convergence", "no convergence"),
    (r"le résidu global|Le résidu global", "the global residual"),
    (r"divergence", "divergence"),
    (r"<Erreur>", "Error:"),
    (r"<Action>", "Action:"),
    (r"La maille (\S+) (\S+) est incluse dans une autre", r"Element \1 \2 lies inside another"),
    (
        r"Le message d'alarme '(\S+)' a été émis (\d+) fois, il ne sera plus affiché",
        r"Warning \1 was given \2 times; it won't be shown again",
    ),
    (
        r"Ceci est une alarme\. Si vous ne comprenez pas le sens de cette alarme, vous pouvez obtenir des résultats inattendus !",  # noqa: E501 - the solver's own words
        "",
    ),
    (
        r"Ceci est une erreur fatale\. L'exécution du code est arrêtée",
        "This is a fatal error: the run stops",
    ),
    (r"Le modèle contient", "The model contains"),
    (r"mailles", "elements"),
    (r"noeuds", "nodes"),
    (r"Attention\s*:", "Warning:"),
]
_COMPILED = [(re.compile(a, re.IGNORECASE), b) for a, b in TRANSLATIONS]


def translate(text: str) -> str:
    """Code_Aster's French in English, phrase by phrase; what isn't known stays."""
    out = text
    for pattern, english in _COMPILED:
        out = pattern.sub(english, out)
    return out


# --- diagnosis (ADR 0054 5, shown by the Advisor) -----------------------------------

_PIVOT = re.compile(r"Degree of freedom at node (\S+), component (\S+)")


@dataclass(frozen=True)
class Diagnosis:
    # redundant_constraints, friction_singular, rigid_body, cutbacks, chattering, iterations
    code: str
    text: str  # what it means and what to try, in plain words


def diagnose(events: list[Event]) -> list[Diagnosis]:
    """Known patterns in a run's events as plain advice, most telling first."""
    out: list[Diagnosis] = []
    texts = [e.text for e in events if isinstance(e, Message)]
    pivots = [m for t in texts if (m := _PIVOT.search(t))]
    components = {m[2].upper() for m in pivots}
    if any(c.startswith("LAGS_F") for c in components):
        out.append(Diagnosis(
            "friction_singular",
            "Friction multipliers went singular (a zero pivot on LAGS_F): contact nodes that "
            "neither touch nor slide. Check that the contact's source face lies within its "
            "target, that no source node is also held or tied (bolt ends, supports), and "
            "that the model is held by more than friction; or try frictionless contact first.",
        ))  # fmt: skip
    elif any(c.startswith(("LAGS", "LAGR")) for c in components):
        out.append(Diagnosis(
            "redundant_constraints",
            "A zero pivot on a Lagrange multiplier: constraints that repeat one another, as a "
            "contact node that is also tied or held, or two supports on one node.",
        ))  # fmt: skip
    elif components & {"DX", "DY", "DZ", "DRX", "DRY", "DRZ", "TEMP"}:
        node = next(
            m[1] for m in pivots if m[2].upper() in {"DX", "DY", "DZ", "DRX", "DRY", "DRZ", "TEMP"}
        )
        out.append(Diagnosis(
            "rigid_body",
            f"A zero pivot on a physical degree of freedom (node {node}): a part or a "
            "direction that nothing holds, a rigid-body motion. Check the supports, and that "
            "parts held only through contact have a support or a bond to start from.",
        ))  # fmt: skip
    s = state(events)
    if s.failed and s.cutbacks >= 3:
        out.append(Diagnosis(
            "cutbacks",
            f"The load step was cut back {s.cutbacks} times before the run gave up: the "
            "increments are too large for the model, or it is held only by contact or "
            "friction. Use more increments, ramp the load, or give the model a support or "
            "a bond that holds it before contact closes.",
        ))  # fmt: skip
    if _chatters(events):
        out.append(Diagnosis(
            "chattering",
            "The residual swings up and down from one iteration to the next without "
            "settling: contact chattering (nodes opening and closing in turn). Try a "
            "smaller contact stiffness (penalty), ramping the load, or more increments.",
        ))  # fmt: skip
    if not out and any("maximum number of Newton iterations" in t for t in texts):
        out.append(Diagnosis(
            "iterations",
            "An increment used up its iterations without converging: smaller increments or a "
            "ramped load usually help.",
        ))  # fmt: skip
    return out


def _chatters(events: list[Event]) -> bool:
    """Whether some increment's main residual alternates up and down for at least 10
    iterations, most of them turning."""
    runs: dict[tuple, list[float]] = {}
    for e in events:
        if isinstance(e, Iteration) and e.residuals:
            name = next(iter(e.criteria), None) or next(iter(e.residuals))
            key = (e.step, e.increment, tuple(sorted(e.loops.items())))
            if name in e.residuals:
                runs.setdefault(key, []).append(e.residuals[name])
    for values in runs.values():
        if len(values) < 10:
            continue
        diffs = [b - a for a, b in zip(values, values[1:], strict=False)]
        turns = sum(1 for a, b in zip(diffs, diffs[1:], strict=False) if a * b < 0)
        if turns >= 0.7 * (len(diffs) - 1):
            return True
    return False


def latest_attempt(root: Path, study_name: str) -> list[Event]:
    """The events of a study's newest run folder (`.puzzle/runs/<study>-<time>`): the
    attempt a stored run may not record, such as a failed or stopped one."""
    from puzzle.project.layout import WORK_DIR

    slug = "".join(c if c.isalnum() else "-" for c in study_name.lower()).strip("-") or "study"
    folders = sorted(
        (p for p in (root / WORK_DIR).glob(f"{slug}-*") if (p / FILE).exists()),
        key=lambda p: (p / FILE).stat().st_mtime,
    )
    return read(folders[-1]) if folders else []
