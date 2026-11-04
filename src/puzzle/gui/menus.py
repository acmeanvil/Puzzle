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
from puzzle.model.catalog import CATALOG, STEP_DEFAULTS, STEP_TYPES, specs
from puzzle.model.schema import (
    Amplitude,
    Component,
    CoordinateSystem,
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
        elif node.label == "Amplitudes":
            add("New amplitude…", lambda: edit_amplitude(window))
        elif node.label == "Coordinate systems":
            add("New Cartesian system…", lambda: new_axes(window, "cartesian"))
            add("New cylindrical system…", lambda: new_axes(window, "cylindrical"))
            add("New spherical system…", lambda: new_axes(window, "spherical"))
        elif node.label == "Transfers":
            add("New transfer…", lambda: new_transfer(window))
        elif node.label == "Operators":  # ADR 0062
            _operator_menu(window, menu)
        elif node.label == "Optimisations":  # ADR 0063
            from puzzle.gui.optimisation_panel import new_optimisation

            add("New optimisation…", lambda: new_optimisation(window))
        elif node.label == "Imported fields":  # ADR 0044
            from puzzle.gui.field_wizard import import_field

            add("Import field…", lambda: import_field(window))
        elif node.label == "Bolt types":  # ADR 0050
            from puzzle.gui.bolt_types import new_bolt_type

            add("New bolt type…", lambda: new_bolt_type(window))
        elif node.label == "Construction geometry":  # ADR 0046
            from puzzle.gui import construction

            add("New path…", lambda: construction.new_path(window))
            add("New plane…", lambda: construction.new_plane(window))
        elif node.label == "Results":
            from puzzle.gui import result_tools as tools

            tool = menu.addMenu("Add result tool")  # ADR 0042
            for text, slot in (
                ("Reaction…", tools.add_reaction),
                ("Result on a selection…", tools.add_selection_result),
                ("Path…", tools.add_path),
                ("Linearized stress…", tools.add_linearized),
                ("Result on a plane…", tools.add_plane_result),
                ("Section force…", tools.add_section_force),
            ):
                tool.addAction(text).triggered.connect(lambda _=False, s=slot: s(window))
            add("New combination…", lambda: window.new_derived("linear"))
            add("New equation…", lambda: window.new_derived("equation"))
            add("New fatigue tool…", lambda: window.new_derived("fatigue"))
            _operator_menu(window, menu)
    elif node.kind == "project":
        add("New part…", lambda: new_part(window))
        add("New amplitude…", lambda: edit_amplitude(window))
        add("New coordinate system…", lambda: new_axes(window, "cartesian"))
        from puzzle.gui import construction

        add("New construction path…", lambda: construction.new_path(window))
        add("New construction plane…", lambda: construction.new_plane(window))
        add("Import geometry…", lambda: import_step_file(window))
        add("New parameter set", lambda: new_parameter_set(window))
        add("New mesh recipe", lambda: new_mesh(window))
        add("New component…", lambda: new_component(window))
        add("New study…", lambda: new_study(window))
    elif isinstance(obj, Component) and node.kind == "object":
        if len(obj.parts) > 1:
            from puzzle.gui.contact_search import find_contacts_dialog

            add("Find contacts…", lambda: find_contacts_dialog(window, obj))
        if obj.contacts:
            add("Contact tool", lambda: window.open_contacts())
        from puzzle.gui.fastener_dialog import add_fastener, fasteners_from_holes

        add("Add fastener…", lambda: add_fastener(window, obj))
        from puzzle.gui.beam_dialog import add_beam_section

        add("Add beam section…", lambda: add_beam_section(window, obj))
        from puzzle.gui.beam_dialog import add_shell_thickness

        add("Add shell thickness…", lambda: add_shell_thickness(window, obj))
        if len(obj.parts) > 1:
            add("Fasteners from holes…", lambda: fasteners_from_holes(window, obj))
            from puzzle.gui.bolt_generator_panel import generate_bolts

            add("Generate bolts…", lambda: generate_bolts(window, obj))
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
    elif isinstance(obj, Component) and node.kind in (
        "feature",
        "assignment",
        "contact",
        "part",
        "connector",
        "beam_section",
        "shell_section",
    ):
        if node.kind in ("feature", "assignment"):
            add("Change selection…", lambda: change_selection(window, obj, node))
        if node.kind == "feature":
            from puzzle.gui import result_tools as tools

            i, j = int(node.path.split(".")[1]), int(node.path.split(".")[3])
            feature = obj.physics[i].features[j]
            if _field_values(feature):  # ADR 0044: a value some solver takes from a field
                add("Take a value from an imported field…",
                    lambda: value_from_field(window, obj, i, j))  # fmt: skip
            if feature.fields:
                add("Back to uniform values", lambda: uniform_again(window, obj, i, j))
            if feature.type in tools.SUPPORTS:  # ADR 0042: what this support takes
                add(
                    "Reaction force…",
                    lambda: tools.add_reaction(
                        window, selection=feature.selection, source=tools.study_of(window, obj)
                    ),
                )
        if node.kind == "beam_section":  # ADR 0055: its first axis from a pick
            from puzzle.gui.beam_dialog import orient_by_pick

            index = int(node.path.split(".")[1])
            add("Orient by a picked face…", lambda: orient_by_pick(window, obj, index, "face"))
            add("Orient along a picked edge…",
                lambda: orient_by_pick(window, obj, index, "edge"))  # fmt: skip
        add("Remove", lambda: remove(window, obj, node.path))
    elif isinstance(obj, Geometry) and node.kind == "selection":
        index = int(node.path.split(".")[1])
        sel = obj.selections[index]
        picked = bool(window.viewport.picked)
        add("Edit in the view", lambda: window.edit_selection_in_view(obj, index))
        for text, mode in (("Replace with picked", "replace"), ("Add picked", "add"),
                           ("Remove picked", "remove")):  # fmt: skip
            action = add(text, lambda m=mode: window.edit_selection(obj, index, m))
            action.setEnabled(picked)
            if not picked:
                action.setToolTip("Pick in the view first (Pick, then double-click)")
        menu.addSeparator()
        if sel.entity == "face":
            add("New cylindrical axes from this face", lambda: axes_from_face(window, obj, sel))
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
        from puzzle.geometry.meshfile import is_mesh_file

        if obj.source and is_mesh_file(obj.source):  # ADR 0065: STL, OBJ, 3MF booleans
            add("Combine with another faceted part…", lambda: combine_faceted(window, obj))
        if obj.selections:
            add("Reattach selections…", lambda: reattach_selections(window, obj))
        menu.addSeparator()  # what the view shows, and what studies solve
        add("Show" if obj.hidden else "Hide", lambda: show_geometry(window, [obj], obj.hidden))
        add("Hide all others", lambda: hide_others(window, obj))
        add("Show all", lambda: show_all(window))
        add("Swap hidden", lambda: swap_hidden(window))
        add("Unsuppress" if obj.suppressed else "Suppress",
            lambda: suppress_geometry(window, obj, not obj.suppressed))  # fmt: skip
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
    elif isinstance(obj, CoordinateSystem) and node.kind == "object":
        add("Delete", window.delete_selected)
    elif isinstance(obj, Amplitude) and node.kind == "object":
        add("Edit points…", lambda: edit_amplitude(window, obj))
        menu.addSeparator()
        add("Delete", window.delete_selected)
    elif isinstance(obj, Material) and node.kind == "object":
        add("Save to my materials…", lambda: window.open_material_builder(obj))
        menu.addSeparator()
        add("Delete", window.delete_selected)
    elif isinstance(obj, ParameterSet) and node.kind == "object":
        add("Add parameter…", lambda: add_parameter(window, obj))
        add("Delete", window.delete_selected)
    elif isinstance(obj, Study) and node.kind == "object":
        add("Run", window.run_selected)
        from puzzle.gui.solver_settings import edit_solver_settings

        add("Solver settings…", lambda: edit_solver_settings(window, obj))  # ADR 0023
        from puzzle.gui.solver_settings import apply_preset, save_preset
        from puzzle.model.schema import SettingsPreset

        add("Save settings as preset…", lambda: save_preset(window, obj))
        add("Add a morph handle…", lambda: add_morph_handle(window, obj))  # ADR 0065
        presets = project.of_type(SettingsPreset)
        if presets:
            apply = menu.addMenu("Apply settings preset")
            for p in presets:
                apply.addAction(p.name).triggered.connect(
                    lambda _=False, p=p: apply_preset(window, obj, p)
                )
        add("Materials…", lambda: study_materials(window, obj))
        add("New combination…", lambda: window.new_derived("linear", str(obj.id)))
        add("New equation…", lambda: window.new_derived("equation", str(obj.id)))
        add("New fatigue tool…", lambda: window.new_derived("fatigue", str(obj.id)))
        from puzzle.gui.optimisation_panel import new_optimisation

        add("New optimisation…", lambda: new_optimisation(window, str(obj.id)))  # ADR 0063
        steps = menu.addMenu("Add step")
        for t in STEP_TYPES:
            action = steps.addAction(label(t))
            action.triggered.connect(lambda _=False, k=t: add_step(window, obj, k))
        menu.addSeparator()
        add("Delete", window.delete_selected)
    elif obj is not None and obj.kind == "optimisation" and node.kind == "object":
        from puzzle.gui import optimisation_panel as panel

        add("Run", lambda: panel.run(window, str(obj.id)))
        add("Stop", lambda: panel.stop(window))
        add("Show points", lambda: window.show_optimisation(str(obj.id)))
        add("Apply best", lambda: panel.apply_best(window, str(obj.id)))
        menu.addSeparator()
        add("Delete", window.delete_selected)
    elif obj is not None and obj.kind == "operator" and node.kind == "object":
        from puzzle.gui import operators

        if obj.type == "export":
            add("Export now", lambda: operators.export_now(window, obj))
        if obj.type == "plot":
            add("Show in Plots", lambda: operators.show_plot(window, obj))
        if obj.type == "table":
            add("Read a CSV…", lambda: operators.read_csv(window, obj))
        from puzzle.operators import TYPES

        outs = TYPES[obj.type].outputs
        if outs and outs[0].type == "table":  # ADR 0064 phase 3: rows as a field's points
            add("Use as a field…", lambda: operators.use_as_field(window, obj))
        _operator_menu(window, menu, str(obj.id), "out", "New operator from this")
        menu.addSeparator()
        add("Delete", window.delete_selected)
    elif isinstance(obj, Study) and node.kind == "step":
        add("Remove step", lambda: remove(window, obj, node.path))
    elif node.kind == "object":
        add("Delete", window.delete_selected)
    elif node.kind == "run":
        from puzzle.gui.export_dialog import export_results

        study_id = window._store().get_run(node.object).study_id
        add("Export…", lambda: export_results(window, study_id))
        study = window._store().get_run(node.object).study_id
        menu.addSeparator()
        add("New combination…", lambda: window.new_derived("linear", study))
        add("New equation…", lambda: window.new_derived("equation", study))
        add("New fatigue tool…", lambda: window.new_derived("fatigue", study))
        menu.addSeparator()
        add("Delete results…", lambda: window.delete_run(node.object))
    elif node.kind == "derived" and obj is not None:
        add("Edit…", lambda: window.new_derived(obj.method, definition=obj))
        add("New equation from this…", lambda: window.new_derived("equation", str(obj.id)))
        menu.addSeparator()
        add("Delete", window.delete_selected)
    elif node.kind == "probe" and obj is not None:
        add("Delete", window.delete_selected)
    elif node.kind in ("sweep", "compare"):
        add("Compare…", lambda: window.open_compare(node.object))
    from puzzle.gui import organise

    organise.add_to_menu(window, menu, node)  # Rename, groups (any heading's items)
    return menu if menu.actions() else None


def _operator_menu(window, menu, source=None, port="out", title="New operator") -> None:
    """A submenu of operator types (ADR 0062), wired from `source` when given."""
    from puzzle.gui import operators
    from puzzle.operators import TYPES

    sub = QMenu(title, menu)  # parented, so Qt keeps it (as main_menu._sub)
    sub.setToolTipsVisible(True)
    menu.addMenu(sub)
    for t in TYPES.values():
        action = sub.addAction(t.label)
        action.setToolTip(t.help)
        action.triggered.connect(lambda _=False, k=t.key: operators.new_operator(
            window, k, source, port))  # fmt: skip


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
    bodies = selections_of(project, comp, "body")
    if not bodies:
        window.statusBar().showMessage(f"{comp.name} has no body selection to assign to", 8000)
        return None
    from puzzle.gui.quick_form import Ask

    got = window.ask_form(
        "Assign material",
        [
            Ask("material", "Material", choices=[(n, n) for n in sorted(materials)]),
            Ask("selection", "To the bodies", choices=[(b, b) for b in bodies]),
        ],
    )
    if got is None:
        return None
    value = {"material": str(materials[got["material"]].id), "selection": got["selection"]}
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
    value = {"type": kind, "settings": dict(STEP_DEFAULTS.get(kind, {}))}
    if kind == "transient":
        value["settings"] = {
            "time_step": {"value": 1, "unit": "s"},
            "end_time": {"value": 10, "unit": "s"},
        }
    return window.submit(
        Edit(object=str(study.id), ops=[EditOp(op="append", path="steps", value=value)])
    )


def axes_from_face(window, geometry, selection):
    """Cylindrical axes along a bore's or a shaft's axis (ADR 0037), fitted to the face's
    mesh in a component that uses the geometry (the mesh, else the preview)."""
    from puzzle.mesh.adjacency import cylinder_axis

    project = window.session.project
    for comp in project.of_type(Component):
        for part in comp.parts:
            if part.geometry != geometry.id:
                continue
            meshes = [window.meshes.get(str(comp.id))]
            preview = window.previews.get(str(comp.id))
            meshes.append(preview[1].mesh if preview else None)
            for mesh in [m for m in meshes if m is not None]:
                found = cylinder_axis(mesh, f"{part.name}/{selection.name}")
                if found is None:
                    continue
                origin, axis, _radius = found
                data = {
                    "name": f"Axis of {selection.name}",
                    "type": "cylindrical",
                    "origin": [float(c) for c in origin],
                    "axis": [float(c) for c in axis],
                }
                return window.submit(Create(kind="axes", data=data))
    window.statusBar().showMessage(
        f"'{selection.name}' isn't a cylindrical face in any mesh or preview yet: show the "
        "component (or mesh it) first, and pick a cylinder's face",
        8000,
    )


def new_axes(window, type_: str = "cartesian"):
    """New coordinate system… (ADR 0049): the panel, of this type to start."""
    from puzzle.gui.axes_panel import new_axes as panel

    return panel(window, type_)


def edit_amplitude(window, amplitude=None):
    """A load's factor against time (ADR 0037), typed as a table: a new amplitude, or
    new points for one. Features then pick it in Properties (Amplitude)."""
    from puzzle.gui.material_builder import TableEditor

    rows = list(amplitude.points) if amplitude else [(0.0, 0.0), (1.0, 1.0)]
    title = amplitude.name if amplitude else "New amplitude"
    dialog = TableEditor(f"{title}: factor against time", ("Time (s)", "Factor"), rows, window)
    if not window.run_dialog(dialog) or dialog.result_rows is None:
        return None
    points = [list(r) for r in dialog.result_rows]
    if amplitude is not None:
        op = EditOp(op="set", path="points", value=points)
        return window.submit(Edit(object=str(amplitude.id), ops=[op]))
    n = len(window.session.project.of_type(Amplitude)) + 1
    return window.submit(
        Create(kind="amplitude", data={"name": f"Amplitude {n}", "points": points})
    )


def new_mesh(window):
    from puzzle.mesh.generate import preferred_order

    project = window.session.project
    n = len(project.of_type(MeshRecipe)) + 1
    data = {
        "name": f"Mesh {n}",
        "global_size": {"value": 5, "unit": "mm"},
        "element_order": preferred_order(project),
    }
    return window.submit(Create(kind="mesh", data=data))


def new_component(window):
    project = window.session.project
    geometries = {g.name: g for g in project.of_type(Geometry)}
    meshes = {m.name: m for m in project.of_type(MeshRecipe)}
    if not geometries or not meshes:
        window.statusBar().showMessage("A component needs a geometry and a mesh recipe first", 8000)
        return None
    from puzzle.gui.quick_form import Ask

    taken = {c.name for c in project.of_type(Component)}
    got = window.ask_form(
        "New component",
        [
            Ask("geometry", "Geometry", choices=[(g, g) for g in sorted(geometries)]),
            Ask("mesh", "Mesh recipe", choices=[(m, m) for m in sorted(meshes)]),
            Ask("name", "Name", kind="text", follow=lambda v: v.get("geometry") or ""),
        ],
        check=lambda v: (
            "Give it a name"
            if not v["name"]
            else (f"There is already a component '{v['name']}'" if v["name"] in taken else None)
        ),
    )
    if got is None:
        return None
    geo, mesh = got["geometry"], got["mesh"]
    data = {
        "name": got["name"],
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
    from puzzle.gui.quick_form import Ask

    together = "all"

    def kinds(v) -> list[str]:
        c = comps.get(v.get("component"))
        return list(dict.fromkeys(p.type for p in c.physics)) if c else []

    def what(v) -> str:
        physics = v.get("physics")
        if physics and physics != together and len(kinds(v)) > 1:
            return label(physics).lower()
        return label(v.get("analysis") or "").lower()

    taken = {s.name for s in project.of_type(Study)}
    # A component with several physics: which one this step solves. Most adapters solve
    # one physics per step (thermal stress chains a heat study into a structural one
    # through a temperature transfer), so "all together" is offered last, not first.
    got = window.ask_form(
        "New study",
        [
            Ask("component", "Component", choices=[(c, c) for c in sorted(comps)]),
            Ask(
                "solver",
                "Solver",
                choices=[(solver_label(k), k) for k in sorted(available_adapters())],
            ),
            Ask("analysis", "Analysis", choices=[(label(t), t) for t in STEP_TYPES]),
            Ask(
                "physics",
                "Physics this step solves",
                choices=lambda v: (
                    [(label(k), k) for k in kinds(v)] + [("All together (coupled)", together)]
                ),
                shown=lambda v: len(kinds(v)) > 1,
                help="Most solvers solve one physics per step; chain studies with transfers",
            ),
            Ask("name", "Name", kind="text", follow=lambda v: f"{v.get('component')} {what(v)}"),
        ],
        check=lambda v: (
            "Give it a name"
            if not v["name"]
            else (f"There is already a study '{v['name']}'" if v["name"] in taken else None)
        ),
    )
    if got is None:
        return None
    comp, solver, step, name = got["component"], got["solver"], got["analysis"], got["name"]
    chosen = got.get("physics")
    physics = [] if chosen in (None, together) else [chosen]
    settings = dict(STEP_DEFAULTS.get(step, {}))
    structural = "solid_mechanics" in (physics or [p.type for p in comps[comp].physics])
    if step == "transient" and structural:
        from puzzle.model.catalog import DYNAMICS_DEFAULTS

        settings.update(DYNAMICS_DEFAULTS)
    if step == "frequency_domain" and structural:
        from puzzle.model.catalog import HARMONIC_DEFAULTS

        settings.update(HARMONIC_DEFAULTS)
        if solver == "calculix":  # it superposes modes (ADR 0041)
            settings["method"] = "modal"
    data = {
        "name": name,
        "component": str(comps[comp].id),
        "solver": solver,
        "steps": [{"type": step, "physics": physics, "settings": settings}],
    }
    return window.submit(Create(kind="study", data=data))


ROLES = [
    ("As a load (a temperature's thermal strain)", "load"),
    ("As the starting state", "initial_condition"),
    ("As prestress (stiffness from a static study)", "prestress"),
]
MAPPINGS = [
    ("Interpolate (linear in the source's elements)", "interpolate"),
    ("Nearest node", "nearest"),
]


def new_transfer(window, source: str | None = None, field: str | None = None,
                 target: str | None = None):  # fmt: skip
    """A transfer: one study's result field into another study (ADR 0009), in one form.
    The schematic opens it with both ends filled when a wire is drawn (ADR 0058)."""
    from puzzle.gui.quick_form import Ask
    from puzzle.schematic import outputs, words

    project = window.session.project
    studies = {s.name: s for s in project.of_type(Study)}
    if len(studies) < 2:
        window.statusBar().showMessage("A transfer joins two studies: make another study first",
                                       8000)  # fmt: skip
        return None
    by_id = {str(s.id): s.name for s in studies.values()}

    def fields(v) -> list:
        study = studies.get(v.get("source"))
        found = outputs(project, study) if study else []
        if field and study is not None and str(study.id) == source and field not in found:
            found.append(field)
        return [(words(f), f) for f in found] or [("(no results)", "")]

    def steps(v) -> list:
        study = studies.get(v.get("target"))
        n = len(study.solve_steps()) if study else 1
        return [(f"Step {i + 1}", i) for i in range(max(n, 1))]

    got = window.ask_form(
        "New transfer",
        [
            Ask(
                "source",
                "From study",
                choices=[(n, n) for n in studies],
                default=by_id.get(source or ""),
            ),
            Ask(
                "field",
                "Result",
                choices=fields,
                default=field,
                help="What its physics produce; the last result step is taken",
            ),
            Ask(
                "target",
                "Into study",
                choices=[(n, n) for n in studies],
                default=by_id.get(target or ""),
            ),
            Ask("target_step", "Into its step", choices=steps, shown=lambda v: len(steps(v)) > 1),
            Ask(
                "role", "Used", choices=ROLES, default="prestress" if field == "stress" else "load"
            ),
            Ask(
                "mapping",
                "Onto its mesh",
                choices=MAPPINGS,
                help="Two studies on one mesh copy the values; on different meshes they are mapped",
            ),
            Ask(
                "name",
                "Name",
                kind="text",
                follow=lambda v: f"{words(v.get('field') or '')} from {v.get('source')}",
            ),
        ],  # fmt: skip
        check=lambda v: (
            "A study can't feed itself"
            if v["source"] == v["target"]
            else "Choose a result"
            if not v.get("field")
            else None
        ),
    )
    if got is None:
        return None
    data = {
        "name": got["name"] or f"{got['field']} from {got['source']}",
        "source": str(studies[got["source"]].id),
        "field": got["field"],
        "target": str(studies[got["target"]].id),
        "target_step": int(got.get("target_step") or 0),
        "role": got["role"],
        "mapping": got["mapping"],
    }
    return window.submit(Create(kind="transfer", data=data))


def add_part(window, geometry: str, component: str):
    """A geometry added to a component as one more part (the schematic's drop)."""
    from uuid import UUID

    from puzzle.gui.quick_form import Ask

    project = window.session.project
    geo, comp = project.objects.get(UUID(geometry)), project.objects.get(UUID(component))
    if not isinstance(geo, Geometry) or not isinstance(comp, Component):
        return None
    taken = {p.name for p in comp.parts}
    name, n = geo.name, 2
    while name in taken:
        name, n = f"{geo.name} {n}", n + 1
    got = window.ask_form(
        f"Add {geo.name} to {comp.name}",
        [Ask("name", "Part name", kind="text", default=name)],
        intro=f"{comp.name} gets {geo.name} as one more part, placed where the geometry is.",
        check=lambda v: (
            "Give it a name"
            if not v["name"]
            else f"{comp.name} already has a part '{v['name']}'"
            if v["name"] in taken
            else None
        ),  # fmt: skip
    )
    if got is None:
        return None
    part = {"name": got["name"], "geometry": geometry}
    return window.submit(Edit(object=component, ops=[EditOp(op="append", path="parts",
                                                            value=part)]))  # fmt: skip


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
    from puzzle.gui.quick_form import Ask

    def parse(text: str):
        number, _, unit = text.strip().partition(" ")
        try:
            return {"value": float(number), "unit": unit.strip() or "dimensionless"}
        except ValueError:
            return None

    def check(v) -> str | None:
        if not v["name"].isidentifier():
            return "A name is letters, digits and _, not starting with a digit"
        if v["name"] in params.values:
            return f"There is already a parameter '{v['name']}'"
        if parse(v["value"]) is None:
            return "The value is a number and a unit, e.g. 8 mm"
        return None

    got = window.ask_form(
        "Add parameter",
        [
            Ask("name", "Name", kind="text", help="Letters, digits and _; used in step fields"),
            Ask("value", "Value", kind="text", default="1 mm", help="A number and unit"),
        ],
        check=check,
    )
    if got is None:
        return None
    name, value = got["name"], parse(got["value"])
    op = EditOp(op="set", path=f"values.{name}", value=value)
    return window.submit(Edit(object=str(params.id), ops=[op]))


def _adder(menu: QMenu):
    def add(text: str, slot):
        action = menu.addAction(text)
        action.triggered.connect(lambda _=False: slot())
        return action

    return add


def _uuid(text: str):
    from uuid import UUID

    try:
        return UUID(text)
    except ValueError:
        return text


# --- hiding and suppressing geometry --------------------------------------------------------
# Hidden: not drawn, still meshed and solved. Suppressed: left out of meshing and solving
# (puzzle.suppress), and not drawn. Each is one command batch, so it undoes.


def _set_flags(window, changes: list[tuple]) -> object:
    from puzzle.commands.model import Edit, EditOp

    edits = [Edit(object=str(g.id), ops=[EditOp(op="set", path=field, value=value)])
             for g, field, value in changes if getattr(g, field) != value]  # fmt: skip
    if not edits:
        return None
    out = window.submit(edits[0]) if len(edits) == 1 else window.submit_batch(edits)
    redraw = getattr(window, "_selected", None)
    if redraw is not None:  # the view shows what it held, now without the hidden parts
        redraw()
    return out


def _geometries(window):
    from puzzle.model.schema import Geometry

    return list(window.session.project.of_type(Geometry))


def show_geometry(window, geometries, shown: bool):
    """Show (or hide) these geometries in the view."""
    return _set_flags(window, [(g, "hidden", not shown) for g in geometries])


def hide_others(window, keep):
    """Every geometry hidden but this one, which is shown."""
    return _set_flags(window, [(g, "hidden", g.id != keep.id) for g in _geometries(window)])


def show_all(window):
    return _set_flags(window, [(g, "hidden", False) for g in _geometries(window)])


def swap_hidden(window):
    """What is hidden is shown, and what is shown is hidden."""
    return _set_flags(window, [(g, "hidden", not g.hidden) for g in _geometries(window)])


def suppress_geometry(window, geometry, suppressed: bool):
    """Leave a geometry out of meshing and solving (or bring it back)."""
    return _set_flags(window, [(geometry, "suppressed", suppressed)])


# --- imported fields (ADR 0044) ------------------------------------------------------------


def value_from_field(window, comp, i: int, j: int):
    """One form: which value, which field and column, scaling, offset and mapping; then
    one Edit setting the feature's field use."""
    from puzzle.gui.choices import label
    from puzzle.gui.quick_form import Ask
    from puzzle.model.schema import ImportedField

    feature = comp.physics[i].features[j]
    fields = window.session.project.of_type(ImportedField)
    if not fields:
        window.statusBar().showMessage(
            "No imported fields yet: Imported fields → Import field… first", 8000
        )
        return None
    by_id = {str(f.id): f for f in fields}
    values = window.ask_form("Value from an imported field", [
        Ask("value", "Value", choices=[(label(k), k) for k in _field_values(feature)]),
        Ask("field", "Imported field", choices=[(f.name, str(f.id)) for f in fields]),
        Ask("column", "Column", choices=lambda v: _columns(by_id[v["field"]])),
        Ask("scale", "Scale by", kind="text", default="1"),
        Ask("offset", "Then add (e.g. 10 K, 0.1 MPa)", kind="text", default=""),
        Ask("mapping", "Mapping", choices=[("Linear between the points", "linear"),
                                            ("Nearest point", "nearest"),
                                            ("Inverse distance", "inverse_distance"),
                                            ("Conservative: keeps the total force",
                                             "conservative")]),
        # ADR 0044 stage 2: what a conservative mapping integrates the source with
        Ask("weight", "Each point's area or volume (conservative)",
            choices=lambda v: [("None", "")] + _columns(by_id[v["field"]])),
    ], intro=f"{label(feature.type)} on {feature.selection} takes this value from the field, "
             "mapped onto its faces or bodies each run.")  # fmt: skip
    if values is None:
        return None
    use = {"field": values["field"], "column": values["column"], "mapping": values["mapping"],
           "scale": float(values["scale"] or 1)}  # fmt: skip
    if values.get("weight"):
        use["weight"] = values["weight"]
    offset = (values.get("offset") or "").strip()
    if offset:
        number, _, unit = offset.partition(" ")
        use["offset"] = {"value": float(number), "unit": unit.strip() or "dimensionless"}
    path = f"physics.{i}.features.{j}.fields.{values['value']}"
    return window.submit(Edit(object=str(comp.id), ops=[EditOp(op="set", path=path, value=use)]))


def _field_values(feature) -> list[str]:
    """The feature's values some solver takes from an imported field (ADR 0044)."""
    from puzzle.adapters import available_adapters

    taken = set()
    for adapter in available_adapters().values():
        taken |= set(adapter.capabilities.field_features)
    return [k for k in feature.values if f"{feature.type}:{k}" in taken]


def _columns(f) -> list[tuple[str, str]]:
    return [(c, c) for c in f.value_columns()]


def uniform_again(window, comp, i: int, j: int):
    """Every value of the feature back to its own number (the field uses removed)."""
    ops = [EditOp(op="remove", path=f"physics.{i}.features.{j}.fields.{k}")
           for k in comp.physics[i].features[j].fields]  # fmt: skip
    return window.submit(Edit(object=str(comp.id), ops=ops))


def add_morph_handle(window, study):
    """A morph handle on a study (ADR 0065 phase 2) in one form: the face selection that
    moves, how far (a length, or a parameter's name: an optimisation's variable), the
    way, and what stays still."""
    from puzzle.commands.model import Edit
    from puzzle.gui.quick_form import Ask
    from puzzle.model.schema import Component, Geometry
    from puzzle.operators import parameter_values

    project = window.session.project
    comp = project.objects.get(study.component)
    if not isinstance(comp, Component):
        return None
    faces = []
    for part in comp.parts:
        geo = project.objects.get(part.geometry)
        for s in getattr(geo, "selections", []) if isinstance(geo, Geometry) else []:
            if s.entity == "face":
                faces.append((f"{part.name}/{s.name}", f"{part.name}/{s.name}"))
    if not faces:
        window.statusBar().showMessage("A morph moves a face selection: make one first", 8000)
        return None
    params = sorted(parameter_values(project))
    ways = [("Along the face's outward normal", "normal"), ("Along +X", "x"),
            ("Along +Y", "y"), ("Along +Z", "z")]  # fmt: skip
    vectors = {"normal": None, "x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}

    def check(v) -> str | None:
        d = v.get("distance", "").strip()
        if d in params:
            return None
        try:
            _length(d)
        except ValueError:
            return f"'{d}' is neither a length (2 mm) nor a parameter ({', '.join(params)})"
        return None

    got = window.ask_form("Add a morph handle", [
        Ask("selection", "Face that moves", choices=faces),
        Ask("distance", "How far (a length, or a parameter)", kind="text",
            default=params[0] if params else "1 mm"),
        Ask("direction", "Which way", choices=ways),
        Ask("fixed", "Held still", choices=[("The supports", None)] + faces),
    ], check=check)  # fmt: skip
    if not got:
        return None
    d = got["distance"].strip()
    handle = {"selection": got["selection"], "distance": d if d in params else _length(d),
              "direction": vectors[got["direction"]]}  # fmt: skip
    morph = (study.morph.model_dump(mode="json") if study.morph is not None
             else {"handles": [], "fixed": []})  # fmt: skip
    morph["handles"].append(handle)
    if got.get("fixed") and got["fixed"] not in morph["fixed"]:
        morph["fixed"].append(got["fixed"])
    return window.submit(
        Edit(object=str(study.id), ops=[{"op": "set", "path": "morph", "value": morph}])
    )


def _length(text: str) -> float:
    """ "2 mm" -> 0.002 (m); a bare number is in the display unit of lengths."""
    from puzzle.model.units import known, plain, to_si
    from puzzle.settings import length_unit

    number, _, unit = text.strip().partition(" ")
    unit = plain(unit.strip()) if unit.strip() else length_unit()[0]
    if not known(unit):
        raise ValueError(unit)
    return to_si(float(number), unit)


def combine_faceted(window, geo):
    """Two faceted parts (STL, OBJ, 3MF) united, cut or intersected (ADR 0065 phase 4,
    manifold3d) into a new faceted geometry, its STL written into the project's
    imported folder; the two parts are left as they are."""
    from pathlib import Path

    from puzzle.commands.model import Create
    from puzzle.geometry import meshfile
    from puzzle.gui.quick_form import Ask
    from puzzle.model.schema import Geometry

    project, root = window.session.project, Path(window.session.root)
    others = [(g.name, str(g.id)) for g in project.of_type(Geometry)
              if g.id != geo.id and g.source and meshfile.is_mesh_file(g.source)]  # fmt: skip
    if not others:
        window.statusBar().showMessage("Another faceted part (STL, OBJ, 3MF) is needed", 8000)
        return None
    got = window.ask_form("Combine faceted parts", [
        Ask("other", "With", choices=others),
        Ask("mode", "How", choices=[("United (add)", "add"), (f"{geo.name} minus it (cut)",
                                    "cut"), ("Only where they overlap", "intersect")]),
        Ask("name", "Name", kind="text", default=f"{geo.name} combined"),
    ])  # fmt: skip
    if not got:
        return None
    from uuid import UUID

    other = project.objects[UUID(got["other"])]

    def tris(g):
        path = Path(g.source) if Path(g.source).is_absolute() else root / g.source
        return meshfile.triangles(path, g.source_unit)

    try:
        out = meshfile.boolean(tris(geo), tris(other), got["mode"])
    except meshfile.MeshFileError as exc:
        window.statusBar().showMessage(str(exc), 10000)
        return None
    stem = "".join(c if c.isalnum() or c in "-_" else "_" for c in got["name"]).strip("_")
    rel = f"imported/{stem or 'combined'}.stl"
    meshfile.write_stl(out, root / rel)
    return window.submit(Create(kind="geometry", data={"name": got["name"], "source": rel,
                                                       "source_unit": "m"}))  # fmt: skip
