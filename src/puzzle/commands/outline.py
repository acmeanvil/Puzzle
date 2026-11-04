"""The model tree as compact text: what an agent (or a terminal) reads first.

One line per object, indented details below, names rather than ids, values with
their units. `outline(project)` is meant to stay short enough for a small model.
"""

from __future__ import annotations

import contextlib

from puzzle.bolts import resolve
from puzzle.errors import PuzzleError
from puzzle.model.schema import (
    Amplitude,
    BoltType,
    Component,
    ConstructionGeometry,
    CoordinateSystem,
    Geometry,
    Material,
    MeshRecipe,
    ParameterSet,
    Project,
    Quantity,
    ResultDefinition,
    ResultProbe,
    SettingsPreset,
    Study,
    SweepStep,
    Transfer,
)


def outline(project: Project, *, ids: bool = False, runs=None) -> str:
    """The model as text; with the store's `runs`, each study and derived result says
    its state ("up to date", "out of date", "not run yet": ADR 0033)."""
    lines = [f'Project "{project.name}"  (agents: {project.agent.level}, mcp: {project.agent.mcp})']
    units = project.units()
    if units is not None:
        from puzzle.model.units import SYSTEMS

        lines.append(f"Units shown: {SYSTEMS[units.system][0]} (the project's own; values are SI)")

    def head(kind: str, obj) -> str:
        return f"{kind} {obj.name}" + (f"  [{obj.id}]" if ids else "")

    for ps in project.of_type(ParameterSet):
        lines.append(head("Parameters", ps) + ": " + _values(ps.values))
    for geo in project.of_type(Geometry):
        scope = "global" if geo.scope == "global" else "local"
        origin = geo.source or f"history of {len(geo.history)} steps"
        lines.append(head("Geometry", geo) + f"  ({scope}, {origin})")
        for s in geo.selections:
            lines.append(f"    {s.name}: {s.entity} x{len(s.members)}")
    for m in project.of_type(Material):
        props = _values(m.properties)
        tables = f"; tables: {', '.join(sorted(m.tables))}" if m.tables else ""
        lines.append(head("Material", m) + f": {props}{tables}")
    for t in project.of_type(BoltType):  # ADR 0050
        grade = project.objects.get(t.material)
        if t.preload == "force":
            preload = f"preload {_q(t.preload_force)}"
        elif t.preload == "torque":
            preload = f"torque {_q(t.torque)}, K {t.nut_factor:g}"
        elif t.preload == "yield_percent":
            preload = f"preload {t.preload_percent:g} % of yield"
        else:
            preload = "no preload"
        lines.append(
            head("Bolt type", t) + f": {t.size or _q(t.diameter)} ({t.standard}), "
            f"{grade.name if grade is not None else '?'}, {preload}, {t.formulation}, "
            f"{t.ends} ends"
        )
    for r in project.of_type(MeshRecipe):
        size = f", global {_q(r.global_size)}" if r.global_size else ""
        mesher = f" ({r.mesher})" if r.mesher != "gmsh" else ""
        lines.append(head("Mesh", r) + f": {r.element_shape} order {r.element_order}{size}{mesher}")
        lines += [f"    {c.describe()}" for c in r.controls]
    names = {o.id: o.name for o in project.objects.values()}
    for c in project.of_type(Component):
        lines.append(head("Component", c) + f"  ({c.dimension}, mesh {names.get(c.mesh, '-')})")
        for p in c.parts:
            moved = f" at {tuple(p.translation)}" if any(p.translation) else ""
            turned = f" rotated {tuple(p.rotation)}" if any(p.rotation) else ""
            lines.append(f"    part {p.name}: {names.get(p.geometry, '?')}{moved}{turned}")
        for a in c.materials:
            lines.append(f"    material {names.get(a.material, '?')} -> {a.selection}")
        for k in c.contacts:
            mu = f", friction {k.friction:g}" if k.friction is not None else ""
            lines.append(
                f"    contact {k.name}: {k.type} {k.source} ~ {k.target}{mu} ({k.capability})"
            )
        for f in c.connectors:  # ADR 0027; a bolt type's values filled in (ADR 0050)
            typed = f" (type {names.get(f.type, '?')})" if f.type is not None else ""
            with contextlib.suppress(PuzzleError):  # no yield strength: shown unresolved
                f = resolve(project, f)
            lines.append(
                f"    fastener {f.name}: {f.source} -> {f.target or 'ground'}, "
                f"diameter {_q(f.diameter)}, "
                f"{names.get(f.material, '?')}, {f.ends} ends"
                + (f", pretension {_q(f.pretension)}" if f.pretension else "")
                + typed
            )

        for s in c.shell_sections:  # ADR 0027 shells
            lines.append(f"    shell on {s.selection}: {s.describe()}")
        for b in c.beam_sections:  # ADR 0027
            lines.append(f"    beam section on {b.selection}: {b.describe()}")
        for ph in c.physics:
            settings = f" {ph.settings}" if ph.settings else ""
            lines.append(f"    physics {ph.type}{settings}")
            for f in ph.features:
                vals = f" {_values(f.values)}" if f.values else ""
                amp = f" x amplitude {names.get(f.amplitude, '?')}" if f.amplitude else ""
                amp += f" in axes {names.get(f.axes, '?')}" if f.axes else ""
                amp += f" in steps {', '.join(map(str, f.steps))}" if f.steps else ""
                lines.append(f"        {f.type} on {f.selection}{vals}{amp}")
    states = {}
    if runs is not None:
        from puzzle.project.status import status

        states = status(project, runs)

    def state(obj) -> str:
        st = states.get(str(obj.id))
        return f"  [{st.words.lower()}]" if st is not None else ""

    from puzzle.operators import resolved

    swept = resolved(project)  # a sweep over an operator's list: its values (ADR 0062)
    for s in project.of_type(Study):
        lines.append(head("Study", s) + f": {s.solver} on {names.get(s.component, '?')}" + state(s))
        for n, step in enumerate(s.steps):
            if isinstance(step, SweepStep):
                params = ", ".join(p.name + (f" <- {names.get(p.source, '?')}" if p.source
                                             else "") for p in step.parameters)  # fmt: skip
                count = len(swept.objects[s.id].steps[n].points())
                lines.append(f"    sweep {params} ({count} points)")
            else:
                physics = f" {', '.join(step.physics)}" if step.physics else ""
                settings = f" {step.settings}" if step.settings else ""
                lines.append(f"    {step.type}{physics}{settings}")
        for solver, block in s.solver_settings.items():  # ADR 0023
            lines.append(
                f"    {solver} settings: {', '.join(f'{k}={v}' for k, v in block.items())}"
            )
        for solver, block in s.expert_settings.items():
            lines.append(f"    {solver} expert pass-through: {', '.join(block)}")
    for preset in project.of_type(SettingsPreset):  # ADR 0023
        parts = [f"{k}={v}" for k, v in preset.shared.items()]
        parts += [f"{s}: {', '.join(f'{k}={v}' for k, v in b.items())}"
                  for s, b in preset.solver_settings.items()]  # fmt: skip
        parts += [f"{s} expert: {', '.join(b)}" for s, b in preset.expert_settings.items()]
        lines.append(head("Preset", preset) + f": {'; '.join(parts) or 'defaults'}")
    for a in project.of_type(Amplitude):
        pts = ", ".join(f"({t:g} s, {v:g})" for t, v in a.points)
        lines.append(head("Amplitude", a) + f": {pts}")
    for a in project.of_type(CoordinateSystem):
        where = (
            f"about {a.axis} through {a.origin}"
            if a.type == "cylindrical"
            else f"x {a.x_axis}, xy {a.xy_direction} at {a.origin}"
        )
        lines.append(head("Axes", a) + f": {a.type} {where}")
    for c in project.of_type(ConstructionGeometry):  # ADR 0046
        if c.type == "path":
            what = f"path {xyz(c.start)} to {xyz(c.end)}"
        elif c.axes is not None:
            what = f"plane {c.plane} of axes {names.get(c.axes, '?')}, offset {c.offset:g} m"
        else:
            what = f"plane through {xyz(c.origin)}, normal {tuple(c.normal)}"
        lines.append(head("Construction", c) + f": {what}")
    for t in project.of_type(Transfer):
        lines.append(
            head("Transfer", t)
            + f": {t.field} ({t.role}) {names.get(t.source, '?')} -> {names.get(t.target, '?')}"
        )
    from puzzle.model.schema import ImportedField

    for f in project.of_type(ImportedField):  # ADR 0044: the file, and where it is used
        uses = [
            f"{feat.type}.{key} on {feat.selection} in {c.name}"
            + (f" x{use.scale:g}" if use.scale != 1 else "")
            for c in project.of_type(Component)
            for ph in c.physics
            for feat in ph.features
            for key, use in feat.fields.items()
            if use.field == f.id
        ]
        lines.append(
            head("Imported field", f)
            + f": {f.file}, values {', '.join(f.value_columns()) or '-'}, points in "
            + f"{f.length_unit}"
            + (f" ({f.format})" if f.format != "csv" else "")
            + (
                f", at {len(f.times)} {'frequencies' if f.along() == 'frequency' else 'times'}"
                if f.times
                else ""
            )
            + (f", rows by {f.along()}" if f.varies() and not f.times else "")
            + (f"; used by {'; '.join(uses)}" if uses else "; not used")
        )
    for d in project.of_type(ResultDefinition):
        if d.method == "fatigue":
            f = d.fatigue
            duty = " + ".join(
                f"{b.cycles:g} x {b.loading} "
                + (names.get(b.combination, "?") if b.combination else names.get(b.case.study, "?"))
                + (f" (x{b.scale:g})" if b.scale != 1 else "")
                for b in f.blocks
            )
            notch = (
                f", Kf by Neuber (Kt {f.notch_kt:g}, r {f.notch_radius:g} m)"
                if f.notch_from == "neuber"
                else (f", Kf {f.notch_factor:g}" if f.notch_factor != 1 else "")
            )
            what = (
                f"fatigue {f.life_model}, {f.stress}, {f.mean_stress}{notch}, design life "
                f"{f.design_life:g}: {duty}"
            )
        elif d.method == "equation":
            what = f"{d.expression} over {names.get(d.source, '?')}"
        else:
            cases = " + ".join(
                f"{c.coefficient:g} x {names.get(c.study, '?')}"
                + (f" [{c.point}]" if c.point else "")
                + (f" step {c.step + 1}" if c.step >= 0 else "")
                for c in d.cases
            )
            extra = f" of {d.quantity} ({d.extreme})" if d.method == "envelope" else ""
            what = f"{d.method}{extra}: {cases}"
        lines.append(head("Result", d) + f": {what}" + state(d))
    for pr in project.of_type(ResultProbe):
        from puzzle.post.quantities import label_of

        of = names.get(pr.source, "?")
        if pr.type == "reaction":  # ADR 0042 result tools
            about = f", moment about {xyz(pr.about)}" if pr.about else ""
            what = f"reaction on {pr.selection} of {of}{about}"
        elif pr.type == "selection":
            what = f"{pr.reduction} of {label_of(pr.quantity)} on {pr.selection} of {of}"
        elif pr.type == "path":
            line = (
                f"construction path {names.get(pr.construction, '?')}"
                if pr.construction
                else f"{xyz(pr.point)} to {xyz(pr.end)}"
            )
            what = f"{label_of(pr.quantity)} of {of} along {line}, {pr.samples} points"
        elif pr.type in ("plane", "section"):
            plane = names.get(pr.construction, "?")
            what = (
                f"force and moment across plane {plane} of {of}"
                if pr.type == "section"
                else f"{pr.reduction} of {label_of(pr.quantity)} on plane {plane} of {of}"
            )
        elif pr.type == "linearized":
            line = (
                f"along construction path {names.get(pr.construction, '?')}"
                if pr.construction
                else f"from {xyz(pr.point)} to {xyz(pr.end)}"
            )
            what = f"stress linearization ({pr.equivalent}) of {of} {line}"
        else:
            what = f"{label_of(pr.quantity)} of {of} at {xyz(pr.point)}"
        lines.append(head("Probe", pr) + f": {what}")
    from puzzle.model.schema import Operator
    from puzzle.operators import describe

    for op in project.of_type(Operator):  # ADR 0062; values: `operator_value`
        lines.append(head("Operator", op) + f": {describe(project, op)}")
    from puzzle.model.schema import Optimisation

    for opt in project.of_type(Optimisation):  # ADR 0063; points: `optimisation_points`
        from puzzle.optimise import METHODS, adaptive
        from puzzle.optimise import limit as most

        variables = ", ".join(
            f"{v.name} {v.low.value:g}..{v.high.value:g} {v.low.unit}"
            if v.kind != "choice" else f"{v.name} in {len(v.choices)} choices"
            for v in opt.variables)  # fmt: skip
        what = []
        if opt.objective is not None:
            what.append(f"{opt.objective.sense} {names.get(opt.objective.source, '?')}")
        for c in opt.constraints:
            limit = (f"<= {c.upper.value:g} {c.upper.unit}" if c.upper is not None
                     else f">= {c.lower.value:g} {c.lower.unit}" if c.lower is not None
                     else "")  # fmt: skip
            what.append(f"{names.get(c.source, '?')} {limit}".replace(" dimensionless", ""))
        count = f"up to {most(opt)} points" if adaptive(opt) else f"{most(opt)} points"
        lines.append(head("Optimisation", opt) + f": {METHODS[opt.method]} of "
                     f"{names.get(opt.study, '?')} ({count}) over "
                     f"{variables}; {'; '.join(what)}")  # fmt: skip
    lines += _convergence(project, runs, head, names)
    lines += _flow(project, runs)
    problems = project.problems()
    if problems:
        lines.append("Problems:")
        lines += [f"    [{p.code}] {p.message}" for p in problems]
    return "\n".join(lines)


def _convergence(project: Project, runs, head, names) -> list[str]:
    """Each study's mesh convergence check (ADR 0066): its settings and the meshes run;
    the verdict itself is `mesh_convergence`'s (it reads the values)."""
    from puzzle.model.schema import MeshStudy, Study

    checks = [(head("Study", s) + " mesh convergence", s.id, s.convergence)
              for s in project.of_type(Study) if s.convergence is not None]  # fmt: skip
    checks += [(head("Mesh study", m) + f" of {names.get(m.study, '?')}", m.study, m.check())
               for m in project.of_type(MeshStudy)]  # fmt: skip
    out = []
    for st in project.of_type(Study):
        if st.adapt is not None:
            a = st.adapt
            ran = sum(1 for r in runs or () if r.study_id == str(st.id)
                      and r.status == "completed" and "adapt" in r.point_labels())  # fmt: skip
            count = f"; {ran} passes run" if runs is not None else ""
            out.append(f"{head('Study', st)} adaptive refinement: {a.criterion} to "
                       f"{a.target * 100:g} %, up to {a.passes} passes{count}")  # fmt: skip
    for title, study_id, c in checks:
        ran = {r.point_labels().get("mesh") for r in runs or ()
               if r.study_id == str(study_id) and r.status == "completed"
               and (r.point_labels() or {}).get("mesh")}  # fmt: skip
        count = f"; {len(ran)} of {c.levels} meshes run" if runs is not None else ""
        out.append(f"{title}: {c.levels} meshes, each finer by {c.ratio:g}, target GCI "
                   f"{c.target * 100:g} %{count}")  # fmt: skip
    return out


def _flow(project: Project, runs) -> list[str]:
    """What feeds what and what is shared, from the schematic (ADR 0058)."""
    from puzzle import schematic

    try:
        found = schematic.build(project, runs or (), states={}).flow_lines()
    except (ValueError, KeyError):  # a broken model: its problems say why
        return []
    return ["Flow:", *(f"    {line}" for line in found)] if found else []


def _q(q: Quantity) -> str:
    return f"{q.value:g}" if q.unit == "dimensionless" else f"{q.value:g} {q.unit}"


def _values(values: dict[str, Quantity]) -> str:
    return ", ".join(f"{k}={_q(v)}" for k, v in values.items())


def xyz(point) -> str:
    return "(" + ", ".join(f"{c:g}" for c in point) + ") m"
