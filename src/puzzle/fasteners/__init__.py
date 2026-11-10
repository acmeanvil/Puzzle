"""Standard fastener dimensions (ADR 0050): screws, nuts and washers as their standards
tabulate them, ISO 273's clearance holes and Shigley's thread table, shipped as JSON in SI
(made by `scripts/import_fasteners.py` from Lacewing Lab's tables; not edited by hand).

Each `Table` keeps its standard, reference and notes, and its columns as printed (symbol,
name, the feature it measures, the limit: nominal, max, min or ref). Lengths are metres;
threads per inch, angles, masses and text are as printed. A row's `d` is its nominal
diameter, from its designation (M10, 1 1/8, #10).

`head(style, d, metric)`, `nut(style, d, metric)` and `washer(style, d, metric)` pick the
standard's row for a bolt diameter and give the numbers a bolt check needs: the bearing
diameter under the head or nut (the washer face's dw where the table has one; else the
width across flats for a hex, the head diameter for a round head; minimums, so the
annulus is never larger than the part), the head height or nut thickness. These are
for checks and defaults; `bolts.Size.bearing` stays the bearing a joint style uses.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import cache
from importlib.resources import files


@dataclass(frozen=True)
class Column:
    symbol: str  # as the standard writes it: "s", "dw", "F"
    name: str
    role: str | None  # acrossFlats, washerFaceDiameter, headHeight, nutThickness, ...
    limit: str  # nominal, max, min, ref
    kind: str  # length (m), count, angle (°), mass (as printed), text
    unit: str | None = None

    @property
    def heading(self) -> str:
        return self.symbol if self.limit == "nominal" else f"{self.symbol} {self.limit}"


@dataclass(frozen=True)
class Row:
    size: str  # "M10", "1/2", "#10"
    d: float | None  # nominal diameter, m
    values: tuple
    note: str | None = None


@dataclass(frozen=True)
class Table:
    id: str  # "iso.hexBolt"
    title: str
    standard: str
    reference: str
    kind: str  # screw, nut, washer
    figure: str | None
    printed_in: str  # mm or inch
    nut: str | None  # the matching nut table's id
    notes: tuple[str, ...]
    columns: tuple[Column, ...]
    rows: tuple[Row, ...] = field(repr=False)

    def row(self, size: str) -> Row | None:
        return next((r for r in self.rows if r.size == size), None)

    def at(self, d: float, tolerance: float = 1e-6) -> Row | None:
        """The row of nominal diameter `d` (m), if the table has that size."""
        return next((r for r in self.rows if r.d is not None and abs(r.d - d) <= tolerance), None)

    def value(self, row: Row, role: str, limit: str | None = None) -> float | None:
        """The row's value of the first column measuring `role` (at `limit`, if given)."""
        for c, v in zip(self.columns, row.values, strict=True):
            if c.role == role and (limit is None or c.limit == limit) and v is not None:
                return v
        return None

    def dimensions(self, row: Row) -> dict[str, object]:
        """The row by column heading ("s max": 0.016), blanks left out."""
        out: dict[str, object] = {}
        for c, v in zip(self.columns, row.values, strict=True):
            if v is not None:
                out.setdefault(c.heading, v)
        return out


@cache
def _data() -> dict:
    return json.loads(files(__package__).joinpath("fasteners.json").read_text(encoding="utf-8"))


@cache
def _tables() -> dict[str, Table]:
    out = {}
    for t in _data()["tables"]:
        cols = tuple(Column(**c) for c in t["columns"])
        rows = tuple(Row(r["size"], r["d"], tuple(r["values"]), r.get("note")) for r in t["rows"])
        out[t["id"]] = Table(
            t["id"],
            t["title"],
            t["standard"],
            t["reference"],
            t["kind"],
            t["figure"],
            t["printed_in"],
            t["nut"],
            tuple(t["notes"]),
            cols,
            rows,
        )
    return out


def tables(kind: str | None = None) -> list[Table]:
    """Every table, or those of one kind (screw, nut, washer)."""
    return [t for t in _tables().values() if kind is None or t.kind == kind]


def table(table_id: str) -> Table:
    found = _tables().get(table_id)
    if found is None:
        raise KeyError(f"no fastener table '{table_id}'")
    return found


# Styles: the (metric, inch) tables of each kind of head, nut and washer.
HEADS = {
    "hex": ("iso.hexBolt", "asme.hexBolt"),
    "hex_cap": ("iso.hexBolt", "asme.hexCapScrew"),
    "heavy_hex": ("iso.heavyHexBolt", "asme.heavyHexBolt"),
    "structural": ("iso.heavyHexBolt", "asme.structuralBolt"),
    "socket": ("iso.socketHeadCapScrew", "asme.socketHeadCapScrew"),
    "button": ("iso.buttonHeadCapScrew", "asme.buttonHeadCapScrew"),
    "flat": ("iso.flatHeadCapScrew", "asme.flatHeadCapScrew"),
    "low_head": ("din.lowHeadCapScrew", "asme.lowHeadCapScrew"),
}
NUTS = {
    "hex": ("iso.hexNut", "asme.hexNut"),
    "heavy_hex": ("iso.heavyHexNut", "asme.heavyHexNut"),
    "thin": ("iso.thinNut", "asme.hexJamNut"),
}
WASHERS = {
    "normal": ("iso.washerNormal", "asme.washerTypeAN"),
    "small": ("iso.washerSmall", "asme.washerTypeBN"),
    "large": ("iso.washerLarge", "asme.washerTypeAW"),
    "extra_large": ("iso.washerExtraLarge", "asme.washerTypeBW"),
    "structural": (None, "astm.f436Washer"),  # hardened, for structural bolts
}


@dataclass(frozen=True)
class Part:
    """A head, nut or washer of one size: what a bolt check uses, in metres."""

    style: str
    table: str
    size: str
    bearing: float | None  # outer diameter of the face it bears on (min)
    height: float | None  # head height, nut or washer thickness (max where tabulated)
    across_flats: float | None = None  # min
    inner: float | None = None  # a washer's hole (min)
    dimensions: dict = field(default_factory=dict, repr=False)  # everything tabulated


def _pick(styles: dict, style: str, d: float, metric: bool) -> tuple[Table, Row] | None:
    if style not in styles:
        raise KeyError(f"no style '{style}': {', '.join(styles)}")
    table_id = styles[style][0 if metric else 1]
    if table_id is None:
        return None
    t = table(table_id)
    r = t.at(d)
    return (t, r) if r is not None else None


def _first(t: Table, r: Row, *wanted: tuple[str, str | None]) -> float | None:
    for role, limit in wanted:
        v = t.value(r, role, limit)
        if v is not None:
            return v
    return None


def head(style: str, d: float, metric: bool = True) -> Part | None:
    """The head of a `style` screw of nominal diameter `d` (m), or None off the table."""
    hit = _pick(HEADS, style, d, metric)
    if hit is None:
        return None
    t, r = hit
    flats = _first(t, r, ("acrossFlats", "min"), ("acrossFlats", None))
    bearing = _first(
        t,
        r,
        ("washerFaceDiameter", "min"),
        ("bearingFaceDiameter", "min"),
        ("acrossFlats", "min"),
        ("headDiameter", "min"),
        ("headDiameter", None),
    )
    height = _first(t, r, ("headHeight", "max"), ("headHeight", None))
    return Part(style, t.id, r.size, bearing, height, flats, None, t.dimensions(r))


def nut(style: str, d: float, metric: bool = True) -> Part | None:
    """The nut of a `style` for a bolt of nominal diameter `d` (m), or None."""
    hit = _pick(NUTS, style, d, metric)
    if hit is None:
        return None
    t, r = hit
    flats = _first(t, r, ("acrossFlats", "min"), ("acrossFlats", None))
    bearing = _first(
        t, r, ("bearingFaceDiameter", "min"), ("acrossFlats", "min"), ("acrossFlats", None)
    )
    height = _first(t, r, ("nutThickness", "max"), ("nutThickness", None))
    return Part(style, t.id, r.size, bearing, height, flats, None, t.dimensions(r))


def washer(style: str, d: float, metric: bool = True) -> Part | None:
    """The plain washer of a `style` for a bolt of nominal diameter `d` (m), or None."""
    hit = _pick(WASHERS, style, d, metric)
    if hit is None:
        return None
    t, r = hit
    outer = _first(t, r, ("outerDiameter", "min"), ("outerDiameter", None))
    inner = _first(t, r, ("innerDiameter", "min"), ("innerDiameter", None))
    height = _first(t, r, ("thickness", "max"), ("thickness", None))
    return Part(style, t.id, r.size, outer, height, None, inner, t.dimensions(r))


# ISO 273 clearance holes -----------------------------------------------------------------


def clearance_holes() -> list[tuple[float, float]]:
    """ISO 273's medium series: (bolt d, hole dh) in m."""
    return [tuple(p) for p in _data()["clearance_holes"]["medium"]]


def clearance_hole(d: float) -> float:
    """ISO 273's medium clearance hole (m) for a bolt of diameter `d` (m): the table's hole
    for a listed size; between sizes (inch bolts) the clearance dh − d interpolated
    linearly in d; past either end the end's clearance kept in proportion to d."""
    holes = clearance_holes()
    (d0, h0), (dn, hn) = holes[0], holes[-1]
    if d <= d0:
        return d * h0 / d0
    if d >= dn:
        return d * hn / dn
    for (a, ha), (b, hb) in zip(holes, holes[1:], strict=False):
        if d <= b + 1e-10:
            t = (d - a) / (b - a)
            return d + (ha - a) + t * ((hb - b) - (ha - a))
    return d * hn / dn  # not reached


# Shigley's thread table -----------------------------------------------------------------


@dataclass(frozen=True)
class ThreadSize:
    series: str  # iso_coarse, iso_fine, unc, unf
    label: str  # "M10x1.5", "1/2-13 UNC"
    d: float  # m
    pitch: float  # m
    stress_area: float  # m², as tabulated
    minor_area: float  # m², as tabulated


def thread_sizes(series: str | None = None) -> list[ThreadSize]:
    """Shigley's Tables 8-1 and 8-2 (from ANSI B1.1 and ISO): sizes with their tabulated
    tensile-stress and minor-diameter areas."""
    return [
        ThreadSize(**s)
        for s in _data()["threads"]["sizes"]
        if series is None or s["series"] == series
    ]


def references() -> dict[str, str]:
    """Where the clearance holes and thread table come from."""
    return {
        "clearance_holes": _data()["clearance_holes"]["reference"],
        "threads": _data()["threads"]["reference"],
    }


# For agents and the CLI ----------------------------------------------------------------


def _metric(standard: str) -> bool:
    return standard.startswith("iso")


def size_info(size, head_style: str = "hex", nut_style: str = "hex") -> dict:
    """One `bolts.Size` with what the tables add: the ISO 273 medium hole and the head's
    and nut's bearing diameter and height of the chosen styles (m, m²)."""
    metric = _metric(size.standard)
    out = {
        "name": size.name,
        "standard": size.standard,
        "diameter_m": size.diameter,
        "pitch_m": size.pitch,
        "stress_area_m2": size.stress_area,
        "bearing_m": size.bearing,
        "clearance_hole_m": clearance_hole(size.diameter),
    }
    for key, part in (
        ("head", head(head_style, size.diameter, metric)),
        ("nut", nut(nut_style, size.diameter, metric)),
    ):
        if part is not None:
            out[key] = {
                "table": part.table,
                "bearing_m": part.bearing,
                "height_m": part.height,
                "across_flats_m": part.across_flats,
            }
    return out


def table_info(table_id: str | None = None, size: str | None = None) -> dict:
    """The tables (id, title, standard, kind, sizes); with `table_id` its columns, notes and
    rows (one `size`, or all), lengths in m."""
    if table_id is None:
        return {
            "tables": [
                {
                    "id": t.id,
                    "title": t.title,
                    "standard": t.standard,
                    "kind": t.kind,
                    "sizes": [r.size for r in t.rows],
                }
                for t in tables()
            ],
            "heads": list(HEADS),
            "nuts": list(NUTS),
            "washers": list(WASHERS),
        }
    t = table(table_id)
    rows = [r for r in t.rows if size is None or r.size == size]
    if size is not None and not rows:
        raise KeyError(f"'{table_id}' has no size '{size}': {', '.join(r.size for r in t.rows)}")
    return {
        "id": t.id,
        "title": t.title,
        "standard": t.standard,
        "reference": t.reference,
        "kind": t.kind,
        "nut": t.nut,
        "notes": list(t.notes),
        "columns": [{"heading": c.heading, "name": c.name, "kind": c.kind} for c in t.columns],
        "rows": [
            {"size": r.size, "d_m": r.d, "values": t.dimensions(r), "note": r.note} for r in rows
        ],
    }
