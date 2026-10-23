"""The model tree as compact text: what an agent (or a terminal) reads first.

One line per object, indented details below, names rather than ids, values with
their units. `outline(project)` is meant to stay short enough for a small model.
"""

from __future__ import annotations

from puzzle.model.schema import (
    Component,
    Geometry,
    Material,
    MeshRecipe,
    ParameterSet,
    Project,
    Quantity,
    ResultDefinition,
    Study,
    SweepStep,
    Transfer,
)


def outline(project: Project, *, ids: bool = False) -> str:
    lines = [f'Project "{project.name}"  (agents: {project.agent.level}, mcp: {project.agent.mcp})']

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
        local = "".join(f", {k} {_q(v)}" for k, v in r.local_sizes.items())
        lines.append(head("Mesh", r) + f": {r.element_shape} order {r.element_order}{size}{local}")
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
        for ph in c.physics:
            settings = f" {ph.settings}" if ph.settings else ""
            lines.append(f"    physics {ph.type}{settings}")
            for f in ph.features:
                vals = f" {_values(f.values)}" if f.values else ""
                lines.append(f"        {f.type} on {f.selection}{vals}")
    for s in project.of_type(Study):
        lines.append(head("Study", s) + f": {s.solver} on {names.get(s.component, '?')}")
        for step in s.steps:
            if isinstance(step, SweepStep):
                params = ", ".join(p.name for p in step.parameters)
                lines.append(f"    sweep {params} ({len(step.points())} points)")
            else:
                physics = f" {', '.join(step.physics)}" if step.physics else ""
                settings = f" {step.settings}" if step.settings else ""
                lines.append(f"    {step.type}{physics}{settings}")
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
        lines.append(head("Result", d) + f": {what}")
    problems = project.problems()
    if problems:
        lines.append("Problems:")
        lines += [f"    [{p.code}] {p.message}" for p in problems]
    return "\n".join(lines)


def _q(q: Quantity) -> str:
    return f"{q.value:g}" if q.unit == "dimensionless" else f"{q.value:g} {q.unit}"


def _values(values: dict[str, Quantity]) -> str:
    return ", ".join(f"{k}={_q(v)}" for k, v in values.items())
