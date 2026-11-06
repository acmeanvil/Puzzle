"""Choose the local expert model by measurement (ADR 0022): `puzzle agent pick`.

1. **Find** the models the local endpoint serves: Ollama's `/api/tags` (with each
   model's size on disk and quantisation), else an OpenAI-compatible `/v1/models`.
2. **Fit**: a model's memory is its size on disk times `CONTEXT` (the KV cache and
   runtime). It must fit the machine's memory less what Puzzle and a solver need
   (`HEADROOM`). Apple's unified memory is the machine's; elsewhere VRAM is not read,
   so a GPU box may be told a model doesn't fit that would. Models whose size the
   endpoint doesn't say are tried.
3. **Measure**: the evaluation suite (`agent.evals`, its `SUITE_VERSION`) on each
   candidate, each task on a fresh copy of the cantilever example.
4. **Rank** by the core families passed, then all tasks passed, then median seconds
   per task, then memory. The recommendation is the smallest model at the best score,
   with the trade-off said in words.

Results are kept in `<user data dir>/agent-evals/` (one JSON per model and suite
version, naming the tasks it ran), so a later pick compares like with like and a model
is measured once per suite: a record is reused only when it covers the tasks this
machine runs (one made without CalculiX doesn't cover the solving tasks), re-tallied
over just those. A model is not marked down for its endpoint: a connection error stops
its measurement and nothing is kept, as when the pick is cancelled. Embedding models
the endpoint serves are left out with the reason. The choice goes in `agent.toml` with
the date and the suite's version. Nothing is downloaded: only models already on the
endpoint are tried. When none fits or none passes the core families, the advice is a
remote model through the agent setup (Claude first, ADR 0015), saying what the machine
lacks.
"""

from __future__ import annotations

import json
import statistics
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

CONTEXT = 1.3  # memory per byte of weights: the KV cache and the runtime on top
HEADROOM = 6 * 1024**3  # Puzzle with a model open (2 GB) and a solver (4 GB)
TIMEOUT_S = 10


@dataclass
class Candidate:
    name: str
    size: int | None = None  # bytes on disk, when the endpoint says
    quantisation: str = ""
    parameters: str = ""  # "8.0B", as Ollama says
    fits: bool = True
    why: str = ""  # why it doesn't fit

    @property
    def memory(self) -> int | None:
        return int(self.size * CONTEXT) if self.size else None


@dataclass
class Measured:
    model: str
    suite: str
    when: str
    passed: int
    total: int
    core_passed: int
    core_total: int
    median_s: float
    memory: int | None
    tasks: dict[str, bool] = field(default_factory=dict)
    details: dict[str, str] = field(default_factory=dict)
    seconds: dict[str, float] = field(default_factory=dict)  # per task, to re-tally a subset
    # False when the endpoint failed or the pick was cancelled partway: such a record
    # is never kept, and the model is measured again next time.
    complete: bool = True

    def score(self) -> tuple:
        memory = self.memory if self.memory is not None else float("inf")  # unknown: last
        return (self.core_passed, self.passed, -self.median_s, -memory)


@dataclass
class Pick:
    """What `pick` found: every candidate, the measured ones ranked, the advice."""

    candidates: list[Candidate]
    ranked: list[Measured]
    recommended: str | None
    words: str
    memory_budget: int | None
    incomplete: list[Measured] = field(default_factory=list)  # not ranked: measure again
    cancelled: bool = False

    def to_dict(self) -> dict:
        return {"candidates": [asdict(c) for c in self.candidates],
                "ranked": [asdict(m) for m in self.ranked], "recommended": self.recommended,
                "words": self.words, "memory_budget": self.memory_budget,
                "incomplete": [asdict(m) for m in self.incomplete],
                "cancelled": self.cancelled}  # fmt: skip


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=TIMEOUT_S) as r:  # noqa: S310 - a local endpoint
        return json.loads(r.read())


EMBEDDING_FAMILIES = frozenset({"bert", "nomic-bert", "xlm-roberta", "jina-bert"})
NOT_CHAT = "an embedding model (it turns text into vectors and cannot chat or call tools)"


def _embedding(name: str, details: dict) -> bool:
    """Whether a served model only embeds: its name says so, or Ollama's families
    are encoder ones (nomic-embed-text is nomic-bert, mxbai-embed-large bert)."""
    families = {details.get("family") or ""} | set(details.get("families") or [])
    return "embed" in name.lower() or bool(families & EMBEDDING_FAMILIES)


def _candidate(name: str, size=None, details: dict | None = None) -> Candidate:
    details = details or {}
    c = Candidate(name, size, details.get("quantization_level", ""),
                  details.get("parameter_size", ""))  # fmt: skip
    if _embedding(name, details):
        c.fits, c.why = False, NOT_CHAT
    return c


def discover(base_url: str) -> list[Candidate]:
    """The endpoint's models: Ollama's tags (sizes known), else /v1/models. Embedding
    models are listed but left out, with the reason."""
    root = base_url.rstrip("/")
    root = root[: -len("/v1")] if root.endswith("/v1") else root
    try:
        tags = _get(root + "/api/tags")
        return [_candidate(m["name"], m.get("size"), m.get("details"))
                for m in tags.get("models", [])]  # fmt: skip
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        pass
    try:
        listed = _get(root + "/v1/models")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        from puzzle.agent.providers import ProviderError

        raise ProviderError(f"no models answer at {base_url}: {exc}") from None
    return [_candidate(m["id"]) for m in listed.get("data", [])]


def budget() -> int | None:
    """Memory a model may take: the machine's less Puzzle's and a solver's."""
    from puzzle.machine import memory_bytes

    total = memory_bytes()
    return None if total is None else max(0, total - HEADROOM)


def fit(candidates: list[Candidate], memory: int | None) -> list[Candidate]:
    for c in candidates:
        if c.fits and memory is not None and c.memory is not None and c.memory > memory:
            c.fits = False
            c.why = (f"needs about {c.memory / 1e9:.1f} GB with its context; "
                     f"{memory / 1e9:.1f} GB is free for it")  # fmt: skip
    return candidates


def results_dir() -> Path:
    from puzzle.materials import user_data_dir

    return user_data_dir() / "agent-evals"


def _safe(model: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-._" else "-" for ch in model)


def _kept(model: str, suite: str) -> Path:
    return results_dir() / f"{_safe(model)}@{suite}.json"


def planned(solvers=()) -> list:
    """The suite's tasks this machine can do: those needing a missing solver are left
    out, so a kept record is reused only when it covers these."""
    from puzzle.agent import evals

    return [t for t in evals.TASKS + evals.HARD_TASKS if not t.needs or t.needs in solvers]


def _tally(model: str, suite: str, when: str, memory: int | None, tasks: dict[str, bool],
           seconds: dict[str, float], details: dict[str, str], *,
           median: float | None = None, complete: bool = True) -> Measured:  # fmt: skip
    """A record from per-task outcomes: totals, the core families' share, the median."""
    from puzzle.agent import evals

    family = {t.name: t.family for t in evals.TASKS + evals.HARD_TASKS}
    core = [n for n in tasks if family.get(n) in evals.CORE_FAMILIES]
    times = [seconds[n] for n in tasks if n in seconds]
    if median is None:
        median = statistics.median(times) if times else 0.0
    return Measured(model=model, suite=suite, when=when, passed=sum(tasks.values()),
                    total=len(tasks), core_passed=sum(tasks[n] for n in core),
                    core_total=len(core), median_s=median, memory=memory, tasks=tasks,
                    details={n: d for n, d in details.items() if n in tasks},
                    seconds={n: s for n, s in seconds.items() if n in tasks},
                    complete=complete)  # fmt: skip


def _reuse(path: Path, names: list[str]) -> Measured | None:
    """A kept record, re-tallied over just `names`, when it measured all of them (a
    record made without a solver doesn't cover the tasks that run it)."""
    try:
        kept = Measured(**json.loads(path.read_text()))
    except (OSError, ValueError, TypeError):
        return None
    if not kept.complete or not set(names) <= set(kept.tasks):
        return None
    if set(kept.tasks) == set(names):
        return kept
    tasks = {n: kept.tasks[n] for n in names}
    # Records from before per-task seconds keep their median: the closest there is.
    median = None if kept.seconds else kept.median_s
    return _tally(kept.model, kept.suite, kept.when, kept.memory, tasks, kept.seconds,
                  kept.details, median=median)  # fmt: skip


def measure(candidate: Candidate, base_url: str, workdir: Path, *, solvers=(),
            again: bool = False, on_task=None, cancel=None) -> Measured:  # fmt: skip
    """The suite on one model (or its kept results for this suite version, when they
    cover the tasks this machine runs).

    A task the endpoint failed (connection refused, a timeout, an HTTP error) is not
    the model's failure: measuring that model stops, and the record is marked
    incomplete and not kept, so the next pick measures it again. `cancel` (a
    threading.Event) stops after the task under way, likewise not kept.
    """
    from puzzle.agent import evals
    from puzzle.agent.settings import AgentSettings

    tasks = planned(solvers)
    path = _kept(candidate.name, evals.SUITE_VERSION)
    if path.exists() and not again:
        kept = _reuse(path, [t.name for t in tasks])
        if kept is not None:
            return kept
    provider = AgentSettings(provider="openai", model=candidate.name, base_url=base_url).build()
    passed, seconds, details, complete = {}, {}, {}, True
    for task in tasks:
        if cancel is not None and cancel.is_set():
            complete = False
            details["(cancelled)"] = f"stopped before '{task.name}'"
            break
        o = evals.run_task(task, provider, workdir / _safe(candidate.name))
        if on_task is not None:
            on_task(candidate.name, o)
        if o.connection:
            complete = False
            details["(endpoint)"] = f"'{task.name}': {o.detail}"
            break
        passed[o.task], seconds[o.task] = o.passed, o.seconds
        if not o.passed:
            details[o.task] = o.detail
    m = _tally(candidate.name, evals.SUITE_VERSION,
               datetime.now(UTC).isoformat(timespec="seconds"), candidate.memory,
               passed, seconds, {}, complete=complete)  # fmt: skip
    m.details = details
    if complete:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(m), indent=1))
    return m


def rank(measured: list[Measured]) -> list[Measured]:
    return sorted(measured, key=lambda m: m.score(), reverse=True)


def recommend(ranked: list[Measured], candidates: list[Candidate],
              memory: int | None) -> tuple[str | None, str]:  # fmt: skip
    """The smallest model at the best pass rate, and why, in words."""
    gb = (lambda b: f"{b / 1e9:.1f} GB") if memory else (lambda b: "?")  # noqa: E731
    if not candidates:
        return None, ("The endpoint serves no models. Pull one there (for Ollama: ollama pull "
                      "qwen3:8b), or use Claude in Settings > Agent.")  # fmt: skip
    if not ranked:
        if any(c.fits for c in candidates):  # tried, but none measured in full
            return None, ("No model was measured in full. Use Claude in Settings > Agent "
                          "meanwhile.")  # fmt: skip
        sizes = ", ".join(f"{c.name} ({c.why})" for c in candidates if not c.fits)
        return None, (f"None of the models fits this machine: {sizes}. Use Claude in "
                      "Settings > Agent, or a smaller chat model.")  # fmt: skip
    best = ranked[0]
    if best.core_passed < best.core_total:
        return None, (f"No local model passes the core tasks: the best, {best.model}, "
                      f"passed {best.core_passed} of {best.core_total}. Claude does them all; "
                      "use it in Settings > Agent for model changes.")  # fmt: skip
    top = [m for m in ranked if m.core_passed == best.core_passed and m.passed == best.passed]
    smallest = min(top, key=lambda m: (m.memory is None, m.memory or 0))  # unknown last
    words = (f"{smallest.model}: {smallest.passed} of {smallest.total} tasks, every core one, "
             f"{smallest.median_s:.0f} s a task")  # fmt: skip
    if smallest.memory:
        words += f", about {gb(smallest.memory)} of memory"
    others = [m for m in ranked if m is not smallest][:2]
    if others:
        words += ". Next: " + "; ".join(
            f"{m.model} {m.passed}/{m.total}, {m.median_s:.0f} s" for m in others
        )
    return smallest.model, words + "."


def pick(base_url: str = "http://localhost:11434/v1", workdir: Path | None = None, *,
         solvers=(), only: list[str] | None = None, again: bool = False,
         on_task=None, cancel=None) -> Pick:  # fmt: skip
    """Find, fit, measure and rank the endpoint's models (see the module's doc).
    `cancel` (a threading.Event) stops after the task under way; what was measured in
    full is still ranked."""
    import tempfile

    memory = budget()
    candidates = fit(discover(base_url), memory)
    if only:
        candidates = [c for c in candidates if c.name in only]
    work = workdir or Path(tempfile.mkdtemp(prefix="puzzle-pick-"))
    measured = []
    for c in candidates:
        if not c.fits:
            continue
        if cancel is not None and cancel.is_set():
            break
        measured.append(measure(c, base_url, work, solvers=solvers, again=again,
                                on_task=on_task, cancel=cancel))  # fmt: skip
    ranked = rank([m for m in measured if m.complete])
    incomplete = [m for m in measured if not m.complete]
    name, words = recommend(ranked, candidates, memory)
    cancelled = cancel is not None and cancel.is_set()
    if incomplete:
        why = "; ".join(f"{m.model} ({next(iter(m.details.values()), '')})" for m in incomplete)
        failed = any("(endpoint)" in m.details for m in incomplete)
        words += (f" Not measured in full, so not ranked and not kept: {why}. "
                  + ("The endpoint failed, not the model: check it and pick again."
                     if failed else "Pick again to finish."))  # fmt: skip
    elif cancelled:
        words += " Cancelled: the models not reached were not measured."
    return Pick(candidates, ranked, name, words, memory, incomplete, cancelled)


def choose(model: str, base_url: str) -> Path:
    """The pick into agent.toml: the provider, model and endpoint, with when and by
    which suite (ADR 0022 decision 1)."""
    from puzzle.agent import evals
    from puzzle.agent.settings import load_settings, save_settings

    s = load_settings()
    s.provider, s.model, s.base_url = "openai", model, base_url
    s.picked = f"{datetime.now(UTC).date().isoformat()} suite {evals.SUITE_VERSION}"
    return save_settings(s)
