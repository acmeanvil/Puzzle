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


# --- Geometry → New frame from a DXF… (ADR 0079 step 1, ADR 0072 section 10) -----------

UNIT_NAMES = {0.0254: "inches", 0.3048: "feet", 0.001: "millimetres", 0.01: "centimetres",
              1.0: "metres", 0.1: "decimetres"}  # fmt: skip


def dxf_layers(path: str) -> tuple[list[str], float]:
    """A DXF's layers holding lines and its scale (m per drawing unit, from $INSUNITS,
    mm when unset), as `frames.lines` reads it."""
    import ezdxf

    from puzzle.frames.lines import DXF_UNITS

    doc = ezdxf.readfile(path)
    kinds = ("LINE", "LWPOLYLINE", "POLYLINE")
    layers = sorted({e.dxf.layer for e in doc.modelspace() if e.dxftype() in kinds})
    return layers, DXF_UNITS.get(doc.header.get("$INSUNITS", 4), 0.001)


def new_frame_from_dxf(window, path: str | None = None):
    """A frame from a DXF's lines (layers become member groups): one Create of the
    geometry `frames.geometry` makes, as MCP `frame_geometry` with `dxf` returns. A file
    outside the project is copied into it first, as Import geometry… does."""
    import shutil
    from pathlib import Path

    from PySide6.QtWidgets import QLabel

    from puzzle import frames
    from puzzle.commands.model import Create
    from puzzle.frames.lines import from_args
    from puzzle.gui.agent_prefill import add_ask_button
    from puzzle.gui.live_form import LiveForm
    from puzzle.gui.theme import role

    root = Path(window.session.root)
    read: dict = {}  # path -> (layers, scale) or the reason it can't be read

    def file_of(v) -> Path | None:
        text = (v.get("file") or "").strip()
        if not text:
            return None
        p = Path(text)
        return p if p.is_absolute() else root / p

    def info(v):
        p = file_of(v)
        if p is None:
            raise ValueError("choose a DXF file")
        key = str(p)
        if key not in read:
            try:
                read[key] = dxf_layers(key) if p.exists() else f"no file {p}"
            except Exception as exc:  # noqa: BLE001 - not a DXF ezdxf reads: say why
                read[key] = f"can't read {p.name}: {exc}"
        if isinstance(read[key], str):
            raise ValueError(read[key])
        return read[key]

    def layers(v):
        try:
            found, _ = info(v)
        except ValueError:
            found = []
        return [("Every layer", "")] + [(n, n) for n in found]

    def default_scale(v) -> str:
        try:
            return f"{info(v)[1]:g}"
        except ValueError:
            return ""

    def scale_of(v) -> float | None:
        """The scale typed, or None when it is the file's own (as the tool's default)."""
        text = (v.get("scale") or "").strip()
        if not text or text == default_scale(v):
            return None
        value = float(text)
        if value <= 0:
            raise ValueError("the scale is positive: metres per drawing unit")
        return value

    def lines_of(v):
        info(v)
        args = {"dxf": str(file_of(v))}
        if v.get("layer"):
            args["layer"] = v["layer"]
        scale = scale_of(v)
        if scale:
            args["scale"] = scale
        lines = from_args(args)
        frames.lines.check(lines)
        return lines

    def check(v):
        try:
            lines_of(v)
        except ValueError as exc:  # FrameError too
            return str(exc).replace("could not convert string to float", "not a number")
        if not (v.get("name") or "").strip():
            return "give the frame a name"
        return None

    preview = role(QLabel(""), "muted")
    preview.setWordWrap(True)

    def show(v):
        try:
            lines = lines_of(v)
            _, scale = info(v)
        except ValueError:
            preview.setText("")
            return
        groups = lines.groups()
        xyz = list(lines.nodes.values())
        span = [max(c[i] for c in xyz) - min(c[i] for c in xyz) for i in range(3)]
        from puzzle.settings import length_unit

        unit, per_metre = length_unit()
        size = " × ".join(f"{s * per_metre:.4g}" for s in span) + f" {unit}"
        drawn = UNIT_NAMES.get(scale_of(v) or scale, "the scale given")
        preview.setText(
            f"{len(lines.members)} members between {len(lines.nodes)} nodes, {size} "
            f"(drawn in {drawn}). Groups: "
            + (", ".join(f"{g} ({len(m)})" for g, m in sorted(groups.items()))
               or "none (layer 0's lines have no group)") + ".")  # fmt: skip

    asks = [
        Ask("file", "DXF file", kind="file", default=path or "",
            help="LINE, LWPOLYLINE and POLYLINE entities become members; copied into the "
            "project when outside it"),
        Ask("layer", "Layer", choices=layers, help="Every layer, or the members of one"),
        Ask("scale", "Scale (m per drawing unit)", kind="text", follow=default_scale,
            help="From the drawing's units ($INSUNITS; mm when unset): 0.001 for mm"),
        Ask("name", "Name", kind="text",
            follow=lambda v: Path(v.get("file") or "Frame").stem or "Frame"),
    ]  # fmt: skip
    dialog = LiveForm(window, "New frame from a DXF", asks, check=check, changed=show,
                      below=preview, help_key="form:frame_dxf", file_filter="DXF (*.dxf)",
                      intro="Each line a member, nodes where lines meet; each layer a group "
                      "of members for their section, each node a point selection for "
                      "supports and loads.")  # fmt: skip

    def prompt(v):
        layer = f", layer {v['layer']}" if v.get("layer") else ""
        return (
            f"Make a frame named '{v.get('name')}' from the DXF {v.get('file')}{layer}, "
            f"scale {v.get('scale')} m per drawing unit (MCP frame_geometry), then give "
            "its groups sections, a material, supports and loads (skill frames)."
        )

    add_ask_button(dialog, window, prompt)
    if not window.run_dialog(dialog) or dialog.problem():
        return None
    v = dialog.values()
    source = file_of(v)
    try:
        rel = source.resolve().relative_to(root.resolve())
    except ValueError:  # outside the project: a copy inside, as imported geometry is
        target = root / source.name
        if target.exists() and target.resolve() != source.resolve():
            window.statusBar().showMessage(
                f"The project has a different {source.name} already: rename the DXF", 10000
            )
            return None
        shutil.copyfile(source, target)
        rel = Path(source.name)
    geo = frames.geometry(v["name"].strip(), dxf=rel.as_posix(), layer=v.get("layer") or None,
                          scale=scale_of(v), root=root)  # fmt: skip
    return window.submit(Create(kind="geometry", data=geo.model_dump(mode="json")))
