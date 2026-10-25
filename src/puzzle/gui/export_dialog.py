"""Export… (ADR 0043): one form for any export, then where to save it.

The results (a study's latest run), the format by name, the step, and what the
format needs: a point cloud's result, selection and units; the deformed shape's
exaggeration and whether it is compensated. Fields that don't apply to the chosen
format are hidden.
"""

from __future__ import annotations

from pathlib import Path

from puzzle.gui.quick_form import Ask
from puzzle.post.exports import EXPORTS

SHAPES = [("Deformed (moved by +u)", False), ("Compensated (moved by -u: the shape to make)", True)]
UNITS = [("SI (m, Pa, ...)", "si"), ("The display units", "display")]


def _steps(window, source_id: str | None) -> list:
    from puzzle.gui.result_tools import _run_of

    if not source_id:
        return [("Last", -1)]
    _, results = _run_of(window, source_id)
    steps = [(s.label(), i) for i, s in enumerate(results.steps)]
    return [("Last", -1)] + steps if len(steps) > 1 else [("Last", -1)]


def export_results(window, source: str | None = None):
    from puzzle.gui import result_tools as tools
    from puzzle.post.exports import export

    if tools._no_runs(window):
        return None
    cloud = lambda v: v.get("format") == "point_cloud"  # noqa: E731
    shape = lambda v: str(v.get("format", "")).startswith("deformed")  # noqa: E731

    def check(v) -> str | None:
        if shape(v):
            try:
                if float(v.get("scale", "")) <= 0:
                    return "The exaggeration is a number above zero (1 is true size)"
            except ValueError:
                return "The exaggeration is a number above zero (1 is true size)"
        return None

    got = window.ask_form(
        "Export",
        [
            Ask("source", "Results of", choices=tools.sources(window), default=source),
            Ask(
                "format",
                "Format",
                choices=[(e.label, k) for k, e in EXPORTS.items()],
                help="ParaView: every result; a point cloud: one result for another tool's "
                "mesh; the deformed shape: for CAD",
            ),
            Ask("step", "Step", choices=lambda v: _steps(window, v.get("source"))),
            Ask(
                "quantity",
                "Result",
                choices=lambda v: tools.quantity_choices(window, v.get("source")),
                shown=cloud,
            ),
            Ask(
                "selection",
                "Nodes",
                choices=lambda v: (
                    [("The whole model", "")] + tools.selection_choices(window, v.get("source"))
                ),
                shown=cloud,
            ),
            Ask("units", "Units", choices=UNITS, shown=cloud),
            Ask(
                "scale",
                "Exaggeration",
                kind="text",
                default="1",
                shown=shape,
                help="1 is the true deformation; larger exaggerates it (and says so)",
            ),
            Ask("compensate", "Shape", choices=SHAPES, shown=shape),
        ],  # fmt: skip
        intro="Results for other software. Each export writes a .txt beside it naming the "
        "study, run, step and units.",
        check=check,
    )
    if got is None:
        return None
    fmt = EXPORTS[got["format"]]
    path = window.ask_save_path("Export", f"export{fmt.suffix}", f"{fmt.label} (*{fmt.suffix})")
    if not path:
        return None
    from puzzle.post import derived

    record = derived.run_for(window.session.project, window._store(), tools._uuid(got["source"]))
    try:
        files = export(
            window.session.project,
            window._store(),
            record.run_id,
            got["format"],
            Path(path),
            step=got["step"],
            quantity=got.get("quantity"),
            selection=got.get("selection") or None,
            scale=float(got.get("scale") or 1.0),
            compensate=bool(got.get("compensate")),
            units=got.get("units", "si"),
        )
    except Exception as exc:  # noqa: BLE001 - said in the window, never a crash
        window.report(exc) if hasattr(exc, "code") else window.statusBar().showMessage(
            f"Export failed: {exc}", 10000
        )
        return None
    window.statusBar().showMessage(f"Wrote {files[0]}", 10000)
    return files
