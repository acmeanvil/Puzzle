"""Mesh shoot-out… (ADR 0019; ADR 0072 section 10): a study solved on its recipe meshed
by Gmsh and by Netgen, compared, asked in one form and run in the background. It calls
`shootout.mesh_shootout_report`, as MCP `mesh_shootout` does, and shows the comparison
as a table in the Messages area. No project change: the runs are kept in a scratch
folder, never in the store."""

from __future__ import annotations

from uuid import UUID

from PySide6.QtWidgets import QHeaderView, QTableWidget, QTableWidgetItem

NOTHING = ("Nothing else", "")
DEFAULTS = [("Displacement", "displacement"), ("Temperature", "temperature"),
            ("Eigenfrequencies", "eigenfrequencies")]  # fmt: skip
HEADS = ["Quantity", "Netgen against Gmsh", "Tolerance", "Result"]


def quantities(window, study_id: str | None) -> list[tuple[str, str]]:
    """The fields the study's newest run has (by their peak, or a global value), and
    eigenfrequencies for a modal one; before any run, the usual ones."""
    from puzzle.post.derived import run_for
    from puzzle.post.quantities import words

    if not study_id:
        return list(DEFAULTS)
    try:
        record = run_for(window.session.project, window._store(), UUID(study_id))
        steps = window._store().read_results(record.run_id).steps if record else []
    except Exception:  # noqa: BLE001 - no run, or no store yet: the usual ones
        steps = []
    if not steps:
        return list(DEFAULTS)
    out = [(words(n), n) for n in steps[-1].fields]
    if any(s.kind == "modal" for s in steps):
        out.append(("Eigenfrequencies", "eigenfrequencies"))
    return out or list(DEFAULTS)


def mesh_shootout(window, study_id: str | None = None):
    """Ask the study, what to compare and the tolerance; solve both meshes off the GUI
    thread; show the table. Returns the background task (None if cancelled)."""
    from puzzle.gui.agent_prefill import run_form
    from puzzle.gui.quick_form import Ask
    from puzzle.model.schema import Study

    project = window.session.project
    studies = [(s.name, str(s.id)) for s in project.of_type(Study)]
    if not studies:
        window.statusBar().showMessage("A mesh shoot-out solves a study: make one first", 8000)
        return None

    def valid(v) -> str | None:
        return None if _fraction(v.get("tolerance")) else "the tolerance is a percentage, e.g. 2"

    def prompt(v) -> str:
        study = next((n for n, i in studies if i == v.get("study")), studies[0][0])
        what = " and ".join(q for q in (v.get("quantity"), v.get("also")) if q)
        return (f"Run a mesh shoot-out on the study '{study}': its recipe meshed by Gmsh "
                f"and by Netgen, comparing {what} within {v.get('tolerance')} % "
                "(mesh_shootout). Tell me what the difference says about the mesh.")  # fmt: skip

    got = run_form(window, "Mesh shoot-out", [
        Ask("study", "Study", choices=studies, default=study_id or studies[0][1]),
        Ask("quantity", "Compare", choices=lambda v: quantities(window, v.get("study")),
            help="A result's peak (or a global value), or the eigenfrequencies"),
        Ask("also", "And", choices=lambda v: [NOTHING, *quantities(window, v.get("study"))],
            default=""),
        Ask("tolerance", "Agree within (%)", kind="text", default="2",
            help="The largest relative difference between the two meshes' values"),
    ], help="form:mesh_shootout", prompt=prompt, check=valid,
       intro="The study is solved twice, on its recipe meshed by Gmsh and by Netgen "
       "(the recipe otherwise unchanged), and the values compared. The meshes differ, "
       "so only whole-result values compare.")  # fmt: skip
    if not got:
        return None
    asked = list(dict.fromkeys(q for q in (got["quantity"], got.get("also")) if q))
    return run(window, got["study"], asked, _fraction(got["tolerance"]))


def run(window, study_id: str, asked: list[str], tolerance: float):
    """The two solves in the background; the table when they finish."""
    import tempfile
    from pathlib import Path

    from puzzle.shootout import mesh_shootout_report

    s = window.session
    project, root = s.project, s.root
    study = project.objects.get(UUID(study_id))

    def work(emit):
        with tempfile.TemporaryDirectory() as scratch:
            return mesh_shootout_report(project, UUID(study_id), root, Path(scratch), asked,
                                        tolerance, on_output=emit)  # fmt: skip

    def done(report) -> None:
        show(window, study.name if study else "", report, tolerance)

    return window.background(f"Mesh shoot-out on {study.name if study else 'the study'}…",
                             work, done)  # fmt: skip


def rows(report: dict, tolerance: float) -> list[list[str]]:
    """The table's cells from the report (`mesh_shootout`'s answer)."""
    from puzzle.post.quantities import words

    return [[words(c["quantity"]), f"{c['relative_difference'] * 100:.3g} %",
             f"{tolerance * 100:g} %", "✓ agree" if c["passed"] else "✗ differ"]
            for c in report["comparisons"]]  # fmt: skip


def show(window, study: str, report: dict, tolerance: float) -> QTableWidget:
    """The comparison as a Messages tab of its own (reused by the next shoot-out)."""
    table = getattr(window, "mesh_shootout_table", None)
    if table is None:
        table = QTableWidget(0, len(HEADS))
        table.setProperty("help", "form:mesh_shootout")
        table.verticalHeader().hide()
        window.mesh_shootout_table = table
    table.setHorizontalHeaderLabels(HEADS)
    cells = rows(report, tolerance)
    table.setRowCount(len(cells))
    for r, row in enumerate(cells):
        for c, text in enumerate(row):
            table.setItem(r, c, QTableWidgetItem(text))
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    tabs = window.output_tabs
    if tabs.indexOf(table) < 0:
        tabs.addTab(table, "Mesh shoot-out")
    tabs.setCurrentWidget(table)
    window.fold_messages(False)
    said = "the meshes agree" if report["passed"] else "the meshes differ: refine"
    window.statusBar().showMessage(f"{study}: mesh shoot-out, {said}", 15000)
    return table


def _fraction(text) -> float | None:
    try:
        t = float(str(text).strip().rstrip("%")) / 100
    except ValueError:
        return None
    return t if 0 < t < 1 else None
