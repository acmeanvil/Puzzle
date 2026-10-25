"""The Advisor (ADR 0040): what to do next, computed from the project's state.

`advise(project, runs, component, study)` walks the workflow for one component and
study: geometry, selections, materials, mesh, physics, supports and loads, study, run,
results. Each step is done, needed or optional, with a short reason and one action
(a key the GUI turns into a button: `assign_material`, `add_feature:solid_mechanics:
fixed`, `run`). Nothing here blocks anything: experts ignore it.

The words come from `TEXT`, one table, so the Advisor panel, the agent (MCP
`next_steps`) and `puzzle advise` say the same thing. What each physics needs
(`NEEDS`) is the same knowledge as the `unconstrained` problem, written once.
"""

from __future__ import annotations

import contextlib
from dataclasses import asdict, dataclass

from puzzle.model.schema import Component, Geometry, Material, MeshRecipe, Project, Study

STEPS = (
    "geometry", "selections", "materials", "mesh", "physics", "conditions", "study", "run",
    "results",
)  # fmt: skip

# Step -> (title, what it is for): the one table of the Advisor's words.
TEXT = {
    "geometry": ("Geometry", "The shape to analyse: import a CAD file or draw a part."),
    "selections": (
        "Selections",
        "Named faces (and edges, points) where supports and loads go: pick them in the view.",
    ),
    "materials": ("Materials", "Every body needs a material with the properties the study uses."),
    "component": ("Component", "Puts the parts, their materials and a mesh recipe together."),
    "mesh": ("Mesh", "Meshing happens when a study runs; meshing now checks it first."),
    "physics": ("Physics", "What to solve: structural, heat, flow, electromagnetic…"),
    "conditions": ("Supports and loads", "What holds the model and what acts on it."),
    "study": ("Study", "Which analysis to run (static, modal, transient…) and on which solver."),
    "run": ("Run", "Solve the study; results are kept until the model changes."),
    "results": (
        "Results",
        "Look at them: probes, reactions, paths, cuts, plots, and exports for other tools.",
    ),
}

# What each physics needs before a solve, as (what, feature types that give it,
# the feature to suggest, step types it applies to (empty: all)).
NEEDS = {
    "solid_mechanics": [
        ("a support", ("fixed", "displacement", "remote_displacement", "elastic_support",
                       "compression_only_support"), "fixed", ("stationary", "transient",
                                                              "frequency_domain", "buckling")),
        ("a load", ("force", "pressure", "remote_force", "remote_force_rigid", "gravity",
                    "acceleration", "rotation", "bolt_pretension", "bolt_adjustment",
                    "displacement"), "force", ("stationary", "transient", "frequency_domain",
                                               "buckling")),
    ],
    "heat_transfer": [
        ("a temperature or convection", ("temperature", "convection"), "temperature",
         ("stationary",)),
    ],
    "fluid_flow": [
        ("an inlet", ("velocity_inlet", "mass_flow_inlet"), "velocity_inlet", ()),
        ("an outlet", ("pressure_outlet",), "pressure_outlet", ()),
    ],
    "electrostatics": [
        ("a potential", ("electric_potential",), "electric_potential", ()),
        ("a ground", ("ground",), "ground", ()),
    ],
    "magnetic_fields": [("a coil current", ("coil_current",), "coil_current", ())],
    "custom_pde": [("a Dirichlet condition", ("dirichlet",), "dirichlet", ())],
}  # fmt: skip


@dataclass
class Advice:
    step: str
    title: str
    state: str  # "done", "needed" or "optional"
    detail: str
    action: str | None = None  # a key the GUI turns into a button
    label: str = ""  # the button's words

    def as_dict(self) -> dict:
        return asdict(self)


def _feature_label(physics: str, kind: str) -> str:
    from puzzle.model.catalog import spec

    s = spec(physics, kind)
    return s.label if s else kind.replace("_", " ")


def advise(
    project: Project,
    runs=(),
    component: Component | None = None,
    study: Study | None = None,
    meshed: bool = False,
) -> list[Advice]:
    """The workflow's steps for `component` and `study` (each defaults to the only one,
    or the first; the study to one of the component's). `runs`: the stored run records;
    `meshed`: whether the component's mesh is on hand."""
    from puzzle.project.status import status

    out: list[Advice] = []

    def add(step, state, detail="", action=None, label=""):
        title, why = TEXT[step]
        out.append(Advice(step, title, state, detail or why, action, label))

    geometries = project.of_type(Geometry)
    if not geometries:
        add("geometry", "needed", action="import_geometry", label="Import geometry…")
    else:
        add("geometry", "done", f"{len(geometries)} part{'s' * (len(geometries) > 1)}.")
    faces = [s for g in geometries for s in g.selections if s.entity != "body"]
    if not geometries:
        add("selections", "needed", "Comes after the geometry.")
    elif not faces:
        add("selections", "needed", "No faces are named yet: supports and loads go on them.",
            "pick", "Pick faces…")  # fmt: skip
    else:
        add("selections", "done", f"{len(faces)} named.")

    comps = project.of_type(Component)
    if component is None and comps:
        component = comps[0]
    materials = project.of_type(Material)
    if component is None:
        if not materials:
            add("materials", "needed", "Add one from the library first.", "add_material",
                "Add a material…")  # fmt: skip
        else:
            add("materials", "done", f"{len(materials)} in the project.")
        needs_mesh = not project.of_type(MeshRecipe)
        add("component", "needed",
            "Make a mesh recipe first, then a component." if needs_mesh else "",
            "new_mesh" if needs_mesh else "new_component",
            "New mesh recipe" if needs_mesh else "New component…")  # fmt: skip
        return out

    problems = project.problems()
    unassigned = [p.name for p in component.parts if not _assigned(project, component, p.name)]
    if not component.materials or unassigned:
        what = (
            f"{', '.join(unassigned)} ha{'ve' if len(unassigned) > 1 else 's'} none"
            if unassigned
            else ""
        )
        add("materials", "needed", what or "No body has a material yet.",
            "assign_material" if materials else "add_material",
            "Assign a material…" if materials else "Add a material…")  # fmt: skip
    else:
        incomplete = [p for p in problems if p.code == "incomplete_material"]
        if incomplete:
            add("materials", "needed", incomplete[0].message, "material_builder",
                "Material builder…")  # fmt: skip
        else:
            add("materials", "done", f"{len(component.materials)} assigned.")
    if meshed:
        add("mesh", "done", "Meshed.")
    else:
        add("mesh", "optional", action="mesh", label="Mesh now")

    if not component.physics:
        add("physics", "needed", f"{component.name} solves nothing yet.", "add_physics",
            "Add physics…")  # fmt: skip
    else:
        from puzzle.gui.choices import label

        add("physics", "done", ", ".join(label(p.type) for p in component.physics) + ".")

    studies = [s for s in project.of_type(Study) if s.component == component.id]
    if study is None or study.component != component.id:
        study = studies[0] if studies else None
    kinds = {s.type for s in study.solve_steps()} if study else set()
    missing = []
    for i, ph in enumerate(component.physics):
        present = {f.type for f in ph.features}
        for what, types, suggest, steps in NEEDS.get(ph.type, []):
            if steps and kinds and not (kinds & set(steps)):
                continue  # not needed by this study's steps (a free modal study: no support)
            if not present & set(types):
                missing.append((ph.type, i, what, suggest))
    if not component.physics:
        add("conditions", "needed", "Comes after the physics.")
    elif missing:
        ph, i, what, suggest = missing[0]
        others = f" (and {len(missing) - 1} more)" if len(missing) > 1 else ""
        add("conditions", "needed", f"Needs {what}{others}.", f"add_feature:{i}:{suggest}",
            f"Add {_feature_label(ph, suggest).lower()}…")  # fmt: skip
    else:
        count = sum(len(p.features) for p in component.physics)
        add("conditions", "done", f"{count} supports and loads.")

    if study is None:
        add("study", "needed", f"No study runs {component.name} yet.", "new_study", "New study…")
        return out
    from puzzle.adapters import check_study

    trouble = [p.message for p in problems if p.path == study.name]
    with contextlib.suppress(Exception):  # an adapter that can't load says nothing here
        trouble += [p for found in check_study(project, study).values() for p in found]
    if trouble:
        add("study", "needed", f"{study.name}: {trouble[0]}", "solver_settings" if
            "setting" in trouble[0] else "show_problems", "Show the problems")  # fmt: skip
    else:
        from puzzle.gui.choices import solver_label

        add("study", "done", f"{study.name} on {solver_label(study.solver)}.")

    state = status(project, runs).get(str(study.id))
    word = state.state if state else "not_run"
    if word == "up_to_date":
        add("run", "done", "Its results match the model.")
        add("results", "optional", "", "results", "Show results")
    elif word == "out_of_date":
        add("run", "needed", "The model changed since the last run.", "run", "Run again")
        add("results", "optional", "Older results are kept until then.", "results", "Show them")
    else:
        later = "Once the study's problems are solved." if trouble else ""
        add("run", "optional" if trouble else "needed", later, "run", "Run")
        add("results", "optional", "Comes after a run.")
    return out


def _assigned(project: Project, comp: Component, part: str) -> bool:
    """Whether some material assignment covers the part (its name, or a selection of
    it)."""
    return any(a.selection == part or a.selection.startswith(f"{part}/") for a in comp.materials)


def next_step(advice: list[Advice]) -> Advice | None:
    """The first needed step: what the Advisor points at."""
    return next((a for a in advice if a.state == "needed"), None)
