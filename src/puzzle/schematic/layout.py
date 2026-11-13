"""Where the schematic's nodes go (ADR 0058 item 6): automatic, stable, and around the
nodes a user has dragged.

Layered by column (inputs, resources, models, studies, results), a column split into
sub-columns where its nodes feed each other (a study fed by another sits to its right),
then ordered within each layer to shorten wires (barycentric ordering, a few sweeps down
and up), then placed vertically near their neighbours without overlapping. Pure Python,
deterministic: the same schematic always lays out the same way. Where a node sits never
means anything.

The node's rows (title, badge, chips, ports, a component's parts) are measured here, so
the GUI and the SVG export draw ports where the wires end.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from puzzle.schematic import COLUMNS, Node, Schematic

WIDTH = 220.0
TITLE = 24.0
LINE = 18.0
PAD = 6.0
SKETCH = 44.0  # a curve's small plot
GAP_X = 160.0  # room for a data wire's label
GAP_Y = 22.0
KIND_ORDER = ["file", "geometry", "field", "material", "mesh", "parameters", "component",
              "group", "study", "runs", "result", "operator"]  # fmt: skip


@dataclass(frozen=True)
class Row:
    kind: str  # title, subtitle, badge, value, chip, in, out, part, used
    key: str
    text: str
    top: float
    height: float

    @property
    def middle(self) -> float:
        return self.top + self.height / 2


def rows(node: Node, unfolded: bool = False) -> list[Row]:
    """The node's rows from its top, in drawing order."""
    out: list[Row] = []
    y = 0.0

    def put(kind: str, key: str, text: str, height: float = LINE) -> None:
        nonlocal y
        out.append(Row(kind, key, text, y, height))
        y += height

    put("title", "", node.label, TITLE)
    if node.subtitle:
        put("subtitle", "", node.subtitle, LINE - 2)
    if node.badge:
        put("badge", "", node.badge)
    if node.value:  # an operator's value (ADR 0062)
        put("value", node.verdict, node.value)
    if node.slider is not None:  # its knob, dragged along the row
        put("slider", "", "")
    if node.sketch:  # a curve's small plot (ADR 0071)
        put("sketch", "", "", SKETCH)
    for chip in node.chips:
        put("chip", chip, chip)
    for p in node.outputs():  # outputs first, as Blender lists them
        put("out", p.key, p.label)
    for p in node.inputs():
        put("in", p.key, p.label)
    if node.rows:
        put("fold", "", ("▾ " if unfolded else "▸ ") + f"{len(node.rows)} part"
            + ("s" if len(node.rows) != 1 else ""))  # fmt: skip
        if unfolded:
            for i, text in enumerate(node.rows):
                put("part", str(i), text)
    if node.used_by > 1:
        put("used", "", f"used by {node.used_by}")
    out.append(Row("end", "", "", y, PAD))
    return out


def height(node: Node, unfolded: bool = False) -> float:
    last = rows(node, unfolded)[-1]
    return last.top + last.height


@dataclass
class Layout:
    positions: dict[str, tuple[float, float]] = field(default_factory=dict)  # top left
    sizes: dict[str, tuple[float, float]] = field(default_factory=dict)
    columns: list[tuple[str, float]] = field(default_factory=list)  # heading, its x
    unfolded: frozenset[str] = frozenset()

    def anchor(self, node: Node, port: str, side: str) -> tuple[float, float]:
        """Where a wire ends on a node: at its port's row, or (port "") at the title."""
        x, y = self.positions[node.id]
        w, _ = self.sizes[node.id]
        middle = TITLE / 2
        if port:
            row = next((r for r in rows(node, node.id in self.unfolded)
                        if r.key == port and r.kind in ("in", "out")), None)  # fmt: skip
            if row is not None:
                middle = row.middle
        return (x + (w if side == "out" else 0.0), y + middle)

    def bounds(self) -> tuple[float, float, float, float]:
        if not self.positions:
            return (0.0, 0.0, 0.0, 0.0)
        xs = [x for x, _ in self.positions.values()]
        ys = [y for _, y in self.positions.values()]
        x1 = max(x + self.sizes[k][0] for k, (x, _) in self.positions.items())
        y1 = max(y + self.sizes[k][1] for k, (_, y) in self.positions.items())
        return (min(xs), min(ys), x1, y1)


def _layers(s: Schematic) -> dict[str, int]:
    """Each node's layer: its column's first layer plus how deep it sits among the
    nodes of its own column that feed it."""
    depth: dict[str, int] = {}
    feeds: dict[str, list[str]] = {k: [] for k in s.nodes}
    for w in s.wires:
        a, b = s.nodes.get(w.source), s.nodes.get(w.target)
        if a is not None and b is not None and a.column == b.column:
            feeds[b.id].append(a.id)

    def deep(k: str, seen: frozenset = frozenset()) -> int:
        if k not in depth:
            if k in seen:  # a cycle (refused by the model): stop here
                return 0
            depth[k] = max((deep(f, seen | {k}) + 1 for f in feeds[k]), default=0)
        return depth[k]

    for k in s.nodes:
        deep(k)
    spans = [max((depth[k] for k, n in s.nodes.items() if n.column == c), default=-1) + 1
             for c in range(len(COLUMNS))]  # fmt: skip
    starts, at = [], 0
    for span in spans:
        starts.append(at)
        at += max(span, 1)
    return {k: starts[n.column] + depth[k] for k, n in s.nodes.items()}


def layout(
    s: Schematic,
    pinned: dict[str, tuple[float, float]] | None = None,
    unfolded: frozenset[str] = frozenset(),
    sweeps: int = 4,
) -> Layout:
    """Positions for every node; `pinned` ones (dragged by the user) stay where they are
    and the rest are placed around them."""
    pinned = {k: v for k, v in (pinned or {}).items() if k in s.nodes}
    layer = _layers(s)
    count = max(layer.values(), default=-1) + 1
    by_layer: list[list[str]] = [[] for _ in range(count)]
    for k in sorted(s.nodes, key=lambda k: (_rank(s.nodes[k]), s.nodes[k].label.lower(), k)):
        by_layer[layer[k]].append(k)
    near: dict[str, set[str]] = {k: set() for k in s.nodes}
    for w in s.wires:
        if w.source in near and w.target in near and w.source != w.target:
            near[w.source].add(w.target)
            near[w.target].add(w.source)

    def rank_of() -> dict[str, float]:
        out = {}
        for nodes in by_layer:
            for i, k in enumerate(nodes):
                out[k] = (i + 0.5) / max(len(nodes), 1)
        return out

    for sweep in range(sweeps):  # barycentric ordering, down then up
        order = range(1, count) if sweep % 2 == 0 else range(count - 2, -1, -1)
        for li in order:
            ranks = rank_of()
            down = sweep % 2 == 0
            keyed = []
            for i, k in enumerate(by_layer[li]):
                around = [ranks[o] for o in near[k] if (layer[o] < li if down else layer[o] > li)]
                keyed.append((sum(around) / len(around) if around else ranks[k], i, k))
            by_layer[li] = [k for _, _, k in sorted(keyed)]

    sizes = {k: (WIDTH, height(n, k in unfolded)) for k, n in s.nodes.items()}
    x_of = [li * (WIDTH + GAP_X) for li in range(count)]
    y: dict[str, float] = {}
    for nodes in by_layer:  # stacked first
        at = 0.0
        for k in nodes:
            y[k] = at
            at += sizes[k][1] + GAP_Y
    for _ in range(3):  # then pulled toward their neighbours, keeping order and gaps
        for nodes in by_layer:
            want = []
            for k in nodes:
                around = [y[o] + sizes[o][1] / 2 for o in near[k]]
                want.append(sum(around) / len(around) - sizes[k][1] / 2 if around else y[k])
            placed = _spread(want, [sizes[k][1] for k in nodes])
            for k, v in zip(nodes, placed, strict=True):
                y[k] = v
    top = min(y.values(), default=0.0)
    positions = {k: (x_of[layer[k]], y[k] - top) for k in s.nodes}
    if pinned:
        positions = _around(by_layer, positions, sizes, pinned)
    starts = {}
    for k, n in s.nodes.items():
        starts[n.column] = min(starts.get(n.column, positions[k][0]), positions[k][0])
    columns = [(COLUMNS[c], x) for c, x in sorted(starts.items())]
    return Layout(positions, sizes, columns, unfolded)


def _rank(n: Node) -> int:
    return KIND_ORDER.index(n.kind) if n.kind in KIND_ORDER else len(KIND_ORDER)


def _spread(want: list[float], heights: list[float]) -> list[float]:
    """Positions as near `want` as the order and the gaps allow: pushed down where they
    would overlap, then the whole run pulled back up by half the push."""
    out: list[float] = []
    for i, w in enumerate(want):
        out.append(w if i == 0 else max(w, out[-1] + heights[i - 1] + GAP_Y))
    if out:
        shift = sum(o - w for o, w in zip(out, want, strict=True)) / len(out)
        out = [o - shift for o in out]
    return out


def _around(by_layer, positions, sizes, pinned):
    """Pinned nodes where the user put them; each other node pushed down past any pinned
    node it would overlap, and past the node above it in its layer."""
    out = dict(positions)
    out.update(pinned)
    boxes = [(x, y, x + sizes[k][0], y + sizes[k][1]) for k, (x, y) in pinned.items()]
    for nodes in by_layer:
        floor = None
        for k in nodes:
            if k in pinned:
                continue
            x, y = out[k]
            w, h = sizes[k]
            if floor is not None:
                y = max(y, floor)
            moved = True
            while moved:
                moved = False
                for bx0, by0, bx1, by1 in boxes:
                    if x < bx1 and x + w > bx0 and y < by1 + GAP_Y and y + h + GAP_Y > by0:
                        y = by1 + GAP_Y
                        moved = True
            out[k] = (x, y)
            floor = y + h + GAP_Y
    return out
