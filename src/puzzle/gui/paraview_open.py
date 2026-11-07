"""Open in ParaView from the results view (ADR 0070): what is shown, in ParaView.

`view_of` reads the results view (the quantity and its unit, the legend's colours and
range, the deformation, the section cut, the camera) into a `paraview_link.View`;
`open_in_paraview` writes the files into the project's exports and starts ParaView,
leaving Puzzle as it was.
"""

from __future__ import annotations

from puzzle.post import paraview_link


def view_of(viewport):
    """The result shown, as ParaView's script needs it; None when none is shown."""
    state = viewport.result_state
    if not state:
        return None
    base, name, unit = state["base"], state["name"], state.get("unit")
    array = f"{name} [{unit}]" if unit else name
    shown = base.copy(deep=False)
    for data in (shown.point_data, shown.cell_data):
        if name in data and array != name:
            data[array] = data[name]
    warp = next((w for w in ("displacement_global", "displacement") if w in base.point_data),
                None) if state.get("scale") else None  # fmt: skip
    legend = viewport.legend
    clim = viewport.shown.get("clim") or (viewport.shown.get("min"), viewport.shown.get("max"))
    cut = None
    if viewport.cut is not None:
        origin, normal = viewport.cut.plane(base.bounds)
        cut = {"origin": [float(v) for v in origin], "normal": [float(v) for v in normal]}
    camera = viewport.camera()
    view = paraview_link.View(
        array=array,
        points=array in shown.point_data,
        title=f"{name} ({unit})" if unit else name,
        clim=tuple(float(c) for c in clim) if None not in clim else None,
        colormap=legend.colormap,
        invert=legend.invert,
        bands=legend.bands,
        warp=warp,
        scale=float(state.get("scale") or 0.0),
        camera=[list(map(float, row)) for row in camera] if camera is not None else None,
        parallel=bool(viewport.style.get("parallel")),
        parallel_scale=viewport.parallel_scale(),
        cut=cut,
        step=state["step"].label() if state.get("step") is not None else "",
    )
    return shown, view


def open_in_paraview(window) -> bool:
    """Write the shown result for ParaView and start it; False (and a note) otherwise."""
    if (why := paraview_link.why_not()) is not None:
        window.statusBar().showMessage(f"Open in ParaView: {why}", 8000)
        return False
    found = view_of(window.viewport)
    run_id = getattr(window, "_result_run", None)
    if found is None or window.result is None or run_id is None:
        window.statusBar().showMessage("Open in ParaView: show a result first", 8000)
        return False
    shown, view = found
    mesh, results = window.result
    folder = paraview_link.folder_for(window.session.root, run_id)

    def work(_emit):
        return paraview_link.write(folder, shown, view, mesh, results)

    def done(script) -> None:
        paraview_link.launch(script)
        window.toasts.show(f"Opened in ParaView: {view.title}")

    window.background("Writing the result for ParaView…", work, done, lock=False)
    return True
