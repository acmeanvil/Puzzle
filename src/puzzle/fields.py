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
- `use_values` is what a study's run asks for: a feature's field use on its nodes.
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
}
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
    """Copy a field file into the project; its path relative to the project, and hash."""
    target_dir = Path(root) / FIELDS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / Path(path).name
    if Path(path).resolve() != target.resolve():
        shutil.copyfile(path, target)
    return f"{FIELDS_DIR}/{target.name}", file_hash(target)


def file_hash(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sniff(path: Path) -> dict:
    """A file's likely reading: format, delimiter, rows to skip, header, and columns
    (name, role, unit); the length unit when the coordinates name one."""
    path = Path(path)
    if path.suffix.lower() in (".vtu", ".vtk"):
        return _sniff_vtu(path)
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
    key = name.strip().lower().replace("-coordinate", "").replace(" coordinate", "")
    return key if key in ("x", "y", "z") else None


def _default_names(n: int) -> list[str]:
    return ["x", "y", "z", *(f"value {i}" for i in range(1, n - 2))][:n]


def read_table(root: Path, f) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """An ImportedField's file as raw points (file units) and value columns (file units)."""
    path = Path(root) / f.file
    if not path.exists():
        raise FieldError(f"field '{f.name}': its file {f.file} is missing", path=f.name)
    if f.format == "vtu":
        return _read_vtu(path, f)
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
    return points, values


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
    placement and then the use's; values in SI."""
    raw, values = read_table(root, f)
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
    c = cloud(project, root, f, use)
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
            for name, v in c.values.items()
        },
        "bounds": [c.points.min(axis=0).tolist(), c.points.max(axis=0).tolist()],
        "warnings": [],
    }
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


def use_values(project, root: Path, use, targets: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """A field use's values (SI, scaled and offset) at `targets`, and their distances."""
    from puzzle.model.schema import ImportedField

    f = project.get(use.field, ImportedField)
    c = cloud(project, root, f, use)
    if use.column not in c.values:
        raise FieldError(
            f"field '{f.name}' has no column '{use.column}' (it has {sorted(c.values)})",
            code="not_found",
        )
    limit = use.max_distance.to_si() if use.max_distance is not None else None
    values, dist = map_values(
        c.points, c.values[use.column], targets, use.mapping, use.beyond, limit
    )
    offset = use.offset.to_si() if use.offset is not None else 0.0
    # An offset in a temperature unit adds a difference: °C and K steps are the same.
    if use.offset is not None and use.offset.unit in ("degC", "°C", "degF", "°F"):
        offset = use.offset.value * (5 / 9 if "F" in use.offset.unit else 1.0)
    return use.scale * values + offset, dist


# --- making one, and checking one on a model ---------------------------------------------------


def new_field_data(root: Path, path: Path, name: str | None = None, **overrides) -> dict:
    """What a Create command for a new imported field holds: the file copied into the
    project, hashed, and read as `sniff` guesses unless `overrides` say otherwise
    (columns as dicts, length_unit, placement as a dict, ...)."""
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
    return data


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
    selection's nodes, named by `feature_key`, for the adapter to write per face."""
    from puzzle.model.schema import Component
    from puzzle.results.model import Field
    from puzzle.transfer import Input

    comp = project.get(study.component, Component)
    solved = set()
    for s in study.solve_steps():
        solved |= set(s.physics) or {p.type for p in comp.physics}
    temperature: dict[int, float] = {}
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
            for name in sorted(set(f.fields) | ({"T"} if f.type == "body_temperature" else set())):
                if name in f.fields:
                    values, _ = use_values(project, root, f.fields[name], xyz)
                else:
                    values = np.full(len(ids), f.values[name].to_si())
                if f.type == "body_temperature":
                    temperature.update(zip(ids.tolist(), values.tolist(), strict=True))
                    continue
                field_ = Field(feature_key(f, name), "node", [""], ids, values[:, None])
                out.append(Input(field_, "feature", f.selection, feature_key(f, name)))
    if temperature:
        ids = np.array(sorted(temperature), dtype=np.int64)
        values = np.array([temperature[i] for i in ids.tolist()])[:, None]
        out.append(Input(Field("temperature", "node", [""], ids, values), "load", None,
                         "body temperature"))  # fmt: skip
    return out
