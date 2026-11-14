"""Load combinations on the finite-element solvers (ADR 0079 step 2, 2026-11-13).

PyNite solves a study's `combinations` itself (load cases and combos). Every other
solver gets them from `applied(project, study)`: the study's one static step repeated
once per combination, each load with a `case` copied into the steps whose combination
factors that case, its values times the factor; loads without a case and supports stay
in every step. So a combination is solved as a load, not added up afterwards: right
under contact or large deflection too, where superposition isn't. Results come back as
one step per combination, kind "combination", named by the global field `combination`,
as PyNite's are, so member checks, envelopes and the Plots panel read them alike.

Features whose values aren't a load to scale (temperatures, imposed displacements,
pretensions) can't belong to a case: refused with the reason.

With a response spectrum (a modal step after the static one, `response_spectrum`, ADR
0079 step 4), the spectrum is the seismic load: loads in case Ex or Ey are left out, as
PyNite does, and a combination factoring Ex or Ey is solved without them and then given
twice by `add_seismic`, "(+E)" and "(−E)": its static fields plus and minus the factored
CQC envelope of the spectrum's direction (`puzzle.spectra`).
"""

from __future__ import annotations

import numpy as np

from puzzle.errors import PuzzleError
from puzzle.model.schema import Component, Project, SolveStep, Study

KIND = "combination"
SEISMIC = {"Ex": 0, "Ey": 1}  # the spectrum's cases, by direction (ADR 0079 step 4)
# Feature types whose values are a load that factors linearly.
SCALABLE = frozenset({
    "force", "pressure", "traction", "line_load", "gravity", "acceleration", "rotation",
    "remote_force", "remote_force_rigid", "body_force", "heat_flux", "heat_generation",
    "moment",
})  # fmt: skip


def needed(study: Study, caps) -> bool:
    """Whether `applied` should expand the study: it has combinations and the solver
    doesn't take them itself (`Capabilities.frame_analysis`)."""
    return bool(study.combinations) and not caps.frame_analysis


def _spectrum_step(study: Study, solve: list[int]):
    """The modal step after the static one that asks for a response spectrum, or None."""
    if len(solve) == 2:
        step = study.steps[solve[1]]
        if step.type == "modal" and step.settings.get("response_spectrum"):
            return step
    return None


def applied(project: Project, study: Study):
    """The project and study with a static step per combination (and no combinations
    left for the adapter), the combinations' names in step order, and each one's
    seismic factors when a spectrum is the seismic load ({k: {direction: factor}})."""
    solve = [i for i, s in enumerate(study.steps) if isinstance(s, SolveStep)]
    spectrum = _spectrum_step(study, solve)
    statics = solve[:1] if spectrum is not None else solve
    if len(statics) != 1 or study.steps[statics[0]].type != "stationary":
        raise PuzzleError(
            f"study '{study.name}': load combinations take one static step (and, for a "
            f"response spectrum, a modal step after it); this study has {len(solve)} "
            "solve step(s)",
            code="unsupported",
        )
    comp = project.get(study.component, Component)
    combos = list(study.combinations)
    seismic: dict[int, dict[int, float]] = {}
    if spectrum is not None:
        for k, c in enumerate(combos, start=1):
            found = {SEISMIC[case]: f for case, f in c.factors.items() if case in SEISMIC and f}
            if found:
                seismic[k] = found
    used = {case for c in combos for case in c.factors
            if spectrum is None or case not in SEISMIC}  # fmt: skip
    physics = []
    for ph in comp.physics:
        features = []
        for f in ph.features:
            if f.case is None:  # a support, or a load in every combination as it is
                features.append(f.model_copy(update={"steps": []}))
                continue
            if f.type not in SCALABLE:
                raise PuzzleError(
                    f"'{f.type}' on {f.selection} is in load case {f.case}, but only loads "
                    "factor in a combination (forces, pressures, body loads...)",
                    code="unsupported",
                )
            if f.case not in used:
                continue  # in no combination (or the spectrum's seismic case): left out
            for k, c in enumerate(combos, start=1):
                factor = c.factors.get(f.case, 0.0)
                if factor == 0:
                    continue
                values = {key: q.model_copy(update={"value": q.value * factor})
                          for key, q in f.values.items()}  # fmt: skip
                features.append(f.model_copy(update={"values": values, "steps": [k], "case": None}))
        physics.append(ph.model_copy(update={"features": features}))
    steps = list(study.steps)
    one = steps[statics[0]]
    steps[statics[0] : statics[0] + 1] = [one.model_copy(deep=True) for _ in combos]
    new_comp = comp.model_copy(update={"physics": physics})
    new_study = study.model_copy(update={"steps": steps, "combinations": []})
    objects = dict(project.objects)
    objects[comp.id] = new_comp
    objects[study.id] = new_study
    from dataclasses import replace

    return replace(project, objects=objects), new_study, [c.name for c in combos], seismic


def add_seismic(results, names: list[str], seismic: dict[int, dict[int, float]]) -> None:
    """Each combination factoring the spectrum's directions, as two steps: its static
    fields plus and minus the sum of |factor| × that direction's CQC envelope; a
    combination whose seismic factors are all negative is the "+E" one's twin, so it
    goes (the spectrum acts both ways), as PyNite's."""
    from puzzle.results.model import Field, StepResult

    if not seismic:
        return
    envelopes = {int(s.value): s for s in results.steps if s.kind == "spectrum"}
    out = []
    for step in results.steps:
        k = int(step.value) if step.kind == KIND else 0
        factors = seismic.get(k)
        if not factors:
            out.append(step)
            continue
        if all(f < 0 for f in factors.values()):
            continue
        for sense, mark in ((1.0, "+"), (-1.0, "−")):
            fields = {}
            for name, f in step.fields.items():
                add = np.zeros_like(f.values)
                for axis, factor in factors.items():
                    env = envelopes.get(axis)
                    if env is not None and name in env.fields and f.location != "global":
                        add = add + abs(factor) * env.fields[name].at(f.ids)
                fields[name] = Field(f.name, f.location, f.components, f.ids,
                                     f.values + sense * add)  # fmt: skip
            label_ = f"{names[k - 1]} ({mark}E)"
            fields["combination"] = Field("combination", "global", [label_], np.array([0]),
                                          np.array([[float(k)]]))  # fmt: skip
            out.append(StepResult(step.index, KIND, float(k), step.mode, fields))
    results.steps = out


def label(results, names: list[str], first: int = 1) -> None:
    """Mark the combination steps' results: step `first` + k - 1 is combination k (a
    preload step in front shifts them by one)."""
    from puzzle.results.model import Field

    for step in results.steps:
        k = step.index - first + 1
        if 1 <= k <= len(names):
            step.kind = KIND
            step.value = float(k)
            step.fields["combination"] = Field(
                "combination", "global", [names[k - 1]], np.array([0]), np.array([[float(k)]])
            )
