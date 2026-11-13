"""The Report operator (ADR 0071 step 5, live nodes): what the schematic worked out,
gathered into one HTML file for a reader who doesn't open Puzzle.

Up to eight inputs (`a` to `h`), each anything: a check's verdict (pass or fail, its
margin), a number with its unit, a curve (a plot, drawn by matplotlib and embedded), a
table (its first rows in the shown units), a field (its range), a list or text. Each
item is headed by the node it comes from. It is written with Export now (the GUI, CLI
`operators --export`, MCP) into the project (`file`, default exports/<name>.html), one
self-contained file: no links to the project, so it can be mailed.
"""

from __future__ import annotations

import base64
import html
import io
from datetime import datetime
from pathlib import Path

import numpy as np

from puzzle.errors import PuzzleError

KEYS = "abcdefgh"
ROWS = 50  # a table's rows shown


def path_of(op) -> str:
    if op.file:
        return op.file
    stem = "".join(c if c.isalnum() or c in "-_" else "_" for c in op.name).strip("_")
    return f"exports/{stem or 'report'}.html"


def _source_name(ctx, op, key: str) -> str:
    wired = op.inputs.get(key)
    if wired is None or wired.source is None:
        return f"Value {key.upper()}"
    obj = ctx.project.objects.get(wired.source)
    return obj.name if obj is not None else f"Value {key.upper()}"


def _plot(series) -> str:
    """A curve drawn as an embedded PNG, in display units."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from puzzle.model.units import DIMENSIONS, convert
    from puzzle.operators.evaluate import display_unit

    def scale(kind):
        if kind is None or kind not in DIMENSIONS:
            return 1.0, 0.0, ""
        unit = display_unit(kind)
        f = convert(1.0, DIMENSIONS[kind][1], unit)
        o = convert(0.0, DIMENSIONS[kind][1], unit)
        return f - o, o, unit

    fx, ox, ux = scale(series.x_kind)
    fy, oy, uy = scale(series.y_kind)
    fig, ax = plt.subplots(figsize=(6.4, 3.6), dpi=110)
    for c in series.curves:
        if c.style == "limit":  # a limit line: across
            ax.axhline(float(c.y[0]) * fy + oy, linestyle="--", linewidth=1, label=c.label)
        else:
            ax.plot(np.asarray(c.x) * fx + ox, np.asarray(c.y) * fy + oy, label=c.label)
    ax.set_xlabel(f"{series.x_label} [{ux}]" if ux else series.x_label)
    ax.set_ylabel(f"{series.label} [{uy}]" if uy else series.label)
    if len(series.curves) > 1:
        ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    data = base64.b64encode(buf.getvalue()).decode("ascii")
    return f'<img alt="{html.escape(series.label)}" src="data:image/png;base64,{data}">'


def _table(table) -> str:
    from puzzle.operators.frames import shown_frame

    frame = shown_frame(table)
    head = "".join(f"<th>{html.escape(str(c))}</th>" for c in frame.columns)
    rows = []
    for r in frame.head(ROWS).rows():
        cells = "".join(f"<td>{v:.6g}</td>" if isinstance(v, float) else
                        f"<td>{html.escape(str(v))}</td>" for v in r)  # fmt: skip
        rows.append(f"<tr>{cells}</tr>")
    more = (f"<p class=more>{frame.height - ROWS} more rows</p>"
            if frame.height > ROWS else "")  # fmt: skip
    return f"<table><tr>{head}</tr>{''.join(rows)}</table>{more}"


def _item(got) -> tuple[str, str]:
    """(html, verdict) for one input."""
    from puzzle.operators.evaluate import FieldValue, Table, Verdict

    if isinstance(got, Verdict):
        word = "Pass" if got.passed else "Fail"
        return f"<p class={word.lower()}>{html.escape(got.text())}</p>", word.lower()
    if getattr(got, "type", "") == "series":
        if not got.curves:
            return "<p>An empty curve</p>", ""
        try:
            return _plot(got), ""
        except ImportError:  # no matplotlib here: the curve's points as a table
            return _table(got.table()), ""
    if isinstance(got, Table):
        return _table(got), ""
    if isinstance(got, FieldValue):
        v = np.asarray(got.values, float)
        v = v[np.isfinite(v)]
        text = got.text()
        if v.size:
            from puzzle.operators.evaluate import Number

            low, high = Number(float(v.min()), got.item.unit), Number(float(v.max()), got.item.unit)
            text += f"; from {low.text()} to {high.text()}"
        return f"<p>{html.escape(text)}</p>", ""
    return f"<p>{html.escape(got.text())}</p>", ""


STYLE = """body{font:15px/1.45 system-ui,sans-serif;max-width:52em;margin:2em auto;padding:0 1em;
color:#1d1d1f;background:#fff}h1{font-size:1.6em;margin-bottom:.2em}.meta{color:#666}
section{border-top:1px solid #ddd;padding:.6em 0}h2{font-size:1.1em}table{border-collapse:
collapse;font-size:.9em}td,th{border:1px solid #ccc;padding:.2em .5em;text-align:right}
th{background:#f3f3f3}.pass{color:#1a7f37;font-weight:600}.fail{color:#c62828;
font-weight:600}.more{color:#666;font-size:.85em}img{max-width:100%}
.summary{margin:.6em 0;font-weight:600}"""


def write(ctx, op, root: Path) -> list[Path]:
    """The report as one HTML file in the project; its path."""
    from puzzle.operators.evaluate import _input

    rel = Path(path_of(op))
    if rel.is_absolute() or ".." in rel.parts:
        raise PuzzleError(f"'{op.name}': the file must be inside the project folder",
                          code="invalid")  # fmt: skip
    if rel.suffix.lower() not in (".html", ".htm"):
        rel = rel.with_suffix(".html")
    sections, verdicts = [], []
    for key in KEYS:
        if key not in op.inputs:
            continue
        name = _source_name(ctx, op, key)
        try:
            got = _input(ctx, op, key)
            body, verdict = _item(got) if got is not None else ("<p>Nothing wired</p>", "")
        except PuzzleError as exc:
            body, verdict = f"<p class=fail>{html.escape(exc.message)}</p>", "fail"
        verdicts.append(verdict)
        sections.append(f"<section><h2>{html.escape(name)}</h2>{body}</section>")
    if not sections:
        raise PuzzleError(f"'{op.name}': wire what it reports into it", code="missing_input")
    checks = [v for v in verdicts if v]
    summary = ""
    if checks:
        failed = checks.count("fail")
        summary = (f"<p class='summary {'fail' if failed else 'pass'}'>"
                   f"{len(checks) - failed} of {len(checks)} checks pass</p>")  # fmt: skip
    when = datetime.now().strftime("%Y-%m-%d %H:%M")
    page = (f"<!doctype html><html lang=en><meta charset=utf-8><title>"
            f"{html.escape(op.name)}</title><style>{STYLE}</style><body>"
            f"<h1>{html.escape(op.name)}</h1><p class=meta>{html.escape(ctx.project.name)}"
            f" · written by Puzzle on {when}</p>{summary}"
            f"{''.join(sections)}</body></html>")  # fmt: skip
    path = Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")
    return [path]
