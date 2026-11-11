"""What state each object is in (ADR 0033): one answer for the tree, the outline and
the CLI.

- complete: nothing missing;
- missing: a problem (`Project.problems()`) names it, with that problem as the reason;
- unused: nothing uses it (a material assigned nowhere);
- studies and derived results:
  - up_to_date: a stored run matches the model as it is (its run key is current);
  - out_of_date: runs exist, but the model changed since;
  - not_run;
  - failed: the latest run failed;
  - running: the GUI says a run is going.

`worst` rolls states up (a section shows its worst object), most urgent first.
"""

from __future__ import annotations

from dataclasses import dataclass

from puzzle.model.schema import Component, Material, Project, ResultDefinition, Study

# Most urgent first: a roll-up shows the first that any child has.
ORDER = [
    "failed",
    "missing",
    "out_of_date",
    "running",
    "not_run",
    "unused",
    "up_to_date",
    "complete",
]
WORDS = {
    "failed": "Failed",
    "missing": "Missing input",
    "out_of_date": "Out of date",
    "running": "Running",
    "not_run": "Not run yet",
    "unused": "Not used",
    "up_to_date": "Up to date",
    "complete": "Complete",
}


@dataclass(frozen=True)
class State:
    state: str
    reason: str = ""

    @property
    def words(self) -> str:
        return WORDS[self.state] + (f": {self.reason}" if self.reason else "")


def worst(states) -> str:
    present = {s.state if isinstance(s, State) else s for s in states}
    return next((s for s in ORDER if s in present), "complete")


def status(project: Project, runs=(), running=frozenset(), failed=frozenset()) -> dict[str, State]:
    """Object id -> its state. `runs` are the store's records (completed runs only);
    `running` and `failed` are study ids the GUI knows about (a failed run isn't
    stored)."""
    from puzzle.project.graph import run_hashes

    problems: dict[str, list[str]] = {}
    for p in project.problems():
        problems.setdefault(p.object, []).append(p.message)
    used = {a.material for c in project.of_type(Component) for a in c.materials}
    keys = run_hashes(project)
    out: dict[str, State] = {}
    for obj in project.objects.values():
        found = problems.get(obj.name)
        if found:
            more = f" (and {len(found) - 1} more)" if len(found) > 1 else ""
            out[str(obj.id)] = State("missing", found[0] + more)
        elif isinstance(obj, Material) and obj.id not in used:
            out[str(obj.id)] = State("unused", "not assigned to any part")
        elif isinstance(obj, Study):
            out[str(obj.id)] = _study(obj, keys.get(obj.id, {}), runs, running, failed)
        else:
            out[str(obj.id)] = State("complete")
    for d in project.of_type(ResultDefinition):  # as fresh as the studies it reads
        if out[str(d.id)].state == "missing":
            continue
        inputs = [out.get(str(i)) for i in d.dependencies()]
        state = worst(s for s in inputs if s is not None)
        if state in ("out_of_date", "not_run", "failed"):
            out[str(d.id)] = State(state, "a study it reads is " + WORDS[state].lower())
        else:
            out[str(d.id)] = State("up_to_date" if state == "up_to_date" else "complete")
    _operators(project, out)
    return out


def _operators(project: Project, out: dict[str, State]) -> None:
    """Operators (ADR 0062) are as fresh as what they read, through other operators."""
    from puzzle.model.schema import Operator, ResultProbe

    done: set[str] = set()

    def of(obj, seen=()) -> State | None:
        key = str(obj.id)
        if isinstance(obj, ResultProbe):  # a result tool is its source's
            src = project.objects.get(obj.source)
            return of(src, seen) if src is not None else None
        if not isinstance(obj, Operator) or key in done or key in seen:
            return out.get(key)
        if out[key].state != "missing":
            inputs = [of(project.objects[d], (*seen, key)) for d in obj.dependencies()
                      if d in project.objects]  # fmt: skip
            state = worst(s for s in inputs if s is not None)
            if state in ("out_of_date", "not_run", "failed"):
                out[key] = State(state, "a study it reads is " + WORDS[state].lower())
            elif state == "missing":
                out[key] = State(state, "something it reads has a missing input")
            else:
                out[key] = State("up_to_date" if state == "up_to_date" else "complete")
        done.add(key)
        return out[key]

    for op in project.of_type(Operator):
        of(op)


def still_failed(failed: dict, runs) -> dict:
    """The failures (study id -> when, UTC) no completed run has been stored after: a
    study that failed in the GUI and then solved from the agent or the CLI is not failed."""
    later = {r.study_id for r in runs for when in [failed.get(r.study_id)]
             if when is not None and r.status == "completed" and r.recorded_at > when}  # fmt: skip
    return {sid: when for sid, when in failed.items() if sid not in later}


def _study(study: Study, keys: dict[str, list[str]], runs, running, failed) -> State:
    if str(study.id) in running:
        return State("running")
    if str(study.id) in failed:
        return State("failed", "the last run failed; see Messages")
    mine = [r for r in runs if r.study_id == str(study.id)]
    if not mine:
        return State("not_run", "run it to get results")
    current = set(keys.get(study.solver, []))
    done = {r.run_key for r in mine if r.status == "completed"}
    if current and current <= done:
        return State("up_to_date")
    return State("out_of_date", "the model changed since the last run")


def run_state(record, project: Project) -> State:
    """A stored run against the model as it is now."""
    from puzzle.project.graph import run_hashes

    study = next((s for s in project.of_type(Study) if str(s.id) == record.study_id), None)
    if study is None:
        return State("out_of_date", "its study is gone")
    keys = {k for ks in run_hashes(project).get(study.id, {}).values() for k in ks}
    if record.run_key in keys:
        return State("up_to_date")
    return State("out_of_date", "the model changed since this run")
