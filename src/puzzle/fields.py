"""Imported fields (ADR 0044): values at points from another tool or a test, read from
a file, placed on the model and mapped onto a mesh when a study runs.

- `sniff` guesses a file's format, delimiter and columns (the wizard's first page and
  `puzzle import-field`'s defaults): "x [mm], y [mm], z [mm], p [MPa]" headers, as
  Puzzle's own point clouds (ADR 0043) and Ansys's External Data write them, give the
  roles and units outright.
- `import_file` copies the file into the project's `fields/` folder and hashes it.
- `cloud` reads an `ImportedField` into points (m, on the model) and values (SI),
  placed by the field's own placement and then a use's.
- `map_values` puts a column's values on target points: linear in the points'
  Delaunay cells (scattered 3D points, or a plane's or a line's), nearest, or inverse
  distance; past `max_distance` the nearest value, zero, or a refusal.
- `check` is the verification report: counts, ranges, coverage of the target within a
  tolerance, the distance histogram, points off the model, and plain-word warnings.
- `use_values` is what a study's run asks for: a feature's field use on its nodes, at
  a time when the field varies in time (`use_history` for every time, transient steps).

Formats beyond CSV and VTU (Fluent profiles, Exodus II, EnSight Gold, OpenFOAM case
folders) are read by `puzzle.field_readers`.

Time (ADR 0044 decision 2.1, 3): a field varies in time when it lists `times` (a file
each, or a multi-step source's steps) or has a time column (rows grouped by its value).
A study's solve step k takes it at the step's time: its `end_time` setting when it has
one, else k (1, 2, ...: CalculiX's total time when each static step lasts 1), linearly
between the field's times and held at the first or last outside them, as an Amplitude
is. A transient step takes the whole history (adapters that follow it list the feature
in `Capabilities.field_histories`; the rest refuse it).

Stage 2: `vector_values` maps a vector feature's components (`VECTORS`) as one vector,
  rotated with the points when a placement turns them; `conserve` makes a conservative
  use's total on the target equal the source's (`CONSERVED`: force-type values, the
  source's points weighted by their area or volume).
"""

from __future__ import annotations

import csv
import hashlib
import io
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from puzzle.errors import PuzzleError

FIELDS_DIR = "fields"
FORMATS = {
    "csv": "Delimited text (CSV, TXT, DAT: Puzzle's point clouds, Ansys External Data, tests)",
    "vtu": "VTK unstructured grid (.vtu, .vtk): point or cell data",
    "fluent_profile": "Ansys Fluent profile (.prof): point profiles",
    "exodus": "Exodus II (.exo, .e): nodal or element variables, every time step",
    "ensight": "EnSight Gold (.case: Code_Saturne, Puzzle's exports): per node or element",
    "openfoam": "OpenFOAM case folder: a time directory's cell or patch values",
}
READERS = ("fluent_profile", "exodus", "ensight", "openfoam")  # in puzzle.field_readers
COMMENTS = ("#", "%", "//", "!")
SPARSE = 20  # fewer points than this is test data: flagged, interpolated with care


class FieldError(PuzzleError):
    code = "invalid_input"


@dataclass
class Cloud:
    """Points (n, 3) in metres on the model and each value column's values in SI."""

    points: np.ndarray
    values: dict[str, np.ndarray] = field(default_factory=dict)
    units: dict[str, str] = field(default_factory=dict)  # the columns' SI units


# --- reading -------------------------------------------------------------------------------


def import_file(root: Path, path: Path) -> tuple[str, str]:
    """Copy a field file into the project; its path relative to the project, and hash.
    A source of several files comes with them: an EnSight case with its geometry and
    variable files (into `fields/<case>/`), an OpenFOAM case's polyMesh and time
    directories (into `fields/<case>/`, the case folder being what the field names)."""
    from puzzle.field_readers import format_of, openfoam_case, source_files

    path = Path(path)
    target_dir = Path(root) / FIELDS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    fmt = format_of(path)
    if fmt in ("ensight", "openfoam"):
        top = openfoam_case(path) if fmt == "openfoam" else path.parent
        name = top.name if fmt == "openfoam" else path.stem
        folder = target_dir / name
        if top.resolve() != folder.resolve():
            for f in source_files(path, fmt):
                dest = folder / f.resolve().relative_to(top.resolve())
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(f, dest)
        target = folder if fmt == "openfoam" else folder / path.name
        return target.relative_to(root).as_posix(), file_hash(target)
    target = target_dir / path.name
    if path.resolve() != target.resolve():
        shutil.copyfile(path, target)
    return f"{FIELDS_DIR}/{target.name}", file_hash(target)


def file_hash(path: Path) -> str:
    """SHA-256 of a field file, or of every file a several-file source is made of."""
    from puzzle.field_readers import format_of, source_files

    path = Path(path)
    fmt = format_of(path)
    if fmt not in ("ensight", "openfoam"):
        return hashlib.sha256(path.read_bytes()).hexdigest()
    digest = hashlib.sha256()
    top = path if path.is_dir() else path.parent
    for f in sorted(source_files(path, fmt)):
        digest.update(f.resolve().relative_to(top.resolve()).as_posix().encode())
        digest.update(hashlib.sha256(f.read_bytes()).digest())
    return digest.hexdigest()


def sniff(path: Path) -> dict:
    """A file's likely reading: format, delimiter, rows to skip, header, and columns
    (name, role, unit); the length unit when the coordinates name one."""
    from puzzle.field_readers import format_of

    path = Path(path)
    if path.suffix.lower() in (".vtu", ".vtk"):
        return _sniff_vtu(path)
    fmt = format_of(path)
    if fmt in READERS:
        return _sniff_reader(path, fmt)
    lines = path.read_text(errors="replace").splitlines()
    skip = 0
    while skip < len(lines) and (
        not lines[skip].strip() or lines[skip].lstrip().startswith(COMMENTS)
    ):
        skip += 1
    if skip >= len(lines):
        raise FieldError(f"{path.name} has no data rows")
    first = lines[skip]
    delimiter = _delimiter(lines[skip : skip + 20])
    cells = _split(first, delimiter)
    header = not all(_number(c) is not None for c in cells)
    names = [c.strip() for c in cells] if header else _default_names(len(cells))
    columns, length = [], None
    for i, raw in enumerate(names):
        name, unit = _name_and_unit(raw)
        role = _coordinate(name) or ("value" if i >= 3 or header else "xyz"[i])
        if header and _label(name):  # node or element numbers (Ansys, Fluent): not values
            role = "ignore"
        if header and name.strip().lower() in ("time", "time value"):
            role = "time"
        if role in ("x", "y", "z"):
            length = length or unit or None
            unit = ""
        columns.append({"name": name or f"column {i + 1}", "role": role, "unit": unit})
    return {
        "format": "csv",
        "delimiter": delimiter,
        "skip_rows": skip,
        "header": header,
        "columns": columns,
        "length_unit": length or "m",
    }


def _sniff_reader(path: Path, fmt: str) -> dict:
    """What a Fluent profile, Exodus, EnSight or OpenFOAM source offers, as `sniff`
    says it: value columns (and where each sits: "locations"), its steps ("steps":
    time and index, for a field varying in time) and its regions."""
    from puzzle.field_readers import sniff_source

    offer = sniff_source(path, fmt)
    return {
        "format": fmt,
        "delimiter": None,
        "skip_rows": 0,
        "header": True,
        "columns": [{"name": c.name, "role": "value", "unit": ""} for c in offer.columns],
        "length_unit": "m",
        "locations": {c.name: c.location for c in offer.columns},
        "steps": [{"time": s.time, "index": s.index} for s in offer.steps],
        "regions": offer.regions,
        "notes": offer.notes,
    }


def _sniff_vtu(path: Path) -> dict:
    import pyvista as pv

    grid = pv.read(path)
    columns = [{"name": n, "role": "value", "unit": ""} for n in grid.point_data]
    columns += [{"name": n, "role": "value", "unit": ""} for n in grid.cell_data
                if n not in grid.point_data]  # fmt: skip
    return {"format": "vtu", "delimiter": None, "skip_rows": 0, "header": True,
            "columns": columns, "length_unit": "m"}  # fmt: skip


def _name_and_unit(header: str) -> tuple[str, str]:
    """ "p [MPa]" or "h (W/(m^2*K))": the name, and the unit between the first opening
    bracket and the last closing one (units hold brackets of their own)."""
    text = header.strip()
    for opening, closing in (("[", "]"), ("(", ")")):
        if text.endswith(closing) and opening in text:
            at = text.index(opening)
            return text[:at].strip(), text[at + 1 : -1].strip()
    return text, ""


def _blank(line: str) -> bool:
    return not line.strip() or line.lstrip().startswith(COMMENTS)


def _delimiter(lines: list[str]) -> str | None:
    for d in (",", ";", "\t"):
        counts = {ln.count(d) for ln in lines if ln.strip()}
        if len(counts) == 1 and counts.pop() > 0:
            return d
    return None  # whitespace


def _split(line: str, delimiter: str | None) -> list[str]:
    if delimiter is None:
        return line.split()
    return next(csv.reader(io.StringIO(line), delimiter=delimiter))


def _number(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None


def _coordinate(name: str) -> str | None:
    """x, y or z from "x", "X-Coordinate" (Fluent), "X Location" (Ansys Mechanical)."""
    key = name.strip().lower()
    for word in ("-coordinate", " coordinate", " location", "-location", " coord"):
        key = key.replace(word, "")
    return key if key in ("x", "y", "z") else None


def _label(name: str) -> bool:
    """A node or element number column (Ansys's "Node Number", Fluent's "nodenumber")."""
    key = name.strip().lower().replace(" ", "").replace("_", "")
    return key in ("nodenumber", "node", "nodeid", "elementnumber", "element", "elementid",
                   "cellnumber", "id")  # fmt: skip


def _default_names(n: int) -> list[str]:
    return ["x", "y", "z", *(f"value {i}" for i in range(1, n - 2))][:n]


def read_table(root: Path, f, file: str = "", index: str | None = None
               ) -> tuple[np.ndarray, dict[str, np.ndarray]]:  # fmt: skip
    """An ImportedField's file as raw points (file units) and value columns (file units):
    `file` (default the field's own) at the source's step `index` (default the field's)."""
    path = Path(root) / (file or f.file)
    if not path.exists():
        raise FieldError(f"field '{f.name}': its file {file or f.file} is missing", path=f.name)
    if f.format == "vtu":
        return _read_vtu(path, f)
    if f.format in READERS:
        from puzzle.field_readers import read_source

        names = [c.name for c in f.columns if c.role == "value"]
        return read_source(path, f.format, names, f.region, f.index if index is None else index)
    points, values, _ = _read_csv(path, f)
    return points, values


def snapshots(root: Path, f) -> list[tuple[float | None, np.ndarray, dict[str, np.ndarray]]]:
    """The field at each of its times (s), in time order: (time, raw points, values).
    A field that holds still is one snapshot at time None."""
    if f.times:
        out = [(t.time, *read_table(root, f, t.file, t.index)) for t in f.times]
        return sorted(out, key=lambda s: s[0])
    if f.format in ("csv",) and any(c.role == "time" for c in f.columns):
        points, values, times = _read_csv(Path(root) / f.file, f)
        a, b, _ = _si(next(c.unit for c in f.columns if c.role == "time") or "s")
        times = a * times + b
        out = []
        for t in np.unique(times):
            rows = times == t
            out.append((float(t), points[rows], {k: v[rows] for k, v in values.items()}))
        return out
    return [(None, *read_table(root, f))]


def _read_csv(path: Path, f) -> tuple[np.ndarray, dict[str, np.ndarray], np.ndarray | None]:
    """A delimited file's points, value columns, and time column if it has one."""
    lines = path.read_text(errors="replace").splitlines()[f.skip_rows + (1 if f.header else 0) :]
    rows = []
    for ln in lines:
        if not ln.strip() or ln.lstrip().startswith(COMMENTS):
            continue
        cells = _split(ln, f.delimiter if f.delimiter is not None else _delimiter([ln]))
        rows.append(cells)
    if not rows:
        raise FieldError(f"field '{f.name}' has no data rows", path=f.name)
    width = len(f.columns)
    try:
        table = np.array([[float(c) for c in r[:width]] for r in rows if len(r) >= width])
    except ValueError as exc:
        raise FieldError(f"field '{f.name}': a row is not all numbers ({exc})") from None
    by_role = {c.role: i for i, c in enumerate(f.columns) if c.role in ("x", "y", "z")}
    if len(by_role) < 3:
        missing = sorted(set("xyz") - set(by_role))
        raise FieldError(f"field '{f.name}' names no {', '.join(missing)} column", path=f.name)
    points = table[:, [by_role["x"], by_role["y"], by_role["z"]]]
    values = {c.name: table[:, i] for i, c in enumerate(f.columns) if c.role == "value"}
    at = next((i for i, c in enumerate(f.columns) if c.role == "time"), None)
    return points, values, (table[:, at] if at is not None else None)


def _read_vtu(path: Path, f) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    import pyvista as pv

    grid = pv.read(path)
    values = {}
    for c in f.columns:
        if c.role != "value":
            continue
        if c.name in grid.point_data:
            values[c.name] = np.asarray(grid.point_data[c.name], dtype=float)
        elif c.name in grid.cell_data:  # cell values at the cells' centres
            values[c.name] = np.asarray(grid.cell_data[c.name], dtype=float)
    cell_only = any(c.name not in grid.point_data for c in f.columns if c.role == "value")
    points = np.asarray(grid.cell_centers().points if cell_only else grid.points, dtype=float)
    return points, values


# --- units and placement -------------------------------------------------------------------


def _si(unit: str) -> tuple[float, float, str]:
    """value_SI = a * value + b for `unit`, and the SI unit (°C and °F are offset)."""
    from puzzle.model import units

    if not unit:
        return 1.0, 0.0, ""
    if not units.known(unit):
        raise FieldError(f"unknown unit '{unit}' (e.g. mm, MPa, W/(m^2*K), degC)")
    b = units.to_si(0.0, unit)
    a = units.to_si(1.0, unit) - b
    si = str(units.registry().Quantity(1.0, unit).to_base_units().units)
    return a, b, si


def _rotation(degrees) -> np.ndarray:
    from puzzle.geometry.model import rotation_matrix

    return rotation_matrix(tuple(float(d) for d in degrees))


def place(points: np.ndarray, placement, project=None) -> np.ndarray:
    """Points moved by a placement: into a coordinate system's axes first, then rotated
    about `about` and moved by `translation`."""
    out = np.asarray(points, dtype=float)
    if placement.axes is not None:
        from puzzle.model.schema import CoordinateSystem

        if project is None:
            raise FieldError("a placement in a coordinate system needs the project")
        from puzzle.axes import absolute

        cs = absolute(project, project.get(placement.axes, CoordinateSystem))
        out = cs.centre() + out @ np.asarray(cs.orientation()).T
    if any(placement.rotation):
        about = np.asarray(placement.about, dtype=float)
        out = (out - about) @ _rotation(placement.rotation).T + about
    if any(placement.translation):
        out = out + np.asarray(placement.translation, dtype=float)
    return out


def cloud(project, root: Path, f, use=None) -> Cloud:
    """An imported field on the model: points in metres, placed by the field's own
    placement and then the use's; values in SI. A field varying in time: its first."""
    return clouds(project, root, f, use)[0][1]


def clouds(project, root: Path, f, use=None) -> list[tuple[float | None, Cloud]]:
    """The field on the model at each of its times (s; None when it holds still)."""
    return [(t, _placed(project, f, raw, values, use)) for t, raw, values in snapshots(root, f)]


def _placed(project, f, raw, values, use) -> Cloud:
    a, b, _ = _si(f.length_unit)
    points = place(a * raw + b, f.placement, project)
    if use is not None:
        points = place(points, use.placement, project)
    out = Cloud(points)
    for c in f.columns:
        if c.role == "value" and c.name in values:
            ca, cb, unit = _si(c.unit)
            out.values[c.name] = ca * values[c.name] + cb
            out.units[c.name] = unit
    return out


# --- mapping -------------------------------------------------------------------------------


def _model_size(points: np.ndarray) -> float:
    return float(np.linalg.norm(np.ptp(points, axis=0))) or 1.0


def _flat_basis(points: np.ndarray) -> tuple[np.ndarray, int]:
    """The points' own axes (principal directions) and how many they really span."""
    centred = points - points.mean(axis=0)
    _, s, vt = np.linalg.svd(centred, full_matrices=False)
    span = int(np.sum(s > 1e-9 * max(s[0], 1e-300))) if len(s) else 0
    return vt, span


def map_values(
    points: np.ndarray,
    values: np.ndarray,
    targets: np.ndarray,
    mapping: str = "linear",
    beyond: str = "nearest",
    max_distance: float | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Values at `targets`, and each target's distance to the nearest point. Linear works
    in the points' own span: Delaunay cells in 3D, triangles on a plane, segments on a
    line; a target outside them takes the nearest value."""
    from scipy.spatial import cKDTree

    tree = cKDTree(points)
    dist, nearest = tree.query(targets)
    values = np.asarray(values, dtype=float)
    if mapping == "nearest" or len(points) < 4:
        out = values[nearest].copy()
    elif mapping == "inverse_distance":
        k = min(8, len(points))
        d, idx = tree.query(targets, k=k)
        d, idx = np.atleast_2d(d), np.atleast_2d(idx)
        w = 1.0 / np.maximum(d, 1e-12 * _model_size(points)) ** 2
        out = (w * values[idx]).sum(axis=1) / w.sum(axis=1)
    else:
        out = _linear(points, values, targets, values[nearest])
    if max_distance is not None:
        far = dist > max_distance
        if far.any():
            if beyond == "refuse":
                raise FieldError(
                    f"{int(far.sum())} of {len(targets)} target points are more than "
                    f"{max_distance:g} m from any field point",
                    code="invalid_input",
                )
            if beyond == "zero":
                out[far] = 0.0
            else:
                out[far] = values[nearest[far]]
    return out, dist


def _linear(points, values, targets, fallback) -> np.ndarray:
    from scipy.interpolate import LinearNDInterpolator, interp1d

    vt, span = _flat_basis(points)
    centre = points.mean(axis=0)
    if span >= 3:
        local, at = points - centre, targets - centre
    else:
        local, at = (points - centre) @ vt[:span].T, (targets - centre) @ vt[:span].T
    if span == 1:
        order = np.argsort(local[:, 0])
        f = interp1d(local[order, 0], values[order], bounds_error=False, fill_value=np.nan)
        out = f(at[:, 0])
    elif span == 0:
        return fallback.copy()
    else:
        out = LinearNDInterpolator(local, values)(at)
    out = np.asarray(out, dtype=float)
    bad = ~np.isfinite(out)
    out[bad] = fallback[bad]
    return out


# --- verification ---------------------------------------------------------------------------


def check(project, root: Path, f, targets: np.ndarray | None = None, tolerance: float | None = None,
          use=None) -> dict:  # fmt: skip
    """The verification report (ADR 0044): per column count, range and mean; with
    `targets` (points of the faces or bodies it will be used on): the share within
    `tolerance` of a data point (default 2 % of the target's size, or 1.5 times the
    data's median spacing when that is larger: a coarse grid still covers), a histogram of
    distances, data points far off the target, and warnings in plain words."""
    states = clouds(project, root, f, use)
    c = states[0][1]  # coverage on the first time's points; ranges over every time
    every = {n: np.concatenate([s.values[n] for _, s in states]) for n in c.values}
    report: dict = {
        "field": f.name,
        "points": int(len(c.points)),
        "sparse": bool(len(c.points) < SPARSE),
        "columns": {
            name: {
                "min": float(v.min()),
                "max": float(v.max()),
                "mean": float(v.mean()),
                "unit": c.units.get(name, ""),
            }  # fmt: skip
            for name, v in every.items()
        },
        "bounds": [c.points.min(axis=0).tolist(), c.points.max(axis=0).tolist()],
        "warnings": [],
    }
    if states[0][0] is not None:
        report["times"] = [float(t) for t, _ in states]
    if report["sparse"]:
        report["warnings"].append(
            f"only {len(c.points)} points: sparse data (test points), interpolated between them"
        )
    if targets is None or not len(targets):
        return report
    from scipy.spatial import cKDTree

    size = _model_size(np.asarray(targets))
    tree = cKDTree(c.points)
    if tolerance is None:  # 2 % of the target, or the data's own spacing if coarser
        spacing = tree.query(c.points, k=2)[0][:, 1] if len(c.points) > 1 else np.zeros(1)
        tolerance = max(0.02 * size, 1.5 * float(np.median(spacing)))
    tol = tolerance
    dist = tree.query(targets)[0]
    covered = float(np.mean(dist <= tol))
    counts, edges = np.histogram(dist, bins=10, range=(0.0, max(float(dist.max()), tol)))
    back = cKDTree(targets).query(c.points)[0]
    off = int(np.sum(back > max(tol, 0.1 * size)))
    report.update(
        tolerance=float(tol),
        coverage=covered,
        largest_distance=float(dist.max()),
        histogram={"edges": edges.tolist(), "counts": counts.tolist()},
        off_model=off,
    )
    if covered < 0.999:
        report["warnings"].append(
            f"{100 * (1 - covered):.3g} % of the target is more than {tol * 1e3:.3g} mm from "
            "any data point"
        )
    if covered < 0.5:
        report["warnings"].append(
            "the data misses most of the target: check its unit and placement"
        )
    if off:
        report["warnings"].append(f"{off} data points lie well off the target")
    ratio = _model_size(c.points) / size
    report["size_ratio"] = ratio
    if ratio > 5 or ratio < 0.2:
        report["warnings"].append(
            f"the data spans {ratio:.3g} times the target's size: check its length unit "
            "(millimetres read as metres?) and placement"
        )
    return report


# --- a feature's field use, on its nodes -------------------------------------------------------


def _scaled(use, values: np.ndarray) -> np.ndarray:
    """A use's scale, then its offset (an offset in a temperature unit adds a difference:
    °C and K steps are the same)."""
    offset = use.offset.to_si() if use.offset is not None else 0.0
    if use.offset is not None and use.offset.unit in ("degC", "°C", "degF", "°F"):
        offset = use.offset.value * (5 / 9 if "F" in use.offset.unit else 1.0)
    return use.scale * values + offset


def _column(f, c: Cloud, name: str) -> np.ndarray:
    if name not in c.values:
        raise FieldError(
            f"field '{f.name}' has no column '{name}' (it has {sorted(c.values)})",
            code="not_found",
        )
    return c.values[name]


def _at(f, states, time: float | None) -> list[tuple[float, Cloud]]:
    """The field's states to blend at `time` (s), with their weights: linear between the
    two times about it, held at the first or last outside them."""
    if states[0][0] is None:
        return [(1.0, states[0][1])]
    if time is None:
        raise FieldError(f"field '{f.name}' varies in time: say at which time to take it")
    times = np.array([s for s, _ in states])
    if time <= times[0] or len(times) == 1:
        return [(1.0, states[0][1])]
    if time >= times[-1]:
        return [(1.0, states[-1][1])]
    j = int(np.searchsorted(times, time))  # times[j - 1] < time <= times[j]
    w = (time - times[j - 1]) / (times[j] - times[j - 1])
    return [(1 - w, states[j - 1][1]), (w, states[j][1])]


def use_values(project, root: Path, use, targets: np.ndarray, weights=None, entity=None, *,
               time: float | None = None) -> tuple[np.ndarray, np.ndarray]:  # fmt: skip
    """A field use's values (SI, scaled and offset) at `targets`, and their distances. A
    field varying in time is taken at `time` (s): linearly between its two times about
    it, held at the first or last outside them. A conservative use needs the targets'
    `weights` (∫N dA or ∫N dV per target, from `adapters.common.node_weights`) and
    whether it is spread over a "face" or a "body"."""
    from puzzle.model.schema import ImportedField

    f = project.get(use.field, ImportedField)
    parts = [(w, _mapped(f, c, use, targets, weights, entity))
             for w, c in _at(f, clouds(project, root, f, use), time)]  # fmt: skip
    values = sum(w * v for w, (v, _) in parts)
    return values, np.maximum.reduce([d for _, (_, d) in parts])


def use_history(project, root: Path, use, targets: np.ndarray, weights=None, entity=None
                ) -> tuple[np.ndarray, np.ndarray]:  # fmt: skip
    """A field use's times (s) and its values at `targets` at each, (n, times): what a
    transient step follows, linearly between the times."""
    from puzzle.model.schema import ImportedField

    f = project.get(use.field, ImportedField)
    states = clouds(project, root, f, use)
    if states[0][0] is None:
        raise FieldError(f"field '{f.name}' holds still in time")
    values = np.column_stack([_mapped(f, c, use, targets, weights, entity)[0] for _, c in states])
    return np.array([t for t, _ in states]), values


def step_time(step, k: int) -> float:
    """The time (s) a solve step takes a field varying in time at: its end_time setting
    when it has one, else its number k from 1 (CalculiX's total time when each static
    step lasts 1)."""
    value = step.settings.get("end_time") if step is not None else None
    if value is None:
        return float(k)
    return value.to_si() if hasattr(value, "to_si") else float(value)


def _mapped(f, c: Cloud, use, targets, weights=None, entity=None
            ) -> tuple[np.ndarray, np.ndarray]:  # fmt: skip
    source = _scaled(use, _column(f, c, use.column))
    limit = use.max_distance.to_si() if use.max_distance is not None else None
    mapping = "linear" if use.mapping == "conservative" else use.mapping
    values, dist = map_values(c.points, source, targets, mapping, use.beyond, limit)
    if use.mapping == "conservative":
        if weights is None:
            raise FieldError("conservative mapping needs the target's areas or volumes")
        total = source_weights(f, c, use, entity) @ source
        values = conserve(np.array([total]), values[:, None], np.asarray(weights))[:, 0]
    return values, dist


# --- vectors and totals (stage 2) -------------------------------------------------------------

# Feature values that make one vector: taken from one field and placement, they are
# rotated with the points; a conservative mapping keeps their total as a vector.
VECTORS = {
    "displacement": ("x", "y", "z"),
    "body_force": ("fx", "fy", "fz"),
    "traction": ("tx", "ty", "tz"),
}
# Values a conservative mapping applies to (their total is a force, or a heat flow), and
# what they're spread over: per area on faces, per volume on bodies.
CONSERVED = {"pressure": "face", "traction": "face", "heat_flux": "face", "body_force": "body"}


def turn(project, f, use) -> np.ndarray:
    """The rotation (3 x 3) a use's points get, the field's placement then the use's: the
    linear part of the two placements, which a vector's components follow too."""
    zero, unit = np.zeros((1, 3)), np.eye(3)
    a = place(unit, f.placement, project) - place(zero, f.placement, project)
    b = place(unit, use.placement, project) - place(zero, use.placement, project)
    return b.T @ a.T


def source_weights(f, c: Cloud, use, entity: str | None) -> np.ndarray:
    """Each field point's area (a face load) or volume (a body load), m² or m³, from the
    use's weight column; a column without a unit is in the points' length unit squared
    or cubed."""
    if entity is None:
        raise FieldError("conservative mapping applies to force-type values only "
                         f"({', '.join(sorted(CONSERVED))})")  # fmt: skip
    if not use.weight:
        raise FieldError(
            f"conservative mapping of field '{f.name}' needs each point's "
            f"{'area' if entity == 'face' else 'volume'}: name the column holding it "
            "(weight). Without one the source's total isn't defined",
            path=f.name,
        )
    w = _column(f, c, use.weight)
    column = next(col for col in f.columns if col.name == use.weight)
    if not column.unit:  # read as written: in the points' unit to a power
        a, _, _ = _si(f.length_unit)
        w = w * a ** (2 if entity == "face" else 3)
    return np.asarray(w, dtype=float)


def conserve(total: np.ndarray, values: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """Mapped values (n, c) corrected so their integral ∑ w v equals the source's `total`
    (c,): v + |v| k, k = (total - ∑ w v) / ∑ w |v|. The correction goes where the load
    is (zero stays zero), turns with a rotation, and is exact whatever the shapes."""
    values = np.asarray(values, dtype=float)
    size = np.linalg.norm(values, axis=1)
    spread = float(weights @ size)
    if spread <= 1e-300 or not np.isfinite(spread):
        if np.allclose(total, 0.0):
            return values
        raise FieldError(
            "conservative mapping: no load lands on the target (check the field's placement)"
        )
    k = (np.asarray(total, dtype=float) - weights @ values) / spread
    return values + size[:, None] * k[None, :]


def vector_values(project, root: Path, f, targets, weights=None, time: float | None = None
                  ) -> dict[str, np.ndarray]:  # fmt: skip
    """A vector feature's components at `targets` (SI), from its fields and uniform
    values. When a placement turns the points, or the mapping is conservative, the
    components must come from one field and placement: they are rotated with the points
    (and conserved) as one vector. Otherwise each component maps on its own."""
    from puzzle.model.schema import ImportedField

    names = VECTORS[f.type]
    if f.type == "displacement":  # a component it doesn't name is left free
        present = [n for n in names if n in f.fields or n in f.values]
    else:  # a load's missing component is zero
        present = list(names)
    if f.axes is not None:
        raise FieldError(
            f"{f.type} on '{f.selection}' takes its vector from an imported field: place "
            "the field (its placement or the use's) instead of giving the feature axes",
            code="unsupported",
        )
    uses = {n: f.fields[n] for n in present if n in f.fields}
    fields_of = {n: project.get(u.field, ImportedField) for n, u in uses.items()}
    turned = any(
        not np.allclose(turn(project, fields_of[n], u), np.eye(3)) for n, u in uses.items()
    )
    together = turned or any(u.mapping == "conservative" for u in uses.values())
    if not together:
        out = {}
        for n in present:
            if n in uses:
                out[n] = use_values(project, root, uses[n], targets, time=time)[0]
            else:
                q = f.values.get(n)
                out[n] = np.full(len(targets), q.to_si() if q is not None else 0.0)
        return out
    first = uses[next(iter(uses))]
    alike = all(
        u.field == first.field and u.placement == first.placement and u.mapping == first.mapping
        and u.weight == first.weight and u.beyond == first.beyond
        and u.max_distance == first.max_distance
        for u in uses.values()
    )  # fmt: skip
    if set(uses) != set(present) or not alike:
        why = "turns the points" if turned else "is conservative"
        raise FieldError(
            f"{f.type} on '{f.selection}': its placement {why}, so its components "
            f"({', '.join(present)}) must all come from one field with the same placement "
            "and mapping, to be rotated and conserved as one vector",
            code="invalid_input",
        )
    field_ = fields_of[next(iter(uses))]
    limit = first.max_distance.to_si() if first.max_distance is not None else None
    mapping = "linear" if first.mapping == "conservative" else first.mapping
    mapped = np.zeros((len(targets), 3))
    for w, c in _at(field_, clouds(project, root, field_, first), time):
        full = np.zeros((len(c.points), 3))
        for d, n in enumerate(names):
            if n in uses:
                full[:, d] = _scaled(uses[n], _column(field_, c, uses[n].column))
        full = full @ turn(project, field_, first).T  # the file's axes to the model's
        part = np.column_stack([
            map_values(c.points, full[:, d], targets, mapping, first.beyond, limit)[0]
            for d in range(3)
        ])  # fmt: skip
        if first.mapping == "conservative":
            if weights is None:
                raise FieldError("conservative mapping needs the target's areas or volumes")
            total = source_weights(field_, c, first, CONSERVED.get(f.type)) @ full
            part = conserve(total, part, np.asarray(weights))
        mapped += w * part
    return {n: mapped[:, d] for d, n in enumerate(names) if n in present}


# --- making one, and checking one on a model ---------------------------------------------------


def new_field_data(
    root: Path,
    path: Path,
    name: str | None = None,
    *,
    files: list[Path] | None = None,
    times: list[float] | None = None,
    every_step: bool = False,
    **overrides,
) -> dict:
    """What a Create command for a new imported field holds: the file copied into the
    project, hashed, and read as `sniff` guesses unless `overrides` say otherwise
    (columns as dicts, length_unit, placement as a dict, region, index, ...).

    Varying in time (ADR 0044): `files` are more files of the same layout, one per time,
    after `path`, with `times` (s) one per file, `path`'s first; or `every_step` takes
    every step of a multi-step source (Exodus, EnSight, OpenFOAM times) at its own
    time, or at `times` when given (one per step)."""
    rel, digest = import_file(root, path)
    found = sniff(Path(root) / rel)
    data = {
        "name": name or Path(path).stem,
        "file": rel,
        "file_hash": digest,
        **{k: found[k] for k in ("format", "delimiter", "skip_rows", "header", "length_unit")},
        "columns": found["columns"],
    }
    data.update({k: v for k, v in overrides.items() if v is not None})
    data["times"] = _times(root, path, rel, digest, found, files, times, every_step)
    if not data["times"]:
        data.pop("times")
    return data


def _times(root, path, rel, digest, found, files, times, every_step) -> list[dict]:
    if files:
        if times is None or len(times) != len(files) + 1:
            raise FieldError(f"{len(files) + 1} files need {len(files) + 1} times, one each")
        out = [{"time": float(times[0]), "file": rel, "file_hash": digest}]
        for t, more in zip(times[1:], files, strict=True):
            r, d = import_file(root, more)
            out.append({"time": float(t), "file": r, "file_hash": d})
        return out
    if every_step:
        steps = found.get("steps") or []
        if len(steps) < 2:
            raise FieldError(f"{Path(path).name} holds {len(steps)} step(s): nothing varies")
        if times is not None and len(times) != len(steps):
            raise FieldError(f"{Path(path).name} has {len(steps)} steps; give as many times")
        return [{"time": float(times[k] if times is not None else s["time"]), "index": s["index"]}
                for k, s in enumerate(steps)]  # fmt: skip
    if times:
        raise FieldError("times go with several files or every_step; a time column is a "
                         "column with the role time")  # fmt: skip
    return []


def target_points(project, root: Path, where: str, work: Path) -> np.ndarray:
    """The mesh nodes of `where` ("Component" or "Component/Part/Selection"), meshed as
    a study would mesh them: what a check measures coverage on."""
    from puzzle.mesh import generate_mesh
    from puzzle.model.schema import Component

    name, _, selection = where.partition("/")
    comp = next((c for c in project.of_type(Component) if c.name == name), None)
    if comp is None:
        raise FieldError(f"no component '{name}'", code="not_found")
    mesh = generate_mesh(project, comp.id, Path(root), Path(work)).mesh
    if not selection:
        return mesh.coords
    group = mesh.groups.get(selection)
    if group is None:
        raise FieldError(f"component '{name}' has no selection '{selection}'", code="not_found")
    return mesh.node_coords(group.node_ids)


# --- into a study's run ------------------------------------------------------------------------


def feature_key(feature, value: str) -> str:
    """How a feature's field-driven value is named to adapters: its type, selection and
    value ("pressure:Plate/Top:p"), which survives the per-step copies split studies get."""
    return f"{feature.type}:{feature.selection}:{value}"


def field_inputs(project, root: Path, study, mesh) -> list:
    """The inputs a study's imported fields and body temperatures make (ADR 0044): a
    body temperature (uniform or a field) becomes a temperature load on the body's nodes,
    as a transfer's would; any other field-driven value goes as a "feature" input on its
    selection's nodes, named by `feature_key`, for the adapter to write per face.

    A field varying in time gives one input per solve step (`Input.step`), at the step's
    time (`step_time`); in a transient step, its whole history (`Input.times`, one value
    column per time) for the adapter to follow."""
    from puzzle.model.schema import Component, ImportedField
    from puzzle.results.model import Field
    from puzzle.transfer import Input

    comp = project.get(study.component, Component)
    steps = study.solve_steps()
    solved = set()
    for s in steps:
        solved |= set(s.physics) or {p.type for p in comp.physics}

    def varies(use) -> bool:
        return project.get(use.field, ImportedField).varies()

    temperature: dict[int | None, dict[int, float]] = {}
    out = []
    for physics in comp.physics:
        if physics.type not in solved:
            continue
        for f in physics.features:
            if f.type != "body_temperature" and not f.fields:
                continue
            group = mesh.groups.get(f.selection)
            if group is None:
                raise FieldError(f"{f.type} on '{f.selection}': no such selection in the mesh")
            ids = group.node_ids
            xyz = mesh.node_coords(ids)
            when: list[int | None] = [None]
            if any(varies(u) for u in f.fields.values()):
                when = [k for k, s in enumerate(steps, 1)
                        if s.solves(physics.type) and (not f.steps or k in f.steps)]  # fmt: skip
            weights = _weights(f, group, mesh, ids)
            for k in when:
                step = steps[k - 1] if k is not None else None
                if f.type in VECTORS:  # stage 2: the components as one vector
                    if (
                        step is not None
                        and step.type == "transient"
                        and any(varies(u) for u in f.fields.values())
                    ):
                        raise FieldError(
                            f"{f.type} from a field varying in time can't be followed "
                            f"through the transient step {k} yet (on {f.selection})",
                            code="unsupported",
                        )
                    time = step_time(step, k) if k is not None else None
                    for name, values in vector_values(project, root, f, xyz, weights,
                                                      time).items():  # fmt: skip
                        key = feature_key(f, name)
                        field_ = Field(key, "node", [""], ids, values[:, None])
                        out.append(Input(field_, "feature", f.selection, key, step=k))
                    continue
                names = set(f.fields) | ({"T"} if f.type == "body_temperature" else set())
                for name in sorted(names):
                    key, use, history = feature_key(f, name), f.fields.get(name), None
                    if use is None:
                        values = np.full(len(ids), f.values[name].to_si())
                    elif step is not None and step.type == "transient" and varies(use):
                        if f.type == "body_temperature":
                            raise FieldError(
                                f"a body temperature varying in time can't be followed through "
                                f"the transient step {k} yet (on {f.selection})",
                                code="unsupported",
                            )
                        history, values = use_history(
                            project, root, use, xyz, weights, CONSERVED.get(f.type)
                        )
                    else:
                        time = step_time(step, k) if k is not None else None
                        values, _ = use_values(
                            project, root, use, xyz, weights, CONSERVED.get(f.type), time=time
                        )
                    if f.type == "body_temperature":
                        temperature.setdefault(k, {}).update(
                            zip(ids.tolist(), values.tolist(), strict=True)
                        )
                        continue
                    if history is not None:
                        comps = [f"{t:g} s" for t in history]
                        field_ = Field(key, "node", comps, ids, values)
                        out.append(Input(field_, "feature", f.selection, key, step=k,
                                         times=tuple(float(t) for t in history)))  # fmt: skip
                    else:
                        field_ = Field(key, "node", [""], ids, values[:, None])
                        out.append(Input(field_, "feature", f.selection, key, step=k))
    still = temperature.pop(None, {})
    per_step: dict[int | None, dict[int, float]] = {None: still} if still else {}
    if temperature:  # some vary: every step gets its own, the still ones in each
        every = range(1, len(steps) + 1)
        per_step = {k: still | temperature.get(k, {}) for k in every}
        per_step = {k: v for k, v in per_step.items() if v}
    for k, temps in sorted(per_step.items(), key=lambda kv: kv[0] or 0):
        ids = np.array(sorted(temps), dtype=np.int64)
        values = np.array([temps[i] for i in ids.tolist()])[:, None]
        out.append(Input(Field("temperature", "node", [""], ids, values), "load", None,
                         "body temperature", step=k))  # fmt: skip
    return out


def _weights(f, group, mesh, ids) -> np.ndarray | None:
    """The selection's node weights (∫N dA or ∫N dV) when a use of `f` is conservative."""
    if not any(u.mapping == "conservative" for u in f.fields.values()):
        return None
    entity = CONSERVED.get(f.type)
    if entity is None:
        raise FieldError(
            f"conservative mapping keeps a total force or heat flow: {f.type} has none "
            f"(it applies to {', '.join(sorted(CONSERVED))})",
            path=f.selection,
        )
    from puzzle.adapters.common import node_weights

    w = node_weights(group, mesh, entity)
    return np.array([w.get(int(i), 0.0) for i in ids])
