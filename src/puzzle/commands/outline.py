"""The model tree as compact text: what an agent (or a terminal) reads first.

One line per object, indented details below, names rather than ids, values with
their units. `outline(project)` is meant to stay short enough for a small model.
"""

from __future__ import annotations

from puzzle.model.schema import (
    Amplitude,
    Component,
    CoordinateSystem,
    Geometry,
    Material,
    MeshRecipe,
    ParameterSet,
    Project,
    Quantity,
    ResultDefinition,
    ResultProbe,
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
            lines.append(f"    contact {k.name}: {k.type} {k.source} ~ {k.target} ({k.capability})")
        for f in c.connectors:  # ADR 0027
            lines.append(
                f"    fastener {f.name}: {f.source} -> {f.target}, diameter {_q(f.diameter)}, "
                f"{names.get(f.material, '?')}, {f.ends} ends"
            )
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

    for s in project.of_type(Study):
        lines.append(head("Study", s) + f": {s.solver} on {names.get(s.component, '?')}" + state(s))
        for step in s.steps:
            if isinstance(step, SweepStep):
                params = ", ".join(p.name for p in step.parameters)
                lines.append(f"    sweep {params} ({len(step.points())} points)")
            else:
                physics = f" {', '.join(step.physics)}" if step.physics else ""
                settings = f" {step.settings}" if step.settings else ""
                lines.append(f"    {step.type}{physics}{settings}")
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
    for t in project.of_type(Transfer):
        lines.append(
            head("Transfer", t)
            + f": {t.field} ({t.role}) {names.get(t.source, '?')} -> {names.get(t.target, '?')}"
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

        at = ", ".join(f"{c:g}" for c in pr.point)
        lines.append(
            head("Probe", pr)
            + f": {label_of(pr.quantity)} of {names.get(pr.source, '?')} at ({at}) m"
        )
    problems = project.problems()
    if problems:
        lines.append("Problems:")
        lines += [f"    [{p.code}] {p.message}" for p in problems]
    return "\n".join(lines)


def _q(q: Quantity) -> str:
    return f"{q.value:g}" if q.unit == "dimensionless" else f"{q.value:g} {q.unit}"


def _values(values: dict[str, Quantity]) -> str:
    return ", ".join(f"{k}={_q(v)}" for k, v in values.items())
