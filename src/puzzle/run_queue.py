"""Several studies run one after another (ADR 0073): Run All, Run selected… and Stop
all in the GUI, `puzzle run --all` or several `--study` on the command line.

One study runs at a time, in order: a study another one reads through a transfer runs
before it, the rest keep the project's order. A study that fails doesn't stop the queue:
the next one starts, and the failure is reported at the end. A stop ends the queue: the
running study is cancelled and the rest are not started.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from graphlib import TopologicalSorter
from typing import Any
from uuid import UUID


def in_order(project, ids: Iterable[UUID | str] | None = None) -> list:
    """The studies to run (every study when `ids` is None), sources of transfers first,
    otherwise in the project's own order."""
    from puzzle.model.schema import Study, Transfer

    studies = project.of_type(Study)
    if ids is not None:
        wanted = {UUID(str(i)) for i in ids}
        studies = [s for s in studies if s.id in wanted]
    chosen = {s.id for s in studies}
    rank = {s.id: n for n, s in enumerate(studies)}
    graph = {s.id: [t.source for t in project.of_type(Transfer)
                    if t.target == s.id and t.source in chosen] for s in studies}  # fmt: skip
    sorter = TopologicalSorter(graph)
    sorter.prepare()
    out = []
    while sorter.is_active():
        ready = sorted(sorter.get_ready(), key=rank.get)
        out += ready
        sorter.done(*ready)
    by_id = {s.id: s for s in studies}
    return [by_id[i] for i in out]


@dataclass
class Outcome:
    study: Any
    outcome: Any = None  # the session's outcome when it ran
    error: Exception | None = None  # why it failed

    @property
    def ok(self) -> bool:
        return self.error is None


def run_all(session, studies: list, *, stop: threading.Event | None = None,
            on_start: Callable[[Any], None] | None = None,
            submit: Callable[[Any], Any] | None = None) -> list[Outcome]:  # fmt: skip
    """Run `studies` one by one through `session` (or `submit(study)`), going on past a
    failure; `stop` set ends the queue before the next study."""
    from puzzle.commands.model import RunStudy

    done: list[Outcome] = []
    for study in studies:
        if stop is not None and stop.is_set():
            break
        if on_start is not None:
            on_start(study)
        try:
            got = (submit or (lambda s: session.submit(RunStudy(study=str(s.id)))))(study)
            done.append(Outcome(study, outcome=got))
        except Exception as exc:  # noqa: BLE001 - reported per study, the queue goes on
            done.append(Outcome(study, error=exc))
    return done


def summary(done: list[Outcome], left: int = 0) -> str:
    """One line: "3 studies: 2 finished, 1 failed (Thermal)", and any not started."""
    failed = [o.study.name for o in done if not o.ok]
    word = "study" if len(done) == 1 else "studies"
    text = f"{len(done)} {word}: {len(done) - len(failed)} finished"
    if failed:
        text += f", {len(failed)} failed ({', '.join(failed)})"
    if left:
        text += f"; {left} not started (stopped)"
    return text
