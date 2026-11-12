"""Model → Studies → Load combinations… (ADR 0079 step 2): one form generating ASCE 7's
LRFD or ASD combinations from the cases a frame's loads use, or clearing them, as one
Edit. Each load's case is its `case` in Properties."""

from __future__ import annotations

from puzzle.commands.model import Edit
from puzzle.gui.quick_form import Ask

METHODS = [
    ("ASCE 7-16 LRFD (2.3.1)", "LRFD"),
    ("ASCE 7-16 ASD (2.4.1)", "ASD"),
    ("None: every load at its value", "none"),
]


def edit_combinations(window, study):
    from puzzle.frames.combinations import asce7, cases_of, combination_ops

    comp = window.session.project.objects.get(study.component)
    cases = cases_of(comp)
    now = ", ".join(c.name for c in study.combinations) or "none"
    got = window.ask_form(
        f"Load combinations of {study.name}",
        [Ask("method", "Combinations", choices=METHODS)],
        intro=f"The loads' cases: {', '.join(cases) or 'none yet (each load’s Case)'}. "
        f"Now: {now}. Wind and seismic act both ways. PyNite solves them, one result each.",
        check=lambda v: None if v["method"] == "none" or "D" in cases
        else "ASCE 7's combinations start from the dead load: give a load the case D",
    )  # fmt: skip
    if got is None:
        return None
    combos = [] if got["method"] == "none" else asce7(cases, got["method"])
    return window.submit(Edit(object=str(study.id), ops=combination_ops(study, combos)))
