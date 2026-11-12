"""Geometry → New frame from a grid… (ADR 0079 step 6): one form for a frame's grid (bays
along x and y, story heights, as '6, 6, 8' or '3@6'), which members to make (columns,
beams, bracing X or chevron in the end bays or all) and its name, then one Create of
the `frame` geometry (`frames.grid.plan` → `frames.geometry`), the same as MCP
`frame_grid`. The members' groups (Columns, Beams X, Beams Y, Braces) are edge
selections for sections; the nodes are point selections for supports and loads."""

from __future__ import annotations

from puzzle.gui.quick_form import Ask

BRACING = [("None", "none"), ("X (both diagonals)", "x"), ("Chevron (inverted V)", "chevron")]
WHERE = [("The end bays of each outside frame line", "ends"), ("Every bay", "all")]


def _lengths(text: str, unit_per_metre: float) -> list[float]:
    from puzzle.frames.grid import parse_lengths

    return [v / unit_per_metre for v in parse_lengths(text)]


def _check(values: dict, per_metre: float) -> str | None:
    from puzzle.frames.grid import Grid

    try:
        grid = Grid(_lengths(values["bays_x"], per_metre), _lengths(values["bays_y"], per_metre),
                    _lengths(values["stories"], per_metre))  # fmt: skip
        grid.check()
    except ValueError as exc:
        return str(exc).replace("could not convert string to float", "not a number")
    if not values["name"].strip():
        return "give the frame a name"
    return None


def new_frame(window):
    from puzzle import frames
    from puzzle.commands.model import Create
    from puzzle.frames.grid import Grid, plan
    from puzzle.settings import length_unit

    unit, per_metre = length_unit()
    got = window.ask_form(
        "New frame from a grid",
        [
            Ask("name", "Name", kind="text", default="Frame"),
            Ask(
                "bays_x",
                f"Bays along x ({unit})",
                kind="text",
                default=f"3@{6 * per_metre:g}",
                help="widths, comma separated; 3@6 is three of 6",
            ),
            Ask(
                "bays_y",
                f"Bays along y ({unit})",
                kind="text",
                default="",
                help="blank: a plane frame in x-z",
            ),
            Ask(
                "stories", f"Story heights ({unit})", kind="text", default=f"2@{3.5 * per_metre:g}"
            ),
            Ask(
                "members",
                "Members",
                choices=[
                    ("Columns and beams", "both"),
                    ("Columns only", "columns"),
                    ("Beams only", "beams"),
                ],
            ),
            Ask("bracing", "Bracing", choices=BRACING),
            Ask(
                "braced_bays",
                "Braced bays",
                choices=WHERE,
                shown=lambda v: v.get("bracing") != "none",
            ),
        ],  # fmt: skip
        intro="Nodes at the grid's intersections, named by column line, row and level (A1-0 "
        "at the base). Members are grouped Columns, Beams X, Beams Y and Braces for their "
        "sections.",
        check=lambda v: _check(v, per_metre),
    )
    if got is None:
        return None
    grid = Grid(_lengths(got["bays_x"], per_metre), _lengths(got["bays_y"], per_metre),
                _lengths(got["stories"], per_metre), beams=got["members"] != "columns",
                columns=got["members"] != "beams", bracing=got["bracing"],
                braced_bays=got.get("braced_bays") or "ends")  # fmt: skip
    nodes, members = plan(grid)
    geo = frames.geometry(got["name"].strip(), nodes, members)
    return window.submit(Create(kind="geometry", data=geo.model_dump(mode="json")))
