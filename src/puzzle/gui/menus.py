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
from puzzle.gui.tree import Node
from puzzle.model.catalog import CATALOG, STEP_TYPES, specs
from puzzle.model.schema import Component, Geometry, Material, MeshRecipe, Project, Study

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
            add("Import geometry…", lambda: import_step_file(window))
        elif node.label == "Materials":
            add("Add from library…", window._add_material_dialog)
        elif node.label == "Meshes":
            add("New mesh recipe", lambda: new_mesh(window))
        elif node.label == "Components":
            add("New component…", lambda: new_component(window))
        elif node.label == "Studies":
            add("New study…", lambda: new_study(window))
    elif node.kind == "project":
        add("Import geometry…", lambda: import_step_file(window))
        add("New mesh recipe", lambda: new_mesh(window))
        add("New component…", lambda: new_component(window))
        add("New study…", lambda: new_study(window))
    elif isinstance(obj, Component) and node.kind == "object":
        physics = menu.addMenu("Add physics")
        present = {p.type for p in obj.physics}
        for kind in CATALOG:
            action = physics.addAction(kind.replace("_", " "))
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
    elif isinstance(obj, Study) and node.kind == "object":
        add("Run", window.run_selected)
        steps = menu.addMenu("Add step")
        for t in STEP_TYPES:
            action = steps.addAction(t)
            action.triggered.connect(lambda _=False, k=t: add_step(window, obj, k))
        menu.addSeparator()
        add("Delete", window.delete_selected)
    elif isinstance(obj, Study) and node.kind == "step":
        add("Remove step", lambda: remove(window, obj, node.path))
    elif node.kind == "object":
        add("Delete", window.delete_selected)
    elif node.kind == "run":
        add("Export to ParaView…", lambda: export_run(window, node.object))
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
    solver = window.choose("New study", "Solver:", sorted(available_adapters())) if comp else None
    step = window.choose("New study", "Analysis:", STEP_TYPES) if solver else None
    name = window.ask_text("New study", "Name:", f"{comp} {step}") if step else None
    if not name:
        return None
    settings = {"modes": 6} if step == "modal" else {}
    data = {
        "name": name,
        "component": str(comps[comp].id),
        "solver": solver,
        "steps": [{"type": step, "settings": settings}],
    }
    return window.submit(Create(kind="study", data=data))


def import_step_file(window, path: Path | None = None):
    """Copy a STEP file into the project and add it as a geometry with a body selection."""
    from puzzle.geometry import import_step

    if path is None:
        from PySide6.QtWidgets import QFileDialog

        chosen, _ = QFileDialog.getOpenFileName(
            window, "Import geometry", "", "CAD (*.step *.stp *.iges *.igs *.brep)"
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
    for label, members in model.labelled_bodies().items():
        if label != "Body" and len(model.labelled_bodies()) > 1:
            selections.append({"name": label, "entity": "body", "members": members})
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
