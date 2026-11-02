"""Draw Puzzle's app icon and start-page picture: jigsaw pieces meshed by Gmsh and
coloured like results, so the mark says both "puzzle" and "finite elements".

Writes into src/puzzle/gui/art/: icon.svg, icon.png (1024 px), Puzzle.icns (macOS,
when iconutil is there) and start.svg. Run with the dev env's Python:

    pixi run -e dev python scripts/make_art.py
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import gmsh
import numpy as np

ART = Path(__file__).resolve().parents[1] / "src" / "puzzle" / "gui" / "art"

# One knob as cubic Bézier segments in edge coordinates: t along the edge (0..1),
# h out of the piece (a blank is the same knob with h flipped).
KNOB = [
    [(0.36, 0.0), (0.42, 0.0), (0.44, 0.05), (0.40, 0.10)],
    [(0.40, 0.10), (0.34, 0.20), (0.40, 0.30), (0.50, 0.30)],
    [(0.50, 0.30), (0.60, 0.30), (0.66, 0.20), (0.60, 0.10)],
    [(0.60, 0.10), (0.56, 0.05), (0.58, 0.0), (0.64, 0.0)],
]


def _bezier(p, n=14):
    s = np.linspace(0, 1, n)[:, None]
    p = np.asarray(p, float)
    return (
        (1 - s) ** 3 * p[0] + 3 * (1 - s) ** 2 * s * p[1] + 3 * (1 - s) * s**2 * p[2] + s**3 * p[3]
    )


def outline(x, y, size, edges):
    """A piece's outline as points, counter-clockwise in y-up coordinates.

    `edges` gives bottom, right, top, left: +1 a knob out, -1 a blank in, 0 flat."""
    corners = np.array([(x, y), (x + size, y), (x + size, y + size), (x, y + size)], float)
    points = []
    for k, sign in enumerate(edges):
        a, b = corners[k], corners[(k + 1) % 4]
        along = (b - a) / size
        out = np.array([along[1], -along[0]])  # outward for a counter-clockwise loop
        points.append(a)
        if sign:
            for seg in KNOB:
                local = _bezier(seg)
                points += [a + size * (t * along + sign * h * out) for t, h in local[1:]]
    pts = np.array(points)
    keep = np.r_[True, np.linalg.norm(np.diff(pts, axis=0), axis=1) > 1e-9]
    return pts[keep]


def mesh(points, h):
    """Gmsh's triangles inside a closed outline: (nodes, triangles)."""
    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    gmsh.model.add("piece")
    tags = [gmsh.model.geo.addPoint(px, py, 0, h) for px, py in points]
    lines = [gmsh.model.geo.addLine(tags[i], tags[(i + 1) % len(tags)]) for i in range(len(tags))]
    loop = gmsh.model.geo.addCurveLoop(lines)
    gmsh.model.geo.addPlaneSurface([loop])
    gmsh.model.geo.synchronize()
    gmsh.option.setNumber("Mesh.Algorithm", 6)
    gmsh.model.mesh.generate(2)
    ids, xyz, _ = gmsh.model.mesh.getNodes()
    _, _, conn = gmsh.model.mesh.getElements(2)
    gmsh.finalize()
    order = np.argsort(ids)
    nodes = xyz.reshape(-1, 3)[order, :2]
    tri = np.searchsorted(ids[order], conn[0]).reshape(-1, 3)
    return nodes, tri


# Colour maps as stops (value, hex): results-like rainbow, heat, flow, field.
MAPS = {
    "structural": ["#30123b", "#4662d7", "#36aaf9", "#1ae4b6", "#72fe5e", "#c8ef34",
                   "#faba39", "#f66b19", "#ca2a04"],
    "thermal": ["#1b0c41", "#4a0c6b", "#781c6d", "#a52c60", "#cf4446", "#ed6925",
                "#fb9b06", "#f7d13d", "#fcffa4"],
    "flow": ["#0d0887", "#2c7bb6", "#00a6ca", "#00ccbc", "#90eb9d", "#f9f871"],
    "field": ["#2d004b", "#542788", "#8073ac", "#b2abd2", "#d8daeb", "#fee0b6",
              "#fdb863", "#e08214"],
}  # fmt: skip


def colour(name, v):
    stops = MAPS[name]
    v = float(np.clip(v, 0, 1)) * (len(stops) - 1)
    i = min(int(v), len(stops) - 2)
    f = v - i
    a = np.array([int(stops[i][k : k + 2], 16) for k in (1, 3, 5)])
    b = np.array([int(stops[i + 1][k : k + 2], 16) for k in (1, 3, 5)])
    r, g, bl = (a + f * (b - a)).round().astype(int)
    return f"#{r:02x}{g:02x}{bl:02x}"


def piece_svg(points, h, cmap, field, flip, line="#ffffff", line_opacity=0.32, width=1.2):
    """SVG for one meshed piece: flat-shaded triangles, mesh lines, an outline.

    `flip(x, y)` maps y-up drawing coordinates to the SVG's."""
    nodes, tri = mesh(points, h)
    centres = nodes[tri].mean(axis=1)
    values = field(centres[:, 0], centres[:, 1])
    values = (values - values.min()) / (np.ptp(values) or 1.0)
    out = []
    for t, v in zip(tri, values, strict=True):
        p = " ".join(f"{a:.1f},{b:.1f}" for a, b in (flip(*nodes[k]) for k in t))
        c = colour(cmap, v)
        # Each triangle stroked in its own colour first, so no seams show between them.
        out.append(f'<polygon points="{p}" fill="{c}" stroke="{c}" stroke-width="0.8"/>')
    edges = set()
    for t in tri:
        for a, b in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
            edges.add((min(a, b), max(a, b)))
    path = " ".join(
        "M{:.1f},{:.1f}L{:.1f},{:.1f}".format(*flip(*nodes[a]), *flip(*nodes[b])) for a, b in edges
    )
    out.append(
        f'<path d="{path}" fill="none" stroke="{line}" stroke-opacity="{line_opacity}" '
        f'stroke-width="{width}" stroke-linecap="round"/>'
    )
    ring = " ".join(f"{a:.1f},{b:.1f}" for a, b in (flip(*q) for q in points))
    out.append(
        f'<polygon points="{ring}" fill="none" stroke="#0b1020" stroke-opacity="0.55" '
        f'stroke-width="{2.2 * width:.1f}" stroke-linejoin="round"/>'
    )
    return "\n".join(out), ring


def icon() -> str:
    """The app icon: one piece on the macOS icon grid (824 px squircle in 1024)."""
    size = 470.0
    # Knobs out on the right and top, blanks on the bottom and left: centre the extents.
    x0 = 512 - (size * 1.3) / 2
    y0 = 512 - (size * 1.3) / 2
    pts = outline(x0, y0, size, (-1, +1, +1, -1))

    def flip(x, y):
        return x, 1024 - y

    def stress(x, y):  # a hot spot at the top knob's neck, as a bent piece would have
        u, v = (x - x0) / size, (y - y0) / size
        hot = np.exp(-(((u - 0.5) / 0.22) ** 2 + ((v - 1.0) / 0.22) ** 2))
        return 0.75 * hot + 0.35 * v + 0.15 * u

    body, ring = piece_svg(pts, 26.0, "structural", stress, flip, width=1.6)
    shadow = " ".join(
        f"{a + 10:.1f},{b + 18:.1f}" for a, b in (flip(*q) for q in pts)
    )  # fmt: skip
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024">
<defs>
  <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#26324f"/><stop offset="1" stop-color="#0d1222"/>
  </linearGradient>
</defs>
<rect x="100" y="100" width="824" height="824" rx="185" fill="url(#bg)"/>
<rect x="100" y="100" width="824" height="824" rx="185" fill="none" stroke="#ffffff" stroke-opacity="0.08" stroke-width="3"/>
<polygon points="{shadow}" fill="#000000" fill-opacity="0.35"/>
{body}
</svg>
"""  # noqa: E501


def start() -> str:
    """The start page's picture: four interlocking pieces, one per kind of physics,
    the last lifted out as if being put in. Transparent, for light and dark themes."""
    size, gap = 230.0, 0.0
    w, h = 620, 620
    # Cells (column, row from the bottom); edges bottom, right, top, left.
    pieces = [
        ((0, 0), (0, +1, -1, 0), "structural"),
        ((1, 0), (0, 0, +1, -1), "thermal"),
        ((0, 1), (+1, -1, 0, 0), "flow"),
        ((1, 1), (-1, 0, 0, +1), "field"),
    ]
    x_base = w / 2 - size - 17
    y_base = h / 2 - size - 15

    def flip(x, y):
        return x, h - y

    def structural(x, y):  # a stress concentration where it meets its neighbour
        u, v = (x - x_base - size) / 90, (y - y_base - 0.6 * size) / 90
        return np.exp(-(u**2 + v**2)) + 0.002 * y

    def thermal(x, y):  # hot at the far corner
        return -np.hypot(x - (x_base + 2 * size), y - y_base)

    def flow(x, y):  # a channel's velocity profile
        return np.sin((y - y_base - size) / size * np.pi) + 0.0005 * x

    def field(x, y):  # a wire just past the far corner, graded outward
        return -(np.hypot(x - (x_base + 2.3 * size), y - (y_base + 2.25 * size)) ** 0.8)

    fields = {"structural": structural, "thermal": thermal, "flow": flow, "field": field}
    parts = []
    lift = (34.0, 30.0)  # the last piece, lifted up and right
    for (col, row), edges, cmap in pieces:
        dx, dy = lift if cmap == "field" else (0.0, 0.0)
        x, y = x_base + col * (size + gap) + dx, y_base + row * (size + gap) + dy
        pts = outline(x, y, size, edges)
        body, ring = piece_svg(pts, 15.0, cmap, fields[cmap], flip, width=1.0)
        sh = 18 if cmap == "field" else 6
        shadow = " ".join(f"{a + sh * 0.5:.1f},{b + sh:.1f}" for a, b in (flip(*q) for q in pts))
        parts.append(f'<polygon points="{shadow}" fill="#000000" fill-opacity="0.16"/>\n{body}')
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}">\n' + "\n".join(parts) + "\n</svg>\n"
    )


def render_png(svg: Path, png: Path, px: int) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    QGuiApplication.instance() or QGuiApplication([])
    image = QImage(px, px, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(str(svg)).render(painter)
    painter.end()
    image.save(str(png))


def icns(svg: Path, dest: Path) -> None:
    if shutil.which("iconutil") is None:
        return
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "Puzzle.iconset"
        folder.mkdir()
        for px in (16, 32, 128, 256, 512):
            render_png(svg, folder / f"icon_{px}x{px}.png", px)
            render_png(svg, folder / f"icon_{px}x{px}@2x.png", 2 * px)
        subprocess.run(["iconutil", "-c", "icns", str(folder), "-o", str(dest)], check=True)


def main() -> None:
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    ART.mkdir(parents=True, exist_ok=True)
    (ART / "icon.svg").write_text(icon())
    (ART / "start.svg").write_text(start())
    render_png(ART / "icon.svg", ART / "icon.png", 1024)
    icns(ART / "icon.svg", ART / "Puzzle.icns")


if __name__ == "__main__":
    main()
