"""The schematic as an SVG picture (ADR 0058 item 7), for reports: the same nodes,
ports and wires the GUI draws, on a light ground, with no Qt."""

from __future__ import annotations

from html import escape

from puzzle.project.status import WORDS
from puzzle.schematic import FLOW_COLOURS, KIND_WORDS, STATE_COLOURS, Schematic
from puzzle.schematic.layout import TITLE, Layout, rows

MARGIN = 30.0
HEAD = 28.0  # the column headings' band
TEXT, MUTED, BORDER, FILL, HEADER = "#1f2328", "#6a737d", "#c9ced6", "#ffffff", "#eef1f5"
USE = "#a0a7b4"


def wire_path(a: tuple[float, float], b: tuple[float, float]) -> str:
    """A cubic from an output (right side) to an input (left side), as node editors draw."""
    (x0, y0), (x1, y1) = a, b
    bend = max(40.0, abs(x1 - x0) / 2)
    return (
        f"M{x0:.1f},{y0:.1f} C{x0 + bend:.1f},{y0:.1f} {x1 - bend:.1f},{y1:.1f} {x1:.1f},{y1:.1f}"
    )


def to_svg(s: Schematic, lay: Layout, uses: str = "all", title: str = "") -> str:
    """The picture. `uses` is the `schematic.uses` setting: all, or none (a picture has
    no selection, so "for the selected node" draws none)."""
    x0, y0, x1, y1 = lay.bounds()
    dx, dy = MARGIN - x0, MARGIN + HEAD - y0
    w, h = x1 - x0 + 2 * MARGIN, y1 - y0 + 2 * MARGIN + HEAD
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.0f}" height="{h:.0f}" '
        f'viewBox="0 0 {w:.0f} {h:.0f}" font-family="Helvetica, Arial, sans-serif" '
        f'font-size="11">',  # fmt: skip
        f'<rect width="100%" height="100%" fill="{FILL}"/>',
    ]
    if title:
        out.append(f"<title>{escape(title)}</title>")
    for name, x in lay.columns:
        out.append(f'<text x="{x + dx:.1f}" y="{MARGIN + 12:.1f}" fill="{MUTED}" '
                   f'font-size="12" font-weight="bold">{escape(name)}</text>')  # fmt: skip
    for group in s.groups:
        held = [m for m in group.members if m in lay.positions]
        if group.folded or not held:
            continue
        gx0 = min(lay.positions[m][0] for m in held) - 10 + dx
        gy0 = min(lay.positions[m][1] for m in held) - 22 + dy
        gx1 = max(lay.positions[m][0] + lay.sizes[m][0] for m in held) + 10 + dx
        gy1 = max(lay.positions[m][1] + lay.sizes[m][1] for m in held) + 10 + dy
        out.append(f'<rect x="{gx0:.1f}" y="{gy0:.1f}" width="{gx1 - gx0:.1f}" '
                   f'height="{gy1 - gy0:.1f}" rx="8" fill="none" stroke="{BORDER}" '
                   f'stroke-dasharray="5 4"/><text x="{gx0 + 8:.1f}" y="{gy0 + 14:.1f}" '
                   f'fill="{MUTED}">{escape(group.label)}</text>')  # fmt: skip
    for wire in s.wires:
        if wire.kind == "use" and uses != "all":
            continue
        a, b = s.nodes[wire.source], s.nodes[wire.target]
        p0 = lay.anchor(a, wire.source_port, "out")
        p1 = lay.anchor(b, wire.target_port, "in")
        path = wire_path((p0[0] + dx, p0[1] + dy), (p1[0] + dx, p1[1] + dy))
        if wire.kind == "use":
            out.append(f'<path d="{path}" fill="none" stroke="{USE}" stroke-width="1"/>')
            continue
        colour = FLOW_COLOURS[wire.flow]
        out.append(f'<path d="{path}" fill="none" stroke="{colour}" stroke-width="2.5"/>')
        if wire.label:
            mx, my = (p0[0] + p1[0]) / 2 + dx, (p0[1] + p1[1]) / 2 + dy - 5
            out.append(f'<text x="{mx:.1f}" y="{my:.1f}" fill="{colour}" text-anchor="middle" '
                       f'font-size="10" stroke="{FILL}" stroke-width="3" '
                       f'paint-order="stroke">{escape(wire.label)}</text>')  # fmt: skip
    for node in s.nodes.values():
        out += _node(node, lay, dx, dy)
    out.append("</svg>")
    return "\n".join(out)


def _node(node, lay: Layout, dx: float, dy: float) -> list[str]:
    x, y = lay.positions[node.id]
    w, h = lay.sizes[node.id]
    x, y = x + dx, y + dy
    state = STATE_COLOURS.get(node.state, STATE_COLOURS["complete"])
    tip = f"{KIND_WORDS.get(node.kind, node.kind)}: {WORDS.get(node.state, node.state)}"
    tip += f" ({node.reason})" if node.reason else ""
    out = [f"<g><title>{escape(tip)}</title>",
           f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="6" '
           f'fill="{FILL}" stroke="{BORDER}"/>',
           f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{TITLE:.1f}" rx="6" '
           f'fill="{HEADER}"/>',
           f'<rect x="{x:.1f}" y="{y:.1f}" width="4" height="{h:.1f}" '
           f'fill="{state}"/>']  # fmt: skip
    for r in rows(node, node.id in lay.unfolded):
        base = y + r.middle + 4
        if r.kind == "title":
            out.append(f'<circle cx="{x + w - 12:.1f}" cy="{y + TITLE / 2:.1f}" r="4" '
                       f'fill="{state}"/>')  # fmt: skip
            out.append(f'<text x="{x + 10:.1f}" y="{base:.1f}" fill="{TEXT}" '
                       f'font-weight="bold">{escape(_cut(r.text, 30))}</text>')  # fmt: skip
        elif r.kind in ("subtitle", "badge", "used", "fold", "part"):
            out.append(f'<text x="{x + 10:.1f}" y="{base:.1f}" fill="{MUTED}">'
                       f"{escape(_cut(r.text, 36))}</text>")  # fmt: skip
        elif r.kind == "chip":
            out.append(f'<rect x="{x + 8:.1f}" y="{y + r.top + 2:.1f}" width="{w - 16:.1f}" '
                       f'height="{r.height - 4:.1f}" rx="7" fill="{HEADER}"/>'
                       f'<text x="{x + 14:.1f}" y="{base:.1f}" fill="{MUTED}" font-size="10">'
                       f"{escape(_cut(r.text, 36))}</text>")  # fmt: skip
        elif r.kind in ("in", "out"):
            port = node.port(r.key)
            colour = FLOW_COLOURS["other"] if port is None else _port_colour(port.carries)
            px = x + (w if r.kind == "out" else 0)
            anchor = "end" if r.kind == "out" else "start"
            tx = x + w - 10 if r.kind == "out" else x + 10
            out.append(f'<circle cx="{px:.1f}" cy="{y + r.middle:.1f}" r="4" fill="{colour}" '
                       f'stroke="{FILL}"/><text x="{tx:.1f}" y="{base:.1f}" fill="{TEXT}" '
                       f'text-anchor="{anchor}">{escape(_cut(r.text, 34))}</text>')  # fmt: skip
    out.append("</g>")
    return out


def _port_colour(carries: str) -> str:
    from puzzle.schematic import flow_of

    return FLOW_COLOURS[flow_of(carries)] if carries else USE


def _cut(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"
