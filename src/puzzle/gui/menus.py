"""The model tree's right-click menus: build a model without typing data.

Every entry submits one command through the window (so it is undoable and logged),
built from `puzzle.model.catalog` (physics, features and their default values) and
the solver adapters (which solvers exist). Questions go through `window.choose` and
`window.ask_text`, which tests replace.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QMenu

from puzzle.commands.model import CopyComponent, Create, Edit, EditOp
from puzzle.geometry.steps import STEPS
from puzzle.gui.choices import label, solver_label
from puzzle.gui.tree import Node
from puzzle.model.catalog import CATALOG, STEP_TYPES, specs
from puzzle.model.schema import (
    Component,
    Geometry,
    Material,
    MeshRecipe,
    ParameterSet,
    Project,
    Study,
)

if TYPE_CHECKING:
    from puzzle.gui.window import MainWindow


def selections_of(project: Project, comp: Component, entity: str | None = None) -> list[str]:
    """A component's selection names ("Part/Name"), optionally of one entity kind."""
    out = [s.name for s in comp.selections if entity is None or s.entity == entity]
    for part in comp.parts:
        geo = project.objects.get(part.geometry)
        if isinstance(geo, Geometry):
            out += [
                f"{part.name}/{s.name}"
                for s in geo.selections
                if entity is None or s.entity == entity
            ]
    return out


def build_menu(window: MainWindow, node: Node) -> QMenu | None:
    """The menu for a tree item, or None when it has nothing to offer."""
    menu = QMenu(window)
    project = window.session.project
    obj = project.objects.get(_uuid(node.object)) if node.object else None
    add = _adder(menu)

    if node.kind == "section":
        if node.label == "Geometry":
            add("New part…", lambda: new_part(window))
            add("Import geometry…", lambda: import_step_file(window))
        elif node.label == "Parameters":
            add("New parameter set", lambda: new_parameter_set(window))
        elif node.label == "Materials":
            add("Add from library…", window._add_material_dialog)
            add("Material builder…", lambda: window.open_material_builder())
        elif node.label == "Meshes":
            add("New mesh recipe", lambda: new_mesh(window))
        elif node.label == "Components":
            add("New component…", lambda: new_component(window))
        elif node.label == "Studies":
            add("New study…", lambda: new_study(window))
    elif node.kind == "project":
        add("New part…", lambda: new_part(window))
        add("Import geometry…", lambda: import_step_file(window))
        add("New parameter set", lambda: new_parameter_set(window))
        add("New mesh recipe", lambda: new_mesh(window))
        add("New component…", lambda: new_component(window))
        add("New study…", lambda: new_study(window))
    elif isinstance(obj, Component) and node.kind == "object":
        physics = menu.addMenu("Add physics")
        present = {p.type for p in obj.physics}
        for kind in CATALOG:
            action = physics.addAction(label(kind))
            action.setEnabled(kind not in present)
            action.triggered.connect(lambda _=False, k=kind: add_physics(window, obj, k))
        add("Assign material…", lambda: assign_material(window, obj))
        add("Mesh", window.mesh_selected)
        add("Copy…", lambda: copy_component(window, obj))
        menu.addSeparator()
        add("Delete", window.delete_selected)
    elif isinstance(obj, Component) and node.kind == "physics":
        index = int(node.path.split(".")[1])
        sub = menu.addMenu("Add feature")
        for s in specs(obj.physics[index].type):
            action = sub.addAction(s.label)
            action.triggered.connect(lambda _=False, sp=s: add_feature(window, obj, index, sp))
        menu.addSeparator()
        add("Remove physics", lambda: remove(window, obj, node.path))
    elif isinstance(obj, Component) and node.kind in ("feature", "assignment", "contact", "part"):
        if node.kind in ("feature", "assignment"):
            add("Change selection…", lambda: change_selection(window, obj, node))
        add("Remove", lambda: remove(window, obj, node.path))
    elif isinstance(obj, Geometry) and node.kind == "selection":
        add("Remove selection", lambda: remove(window, obj, node.path))
    elif isinstance(obj, Geometry) and node.kind == "object":
        steps = menu.addMenu("Add step")
        for kind in STEPS:
            action = steps.addAction(label(kind))
            action.triggered.connect(lambda _=False, k=kind: add_step_to(window, obj, k))
        if not obj.history:
            add(
                "Edit as history (keep the file as its first step)", lambda: to_history(window, obj)
            )
        add("Check geometry", lambda: check_geometry_of(window, obj))
        if obj.selections:
            add("Reattach selections…", lambda: reattach_selections(window, obj))
        menu.addSeparator()
        add("Delete", window.delete_selected)
    elif isinstance(obj, Geometry) and node.kind == "operation":
        index = int(node.path.split(".")[1])
        step = obj.history[index]
        add("Edit…", lambda: edit_step(window, obj, index))
        after = menu.addMenu("Insert step after")
        for kind in STEPS:
            action = after.addAction(label(kind))
            action.triggered.connect(
                lambda _=False, k=kind: add_step_to(window, obj, k, at=index + 1)
            )
        add(
            "Turn on" if step.suppressed else "Turn off (suppress)",
            lambda: toggle_step(window, obj, index),
        )
        if index > 0:
            add("Move up", lambda: move_step(window, obj, index, -1))
        if index < len(obj.history) - 1:
            add("Move down", lambda: move_step(window, obj, index, +1))
        menu.addSeparator()
        add("Delete step", lambda: remove(window, obj, node.path))
    elif isinstance(obj, Material) and node.kind == "object":
        add("Save to my materials…", lambda: window.open_material_builder(obj))
        menu.addSeparator()
        add("Delete", window.delete_selected)
    elif isinstance(obj, ParameterSet) and node.kind == "object":
        add("Add parameter…", lambda: add_parameter(window, obj))
        add("Delete", window.delete_selected)
    elif isinstance(obj, Study) and node.kind == "object":
        add("Run", window.run_selected)
        add("Materials…", lambda: study_materials(window, obj))
        steps = menu.addMenu("Add step")
        for t in STEP_TYPES:
            action = steps.addAction(label(t))
            action.triggered.connect(lambda _=False, k=t: add_step(window, obj, k))
        menu.addSeparator()
        add("Delete", window.delete_selected)
    elif isinstance(obj, Study) and node.kind == "step":
        add("Remove step", lambda: remove(window, obj, node.path))
    elif node.kind == "object":
        add("Delete", window.delete_selected)
    elif node.kind == "run":
        add("Export to ParaView…", lambda: export_run(window, node.object))
    elif node.kind in ("sweep", "compare"):
        add("Compare…", lambda: window.open_compare(node.object))
    return menu if menu.actions() else None


# --- the commands behind the entries ------------------------------------------------------


def add_physics(window, comp: Component, kind: str):
    value = {"type": kind, "features": []}
    return window.submit(
        Edit(object=str(comp.id), ops=[EditOp(op="append", path="physics", value=value)])
    )


def add_feature(window, comp: Component, index: int, spec):
    choices = selections_of(window.session.project, comp, spec.entity)
    if not choices:
        window.statusBar().showMessage(
            f"No {spec.entity} selections on {comp.name} yet: pick faces in the view first", 8000
        )
        return None
    selection = window.choose(
        f"Add {spec.label}", f"Apply to which {spec.entity} selection?", choices
    )
    if selection is None:
        return None
    value = spec.feature(selection).model_dump(mode="json")
    op = EditOp(op="append", path=f"physics.{index}.features", value=value)
    return window.submit(Edit(object=str(comp.id), ops=[op]))


def assign_material(window, comp: Component):
    project = window.session.project
    materials = {m.name: m for m in project.of_type(Material)}
    if not materials:
        window.statusBar().showMessage("Add a material first (Materials → Add from library)", 8000)
        return None
    name = window.choose("Assign material", "Material:", sorted(materials))
    if name is None:
        return None
    bodies = selections_of(project, comp, "body")
    selection = (
        window.choose("Assign material", "To which body selection?", bodies) if bodies else None
    )
    if selection is None:
        return None
    value = {"material": str(materials[name].id), "selection": selection}
    return window.submit(
        Edit(object=str(comp.id), ops=[EditOp(op="append", path="materials", value=value)])
    )


def change_selection(window, comp: Component, node: Node):
    entity = "body" if node.kind == "assignment" else "face"
    choices = selections_of(window.session.project, comp, entity) or selections_of(
        window.session.project, comp
    )
    selection = window.choose("Change selection", "Selection:", choices)
    if selection is None:
        return None
    op = EditOp(op="set", path=f"{node.path}.selection", value=selection)
    return window.submit(Edit(object=str(comp.id), ops=[op]))


def copy_component(window, comp: Component):
    name = window.ask_text("Copy component", "Name of the copy:", f"{comp.name} copy")
    if name:
        return window.submit(CopyComponent(component=str(comp.id), name=name))
    return None


def remove(window, obj, path: str):
    return window.submit(Edit(object=str(obj.id), ops=[EditOp(op="remove", path=path)]))


def study_materials(window, study: Study):
    """The study's material table: one material per location, or several to compare."""
    from puzzle.gui.study_materials import StudyMaterials

    def add():
        key = window.pick_material()
        if not key:
            return None
        before = {m.id for m in window.session.project.of_type(Material)}
        window.add_material(key)
        new = [m for m in window.session.project.of_type(Material) if m.id not in before]
        return (window.session.project, new[0].id) if new else None

    dialog = StudyMaterials(window.session.project, study, window, add_material=add)
    if not window.run_dialog(dialog):
        return None
    steps = dialog.steps()
    if steps == [s.model_dump(mode="json") for s in study.steps]:
        return None
    return window.submit(
        Edit(object=str(study.id), ops=[EditOp(op="set", path="steps", value=steps)])
    )


def add_step(window, study: Study, kind: str):
    value = {"type": kind, "settings": {"modes": 6} if kind == "modal" else {}}
    if kind == "transient":
        value["settings"] = {
            "time_step": {"value": 1, "unit": "s"},
            "end_time": {"value": 10, "unit": "s"},
        }
    return window.submit(
        Edit(object=str(study.id), ops=[EditOp(op="append", path="steps", value=value)])
    )


def new_mesh(window):
    n = len(window.session.project.of_type(MeshRecipe)) + 1
    data = {"name": f"Mesh {n}", "global_size": {"value": 5, "unit": "mm"}}
    return window.submit(Create(kind="mesh", data=data))


def new_component(window):
    project = window.session.project
    geometries = {g.name: g for g in project.of_type(Geometry)}
    meshes = {m.name: m for m in project.of_type(MeshRecipe)}
    if not geometries or not meshes:
        window.statusBar().showMessage("A component needs a geometry and a mesh recipe first", 8000)
        return None
    geo = window.choose("New component", "Geometry:", sorted(geometries))
    mesh = window.choose("New component", "Mesh recipe:", sorted(meshes)) if geo else None
    name = window.ask_text("New component", "Name:", geo) if mesh else None
    if not name:
        return None
    data = {
        "name": name,
        "parts": [{"name": geo, "geometry": str(geometries[geo].id)}],
        "mesh": str(meshes[mesh].id),
    }
    return window.submit(Create(kind="component", data=data))


def new_study(window):
    from puzzle.adapters import available_adapters

    project = window.session.project
    comps = {c.name: c for c in project.of_type(Component)}
    if not comps:
        window.statusBar().showMessage("Create a component first", 8000)
        return None
    comp = window.choose("New study", "Component:", sorted(comps))
    solvers = [(solver_label(k), k) for k in sorted(available_adapters())]
    solver = window.choose("New study", "Solver:", solvers) if comp else None
    steps = [(label(t), t) for t in STEP_TYPES]
    step = window.choose("New study", "Analysis:", steps) if solver else None
    # A component with several physics: which one this step solves. Most adapters solve
    # one physics per step (thermal stress chains a heat study into a structural one
    # through a temperature transfer), so "all together" is offered last, not first.
    kinds = list(dict.fromkeys(p.type for p in comps[comp].physics)) if step else []
    physics: list[str] = []
    if len(kinds) > 1:
        together = "all"
        options = [(label(k), k) for k in kinds] + [("All together (coupled)", together)]
        chosen = window.choose("New study", "Physics this step solves:", options)
        if chosen is None:
            return None
        physics = [] if chosen == together else [chosen]
    what = label(physics[0] if physics else step).lower() if step else ""
    name = window.ask_text("New study", "Name:", f"{comp} {what}") if step else None
    if not name:
        return None
    settings = {"modes": 6} if step == "modal" else {}
    data = {
        "name": name,
        "component": str(comps[comp].id),
        "solver": solver,
        "steps": [{"type": step, "physics": physics, "settings": settings}],
    }
    return window.submit(Create(kind="study", data=data))


def import_step_file(window, path: Path | None = None):
    """Copy a STEP file into the project and add it as a geometry with a body selection."""
    from puzzle.geometry import import_step

    if path is None:
        from PySide6.QtWidgets import QFileDialog

        chosen, _ = QFileDialog.getOpenFileName(
            window,
            "Import geometry",
            "",
            "CAD (*.step *.stp *.iges *.igs *.brep);;Facets (*.stl *.obj *.3mf)",
        )
        if not chosen:
            return None
        path = Path(chosen)
    root = window.session.root
    target = root / path.name
    if path.resolve() != target.resolve():
        shutil.copyfile(path, target)
    model = import_step(target)
    bodies = [e.name for e in model.of_dim(3)]
    selections = [{"name": "Body", "entity": "body", "members": bodies}]
    # An assembly's parts keep their names: one body selection per STEP part.
    for part, members in model.labelled_bodies().items():
        if part != "Body" and len(model.labelled_bodies()) > 1:
            selections.append({"name": part, "entity": "body", "members": members})
    data = {"name": path.stem, "source": target.name, "selections": selections}
    return window.submit(Create(kind="geometry", data=data))


def export_run(window, run_id: str, path: Path | None = None):
    from puzzle.post.vtk import export

    if path is None:
        from PySide6.QtWidgets import QFileDialog

        chosen, _ = QFileDialog.getSaveFileName(
            window, "Export to ParaView", "results.pvd", "ParaView (*.pvd)"
        )
        if not chosen:
            return None
        path = Path(chosen)
    store = window._store()
    record = store.get_run(run_id)
    files = export(store.read_mesh(record.mesh_id), store.read_results(run_id), path)
    window.statusBar().showMessage(f"Wrote {files[0]} ({len(files) - 1} steps)", 8000)
    return files


# --- geometry history (G4) ------------------------------------------------------------


def new_part(window):
    """A parametric part, started from a box, with a body selection."""
    n = len(window.session.project.of_type(Geometry)) + 1
    name = window.ask_text("New part", "Name:", f"Part {n}")
    if not name:
        return None
    data = {
        "name": name,
        "history": [{"kind": "box", "name": "Base", "args": {"dx": 0.05, "dy": 0.03, "dz": 0.01}}],
        "selections": [{"name": "Body", "entity": "body", "members": ["Body"]}],
    }
    return window.submit(Create(kind="geometry", data=data))


def _tool_steps(geo: Geometry, before: int | None = None) -> list[str]:
    from puzzle.geometry.history import TOOLS

    steps = geo.history if before is None else geo.history[:before]
    return [s.name for s in steps if s.kind in TOOLS]


def add_step_to(window, geo: Geometry, kind: str, at: int | None = None):
    """Add (or insert at `at`) a step, its arguments asked for in the step dialog."""
    reply = window.edit_step(kind, {}, kind.capitalize(), _tool_steps(geo, at))
    if reply is None:
        return None
    name, args = reply
    return apply_step(window, geo, kind, name, args, at=at)


def edit_step(window, geo: Geometry, index: int):
    step = geo.history[index]
    if step.kind == "sketch":  # sketches are edited in the main view (ADR 0017)
        return window.workbench.edit_sketch(geo, index)
    reply = window.edit_step(step.kind, dict(step.args), step.name, _tool_steps(geo, index))
    if reply is None:
        return None
    name, args = reply
    return apply_step(window, geo, step.kind, name, args, index=index)


def apply_step(window, geo: Geometry, kind: str, name: str, args: dict, *, index=None, at=None):
    """One command: step `index` given new arguments (and name), or a new step added at
    the end or inserted at `at`."""
    if index is not None:
        step = geo.history[index]
        if name != step.name and any(s.name == name for s in geo.history):
            window.statusBar().showMessage(f"{geo.name} already has a step '{name}'", 8000)
            return None
        ops = [EditOp(op="set", path=f"history.{index}.args", value=args)]
        if name != step.name:
            ops.append(EditOp(op="set", path=f"history.{index}.name", value=name))
        return window.submit(Edit(object=str(geo.id), ops=ops))
    if any(s.name == name for s in geo.history):
        window.statusBar().showMessage(f"{geo.name} already has a step '{name}'", 8000)
        return None
    step = {"kind": kind, "name": name, "args": args}
    if at is None or at >= len(geo.history):
        op = EditOp(op="append", path="history", value=step)
        return window.submit(Edit(object=str(geo.id), ops=[op]))
    history = [s.model_dump(mode="json") for s in geo.history]
    history.insert(at, step)
    return window.submit(
        Edit(object=str(geo.id), ops=[EditOp(op="set", path="history", value=history)])
    )


def sketch_steps(geo: Geometry, before: int | None = None) -> list[str]:
    """The part's sketch steps before step `before` (newest first): profiles to use."""
    steps = geo.history if before is None else geo.history[:before]
    return [s.name for s in reversed(steps) if s.kind == "sketch" and not s.suppressed]


def toggle_step(window, geo: Geometry, index: int):
    value = not geo.history[index].suppressed
    op = EditOp(op="set", path=f"history.{index}.suppressed", value=value)
    return window.submit(Edit(object=str(geo.id), ops=[op]))


def move_step(window, geo: Geometry, index: int, by: int):
    history = [s.model_dump(mode="json") for s in geo.history]
    history.insert(index + by, history.pop(index))
    return window.submit(
        Edit(object=str(geo.id), ops=[EditOp(op="set", path="history", value=history)])
    )


def to_history(window, geo: Geometry):
    """A file geometry becomes a history whose first step imports the file, so
    preparation and modelling steps can follow it. Its face-N selections become
    `<file>/face-N` (an import names the file's faces in the same order)."""
    step = {"kind": "import", "name": "CAD", "args": {"file": geo.source}}
    selections = []
    for s in geo.selections:
        members = s.members if s.entity == "body" else [f"CAD/{m}" for m in s.members]
        if s.entity == "body":
            members = ["Body"]
        selections.append({**s.model_dump(mode="json"), "members": members})
    ops = [
        EditOp(op="set", path="history", value=[step]),
        EditOp(op="set", path="source", value=None),
        EditOp(op="set", path="selections", value=selections),
    ]
    return window.submit(Edit(object=str(geo.id), ops=ops))


def check_geometry_of(window, geo: Geometry):
    """The geometry check, in the background; its findings join the Problems tab."""
    import copy

    from puzzle.geometry.prep import check_geometry

    project, root = copy.deepcopy(window.session.project), window.session.root

    def done(findings) -> None:
        window.geometry_findings[geo.name] = findings
        window.refresh()
        worst = findings[0]["severity"] if findings else "none"
        window.statusBar().showMessage(
            f"{geo.name}: {len(findings)} finding(s)"
            + (f", worst {worst}" if findings else ", no problems"),
            10000,
        )
        window.output_tabs.setCurrentWidget(window.problems)

    return window.background(
        f"Checking {geo.name}…", lambda _emit: check_geometry(project, geo, root), done, lock=False
    )


def reattach_selections(window, geo: Geometry):
    """Resolve the geometry's selections in the background, then let the user review
    every re-found or detached member at once (G3); the choices are one command."""
    import copy

    from puzzle.geometry.naming import accept_all, check_selections, reattach_ops, rules_for
    from puzzle.gui.reattach import needs_decision

    project, root = copy.deepcopy(window.session.project), window.session.root

    def work(_emit):
        done = check_selections(project, geo, root)
        rules = rules_for(geo, accept_all(done.reports), done.named)
        return done, [r.to_dict() for r in done.reports], rules

    def finish(result) -> None:
        done, reports, rules = result
        if not needs_decision(reports):
            window.statusBar().showMessage(f"Every selection of {geo.name} resolves", 8000)
            return
        choices = window.choose_reattachment(geo.name, reports, rules)
        if not choices:
            return
        current = window.session.project.objects.get(geo.id, geo)
        ops = reattach_ops(current, choices, done.named, done.reports)
        if ops:
            window.submit(Edit(object=str(geo.id), ops=ops))
            window.statusBar().showMessage(f"Reattached the selections of {geo.name}", 8000)

    return window.background(f"Resolving the selections of {geo.name}…", work, finish, lock=False)


def new_parameter_set(window):
    n = len(window.session.project.of_type(ParameterSet)) + 1
    return window.submit(Create(kind="parameters", data={"name": f"Parameters {n}", "values": {}}))


def add_parameter(window, params: ParameterSet):
    name = window.ask_text("Add parameter", "Name (letters, digits, _):", "")
    if not name or not name.isidentifier():
        if name:
            window.statusBar().showMessage(f"'{name}' is not a valid parameter name", 8000)
        return None
    text = window.ask_text("Add parameter", f"{name} = (a number and unit, e.g. 8 mm):", "1 mm")
    if not text:
        return None
    number, _, unit = text.strip().partition(" ")
    try:
        value = {"value": float(number), "unit": unit.strip() or "dimensionless"}
    except ValueError:
        window.statusBar().showMessage(f"'{text}' is not a number and unit", 8000)
        return None
    op = EditOp(op="set", path=f"values.{name}", value=value)
    return window.submit(Edit(object=str(params.id), ops=[op]))


def _adder(menu: QMenu):
    def add(text: str, slot) -> None:
        menu.addAction(text).triggered.connect(lambda _=False: slot())

    return add


def _uuid(text: str):
    from uuid import UUID

    try:
        return UUID(text)
    except ValueError:
        return text
