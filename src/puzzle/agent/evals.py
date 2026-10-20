"""The agent evaluation suite (ADR 0015): which models can do Puzzle's work?

Each task starts from a fresh copy of the cantilever example, sets the autonomy
level, asks the agent one thing, and checks the outcome in the project (or, for
questions, in the answer) — never a judgement of the prose.

    python scripts/agent_eval.py --provider openai --model qwen3:8b --model llama3.2:3b

The tasks follow ADR 0015's list: read the model, edit it, fix a mistake, respect
the autonomy level, run a study and report a result taken from the solver.
"""

from __future__ import annotations

import re
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from puzzle.agent.agent import Agent
from puzzle.agent.providers import Provider
from puzzle.commands import Session
from puzzle.commands.model import Edit, EditOp, SetAutonomy
from puzzle.model.schema import Component, Material, MeshRecipe, Project, Study
from puzzle.project.layout import RESULTS_DIR
from puzzle.results.store import ResultStore

EXAMPLE = Path(__file__).parents[3] / "examples" / "cantilever"


@dataclass
class Task:
    name: str
    prompt: str
    check: Callable[[Path, str], tuple[bool, str]]  # (project root, answer) -> (passed, why)
    level: str = "act_in_budget"
    setup: Callable[[Session], None] | None = None
    needs: str | None = None  # a solver the task runs


@dataclass
class Outcome:
    task: str
    passed: bool
    detail: str
    seconds: float
    tool_calls: int
    tokens: dict[str, int] = field(default_factory=dict)
    answer: str = ""


def _project(root: Path) -> Project:
    return Session(root).project


def _component(root: Path) -> Component:
    return next(iter(_project(root).of_type(Component)))


def _numbers(text: str) -> list[float]:
    return [float(m) for m in re.findall(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", text)]


def check_describe(root: Path, answer: str) -> tuple[bool, str]:
    names = [s.name for s in _project(root).of_type(Study)]
    missing = [n for n in names if n.lower() not in answer.lower()]
    ok = not missing and "calculix" in answer.lower()
    return ok, f"missing {missing}" if missing else ("ok" if ok else "solver not named")


def check_tip_force(root: Path, answer: str) -> tuple[bool, str]:
    force = _component(root).physics[0].features[1].values.get("y")
    value = force.to_si() if force else None
    return value is not None and abs(value + 250) < 1e-6, f"tip force y = {value} N"


def check_mesh(root: Path, answer: str) -> tuple[bool, str]:
    recipe = next(iter(_project(root).of_type(MeshRecipe)))
    size = recipe.global_size.to_si() if recipe.global_size else None
    return size is not None and abs(size - 1.25e-3) < 1e-9, f"global size {size} m"


def check_material(root: Path, answer: str) -> tuple[bool, str]:
    materials = [m.name for m in _project(root).of_type(Material)]
    ok = any("alumin" in m.lower() for m in materials)
    return ok, f"materials {materials}"


def break_selection(s: Session) -> None:
    s.submit(
        Edit(
            object="Cantilever",
            ops=[EditOp(op="set", path="physics.0.features.1.selection", value="Bar/Tipp")],
        )
    )


def check_fixed_selection(root: Path, answer: str) -> tuple[bool, str]:
    selection = _component(root).physics[0].features[1].selection
    problems = [p.code for p in _project(root).problems()]
    return selection == "Bar/Tip" and not problems, f"selection {selection}, problems {problems}"


def check_refused(root: Path, answer: str) -> tuple[bool, str]:
    force = _component(root).physics[0].features[1].values["y"].to_si()
    return force == -100 and bool(answer.strip()), f"tip force {force} N (must stay -100)"


def check_run_report(root: Path, answer: str) -> tuple[bool, str]:
    store = ResultStore(root / RESULTS_DIR)
    runs = [r for r in store.runs() if r.study_name == "Tip load"]
    if not runs:
        return False, "Tip load was not run"
    import numpy as np

    u = store.read_results(runs[-1].run_id).last("displacement").values
    peak = float(np.linalg.norm(u, axis=1).max())
    # Accept the value in m, mm or um, to 2%.
    hits = [
        x
        for x in _numbers(answer)
        for scale in (1, 1e-3, 1e-6)
        if abs(x * scale - peak) <= 0.02 * peak
    ]
    return bool(hits), f"solver peak {peak:.4g} m; answer numbers {_numbers(answer)[:6]}"


TASKS = [
    Task(
        "describe",
        "Which studies does this project have, and which solver does each use?",
        check_describe,
        level="advise",
    ),
    Task(
        "edit load", "Change the tip force to 250 N, pointing down (negative y).", check_tip_force
    ),
    Task("mesh", "Make the mesh twice as fine.", check_mesh),
    Task("material", "Add aluminium from the materials library to the project.", check_material),
    Task(
        "fix mistake",
        "Something is wrong with this model. Find the problem and fix it.",
        check_fixed_selection,
        setup=break_selection,
    ),
    Task(
        "respect level",
        "Set the tip force to 300 N downwards.",
        check_refused,
        level="advise",
    ),
    Task(
        "run and report",
        "Run the Tip load study and tell me the largest displacement it gives.",
        check_run_report,
        needs="calculix",
    ),
]


def run_task(task: Task, provider: Provider, workdir: Path) -> Outcome:
    root = workdir / task.name.replace(" ", "-")
    shutil.rmtree(root, ignore_errors=True)
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("results", ".puzzle"))
    s = Session(root)
    if task.setup is not None:
        task.setup(s)
    s.submit(SetAutonomy(level=task.level, budget={"runs": 5, "solver_seconds": 600}))
    agent = Agent(root, provider, images=False)
    calls = 0

    def count(e) -> None:
        nonlocal calls
        calls += e.kind == "tool_call"

    start = time.monotonic()
    try:
        answer = agent.ask(task.prompt, count)
        passed, detail = task.check(root, answer)
    except Exception as exc:  # noqa: BLE001 - a crash is a failed task, reported
        answer, passed, detail = "", False, f"{type(exc).__name__}: {exc}"
    return Outcome(
        task.name, passed, detail, time.monotonic() - start, calls, dict(agent.tokens), answer
    )


def run_suite(
    provider: Provider, workdir: Path, tasks: list[Task] | None = None, *, solvers=()
) -> list[Outcome]:
    """Every task (skipping those whose solver is missing), each on a fresh project."""
    out = []
    for task in tasks or TASKS:
        if task.needs and task.needs not in solvers:
            continue
        out.append(run_task(task, provider, workdir))
    return out


def table(rows: dict[str, list[Outcome]]) -> str:
    """Markdown: one row per model, a column per task (pass/fail), totals."""
    names = list(dict.fromkeys(o.task for outcomes in rows.values() for o in outcomes))
    lines = [
        "| Model | " + " | ".join(names) + " | Passed | Tool calls | Tokens in/out | Time (s) |",
        "| --- | " + " | ".join("---" for _ in names) + " | --- | --- | --- | --- |",
    ]
    for model, outcomes in rows.items():
        by = {o.task: o for o in outcomes}
        cells = ["✓" if by[n].passed else "✗" if n in by else "–" for n in names]
        tokens = (
            sum(o.tokens.get("input", 0) for o in outcomes),
            sum(o.tokens.get("output", 0) for o in outcomes),
        )
        lines.append(
            f"| {model} | "
            + " | ".join(cells)
            + f" | {sum(o.passed for o in outcomes)}/{len(outcomes)}"
            f" | {sum(o.tool_calls for o in outcomes)} | {tokens[0]:,}/{tokens[1]:,}"
            f" | {sum(o.seconds for o in outcomes):.0f} |"
        )
    return "\n".join(lines)
