"""Run shoot-out… (ADR 0011; ADR 0090 package 16, ADR-7): a study solved by its own
solver and by others on one mesh, compared, asked in one form and run in the background
through the session's `run_shootout` command, as MCP `solver_shootout` and `puzzle run
--shootout` are. The comparison shows as a Messages tab; each solver's run is stored
and appears under the study as any run does."""

from __future__ import annotations

from uuid import UUID

from PySide6.QtWidgets import QHeaderView, QTableWidget, QTableWidgetItem

HEADS = ["Solver", "Quantity", "Measure", "Difference", "Tolerance", "Result"]
MEASURES = {
    "relative_l2": "Relative difference, node by node (L2)",
    "max_abs_difference": "Largest difference (SI)",
    "relative_value": "Relative difference of the peak",
}


def others(window, study) -> list[tuple[str, str]]:
    """The solvers that could run the study beside its own: those whose capabilities
    take its physics and steps (the adapter's check decides the rest)."""
    from puzzle.adapters import available_adapters
    from puzzle.labels import solver_label
    from puzzle.model.schema import Component

    project = window.session.project
    comp = project.objects.get(study.component)
    physics = {p.type for p in comp.physics} if isinstance(comp, Component) else set()
    steps = {s.type for s in study.solve_steps()}
    out = []
    for key, adapter in sorted(available_adapters().items()):
        if key == study.solver:
            continue
        caps = adapter.capabilities
        if all(steps <= set(caps.physics.get(p, ())) for p in physics):
            out.append((solver_label(key), key))
    return out


def solver_shootout(window, study_id: str | None = None):
    """Ask the study and the solvers to compare it with; solve in the background; show
    the table. Returns the background task (None if cancelled or nothing to compare)."""
    from puzzle.gui.quick_form import Ask
    from puzzle.labels import solver_label
    from puzzle.model.schema import Study

    project = window.session.project
    studies = [s for s in project.of_type(Study)]
    if not studies:
        window.statusBar().showMessage("A shoot-out solves a study: make one first", 8000)
        return None
    if study_id is None:
        study = window.study_to_run()
        if study is None:
            return None
    else:
        study = project.objects.get(UUID(study_id))
    choices = others(window, study)
    if not choices:
        window.statusBar().showMessage(
            f"No other solver here runs '{study.name}'s physics and steps", 8000)  # fmt: skip
        return None
    kept = study.shootout.solvers if study.shootout else []
    default = next((k for _, k in choices if k in kept), choices[0][1])
    names = [n for n, k in choices]

    def check(v) -> str | None:
        return None if v.get("first") else "choose a solver to compare with"

    got = window.ask_form(
        "Run shoot-out",
        [
            Ask("first", "Compare with", choices=choices, default=default),
            Ask("second", "And", choices=[("Nothing else", ""), *choices], default=""),
        ],
        intro=(
            f"'{study.name}' is solved by {solver_label(study.solver)} and by the solvers "
            "chosen here, all on one mesh, and their results compared: by the study's "
            "shoot-out metrics, else each physics' first result node by node within 1 % "
            "(the frequencies of a modal study). Solvers that could run it: "
            + ", ".join(names)
            + "."
        ),
        check=check,
    )
    if not got:
        return None
    solvers = list(dict.fromkeys(s for s in (got["first"], got.get("second")) if s))
    return run(window, str(study.id), solvers)


def run(window, study_id: str, solvers: list[str]):
    """The solves in the background through the session; the table when they finish."""
    from puzzle.commands.model import RunShootout

    s = window.session
    study = s.project.objects.get(UUID(study_id))
    name = study.name if study else "the study"

    def work(emit):
        return s.submit(RunShootout(study=study_id, solvers=solvers), on_output=emit).run

    def done(report) -> None:
        window.refresh()  # the runs under the study
        show(window, report)

    return window.background(f"Shoot-out of {name}…", work, done)


def rows(report: dict) -> list[list[str]]:
    """The table's cells from the report (`solver_shootout`'s answer)."""
    from puzzle.labels import solver_label
    from puzzle.post.quantities import words

    out = []
    for c in report["comparisons"]:
        pct = c["norm"] != "max_abs_difference"
        value = f"{c['value'] * 100:.3g} %" if pct else f"{c['value']:.4g}"
        tol = f"{c['tolerance'] * 100:g} %" if pct else f"{c['tolerance']:g}"
        out.append([f"{solver_label(c['solver'])} against {solver_label(report['primary'])}",
                    words(c["quantity"]), MEASURES.get(c["norm"], c["norm"]), value, tol,
                    "✓ agree" if c["passed"] else "✗ differ"])  # fmt: skip
    return out


def show(window, report: dict) -> QTableWidget:
    """The comparison as a Messages tab of its own (reused by the next shoot-out)."""
    table = getattr(window, "solver_shootout_table", None)
    if table is None:
        table = QTableWidget(0, len(HEADS))
        table.setProperty("help", "form:solver_shootout")
        table.verticalHeader().hide()
        window.solver_shootout_table = table
    table.setHorizontalHeaderLabels(HEADS)
    cells = rows(report)
    table.setRowCount(len(cells))
    for r, row in enumerate(cells):
        for c, text in enumerate(row):
            table.setItem(r, c, QTableWidgetItem(text))
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    tabs = window.output_tabs
    if tabs.indexOf(table) < 0:
        tabs.addTab(table, "Solver shoot-out")
    tabs.setCurrentWidget(table)
    window.fold_messages(False)
    said = "the solvers agree" if report["passed"] else "the solvers differ"
    window.statusBar().showMessage(f"{report['study']}: shoot-out, {said}", 15000)
    return table
