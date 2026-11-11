"""Bolt preload in a step of its own (ADR 0037 amended, 2026-11-11).

A joint tightened and loaded in one step asks the solver to find the bolts' clamp and
the service load's share of it at once; with contact between the clamped parts that
is the hardest form of the problem (the pressure vessel assembly's frictional step
chattered for 21 iterations and timed out). Tightening first, then locking each bolt
at its tightened length and loading, is how a joint is assembled and how VDI 2230
reads it: the external load then shares between bolt and clamped parts by their
stiffness (F_S = F_V + Φ F_A).

`applied(project, study)` puts a preload-only step in front of the study's first
static structural step when the study tightens bolts and that step also carries
external loads: a copy of the step (its settings too), holding the supports and the
pretensions only. The loads move to the steps after; a prescribed displacement that
isn't zero is held at zero while preloading. Every adapter then locks the pretension
after its step (CalculiX `*BOUNDARY, FIXED`; Code_Aster `DIDI`, the static steps
chained in one STAT_NON_LINE result). It is solver-neutral and idempotent: a study
that already preloads on its own is left as it is.

`Study.preload_step`: "auto" (the default: as above, on the solvers that lock a
pretension into a later step, CalculiX and Code_Aster), "always" (on any solver),
"never" (tightened with the loads, as before).
"""

from __future__ import annotations

from dataclasses import replace

from puzzle.model.schema import Component, Project, Study

PRETENSION = frozenset({"bolt_pretension", "bolt_adjustment"})
# Features that hold the structure rather than load it: they stay in the preload step.
SUPPORTS = frozenset({"fixed", "elastic_support", "compression_only_support"})
# Held at zero while preloading when their values aren't zero; supports when they are.
IMPOSED = frozenset({"displacement", "remote_displacement"})
# Solvers that carry a pretension's locked length into later static steps.
LOCKING = frozenset({"calculix", "code_aster"})
KIND = "preload"  # the preload step's results' kind; StepResult.label says "Preload"


def _first_static(study: Study) -> int | None:
    """The first solve step (1, 2, ...) that loads the structure, when it is static."""
    for i, s in enumerate(study.solve_steps(), start=1):
        if s.solves("solid_mechanics") and s.type not in ("modal", "buckling"):
            return i if s.type == "stationary" else None
    return None


def _zero(f) -> bool:
    return all(abs(q.value) == 0 for q in f.values.values())


def _tightened(comp: Component, k: int) -> bool:
    if any(c.pretension is not None for c in comp.connectors):
        return True
    return any(
        f.type in PRETENSION and f.active(k)
        for ph in comp.physics if ph.type == "solid_mechanics" for f in ph.features
    )  # fmt: skip


def _loads(comp: Component, k: int) -> bool:
    """Whether step k carries anything but supports and pretensions."""
    for ph in comp.physics:
        if ph.type != "solid_mechanics":
            continue
        for f in ph.features:
            if not f.active(k) or f.type in SUPPORTS | PRETENSION:
                continue
            if f.type in IMPOSED and _zero(f):
                continue
            return True
    return False


def needed(project: Project, study: Study, solver: str | None = None) -> int | None:
    """The solve step a preload step goes in front of, or None. Bolt types' preloads
    count (`bolts.resolved`)."""
    mode = getattr(study, "preload_step", "auto")
    if mode == "never":
        return None
    if mode == "auto" and (solver or study.solver) not in LOCKING:
        return None
    comp = project.objects.get(study.component)
    if not isinstance(comp, Component) or not any(
        ph.type == "solid_mechanics" for ph in comp.physics
    ):
        return None
    k = _first_static(study)
    if k is None:
        return None
    if comp.connectors and any(c.type is not None for c in comp.connectors):
        from puzzle.bolts import resolved

        comp = resolved(project).objects.get(study.component, comp)
    if not _tightened(comp, k) or not _loads(comp, k):
        return None
    return k


def applied(project: Project, study: Study) -> tuple[Project, Study, int | None]:
    """The project and study with a preload step at the index `needed` gives (returned
    too, for labelling its results), or both unchanged and None."""
    k = needed(project, study)
    if k is None:
        return project, study, None
    comp = project.get(study.component, Component)
    steps = list(study.steps)
    from puzzle.model.schema import SolveStep

    solve = [i for i, s in enumerate(steps) if isinstance(s, SolveStep)]
    n = len(solve)  # solve steps before; n + 1 after

    def moved(i: int) -> int:  # an old solve step's new number
        return i + 1 if i >= k else i

    after = [i for i in range(1, n + 2) if i != k]  # every new step but the preload

    def features(ph):
        out = []
        for f in ph.features:
            if ph.type != "solid_mechanics":
                out.append(f.model_copy(update={"steps": [moved(i) for i in f.steps]}))
                continue
            held = f.type in SUPPORTS | PRETENSION or (f.type in IMPOSED and _zero(f))
            if held:  # supports and pretensions: in the preload step too
                kept = {moved(i) for i in f.steps} | ({k} if f.active(k) else set())
                out.append(f.model_copy(update={"steps": sorted(kept) if f.steps else []}))
                continue
            loads = [moved(i) for i in f.steps] if f.steps else after
            if f.type in IMPOSED and f.active(k):  # held still while the bolts go in
                zeros = {key: q.model_copy(update={"value": 0.0}) for key, q in f.values.items()}
                out.append(f.model_copy(update={"values": zeros, "steps": [k]}))
            out.append(f.model_copy(update={"steps": loads}))
        return out

    physics = [ph.model_copy(update={"features": features(ph)}) for ph in comp.physics]
    first = solve[k - 1]  # its position in study.steps (sweeps come first)
    steps.insert(first, steps[first].model_copy(deep=True))
    new_study = study.model_copy(update={"steps": steps})
    copy = replace(project, objects=dict(project.objects))
    copy.objects[comp.id] = comp.model_copy(update={"physics": physics})
    copy.objects[study.id] = new_study
    return copy, new_study, k
