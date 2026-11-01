"""Imported fields' connections in the window (ADR 0044 decision 8): in Properties
(`connections.tree`: an imported field's uses, a field-fed feature's chain from the file
to the solver) and in the 3D view (`connections.view`: the mapped values on the
feature's faces or bodies, extrapolated areas marked, the data's points if asked for).

Mapping runs in the background (`MainWindow.background`); the result is drawn only if
the same item is still selected. The words come from `puzzle.field_display`.
"""

from __future__ import annotations

import copy


def _on(key: str) -> bool:
    from puzzle.settings import current

    return bool(current().get(key))


def _beyond_colour() -> str:
    from puzzle.post.legend import COLOURS
    from puzzle.settings import current

    return COLOURS.get(current().get("connections.beyond_color"), COLOURS["magenta"])


def _feature_at(comp, path: str):
    """(physics, feature) of a tree path "physics.i.features.j"."""
    parts = path.split(".")
    ph = comp.physics[int(parts[1])]
    return ph, ph.features[int(parts[3])]


# --- Properties ------------------------------------------------------------------------------


def show_properties(window, obj, node) -> None:
    """Rows under the object's own: "Used by" for an imported field, and for a
    field-fed feature each value's chain from the file to the solver."""
    if not _on("connections.tree"):
        return
    from puzzle import field_display as fd
    from puzzle.gui.choices import solver_label
    from puzzle.model.schema import Component, ImportedField

    project = window.session.project
    if isinstance(obj, ImportedField):
        rows = fd.used_by(project, obj)
        if not rows:
            rows = [("Not used yet", "a feature's menu → Take a value from an imported field…")]
        window.properties.add_rows("Used by", rows)
        return
    if node is None or node.kind != "feature" or not isinstance(obj, Component):
        return
    ph, feature = _feature_at(obj, node.path)
    for value in sorted(feature.fields):
        rows = fd.chain(project, obj, ph.type, feature, value, solver_label)
        window.properties.add_rows(f"Where {value} comes from", rows)


# --- the 3D view -----------------------------------------------------------------------------


def _draw(window, mesh, selection: str, mapped, key) -> None:
    """Draw a mapping if the same item is still selected."""
    node = window.tree.current()
    if node is None or (node.kind, node.object, node.path) != key:
        return
    viewport = window.viewport
    points = mapped.points if _on("connections.points") else None
    viewport.show_field_values(
        mesh, selection, mapped.node_ids, mapped.values, mapped.beyond, title=mapped.label,
        unit=mapped.unit, points=points, point_values=mapped.point_values,
        beyond_colour=_beyond_colour(),
    )  # fmt: skip
    far = mapped.extrapolated
    message = f"{mapped.label} on {selection}"
    if far:
        tol = mapped.tolerance
        message += (f": {far} of {len(mapped.node_ids)} nodes farther than "
                    f"{_length(tol)} from the data (extrapolated, marked)")  # fmt: skip
    window.statusBar().showMessage(message, 10000)


def _length(value: float) -> str:
    from puzzle.field_display import _length as words

    return words(value)


def _map_in_background(window, mesh, comp, feature, use, key) -> None:
    from puzzle.field_display import mapped
    from puzzle.model.schema import ImportedField

    project = copy.deepcopy(window.session.project)
    root = window.session.root
    f = project.objects.get(use.field)
    if not isinstance(f, ImportedField):
        return
    selection = feature.selection
    if selection not in mesh.groups:
        window.viewport.shown["field_values"] = {"missing": selection}
        return

    def work(_emit):
        return mapped(project, root, f, use.column, mesh, selection, use)

    def failed(exc) -> None:
        window.viewport.shown["field_values"] = {"error": str(exc)}

    window.viewport.shown["field_values"] = {"mapping": True}
    window.background(f"Mapping {f.name} onto {selection}", work,
                      lambda m: _draw(window, mesh, selection, m, key), failed=failed,
                      lock=False)  # fmt: skip


def show_feature(window, comp, node, mesh) -> None:
    """A selected field-fed feature: its (first) field-fed value mapped on its
    selection, over the mesh already shown."""
    if not _on("connections.view") or mesh is None:
        return
    _, feature = _feature_at(comp, node.path)
    if not feature.fields:
        return
    use = feature.fields[sorted(feature.fields)[0]]
    _map_in_background(window, mesh, comp, feature, use, (node.kind, node.object, node.path))


def show_field(window, f, node) -> bool:
    """A selected imported field: on the first feature that uses it, its mapped values
    (the model shown with that selection highlighted); unused, its points over the first
    component. False when there is nothing to draw on."""
    if not _on("connections.view"):
        return False
    from puzzle.model.schema import Component

    project = window.session.project
    key = (node.kind, node.object, node.path)
    for comp in project.of_type(Component):
        for ph in comp.physics:
            for feature in ph.features:
                for use in feature.fields.values():
                    if use.field != f.id:
                        continue
                    mesh = _shown_mesh(window, comp, feature.selection)
                    if mesh is None:
                        return False
                    _map_in_background(window, mesh, comp, feature, use, key)
                    return True
    comps = project.of_type(Component)
    if not comps or _shown_mesh(window, comps[0], None) is None:
        return False
    _points_in_background(window, f, key)
    return True


def _shown_mesh(window, comp, highlight):
    """The component's mesh (or preview) drawn with `highlight`; None while a preview
    is still being made."""
    mesh = window.meshes.get(str(comp.id)) or window._preview(comp)
    if mesh is None:
        return None
    cid = str(comp.id)
    window.mesh_component = cid
    window.viewport.boundaries = window.mesh_boundaries.get(cid, {})
    window.viewport.vertices = window.mesh_vertices.get(cid, {})
    window.viewport.show_mesh(mesh, highlight if highlight in mesh.groups else None)
    return mesh


def _points_in_background(window, f, key) -> None:
    from puzzle.fields import cloud

    project = copy.deepcopy(window.session.project)
    root = window.session.root

    def work(_emit):
        return cloud(project, root, f)

    def done(c) -> None:
        node = window.tree.current()
        if node is None or (node.kind, node.object, node.path) != key:
            return
        column = next(iter(c.values), None)
        values = c.values.get(column) if column else None
        window.viewport.show_field_points(c.points, values, c.units.get(column, ""), bar=True)
        window.statusBar().showMessage(
            f"{f.name}: {len(c.points)} points (not used by a feature yet)", 10000
        )

    window.background(f"Reading {f.name}", work, done, lock=False)
