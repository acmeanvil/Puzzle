"""The menu bar: every ribbon tool and right-click action, in one place.

Project, Materials, Geometry, Mesh, Model, Results, Window and Help. The ribbons and
the tree's menus stay; these menus reach the same functions, so nothing is only a
right-click away. An action that needs a part, component or study takes the one
selected in the tree (or open in its tab), else the only one, else asks with one form.
Geometry and mesh tools open their tab first. Submenus that depend on the project (a
component's physics and features) are rebuilt each time the menu opens.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QMenu, QMessageBox

from puzzle.model.schema import Component, Geometry, ParameterSet, Study

HELP_URL = "https://github.com/acmeanvil/Puzzle"


def build(window, agent: QAction, messages: QAction) -> dict[str, QMenu]:
    """Fill the window's menu bar; returns the menus by title (tests look them up)."""
    bar = window.menuBar()
    bar.clear()
    menus: dict[str, QMenu] = {}
    for title, fill in (
        ("Project", _project),
        ("Materials", _materials),
        ("Geometry", _geometry),
        ("Mesh", _mesh),
        ("Model", _model),
        ("Results", _results),
    ):
        menu = bar.addMenu(f"&{title}")
        menu.setToolTipsVisible(True)
        menus[title] = menu
        fill(window, menu)
        # What needs a project is greyed out without one.
        menu.aboutToShow.connect(lambda m=menu: _enable(window, m))
    window_menu = _sub(bar, "&Window")
    _window(window, window_menu, agent, messages)
    menus["Window"] = window_menu
    help_menu = _sub(bar, "&Help")
    _help(window, help_menu)
    menus["Help"] = help_menu
    return menus


# --- helpers -----------------------------------------------------------------------------


def _add(menu: QMenu, text: str, slot: Callable, tip: str = "", keys=None, free=False):
    action = menu.addAction(text)
    action.triggered.connect(lambda _=False: slot())
    if tip:
        action.setToolTip(tip)
    if keys is not None:
        action.setShortcut(QKeySequence(keys))
    action.setProperty("needs_project", not free)
    return action


def _sub(menu: QMenu, title: str) -> QMenu:
    """A submenu parented to its menu, so Qt owns it: one PySide made by
    `addMenu(title)` can be collected with its Python wrapper, leaving a dead entry."""
    sub = QMenu(title, menu)
    sub.setToolTipsVisible(True)
    menu.addMenu(sub)
    return sub


def _enable(window, menu: QMenu) -> None:
    has = window.session is not None
    for action in menu.actions():
        if action.property("needs_project"):
            action.setEnabled(has)
        if action.menu() is not None:
            action.menu().setEnabled(has)


def _choose(window, title: str, objects: list, what: str):
    """The one object of a kind, or the user's choice in one form."""
    if not objects:
        return None
    if len(objects) == 1:
        return objects[0]
    from puzzle.gui.quick_form import Ask

    got = window.ask_form(
        title,
        [Ask("which", what, choices=[(o.name, str(o.id)) for o in objects])],
    )
    if got is None:
        return None
    return next(o for o in objects if str(o.id) == got["which"])


def current_component(window, title: str = "Component") -> Component | None:
    """The component selected (or owning the selection), the Mesh tab's, else asked."""
    comp = window._component_of(window.tree.current())
    if comp is None and window.left_tabs.currentWidget() is window.mesh_bench:
        comp = window.mesh_bench.component_obj()
    if isinstance(comp, Component) and comp.id in window.session.project.objects:
        return comp
    comps = window.session.project.of_type(Component)
    if not comps:
        window.statusBar().showMessage("Make a component first: Model → New component…", 8000)
        return None
    return _choose(window, title, comps, "Component")


def current_geometry(window, title: str = "Part") -> Geometry | None:
    obj = window._object(window.tree.current())
    if isinstance(obj, Geometry):
        return obj
    geo = window.workbench.geometry()
    if geo is not None:
        return geo
    geos = window.session.project.of_type(Geometry)
    if not geos:
        window.statusBar().showMessage(
            "Make a part first: Geometry → New part… or Import geometry…", 8000
        )
        return None
    return _choose(window, title, geos, "Part")


def current_study(window, title: str = "Study") -> Study | None:
    obj = window._object(window.tree.current())
    if isinstance(obj, Study):
        return obj
    from puzzle.gui.result_tools import current_source

    shown = current_source(window)
    studies = window.session.project.of_type(Study)
    found = next((s for s in studies if str(s.id) == shown), None)
    if found is not None:
        return found
    if not studies:
        window.statusBar().showMessage("Make a study first: Model → New study…", 8000)
        return None
    return _choose(window, title, studies, "Study")


def _in_geometry_tab(window, then: Callable[[], object]) -> None:
    """Open the Geometry tab on the current part, then do `then`."""
    geo = current_geometry(window)
    if geo is None:
        return
    window.left_tabs.setCurrentWidget(window.workbench)
    if window.workbench.geo_id != geo.id:
        window.workbench.show_part(geo.id)
    then()


def _in_mesh_tab(window, then: Callable[[], object]) -> None:
    comp = current_component(window)
    if comp is None:
        return
    window.left_tabs.setCurrentWidget(window.mesh_bench)
    if window.mesh_bench.comp_id != comp.id:
        window.mesh_bench.comp_id = comp.id
        window.mesh_bench.refresh()
    then()


def _with_geometry(window, action) -> Callable[[], None]:
    """The geometry selected in the tree, else one chosen by name."""

    def run() -> None:
        from puzzle.model.schema import Geometry

        project = window.session.project if window.session is not None else None
        if project is None:
            return
        node = window.tree.current()
        obj = project.objects.get(_uuid_or_none(node.object)) if node and node.object else None
        if not isinstance(obj, Geometry):
            items = [(g.name, str(g.id)) for g in project.of_type(Geometry)]
            chosen = window.choose("Geometry", "Which geometry?", items)
            obj = project.objects.get(_uuid_or_none(chosen)) if chosen else None
        if isinstance(obj, Geometry):
            action(obj)

    return run


def _uuid_or_none(text):
    from uuid import UUID

    try:
        return UUID(str(text))
    except ValueError:
        return None


def _with_component(window, action: Callable[[Component], object]) -> Callable[[], None]:
    def run() -> None:
        comp = current_component(window)
        if comp is not None:
            action(comp)

    return run


# --- the menus ---------------------------------------------------------------------------


def _project(window, menu: QMenu) -> None:
    from puzzle.gui import menus

    a = window.actions_by_name
    for name in ("new", "open", "reload"):
        menu.addAction(a[name])
    menu.addSeparator()
    menu.addAction(a["undo"])
    menu.addAction(a["delete"])
    rename = _add(menu, "Rename", lambda: _rename(window), "Rename what is selected in the tree",
                  "F2")  # fmt: skip
    window.actions_by_name["rename"] = rename
    _add(menu, "New group…", lambda: _group(window),
         "A folder in the tree for what is selected, under its heading")  # fmt: skip
    menu.addSeparator()
    _add(menu, "New parameter set", lambda: menus.new_parameter_set(window))
    _add(menu, "Add parameter…", lambda: _add_parameter(window),
         "A named value (8 mm) that geometry steps can use")  # fmt: skip
    _add(menu, "New amplitude…", lambda: menus.edit_amplitude(window),
         "A factor against time, for loads in transient steps")  # fmt: skip
    axes = _sub(menu, "New coordinate system")
    _add(axes, "Cartesian…", lambda: menus.new_axes(window, "cartesian"))
    _add(axes, "Cylindrical…", lambda: menus.new_axes(window, "cylindrical"))
    _add(axes, "Spherical…", lambda: menus.new_axes(window, "spherical"))
    from puzzle.gui import construction

    built = _sub(menu, "New construction geometry")
    _add(built, "Path…", lambda: construction.new_path(window),
         "A straight line for the Path and Linearized stress tools")  # fmt: skip
    _add(built, "Plane…", lambda: construction.new_plane(window),
         "A plane for a result over it, or the force across it")  # fmt: skip
    menu.addSeparator()
    menu.addAction(a["settings"])


def _rename(window) -> None:
    from puzzle.gui.tree import RENAMABLE

    item = window.tree.currentItem()
    node = window.tree.current()
    if item is not None and node is not None and node.kind in RENAMABLE:
        window.tree.editItem(item)
    else:
        window.statusBar().showMessage("Select something in the tree to rename", 6000)


def _group(window) -> None:
    from puzzle.gui import organise

    item = window.tree.currentItem()
    section = window.tree.section_of(item) if item is not None else None
    if section is None:
        window.statusBar().showMessage("Select something under a heading in the tree", 6000)
        return
    organise.new_group(window, section, organise.selected_keys(window, section))


def _add_parameter(window) -> None:
    from puzzle.gui import menus

    sets = window.session.project.of_type(ParameterSet)
    if not sets:
        menus.new_parameter_set(window)
        sets = window.session.project.of_type(ParameterSet)
    params = _choose(window, "Add parameter", sets, "Parameter set")
    if params is not None:
        menus.add_parameter(window, params)


def _materials(window, menu: QMenu) -> None:
    from puzzle.gui import menus

    a = window.actions_by_name
    menu.addAction(a["material"])
    menu.addAction(a["builder"])
    menu.addSeparator()
    _add(menu, "Assign material…", _with_component(
        window, lambda c: menus.assign_material(window, c)),
        "Give a component's bodies a material")  # fmt: skip


def _geometry(window, menu: QMenu) -> None:
    from puzzle.gui import menus
    from puzzle.gui.workbench import GROUPS, tool_name

    _add(menu, "New part…", lambda: window.workbench.new_part(),
         "A new part, starting with a sketch")  # fmt: skip
    _add(menu, "Import geometry…", lambda: menus.import_step_file(window),
         "STEP, IGES, BREP, STL, OBJ or 3MF")  # fmt: skip
    menu.addSeparator()
    wb = window.workbench
    _add(menu, "New sketch…", lambda: _in_geometry_tab(window, wb.new_sketch),
         "Draw a profile on a plane or on a picked flat face")  # fmt: skip
    for title, kinds in GROUPS:
        sub = _sub(menu, title)
        sub.setToolTipsVisible(True)
        for kind in kinds:
            _add(
                sub,
                tool_name(kind),
                lambda k=kind: _in_geometry_tab(window, lambda: wb.start_tool(k)),
            )
        if title == "Prepare":
            _add(sub, "Check", lambda: _in_geometry_tab(window, wb._check),
                 "Gaps, slivers, short edges, overlapping bodies")  # fmt: skip
    menu.addSeparator()
    shown = _sub(menu, "Show and suppress")  # Ansys's hide and suppress, by geometry
    _add(shown, "Hide or show", _with_geometry(window, lambda g: menus.show_geometry(
        window, [g], g.hidden)), "The selected geometry: not drawn, still solved")  # fmt: skip
    _add(shown, "Hide all others", _with_geometry(window, lambda g: menus.hide_others(window, g)))
    _add(shown, "Show all", lambda: menus.show_all(window))
    _add(shown, "Swap hidden", lambda: menus.swap_hidden(window),
         "What is hidden is shown, and what is shown hidden")  # fmt: skip
    _add(shown, "Suppress or unsuppress", _with_geometry(window, lambda g: menus.suppress_geometry(
        window, g, not g.suppressed)), "Left out of meshing and solving")  # fmt: skip
    menu.addSeparator()
    menu.addAction(window.actions_by_name["pick"])
    filters = _sub(menu, "Pick filter")
    from puzzle.gui.window import FILTERS

    for kind, text in FILTERS.items():
        _add(filters, text, lambda k=kind: _pick_filter(window, k))
    sel = _sub(menu, "Selections")
    _add(sel, "New from picks…", lambda: _in_geometry_tab(window, wb._from_picks),
         "Turn on Pick, double-click in the view, then make a selection")  # fmt: skip
    _add(sel, "Add picks to the selected selection", lambda: window.add_picks_to_selection())
    _add(sel, "Reattach…", lambda: _in_geometry_tab(window, wb._reattach),
         "Review selections an edit or a new file detached")  # fmt: skip
    menu.addSeparator()
    _add(menu, "Measure", window.start_measuring,
         "Exact length, area, volume, distance and angle of the picks", "Ctrl+Shift+M")  # fmt: skip


def _pick_filter(window, kind: str) -> None:
    bar = window.pick_bar
    bar.filter.setCurrentIndex(bar.filter.findData(kind))
    window.toggle_picking(True)


def _mesh(window, menu: QMenu) -> None:
    from puzzle.gui import menus
    from puzzle.gui.mesh_bench import TOOLS

    _add(menu, "New mesh recipe", lambda: menus.new_mesh(window))
    menu.addAction(window.actions_by_name["mesh"])
    menu.addSeparator()
    bench = window.mesh_bench
    for title, tools in TOOLS:
        sub = _sub(menu, title)
        for kind, name in tools:
            _add(sub, name, lambda k=kind: _in_mesh_tab(window, lambda: bench.start_tool(k)))
    menu.addSeparator()
    _add(menu, "Show seeds", lambda: _in_mesh_tab(window, bench.show_seeds),
         "Where the edges' nodes will fall, before meshing")  # fmt: skip
    mesher = _sub(menu, "Mesher")
    for name, value in (("Gmsh", "gmsh"), ("Netgen", "netgen")):
        _add(mesher, name, lambda v=value: _in_mesh_tab(window, lambda: bench.set_mesher(v)))
    _add(menu, "Gmsh options…", lambda: _in_mesh_tab(window, bench.edit_gmsh_options),
         "Any of Gmsh's own mesh options, searchable")  # fmt: skip


def _model(window, menu: QMenu) -> None:
    from puzzle.gui import menus

    _add(menu, "New component…", lambda: menus.new_component(window))
    _add(menu, "Copy component…", _with_component(
        window, lambda c: menus.copy_component(window, c)))  # fmt: skip
    menu.addSeparator()
    physics = _sub(menu, "Add physics")
    features = _sub(menu, "Add feature")
    physics.aboutToShow.connect(lambda: _fill_physics(window, physics))
    features.aboutToShow.connect(lambda: _fill_features(window, features))
    menu.addSeparator()
    from puzzle.gui.beam_dialog import add_beam_section
    from puzzle.gui.contact_search import find_contacts_dialog
    from puzzle.gui.fastener_dialog import add_fastener, fasteners_from_holes

    _add(menu, "Find contacts…", _with_component(
        window, lambda c: find_contacts_dialog(window, c)),
        "Where the parts touch: proposals to make bonded contacts")  # fmt: skip
    from puzzle.gui.bolt_types import new_bolt_type

    _add(menu, "New bolt type…", lambda: new_bolt_type(window),
         "A bolt's size, grade and preload, given once for every bolt of that kind")  # fmt: skip
    from puzzle.gui.bolt_generator_panel import generate_bolts

    _add(menu, "Generate bolts…", _with_component(window, lambda c: generate_bolts(window, c)),
         "Bolts for the holes a rule, two selections or picks find, previewed first")  # fmt: skip
    _add(menu, "Bolt table", lambda: window.open_bolts(),
         "Every bolt's properties and results, in a docked table")  # fmt: skip
    _add(menu, "Add fastener…", _with_component(window, lambda c: add_fastener(window, c)))
    _add(menu, "Fasteners from holes…", _with_component(
        window, lambda c: fasteners_from_holes(window, c)),
        "Coaxial holes in different parts, one fastener each")  # fmt: skip
    _add(menu, "Add beam section…", _with_component(
        window, lambda c: add_beam_section(window, c)),
        "A cross-section for a line body (a frame)")  # fmt: skip
    from puzzle.gui.beam_dialog import add_shell_thickness

    _add(menu, "Add shell thickness…", _with_component(
        window, lambda c: add_shell_thickness(window, c)),
        "A thickness for a sheet body (a plate or thin wall)")  # fmt: skip
    menu.addSeparator()
    _add(menu, "New study…", lambda: menus.new_study(window))
    run = window.actions_by_name["run"]  # the toolbar's too: asks which study if none is selected
    run.triggered.disconnect()
    run.triggered.connect(lambda _=False: _run(window))
    run.setText("Run study")
    menu.addAction(run)
    _add(menu, "Solver settings…", lambda: _solver_settings(window),
         "How each solver solves the study: linear solver, tolerances, output")  # fmt: skip


def _solver_settings(window) -> None:
    from puzzle.gui.solver_settings import edit_solver_settings

    study = current_study(window, "Solver settings")
    if study is not None:
        edit_solver_settings(window, study)


def _run(window) -> None:
    study = current_study(window, "Run")
    if study is not None:
        from puzzle.gui.tree import Node

        window.tree.select(Node("object", str(study.id)))
        window.run_selected()


def _fill_physics(window, menu: QMenu) -> None:
    from puzzle.gui import menus
    from puzzle.gui.choices import label
    from puzzle.model.catalog import CATALOG

    menu.clear()
    for kind in CATALOG:
        _add(menu, label(kind), _with_component(
            window, lambda c, k=kind: menus.add_physics(window, c, k)))  # fmt: skip


def _fill_features(window, menu: QMenu) -> None:
    """The selected component's physics, each with its features (none selected: the
    only component's, else a hint)."""
    from puzzle.gui import menus
    from puzzle.gui.choices import label

    menu.clear()
    comp = window._component_of(window.tree.current())
    comps = window.session.project.of_type(Component) if window.session else []
    if comp is None and len(comps) == 1:
        comp = comps[0]
    if comp is None:
        hint = menu.addAction("Select a component in the tree first")
        hint.setEnabled(False)
        return
    if not comp.physics:
        hint = menu.addAction(f"{comp.name} has no physics yet: Model → Add physics")
        hint.setEnabled(False)
        return
    for i, ph in enumerate(comp.physics):
        sub = _sub(menu, label(ph.type))
        for s in menus.specs(ph.type):
            _add(sub, s.label, lambda i=i, s=s: menus.add_feature(window, comp, i, s))


def _results(window, menu: QMenu) -> None:
    from puzzle.gui import result_tools as tools

    ribbon = window.results_ribbon
    _add(menu, "Probe", ribbon.probe, "Read a value: double-click the part")
    _add(menu, "Reaction…", lambda: tools.add_reaction(window),
         "The force and moment a support takes")  # fmt: skip
    _add(menu, "Result on a selection…", lambda: tools.add_selection_result(window),
         "Maximum, minimum, average or integral over a selection")  # fmt: skip
    _add(menu, "Path…", lambda: tools.add_path(window), "A result along a straight line")
    _add(
        menu,
        "Linearized stress…",
        lambda: tools.add_linearized(window),
        "Membrane, bending and peak stress through a wall (ASME VIII-2)",
    )
    _add(menu, "Result on a plane…", lambda: tools.add_plane_result(window),
         "A result's maximum, minimum, average or integral over a plane's cut")  # fmt: skip
    _add(menu, "Section force…", lambda: tools.add_section_force(window),
         "The force and moment carried across a plane")  # fmt: skip
    menu.addSeparator()
    _add(menu, "Section cut", ribbon.cut, "Cut the model by a plane to see inside")
    _add(menu, "Animate", ribbon.animate, "Play the deformation, or the steps")
    _add(menu, "Plots", ribbon.plots, "Result tools against time, steps or distance")
    _add(menu, "Legend…", window.edit_legend, "The colour bar: its range, colours, size and place")
    menu.addSeparator()
    _add(menu, "New combination…", lambda: window.new_derived("linear"),
         "Load cases added, enveloped or ranged")  # fmt: skip
    _add(menu, "New equation…", lambda: window.new_derived("equation"),
         "A result computed from others by a formula")  # fmt: skip
    _add(menu, "New fatigue tool…", lambda: window.new_derived("fatigue"),
         "Life, damage and safety factor from a duty")  # fmt: skip
    _add(menu, "Compare sweep points…", lambda: _compare(window),
         "A swept study's points side by side")  # fmt: skip
    menu.addSeparator()
    _add(menu, "Export…", lambda: _export(window),
         "Results for other software: ParaView, point clouds, the deformed shape")  # fmt: skip


def _compare(window) -> None:
    study = current_study(window, "Compare")
    if study is not None:
        window.open_compare(str(study.id))


def _export(window) -> None:
    from puzzle.gui.export_dialog import export_results
    from puzzle.gui.result_tools import current_source

    obj = window._object(window.tree.current())
    source = str(obj.id) if isinstance(obj, Study) else current_source(window)
    export_results(window, source)


def _window(window, menu: QMenu, agent: QAction, messages: QAction) -> None:
    from PySide6.QtGui import QKeySequence

    # Every panel can be closed with its ×; this menu brings each back.
    for dock, text, keys in (
        (window.model_dock, "Model", "Ctrl+1"),
        (window.properties_dock, "Properties", "Ctrl+2"),
        (window.advisor_dock, "Advisor", "Ctrl+3"),
        (window.sections_dock, "Sections", "Ctrl+4"),
        (window.bolts_dock, "Bolts", "Ctrl+5"),
    ):
        # Not the dock's own toggle action: Qt renames that with the dock's title
        # (Properties reads "Tool" while a tool is open).
        action = QAction(text, window)
        action.setCheckable(True)
        action.setChecked(not dock.isHidden())
        action.setShortcut(QKeySequence(keys))
        action.setToolTip(f"Show or hide the {text} panel")
        action.toggled.connect(dock.setVisible)
        dock.visibilityChanged.connect(lambda _v, a=action, d=dock: a.setChecked(not d.isHidden()))
        window.actions_by_name[text.lower()] = action
        menu.addAction(action)
    menu.addAction(agent)
    menu.addAction(messages)
    menu.addSeparator()
    _add(menu, "3D view background…", lambda: window.pick_view_background(),
         "A colour of your own behind the model (Settings > Interface for a gradient)")  # fmt: skip
    layouts = _sub(menu, "Layouts")  # ADR 0045: built-in and the user's own
    layouts.aboutToShow.connect(lambda: _fill_layouts(window, layouts))
    _fill_layouts(window, layouts)
    hide = _sub(menu, "Auto-hide")
    for key, text in (("model", "Model"), ("properties", "Properties"), ("agent", "Agent"),
                      ("advisor", "Advisor"), ("sections", "Sections"),
                      ("bolts", "Bolts")):  # fmt: skip
        action = QAction(text, window)
        action.setCheckable(True)
        action.setToolTip(f"Hide the {text} panel into a bar on its edge; click there to show it")
        action.toggled.connect(lambda on, k=key: window.docks.set_auto_hide(k, on))
        hide.aboutToShow.connect(lambda a=action, k=key: a.setChecked(k in window.docks.auto))
        hide.addAction(action)
        window.actions_by_name[f"auto_hide_{key}"] = action
    _add(menu, "Reset layout", window.reset_layout, free=True)


def _fill_layouts(window, menu: QMenu) -> None:
    """Window → Layouts: each layout, then save and delete."""
    from puzzle.gui.quick_form import Ask

    menu.clear()
    names = window.docks.names()
    for name, key in names:
        _add(menu, name, lambda k=key: window.docks.apply(k), free=True)
    menu.addSeparator()

    def save() -> None:
        got = window.ask_form("Save layout", [Ask("name", "Name", kind="text",
                                                   default="My layout")],
                              check=lambda v: None if v.get("name", "").strip()
                              else "Give it a name")  # fmt: skip
        if got is not None:
            window.docks.save(got["name"].strip())
            window.statusBar().showMessage(f"Layout '{got['name'].strip()}' saved", 5000)

    def delete() -> None:
        own = [(n, k) for n, k in window.docks.names() if k.startswith("user:")]
        got = window.ask_form("Delete a layout", [Ask("key", "Layout", choices=own)])
        if got is not None:
            window.docks.delete(got["key"])

    _add(menu, "Save current layout…", save, free=True)
    if any(k.startswith("user:") for _, k in names):
        _add(menu, "Delete a layout…", delete, free=True)


def _help(window, menu: QMenu) -> None:
    _add(menu, "Getting started", lambda: _getting_started(window), free=True)
    advisor = window.actions_by_name.get("advisor")
    if advisor is not None:
        menu.addAction(advisor)  # also in Window: what to do next (ADR 0040)
    _add(menu, "Keyboard shortcuts", lambda: _shortcuts(window), free=True)
    _add(menu, "Design decisions (ADRs)", lambda: _open_docs(window), free=True)
    menu.addSeparator()
    about = _add(menu, "About Puzzle", lambda: _about(window), free=True)
    about.setMenuRole(QAction.MenuRole.AboutRole)  # macOS: the application menu


STEPS = (
    "<ol>"
    "<li><b>Geometry:</b> Geometry → New part… or Import geometry…; name faces as "
    "selections (Pick, double-click, Selections → New from picks…).</li>"
    "<li><b>Materials:</b> Materials → Add from library…</li>"
    "<li><b>Mesh:</b> Mesh → New mesh recipe, then Model → New component… to put the "
    "part, its material and the recipe together; Mesh → Mesh (⌘M) to see it.</li>"
    "<li><b>Model:</b> Model → Add physics, then Add feature for supports and loads.</li>"
    "<li><b>Study:</b> Model → New study…, then Run study (⌘R).</li>"
    "<li><b>Results:</b> the Results ribbon above the view, or the Results menu: probes, "
    "reactions, paths, cuts, animation and plots.</li>"
    "</ol>"
    "The Problems tab says what is missing before a run. The Agent panel can do any of "
    "this for you, with your approval."
)


def _getting_started(window) -> None:
    QMessageBox.information(window, "Getting started", STEPS)


def shortcut_rows(window) -> list[tuple[str, str]]:
    """(menu path, keys) for every menu action with a shortcut."""
    out, seen = [], set()

    def walk(menu: QMenu, path: str) -> None:
        for action in menu.actions():
            if action.menu() is not None:
                walk(action.menu(), f"{path} → {action.text()}")
            elif not action.shortcut().isEmpty() and id(action) not in seen:
                seen.add(id(action))  # an action in two menus (Advisor) is listed once
                keys = action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
                out.append((f"{path} → {action.text()}".replace("&", ""), keys))

    for action in window.menuBar().actions():
        if action.menu() is not None:
            walk(action.menu(), action.text().replace("&", ""))
    return out


def _shortcuts(window) -> None:
    rows = "".join(f"<tr><td>{p}</td><td><b>{k}</b></td></tr>" for p, k in shortcut_rows(window))
    QMessageBox.information(window, "Keyboard shortcuts", f"<table>{rows}</table>")


def _open_docs(window) -> None:
    from pathlib import Path

    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices

    local = Path(__file__).parents[3] / "docs" / "adr" / "README.md"
    url = (
        QUrl.fromLocalFile(str(local)) if local.exists() else QUrl(f"{HELP_URL}/tree/main/docs/adr")
    )
    QDesktopServices.openUrl(url)


def _about(window) -> None:
    from importlib.metadata import PackageNotFoundError, version

    try:
        v = version("puzzle")
    except PackageNotFoundError:
        v = "development"
    QMessageBox.about(
        window,
        "About Puzzle",
        f"<b>Puzzle</b> {v}<br>A finite element and CFD pre- and post-processor: one model, "
        "run on CalculiX, Elmer, Code_Aster, FEniCSx, Code_Saturne and OpenFOAM.<br><br>"
        "Free software under the GNU General Public License, version 3 or later.",
    )
