"""The schematic as an SVG picture (ADR 0058 item 7), for reports: the same nodes,
ports and wires the GUI draws, on a light ground, with no Qt."""

from __future__ import annotations

from html import escape

from puzzle.gui.theme import LIGHT
from puzzle.operators import TYPE_COLOURS
from puzzle.project.status import WORDS
from puzzle.schematic import (
    KIND_WORDS,
    STATE_COLOURS,
    VERDICT_COLOURS,
    Schematic,
    port_colour,
    socket_shape,
    wire_colour,
)
from puzzle.schematic.layout import TITLE, Layout, rows

MARGIN = 30.0
HEAD = 28.0  # the column headings' band
# The theme's light tokens (ADR 0045; theme.py imports no Qt).
TEXT, MUTED, BORDER, FILL = LIGHT.text, LIGHT.muted, LIGHT.border, LIGHT.sunken
HEADER, USE = LIGHT.schematic_header, LIGHT.schematic_use


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
        if wire.kind == "drives":
            out.append(f'<path d="{path}" fill="none" stroke="{MUTED}" stroke-width="1.5" '
                       'stroke-dasharray="5 4"/>')  # fmt: skip
            continue
        colour = wire_colour(wire)
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
        elif r.kind == "value":  # an operator's value; a check's verdict in its colour
            colour = VERDICT_COLOURS.get(r.key, TEXT)
            out.append(f'<text x="{x + 10:.1f}" y="{base:.1f}" fill="{colour}" '
                       f'font-weight="bold">{escape(_cut(r.text, 34))}</text>')  # fmt: skip
        elif r.kind == "slider" and node.slider is not None:
            low, high, value = node.slider
            f = min(max((value - low) / (high - low), 0.0), 1.0) if high > low else 0.0
            left, right = x + 14, x + w - 14
            at = left + (right - left) * f
            out.append(f'<line x1="{left:.1f}" y1="{y + r.middle:.1f}" x2="{right:.1f}" '
                       f'y2="{y + r.middle:.1f}" stroke="{BORDER}" stroke-width="3"/>'
                       f'<circle cx="{at:.1f}" cy="{y + r.middle:.1f}" r="5" '
                       f'fill="{TYPE_COLOURS["number"]}"/>')  # fmt: skip
        elif r.kind == "sketch":  # a curve's small plot (ADR 0071)
            fx, fy, fw, fh = x + 12, y + r.top + 4, w - 24, r.height - 8
            out.append(f'<rect x="{fx:.1f}" y="{fy:.1f}" width="{fw:.1f}" height="{fh:.1f}" '
                       f'fill="none" stroke="{BORDER}"/>')  # fmt: skip
            for line in node.sketch:
                pts = " ".join(f"{fx + a * fw:.1f},{fy + fh - b * fh:.1f}" for a, b in line)
                out.append(
                    f'<polyline points="{pts}" fill="none" '
                    f'stroke="{TYPE_COLOURS["series"]}" stroke-width="1.5"/>'
                )
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
            colour = port_colour(port) if port is not None and (port.type or port.carries) \
                else USE  # fmt: skip
            px = x + (w if r.kind == "out" else 0)
            anchor = "end" if r.kind == "out" else "start"
            tx = x + w - 10 if r.kind == "out" else x + 10
            out.append(_socket(socket_shape(port), px, y + r.middle, colour)
                       + f'<text x="{tx:.1f}" y="{base:.1f}" fill="{TEXT}" '
                       f'text-anchor="{anchor}">{escape(_cut(r.text, 34))}</text>')  # fmt: skip
    out.append("</g>")
    return out


def _socket(shape: str, cx: float, cy: float, colour: str) -> str:
    style = f'fill="{colour}" stroke="{FILL}"'
    if shape == "diamond":
        pts = (
            f"{cx:.1f},{cy - 5:.1f} {cx + 5:.1f},{cy:.1f} {cx:.1f},{cy + 5:.1f} "
            f"{cx - 5:.1f},{cy:.1f}"
        )
        return f'<polygon points="{pts}" {style}/>'
    if shape == "square":
        return f'<rect x="{cx - 4:.1f}" y="{cy - 4:.1f}" width="8" height="8" {style}/>'
    if shape == "triangle":
        pts = f"{cx - 4:.1f},{cy - 5:.1f} {cx + 5:.1f},{cy:.1f} {cx - 4:.1f},{cy + 5:.1f}"
        return f'<polygon points="{pts}" {style}/>'
    return f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="4" {style}/>'


def _cut(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"
