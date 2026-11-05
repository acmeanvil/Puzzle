"""Optimisations in the window (ADR 0063): made in one form, run in the background
(each point a stored run, the Output log saying which, the panel filling as points
finish), and read in the Optimisation panel: the points table, a plot (by point,
against a variable, the Pareto front), the sensitivities and the best point, which is
suggested and applied only when asked (one undoable batch, a version saved first).
"""

from __future__ import annotations

import re
import threading
from uuid import UUID

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from puzzle.errors import PuzzleError

ROLE = Qt.ItemDataRole.UserRole
SENSES = [("Smallest (minimise)", "minimise"), ("Largest (maximise)", "maximise")]
STATISTICS = [
    ("The mean over the scatter", "mean"),
    ("Its own value", "nominal"),
    ("The mean plus 3 standard deviations", "mean_plus_sd"),
]


SCATTER = re.compile(
    r"(?:\b(normal|uniform|lognormal)\s+)?(?:±|\+-|\+/-)\s*(.+)$|"
    r"(?:\b(normal|uniform|lognormal)\s+)?(\d[\d.eE+-]*)\s*%$"
)


def _scatter(text: str) -> tuple[str, dict | None]:
    """A line's scatter at its end ("± 10 N", "uniform ± 0.1 mm", "lognormal 5 %"): the
    rest of the line and the scatter (robust design, ADR 0063 item 11)."""
    m = SCATTER.search(text)
    if not m:
        return text, None
    rest = text[: m.start()].strip()
    if m.group(2) is not None:
        return rest, {"distribution": m.group(1) or "normal", "spread": _quantity(m.group(2))}
    return rest, {"distribution": m.group(3) or "normal", "cv": float(m.group(4)) / 100}


def parse_variables(text: str) -> list[dict]:
    """One variable a line: "t: 4 mm .. 8 mm", "n: 2 .. 6 integer", "t: 4 mm, 6 mm, 8 mm"
    (choices). Robust design: "F: random ± 10 N" (normal; or uniform, lognormal; or a
    share, "random lognormal 5 %") stays at its value and scatters; a range may scatter
    too, "t: 4 mm .. 8 mm uniform ± 0.1 mm" (a tolerance)."""
    out = []
    for line in [x.strip() for x in text.splitlines() if x.strip()]:
        name, _, rest = line.partition(":")
        if not rest.strip():
            raise ValueError(f"'{line}': write name: low .. high")
        rest, scatter = _scatter(rest.strip())
        if rest.startswith("random"):
            if scatter is None:
                raise ValueError(f"'{line}': a random variable says how it scatters, "
                                 "e.g. random ± 10 N")  # fmt: skip
            out.append({"name": name.strip(), "kind": "random", "scatter": scatter})
            continue
        integer = rest.endswith(" integer")
        rest = rest.removesuffix(" integer").strip()
        if ".." in rest:
            low, high = (_quantity(x) for x in rest.split("..", 1))
            out.append({"name": name.strip(), "kind": "integer" if integer else "continuous",
                        "low": low, "high": high,
                        **({"scatter": scatter} if scatter else {})})  # fmt: skip
        else:
            out.append({"name": name.strip(), "kind": "choice",
                        "choices": [_quantity(x) for x in rest.split(",")]})  # fmt: skip
    if not out:
        raise ValueError("one variable at least")
    return out


def parse_constraints(text: str, names: dict[str, str]) -> list[dict]:
    """One constraint a line: "Tip <= 1 mm", "First frequency >= 30 Hz", by the name of
    an operator or a result tool; robust design's "Tip <= 1 mm at 99 %" holds it at a
    probability over the scatter."""
    out = []
    for line in [x.strip() for x in text.splitlines() if x.strip()]:
        m = re.match(r"(.+?)\s*(<=|≤|>=|≥)\s*(.+)$", line)
        if not m:
            raise ValueError(f"'{line}': write name <= limit, or name >= limit")
        name, sign, limit = m.group(1).strip(), m.group(2), m.group(3).strip()
        held = re.search(r"\s+at\s+([\d.]+)\s*%$", limit)  # robust design's probability
        probability = float(held.group(1)) / 100 if held else None
        limit = _quantity(limit[: held.start()] if held else limit)
        if name not in names:
            raise ValueError(f"no operator or result tool '{name}'")
        side = "upper" if sign in ("<=", "≤") else "lower"
        out.append({"source": names[name], "port": "out", side: limit,
                    **({"probability": probability} if probability else {})})  # fmt: skip
    return out


def _quantity(text: str) -> dict:
    from puzzle.model.units import known, plain

    number, _, unit = text.strip().partition(" ")
    unit = plain(unit.strip()) if unit.strip() else "dimensionless"
    if not known(unit):
        raise ValueError(f"unknown unit '{unit}'")
    return {"value": float(number), "unit": unit}


def outputs(project) -> dict[str, str]:
    """Name -> id of what an objective or constraint can read: operators giving a
    number or a verdict, and result tools."""
    from puzzle.model.schema import Operator, ResultProbe
    from puzzle.operators import TYPES

    out = {}
    for o in project.of_type(Operator):
        outs = TYPES[o.type].outputs
        if outs and outs[0].type in ("number", "verdict"):
            out[o.name] = str(o.id)
    for p in project.of_type(ResultProbe):
        out[p.name] = str(p.id)
    return out


def new_optimisation(window, study_id: str | None = None):
    """An optimisation in one form (ADR 0063): the study it drives, its variables, the
    objective and constraints, the plan."""
    from puzzle.commands.model import Create
    from puzzle.gui.quick_form import Ask
    from puzzle.model.schema import Study
    from puzzle.optimise import METHODS

    project = window.session.project
    studies = [(s.name, str(s.id)) for s in project.of_type(Study)]
    if not studies:
        window.statusBar().showMessage("An optimisation drives a study: make one first", 8000)
        return None
    names = outputs(project)
    params = sorted(project.parameter_names())
    if not params:
        window.statusBar().showMessage(
            "An optimisation changes parameters: make a parameter set, a Number or a "
            "Slider first", 8000)  # fmt: skip
        return None

    def check(v) -> str | None:
        try:
            for var in parse_variables(v.get("variables", "")):
                if var["name"] not in params:
                    return f"no parameter '{var['name']}' (one of {', '.join(params)})"
            parse_constraints(v.get("constraints", ""), names)
        except ValueError as exc:
            return str(exc)
        if v.get("objective") is None and not v.get("constraints", "").strip():
            return "an objective, or a constraint, to read at each point"
        return None

    first = params[0]
    got = window.ask_form("New optimisation", [
        Ask("study", "Study it runs", choices=studies, default=study_id),
        Ask("variables", "Design variables", kind="multiline",
            default=f"{first}: 1 .. 2", help="One a line: t: 4 mm .. 8 mm; n: 2 .. 6 "
            "integer; t: 4 mm, 6 mm, 8 mm (choices); robust design: F: random ± 10 N"),
        Ask("objective", "Objective", choices=[("None: constraints only", None)]
            + [(n, i) for n, i in names.items()]),
        Ask("sense", "Best is", choices=SENSES),
        Ask("statistic", "Robust design reads", choices=STATISTICS,
            help="Robust design's objective: at the variables' own values, their mean "
            "over the scatter, or the mean plus three standard deviations"),
        Ask("constraints", "Constraints", kind="multiline", default="",
            help="One a line, by an operator's or result tool's name: Tip <= 1 mm (robust "
            "design: Tip <= 1 mm at 99 %)"),
        Ask("method", "Method", choices=[(t, k) for k, t in METHODS.items()],
            default="latin_hypercube", help="A design of experiments plans its points; "
            "COBYLA and Nelder–Mead improve on the current values; differential "
            "evolution searches the whole range"),
        Ask("samples", "Points", kind="text", default="10",
            help="A sampled plan's points (Bayesian and response surface: the plan they "
            "start from), or the most a local or evolutionary optimiser runs"),
        Ask("name", "Name", kind="text", default="Optimisation"),
    ], check=check)  # fmt: skip
    if not got:
        return None
    data = {"name": got["name"], "study": got["study"], "method": got["method"],
            "variables": parse_variables(got["variables"]),
            "constraints": parse_constraints(got["constraints"], names)}  # fmt: skip
    if got.get("samples", "").strip().isdigit():
        from puzzle.optimise import DOE, MODELLED

        key = "samples" if got["method"] in DOE + MODELLED else "max_solves"
        data[key] = int(got["samples"])
    if got.get("objective"):
        data["objective"] = {"source": got["objective"], "sense": got["sense"]}
        if got["method"] == "robust":
            data["objective"]["statistic"] = got.get("statistic") or "mean"
    outcome = window.submit(Create(kind="optimisation", data=data))
    if outcome is not None and outcome.created:
        oid = outcome.created[0]["id"]
        window.select_in_tree(("object", oid))
        window.show_optimisation(oid)
        return oid
    return None


def run(window, opt_id: str) -> None:
    """Run an optimisation's points in the background; Stop finishes the running one."""
    from puzzle import optimise
    from puzzle.project.layout import WORK_DIR

    s = window.session
    opt = s.project.objects.get(UUID(opt_id))
    if opt is None:
        return
    stop = threading.Event()
    window.optimisation_stop = stop
    project, root, store = s.project, s.root, window._store()

    def work(emit, emit_event):
        return optimise.run(project, opt, root, root / WORK_DIR / "optimise" / str(opt.id),
                            store=store, on_output=emit, cancel=stop,
                            on_point=emit_event)  # fmt: skip

    def finished_point(point) -> None:  # the panel fills as points finish
        panel = getattr(window, "optimisation", None)
        if panel is not None and panel.opt_id == opt_id:
            panel.add_point(point)

    def done(points) -> None:
        window.optimisation_stop = None
        window.refresh()
        window.show_optimisation(opt_id)
        said = optimise.suggestion(window.session.project, opt, points)
        n = sum(p.run for p in points)
        text = f"{opt.name}: {n} of {len(points)} points run" + (
            f" ({points.ended})" if optimise.adaptive(opt) and points.ended else ""
        )
        window.statusBar().showMessage(text + (f". Best: {said} (Apply best to use it)"
                                               if said else ""), 15000)  # fmt: skip

    panel = getattr(window, "optimisation", None)
    if panel is not None and panel.opt_id == opt_id:
        panel.start_live()
    window.background(f"Optimising {opt.name}…", work, done, on_event=finished_point,
                      cancel=stop)  # fmt: skip


def stop(window) -> None:
    if getattr(window, "optimisation_stop", None) is not None:
        window.optimisation_stop.set()


def apply_best(window, opt_id: str):
    """The best feasible point's values into the model's parameters: a version saved
    first, then one batch (one undo). Only when asked (ADR 0063 item 6)."""
    from puzzle import optimise

    s = window.session
    opt = s.project.objects.get(UUID(opt_id))
    top = optimise.best(opt, optimise.points(s.project, window._store(), opt))
    if top is None:
        window.statusBar().showMessage(f"{opt.name} has no feasible point yet", 8000)
        return None
    s.save_version(label=f"Before {opt.name}'s best point")
    return window.submit_batch(optimise.apply_commands(s.project, opt, top))


class OptimisationPanel(QWidget):
    """One optimisation's points (ADR 0063 item 6): the table (variables, objective,
    constraints, feasible or why not), a plot, the sensitivities, Run, Stop and Apply
    best."""

    def __init__(self, window) -> None:
        from puzzle.gui import theme
        from puzzle.gui.material_builder import Curve

        super().__init__()
        self.window = window
        self.opt_id: str | None = None
        self.points: list = []
        self.live = False  # a run is filling the table
        column = QVBoxLayout(self)
        top = QHBoxLayout()
        self.title = theme.role(QLabel("No optimisation shown"), "title")
        top.addWidget(self.title, 1)
        self.run_button = QPushButton("Run")
        self.run_button.clicked.connect(lambda: self.opt_id and run(window, self.opt_id))
        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(lambda: stop(window))
        self.apply_button = QPushButton("Apply best")
        self.apply_button.setToolTip("Put the best feasible point's values into the "
                                     "model's parameters (a version is saved first)")  # fmt: skip
        self.apply_button.clicked.connect(lambda: self.opt_id and apply_best(window, self.opt_id))
        for b in (self.run_button, self.stop_button, self.apply_button):
            top.addWidget(b)
        column.addLayout(top)
        self.summary = theme.role(QLabel(""), "muted")
        self.summary.setWordWrap(True)
        column.addWidget(self.summary)
        self.table = QTableWidget(0, 0)
        self.table.verticalHeader().hide()
        self.table.itemDoubleClicked.connect(self._open_run)
        column.addWidget(self.table, 3)
        row = QHBoxLayout()
        row.addWidget(QLabel("Plot:"))
        self.plot_of = QComboBox()
        self.plot_of.currentIndexChanged.connect(lambda _: self._plot())
        row.addWidget(self.plot_of, 1)
        column.addLayout(row)
        self.curve = Curve("Point", "")
        column.addWidget(self.curve, 2)

    def show(self, opt_id: str) -> None:  # noqa: A003 - shows an optimisation, not the widget
        self.opt_id = opt_id
        self.refresh()

    def refresh(self) -> None:
        from puzzle import optimise

        s = self.window.session
        opt = s.project.objects.get(UUID(self.opt_id)) if self.opt_id and s else None
        if opt is None:
            self.title.setText("No optimisation shown")
            self.table.setRowCount(0)
            return
        try:
            self.points = optimise.points(s.project, self.window._store(), opt)
        except PuzzleError as exc:
            self.summary.setText(exc.message)
            self.points = []
        self.live = False
        self._render(opt)

    def _fit_text(self, opt, names) -> str:
        """A response surface's fit (ADR 0063 item 4: a poor fit is seen): each output's
        leave-one-out error as a share of its range."""
        from puzzle import optimise

        if opt.method != "response_surface":
            return ""
        fit = optimise.fit_quality(opt, self.points)
        sources = ([opt.objective] if opt.objective else []) + list(opt.constraints)
        parts = [f"{names.get(o.source, k)} {fit[k] * 100:.1f} %"
                 for k, o in zip(optimise.output_keys(opt), sources, strict=True)
                 if fit.get(k) is not None]  # fmt: skip
        return (" Surface fit (leave-one-out error): " + ", ".join(parts) + ".") if parts else ""

    def _robust_text(self, opt, names) -> str:
        """Robust design's answer: each output's mean ± standard deviation and ±3σ range
        over the scatter, and each constraint's probability of holding."""
        from puzzle import optimise
        from puzzle.operators.evaluate import quantity_text

        if opt.method != "robust" or self.live:
            return ""
        got = optimise.robust_summary(self.window.session.project, opt, self.points)
        if not got:
            return ""
        sources = ([opt.objective] if opt.objective else []) + list(opt.constraints)
        parts = []
        for k, o in zip(optimise.output_keys(opt), sources, strict=True):
            row = got["outputs"].get(k)
            if row is None:
                continue
            q = lambda v, kind=row["kind"]: quantity_text(v, kind)  # noqa: E731
            text = f"{names.get(o.source, k)} {q(row['mean'])} ± {q(row['sd'])} " \
                   f"({q(row['low'])} to {q(row['high'])} at ±3σ)"  # fmt: skip
            if "holds" in row:
                want = f" (wanted {row['wanted'] * 100:g} %)" if row["wanted"] else ""
                text += f", holds {row['holds'] * 100:.2f} %{want}"
            parts.append(text)
        head = (" Robust design: " + ", ".join(f"{k} = {v}" for k, v in got["labels"].items())
                + ".") if got["labels"] else " Over the scatter:"  # fmt: skip
        return head + " " + "; ".join(parts) + f" ({got['samples']} samples on the surface)."

    def start_live(self) -> None:
        """A run is starting: points arrive one by one (`add_point`) and are drawn as
        they come, without reading the store again."""
        self.live = True

    def add_point(self, point) -> None:
        s = self.window.session
        opt = s.project.objects.get(UUID(self.opt_id)) if self.opt_id and s else None
        if opt is None:
            return
        if not self.live:  # the first point of a run: the table starts over
            self.live, self.points = True, []
        self.points = [p for p in self.points if p.title != point.title] + [point]
        self._render(opt)

    def _render(self, opt) -> None:
        from puzzle import optimise

        s = self.window.session
        names = {o.id: o.name for o in s.project.objects.values()}
        heads = ["#", *[v.name for v in opt.variables]]
        if opt.objective is not None:
            heads.append(names.get(opt.objective.source, "Objective"))
        heads += [names.get(c.source, "Constraint") for c in opt.constraints]
        heads += ["Feasible", "Time"]
        chosen = opt.method in optimise.MODELLED
        if chosen:
            heads.append("Chosen by")  # the initial plan, or what the model picked
        self.table.setColumnCount(len(heads))
        self.table.setHorizontalHeaderLabels(heads)
        self.table.setRowCount(len(self.points))
        top = optimise.best(opt, self.points)
        from puzzle.operators.evaluate import quantity_text

        for r, p in enumerate(self.points):
            cells = [str(p.index), *[p.labels.get(v.name, "") for v in opt.variables]]
            if opt.objective is not None:
                cells.append("" if p.objective is None
                             else quantity_text(p.objective, p.kinds.get("objective")))  # fmt: skip
            for i, c in enumerate(p.constraints or [None] * len(opt.constraints)):
                cells.append("" if c is None
                             else quantity_text(c, p.kinds.get(f"constraint {i + 1}")))  # fmt: skip
            cells.append("Yes" if p.feasible else (p.reason or "No"))
            cells.append(f"{p.seconds:.0f} s" if p.run else "")
            if chosen:
                cells.append(p.origin.capitalize())
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setData(ROLE, next(iter(p.runs.values()), ""))
                if p is top:
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                self.table.setItem(r, c, item)
        self.table.resizeColumnsToContents()
        run_count = sum(p.run for p in self.points)
        best = optimise.suggestion(s.project, opt, self.points)
        sens = optimise.sensitivities(opt, self.points)
        words = "; ".join(f"{v}: " + ", ".join(f"{k} r = {x:+.2f}" for k, x in row.items())
                          for v, row in sens.items() if row)  # fmt: skip
        self.title.setText(f"{opt.name}: {optimise.METHODS[opt.method]}")
        if optimise.adaptive(opt):
            ended = getattr(self.points, "ended", "")
            head = f"{run_count} points run" + (
                "; running." if self.live else f"; {ended}." if ended else ".")  # fmt: skip
        else:
            head = f"{run_count} of {len(self.points)} points run."
        self.summary.setText(
            head
            + (f" Best: {best}." if best else (" The model is at the best point."
                                               if top is not None else ""))
            + (f" Sensitivities: {words}." if words else "")
            + self._fit_text(opt, names)
            + self._robust_text(opt, names))  # fmt: skip
        self.apply_button.setEnabled(bool(best))
        keep = self.plot_of.currentData()
        self.plot_of.blockSignals(True)
        self.plot_of.clear()
        if opt.objective is not None:
            self.plot_of.addItem("Objective by point", "objective")
            for v in opt.variables:
                self.plot_of.addItem(f"Objective against {v.name}", v.name)
        for i, c in enumerate(opt.constraints):
            self.plot_of.addItem(f"{names.get(c.source, 'Constraint')} by point", f"c{i}")
        keys = optimise.output_keys(opt)
        label = {k: names.get(o.source, k) for k, o in zip(
            keys, ([opt.objective] if opt.objective else []) + list(opt.constraints),
            strict=True)}  # fmt: skip
        for a, b in [(keys[0], k) for k in keys[1:]]:
            self.plot_of.addItem(f"Pareto front: {label[a]} against {label[b]}",
                                 f"pareto:{a}:{b}")  # fmt: skip
        if opt.method in ("response_surface", "robust", "bayesian"):  # the landscape
            for k in keys:
                for v in opt.variables:
                    if v.kind != "choice":
                        self.plot_of.addItem(f"Surface: {label[k]} against {v.name}",
                                             f"surface:{k}:{v.name}")  # fmt: skip
        if opt.method == "robust":
            for k in keys:
                self.plot_of.addItem(f"Distribution: {label[k]}", f"dist:{k}")
        found = self.plot_of.findData(keep)
        self.plot_of.setCurrentIndex(max(found, 0))
        self.plot_of.blockSignals(False)
        self._plot()

    def _plot(self) -> None:
        what = self.plot_of.currentData()
        done = [p for p in self.points if p.run]
        if what is None or not done:
            self.curve.set_series([])
            return
        if what == "objective":
            pts = [(float(p.index), p.objective) for p in done if p.objective is not None]
            self.curve.x_label = "Point"
        elif what.startswith("c") and what[1:].isdigit():
            i = int(what[1:])
            pts = [(float(p.index), p.constraints[i]) for p in done
                   if p.constraints and p.constraints[i] is not None]  # fmt: skip
            self.curve.x_label = "Point"
        elif what.startswith("pareto:"):
            self._pareto(*what.split(":")[1:])
            return
        elif what.startswith(("surface:", "dist:")):
            self._modelled(what)
            return
        else:
            pts = sorted((p.values[what], p.objective) for p in done if p.objective is not None)
            self.curve.x_label = f"{what} (SI)"
        self.curve.set_series([(self.plot_of.currentText(), pts)])

    def _modelled(self, what: str) -> None:
        """A surface along one variable through the best point, with the solved points
        as dots; or (robust design) an output's distribution over the scatter."""
        from puzzle import optimise

        s = self.window.session
        opt = s.project.objects.get(UUID(self.opt_id))
        if what.startswith("dist:"):
            hist = optimise.distribution(s.project, opt, self.points, what[5:])
            self.curve.x_label = "Value (SI)"
            self.curve.set_series([("Probability density", hist)])
            return
        _, key, var = what.split(":", 2)
        line, dots = optimise.surface_slice(s.project, opt, self.points, key, var)
        self.curve.x_label = f"{var} (SI)"
        self.curve.set_series([("Surface", line), ("Solved points", dots)], dots={1})

    def _pareto(self, a: str, b: str) -> None:
        """The feasible points as dots, the front (those no other beats on both) as a
        line: x the second output, y the first (the objective)."""
        from puzzle import optimise

        opt = self.window.session.project.objects.get(UUID(self.opt_id))

        def xy(p):
            return optimise.output_of(p, b), optimise.output_of(p, a)

        ok = [p for p in self.points if p.feasible and None not in xy(p)]
        front = optimise.pareto(opt, ok, [a, b])
        self.curve.x_label = self.plot_of.currentText().rsplit(" against ", 1)[-1] + " (SI)"
        self.curve.set_series([("Pareto front", [xy(p) for p in front]),
                               ("Feasible points", [xy(p) for p in ok])],
                              dots={1})  # fmt: skip

    def _open_run(self, item) -> None:
        run_id = item.data(ROLE)
        if run_id:
            self.window.show_results_of(run_id)
