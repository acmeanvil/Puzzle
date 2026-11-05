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
version), so a later pick compares like with like and a model is measured once per
suite. The choice goes in `agent.toml` with the date and the suite's version. Nothing
is downloaded: only models already on the endpoint are tried. When none fits or none
passes the core families, the advice is a remote model through the agent setup
(Claude first, ADR 0015), saying what the machine lacks.
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

    def score(self) -> tuple:
        return (self.core_passed, self.passed, -self.median_s, -(self.memory or 0))


@dataclass
class Pick:
    """What `pick` found: every candidate, the measured ones ranked, the advice."""

    candidates: list[Candidate]
    ranked: list[Measured]
    recommended: str | None
    words: str
    memory_budget: int | None

    def to_dict(self) -> dict:
        return {"candidates": [asdict(c) for c in self.candidates],
                "ranked": [asdict(m) for m in self.ranked], "recommended": self.recommended,
                "words": self.words, "memory_budget": self.memory_budget}  # fmt: skip


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=TIMEOUT_S) as r:  # noqa: S310 - a local endpoint
        return json.loads(r.read())


def discover(base_url: str) -> list[Candidate]:
    """The endpoint's models: Ollama's tags (sizes known), else /v1/models."""
    root = base_url.rstrip("/")
    root = root[: -len("/v1")] if root.endswith("/v1") else root
    try:
        tags = _get(root + "/api/tags")
        return [Candidate(m["name"], m.get("size"),
                          (m.get("details") or {}).get("quantization_level", ""),
                          (m.get("details") or {}).get("parameter_size", ""))
                for m in tags.get("models", [])]  # fmt: skip
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        pass
    try:
        listed = _get(root + "/v1/models")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        from puzzle.agent.providers import ProviderError

        raise ProviderError(f"no models answer at {base_url}: {exc}") from None
    return [Candidate(m["id"]) for m in listed.get("data", [])]


def budget() -> int | None:
    """Memory a model may take: the machine's less Puzzle's and a solver's."""
    from puzzle.machine import memory_bytes

    total = memory_bytes()
    return None if total is None else max(0, total - HEADROOM)


def fit(candidates: list[Candidate], memory: int | None) -> list[Candidate]:
    for c in candidates:
        if memory is not None and c.memory is not None and c.memory > memory:
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


def measure(candidate: Candidate, base_url: str, workdir: Path, *, solvers=(),
            again: bool = False, on_task=None) -> Measured:  # fmt: skip
    """The suite on one model (or its kept results for this suite version)."""
    from puzzle.agent import evals
    from puzzle.agent.settings import AgentSettings

    path = _kept(candidate.name, evals.SUITE_VERSION)
    if path.exists() and not again:
        return Measured(**json.loads(path.read_text()))
    provider = AgentSettings(provider="openai", model=candidate.name, base_url=base_url).build()
    outcomes = []
    for task in evals.TASKS + evals.HARD_TASKS:
        if task.needs and task.needs not in solvers:
            continue
        o = evals.run_task(task, provider, workdir / _safe(candidate.name))
        outcomes.append((task, o))
        if on_task is not None:
            on_task(candidate.name, o)
    core = [(t, o) for t, o in outcomes if t.family in evals.CORE_FAMILIES]
    m = Measured(
        model=candidate.name, suite=evals.SUITE_VERSION,
        when=datetime.now(UTC).isoformat(timespec="seconds"),
        passed=sum(o.passed for _t, o in outcomes), total=len(outcomes),
        core_passed=sum(o.passed for _t, o in core), core_total=len(core),
        median_s=statistics.median([o.seconds for _t, o in outcomes]) if outcomes else 0.0,
        memory=candidate.memory, tasks={o.task: o.passed for _t, o in outcomes},
        details={o.task: o.detail for _t, o in outcomes if not o.passed},
    )  # fmt: skip
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
        sizes = ", ".join(f"{c.name} ({c.why})" for c in candidates if not c.fits)
        return None, (f"None of the models fits this machine: {sizes}. Use Claude in "
                      "Settings > Agent, or a smaller model.")  # fmt: skip
    best = ranked[0]
    if best.core_passed < best.core_total:
        return None, (f"No local model passes the core tasks: the best, {best.model}, "
                      f"passed {best.core_passed} of {best.core_total}. Claude does them all; "
                      "use it in Settings > Agent for model changes.")  # fmt: skip
    top = [m for m in ranked if m.core_passed == best.core_passed and m.passed == best.passed]
    smallest = min(top, key=lambda m: m.memory or 0)
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
         on_task=None) -> Pick:  # fmt: skip
    """Find, fit, measure and rank the endpoint's models (see the module's doc)."""
    import tempfile

    memory = budget()
    candidates = fit(discover(base_url), memory)
    if only:
        candidates = [c for c in candidates if c.name in only]
    work = workdir or Path(tempfile.mkdtemp(prefix="puzzle-pick-"))
    measured = [measure(c, base_url, work, solvers=solvers, again=again, on_task=on_task)
                for c in candidates if c.fits]  # fmt: skip
    ranked = rank(measured)
    name, words = recommend(ranked, candidates, memory)
    return Pick(candidates, ranked, name, words, memory)


def choose(model: str, base_url: str) -> Path:
    """The pick into agent.toml: the provider, model and endpoint, with when and by
    which suite (ADR 0022 decision 1)."""
    from puzzle.agent import evals
    from puzzle.agent.settings import load_settings, save_settings

    s = load_settings()
    s.provider, s.model, s.base_url = "openai", model, base_url
    s.picked = f"{datetime.now(UTC).date().isoformat()} suite {evals.SUITE_VERSION}"
    return save_settings(s)
