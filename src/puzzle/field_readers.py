"""Readers for imported fields from other tools (ADR 0044): Ansys Fluent profiles,
Exodus II, EnSight Gold and OpenFOAM case folders. `puzzle.fields` reads CSV and VTU
itself and comes here for the rest.

Every reader gives the same thing: points in the file's own length unit and value
columns by name, at one step of the source. A vector or tensor becomes one column per
component, `<name>_x`, `_y`, `_z` (tensors `_xx`, `_yy`, `_zz`, `_xy`, `_yz`, `_zx`, and
for a full tensor `_yx`, `_zy`, `_xz` too), so the per-column model holds for all of
them. Element (cell) values sit at the elements' centres: the mean of their corner
nodes for Exodus and EnSight, OpenFOAM's own volume-weighted cell centres for OpenFOAM
(or its written `C` field). A field reads node values or element values, not both, since
the two sit at different points.

`sniff_source` says what a file offers: its columns, its steps (time and index, for a
field that varies in time) and its regions (blocks, node sets, parts, patches, profiles).
"""

from __future__ import annotations

import gzip
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from puzzle.errors import PuzzleError

SUFFIXES = {
    ".prof": "fluent_profile",
    ".exo": "exodus",
    ".e": "exodus",
    ".ex2": "exodus",
    ".exii": "exodus",
    ".g": "exodus",
    ".case": "ensight",
    ".encas": "ensight",
}
VECTOR = ["x", "y", "z"]
SYMMETRIC = ["xx", "yy", "zz", "xy", "yz", "zx"]  # Puzzle's order


class ReaderError(PuzzleError):
    code = "invalid_input"


@dataclass
class Column:
    name: str
    location: str  # "node" or "element"


@dataclass
class Step:
    time: float
    index: str  # what FieldTime.index holds for it


@dataclass
class Offer:
    """What a source offers: value columns, steps, regions."""

    columns: list[Column] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    regions: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def format_of(path: Path) -> str | None:
    """A source's format from its name or contents, or None (left to the CSV reader)."""
    path = Path(path)
    if path.is_dir():
        return "openfoam" if openfoam_case(path) is not None else None
    if path.suffix.lower() in SUFFIXES:
        return SUFFIXES[path.suffix.lower()]
    try:
        head = path.read_bytes()[:256]
    except OSError:
        return None
    if head[:3] == b"CDF" or head[:8] == b"\x89HDF\r\n\x1a\n":
        return "exodus"
    if head.lstrip().startswith(b"((") and b"point" in head[:200]:
        return "fluent_profile"
    if b"FoamFile" in head and openfoam_case(path) is not None:
        return "openfoam"
    return None


def source_files(path: Path, fmt: str) -> list[Path]:
    """The files a source is made of (what is copied into the project and hashed)."""
    path = Path(path)
    if fmt == "ensight":
        return [path, *_ensight_case(path).files()]
    if fmt == "openfoam":
        case = openfoam_case(path)
        files = sorted((case / "constant" / "polyMesh").glob("*"))
        for _, d in _foam_times(case):
            files += sorted(p for p in d.iterdir() if p.is_file())
        return [p for p in files if p.is_file()]
    return [path]


def sniff_source(path: Path, fmt: str) -> Offer:
    return {"fluent_profile": _sniff_profile, "exodus": _sniff_exodus,
            "ensight": _sniff_ensight, "openfoam": _sniff_foam}[fmt](Path(path))  # fmt: skip


def read_source(
    path: Path, fmt: str, names: list[str], region: str = "", index: str = ""
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Points (n, 3, file units) and the named columns' values at step `index` (empty:
    the last), on `region` (empty: the volume, or the whole profile file)."""
    read = {"fluent_profile": _read_profile, "exodus": _read_exodus,
            "ensight": _read_ensight, "openfoam": _read_foam}[fmt]  # fmt: skip
    return read(Path(path), names, region, index)


def _one_location(offer: Offer, names: list[str], what: str) -> str:
    where = {c.name: c.location for c in offer.columns}
    missing = [n for n in names if n not in where]
    if missing:
        raise ReaderError(
            f"{what} has no column {', '.join(missing)} (it has {', '.join(where) or 'none'})",
            code="not_found",
        )
    found = {where[n] for n in names}
    if len(found) > 1:
        raise ReaderError(
            f"{what}: node and element values sit at different points; read one kind per "
            f"field ({', '.join(f'{n}: {where[n]}' for n in names)})"
        )
    return found.pop() if found else "node"


def _components(name: str, width: int) -> list[str]:
    if width == 1:
        return [name]
    if width in (2, 3):
        return [f"{name}_{c}" for c in VECTOR[:width]]
    if width == 6:
        return [f"{name}_{c}" for c in SYMMETRIC]
    if width == 9:
        return [f"{name}_{a}{b}" for a in "xyz" for b in "xyz"]
    return [f"{name}_{k + 1}" for k in range(width)]


def _step_position(steps: list[Step], index: str, what: str) -> int:
    if not steps:
        return 0
    if not index:
        return len(steps) - 1
    for k, s in enumerate(steps):
        if s.index == str(index):
            return k
    raise ReaderError(
        f"{what} has no step {index} (it has {', '.join(s.index for s in steps)})",
        code="not_found",
    )


# --- Ansys Fluent profiles ------------------------------------------------------------------


def _profiles(path: Path) -> list[tuple[str, str, dict[str, np.ndarray]]]:
    """A Fluent profile file's profiles: (name, type, {field: values}). The layout is
    `((name type n) (field v1 v2 ...) ...)`, several profiles one after another."""
    text = path.read_text(errors="replace", encoding="utf-8")
    out = []
    at = 0
    head = re.compile(r"\(\(\s*([^\s()]+)\s+([A-Za-z-]+)\s+(\d+)(?:\s+\d+)?\s*\)")
    while True:
        m = head.search(text, at)
        if m is None:
            break
        name, kind, n = m.group(1), m.group(2).lower(), int(m.group(3))
        pos, columns = m.end(), {}
        while True:
            while pos < len(text) and text[pos].isspace():
                pos += 1
            if pos >= len(text) or text[pos] == ")":
                pos += 1
                break
            close = text.index(")", pos)
            body = text[pos + 1 : close].split()
            if not body:
                raise ReaderError(f"{path.name}: an empty field in profile {name}")
            try:
                values = np.array(body[1:], dtype=float)
            except ValueError:
                raise ReaderError(
                    f"{path.name}: field {body[0]} of {name} is not numbers"
                ) from None
            if kind in ("point", "line", "radial", "axial") and len(values) != n:
                raise ReaderError(f"{path.name}: {name}'s {body[0]} has {len(values)} values, "
                                  f"not {n}")  # fmt: skip
            columns[body[0].lower()] = values
            pos = close + 1
        out.append((name, kind, columns))
        at = pos
    if not out:
        raise ReaderError(f"{path.name} holds no Fluent profile ((name point n) ...)")
    return out


def _profile_set(path: Path, region: str):
    found = _profiles(path)
    chosen = [p for p in found if not region or p[0] == region]
    if not chosen:
        raise ReaderError(f"{path.name} has no profile '{region}' "
                          f"({', '.join(p[0] for p in found)})", code="not_found")  # fmt: skip
    for name, kind, _ in chosen:
        if kind != "point":
            raise ReaderError(
                f"{path.name}: profile {name} is a {kind} profile; only point profiles "
                "(values at points) can be imported as fields"
            )
    names = [sorted(c) for _, _, c in chosen]
    if any(n != names[0] for n in names):
        raise ReaderError(f"{path.name}'s profiles hold different fields; pick one as the "
                          "region")  # fmt: skip
    return found, chosen


def _sniff_profile(path: Path) -> Offer:
    found, chosen = _profile_set(path, "")  # all point profiles alike, or say so
    cols = [c for c in chosen[0][2] if c not in ("x", "y", "z")]
    return Offer([Column(c, "node") for c in cols], [], [p[0] for p in found])


def _read_profile(path: Path, names, region, index):
    _, chosen = _profile_set(path, region)
    n = sum(len(next(iter(c.values()))) for _, _, c in chosen)
    points = np.zeros((n, 3))
    values = {name: np.empty(n) for name in names}
    start = 0
    for pname, _, cols in chosen:
        k = len(next(iter(cols.values())))
        for axis, key in enumerate("xyz"):
            if key in cols:
                points[start : start + k, axis] = cols[key]
            elif key != "z":  # a 2D profile has no z
                raise ReaderError(f"{path.name}: profile {pname} has no {key}")
        for name in names:
            if name not in cols:
                raise ReaderError(f"{path.name}: profile {pname} has no {name}", code="not_found")
            values[name][start : start + k] = cols[name]
        start += k
    return points, values


# --- Exodus II -----------------------------------------------------------------------------


class _NetCDF:
    """Variables of a netCDF-3 file (SciPy) or a netCDF-4/HDF5 one (h5py), by name."""

    def __init__(self, path: Path) -> None:
        head = path.read_bytes()[:8]
        if head[:3] == b"CDF":
            from scipy.io import netcdf_file

            self._f = netcdf_file(str(path), "r", mmap=False)
            self.names = set(self._f.variables)
            self.dims = {k: int(v or 0) for k, v in self._f.dimensions.items()}
            if any(v is None for v in self._f.dimensions.values()):
                self.dims["time_step"] = len(self._f.variables["time_whole"].data) if (
                    "time_whole" in self._f.variables) else 0  # fmt: skip
            self._get = lambda n: np.array(self._f.variables[n].data)
        elif head == b"\x89HDF\r\n\x1a\n":
            import h5py

            self._f = h5py.File(path, "r")
            self.names = {k for k, v in self._f.items() if isinstance(v, h5py.Dataset)}
            scale = b"DIMENSION_SCALE"
            self.dims = {k: int(v.shape[0]) for k, v in self._f.items()
                         if isinstance(v, h5py.Dataset) and v.attrs.get("CLASS") == scale
                         and v.shape}  # fmt: skip
            self._get = lambda n: np.array(self._f[n][()])
        else:
            raise ReaderError(f"{path.name} is not an Exodus II (netCDF) file")

    def get(self, name: str) -> np.ndarray:
        return self._get(name)

    def text(self, name: str) -> list[str]:
        if name not in self.names:
            return []
        data = self.get(name)
        if data.dtype.kind == "S" and data.dtype.itemsize == 1:
            rows = data.reshape(-1, data.shape[-1]) if data.ndim > 1 else data[None]
            return [b"".join(r).split(b"\0")[0].decode("utf-8", "replace").strip() for r in rows]
        return [(x.decode() if isinstance(x, bytes) else str(x)).strip() for x in data.ravel()]

    def close(self) -> None:
        self._f.close()


EXODUS_CORNERS = {"TETRA": 4, "TET": 4, "HEX": 8, "WEDGE": 6, "PYRAMID": 5, "TRISHELL": 3,
                  "TRI": 3, "QUAD": 4, "SHELL": 4, "BAR": 2, "BEAM": 2, "TRUSS": 2,
                  "CIRCLE": 1, "SPHERE": 1}  # fmt: skip


def _exodus_corners(elem_type: str) -> int | None:
    t = elem_type.upper()
    for prefix in sorted(EXODUS_CORNERS, key=len, reverse=True):
        if t.startswith(prefix):
            return EXODUS_CORNERS[prefix]
    return None


def _exodus_layout(nc: _NetCDF):
    """(blocks [(name, number)], node variable names, element variable names, times)."""
    count = nc.dims.get("num_el_blk", 0)
    names = nc.text("eb_names") or [""] * count
    ids = nc.get("eb_prop1").tolist() if "eb_prop1" in nc.names else list(range(1, count + 1))
    blocks = [(n or f"block {i}", k) for k, (n, i) in enumerate(zip(names, ids, strict=True), 1)
              if f"connect{k}" in nc.names]  # fmt: skip
    times = nc.get("time_whole").tolist() if "time_whole" in nc.names else []
    return blocks, nc.text("name_nod_var"), nc.text("name_elem_var"), times


def _sniff_exodus(path: Path) -> Offer:
    nc = _NetCDF(path)
    try:
        blocks, nodal, cells, times = _exodus_layout(nc)
        node_sets = nc.text("ns_names")
    finally:
        nc.close()
    columns = [Column(n, "node") for n in nodal]
    columns += [Column(n, "element") for n in cells if n not in nodal]
    steps = [Step(float(t), str(k)) for k, t in enumerate(times, 1)]
    both = sorted(set(cells) & set(nodal))
    notes = [f"element variables named like node ones are left out: {', '.join(both)}"] if (
        both) else []  # fmt: skip
    return Offer(columns, steps, [b for b, _ in blocks] + [n for n in node_sets if n], notes)


def _coords(nc: _NetCDF) -> np.ndarray:
    n = nc.dims.get("num_nodes", 0)
    out = np.zeros((n, 3))
    if "coord" in nc.names:  # the old layout: one (num_dim, num_nodes) variable
        c = nc.get("coord")
        out[:, : c.shape[0]] = c.T
        return out
    for k, axis in enumerate("xyz"):
        if f"coord{axis}" in nc.names:
            out[:, k] = nc.get(f"coord{axis}")
    return out


def _read_exodus(path: Path, names, region, index):
    nc = _NetCDF(path)
    try:
        offer = _sniff_exodus(path)
        location = _one_location(offer, names, path.name)
        step = _step_position(offer.steps, index, path.name)
        blocks, nodal, cells, _ = _exodus_layout(nc)
        xyz = _coords(nc)
        node_sets = nc.text("ns_names")
        chosen = [(b, k) for b, k in blocks if not region or b == region]
        if location == "node":
            keep = np.arange(len(xyz))
            if region and region in node_sets and not chosen:
                keep = nc.get(f"node_ns{node_sets.index(region) + 1}").astype(np.int64) - 1
            elif region:
                if not chosen:
                    raise ReaderError(f"{path.name} has no block or node set '{region}'",
                                      code="not_found")  # fmt: skip
                keep = np.unique(np.concatenate([nc.get(f"connect{k}").ravel() for _, k in chosen])
                                 .astype(np.int64) - 1)  # fmt: skip
            values = {}
            for n in names:
                v = nodal.index(n) + 1
                if f"vals_nod_var{v}" in nc.names:
                    data = nc.get(f"vals_nod_var{v}")[step]
                else:  # the old layout: one (time_step, num_nod_var, num_nodes) variable
                    data = nc.get("vals_nod_var")[step, v - 1]
                values[n] = np.asarray(data, dtype=float)[keep]
            return xyz[keep], values
        if not chosen:
            raise ReaderError(f"{path.name} has no element block '{region}'", code="not_found")
        points, values = [], {n: [] for n in names}
        for _, k in chosen:
            have = [f"vals_elem_var{cells.index(n) + 1}eb{k}" for n in names]
            if not all(h in nc.names for h in have):
                continue  # this block doesn't carry every chosen variable
            conn = nc.get(f"connect{k}").astype(np.int64) - 1
            corners = _exodus_corners(_elem_type(nc, k)) or conn.shape[1]
            points.append(xyz[conn[:, :corners]].mean(axis=1))
            for n, h in zip(names, have, strict=True):
                values[n].append(np.asarray(nc.get(h)[step], dtype=float))
        if not points:
            raise ReaderError(f"{path.name}: no block carries {', '.join(names)}")
        return np.vstack(points), {n: np.concatenate(v) for n, v in values.items()}
    finally:
        nc.close()


def _elem_type(nc: _NetCDF, k: int) -> str:
    f = nc._f
    try:
        if hasattr(f, "variables"):
            t = f.variables[f"connect{k}"].elem_type
        else:
            t = f[f"connect{k}"].attrs["elem_type"]
    except (KeyError, AttributeError):
        return ""
    return t.decode() if isinstance(t, bytes) else str(t)


# --- EnSight Gold ---------------------------------------------------------------------------

ENSIGHT_NODES = {"point": 1, "bar2": 2, "bar3": 3, "tria3": 3, "tria6": 6, "quad4": 4,
                 "quad8": 8, "tetra4": 4, "tetra10": 10, "pyramid5": 5, "pyramid13": 13,
                 "penta6": 6, "penta15": 15, "hexa8": 8, "hexa20": 20}  # fmt: skip
ENSIGHT_CORNERS = {"point": 1, "bar2": 2, "bar3": 2, "tria3": 3, "tria6": 3, "quad4": 4,
                   "quad8": 4, "tetra4": 4, "tetra10": 4, "pyramid5": 5, "pyramid13": 5,
                   "penta6": 6, "penta15": 6, "hexa8": 8, "hexa20": 8}  # fmt: skip
VOLUME_TYPES = {"tetra4", "tetra10", "pyramid5", "pyramid13", "penta6", "penta15", "hexa8",
                "hexa20", "nfaced"}  # fmt: skip
ENSIGHT_WIDTH = {"scalar": 1, "vector": 3, "tensor symm": 6, "tensor asym": 9}
ENSIGHT_SYMMETRIC = [0, 1, 2, 3, 5, 4]  # EnSight's 11 22 33 12 13 23 -> Puzzle's order


@dataclass
class _Variable:
    kind: str  # scalar, vector, tensor symm, tensor asym
    location: str  # node or element
    pattern: str
    time_set: int | None


@dataclass
class _Case:
    folder: Path
    geometry: str
    geometry_set: int | None
    variables: dict[str, _Variable]
    sets: dict[int, tuple[list[float], list[int]]]  # time set -> (times, file numbers)

    def file(self, pattern: str, time_set: int | None, position: int) -> Path:
        if "*" not in pattern:
            return self.folder / pattern
        numbers = self.sets.get(time_set, ([], []))[1] if time_set is not None else []
        number = numbers[position] if numbers else position
        stars = re.search(r"\*+", pattern).group(0)
        return self.folder / pattern.replace(stars, f"{number:0{len(stars)}d}", 1)

    def files(self) -> list[Path]:
        out = []
        listed = [(v.pattern, v.time_set) for v in self.variables.values()]
        for pattern, ts in [(self.geometry, self.geometry_set), *listed]:
            count = len(self.sets.get(ts, ([0], []))[0]) if "*" in pattern else 1
            out += [self.file(pattern, ts, k) for k in range(max(count, 1))]
        return list(dict.fromkeys(p for p in out if p.exists()))


def _ensight_case(path: Path) -> _Case:
    lines = Path(path).read_text(errors="replace", encoding="utf-8").splitlines()
    section, geometry, geometry_set, variables = "", "", None, {}
    sets: dict[int, tuple[list[float], list[int]]] = {}
    current, key = None, None
    numbers: dict[int, list[int]] = {}
    times: dict[int, list[float]] = {}
    counts: dict[int, int] = {}
    starts: dict[int, tuple[int, int]] = {}
    for raw in lines:
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.upper() in ("FORMAT", "GEOMETRY", "VARIABLE", "TIME", "FILE"):
            section, key = line.upper(), None
            continue
        if section == "GEOMETRY" and line.lower().startswith("model:"):
            tokens = line.split(":", 1)[1].split()
            tokens = [t for t in tokens if t not in ("change_coords_only", "cstep")]
            geometry = tokens[-1]
            geometry_set = int(tokens[0]) if len(tokens) > 1 else None
        elif section == "VARIABLE" and ":" in line:
            what, rest = line.split(":", 1)
            m = re.match(
                r"(scalar|vector|tensor symm|tensor asym) per (node|element)", what.strip()
            )
            if m is None:
                continue  # constants and complex variables are not fields at points
            tokens = rest.split()
            ts = int(tokens[0]) if len(tokens) > 2 and tokens[0].isdigit() else None
            variables[tokens[-2]] = _Variable(m.group(1), m.group(2), tokens[-1], ts)
        elif section == "TIME":
            if ":" in line:
                name, rest = (s.strip() for s in line.split(":", 1))
                name = name.lower()
                if name == "time set":
                    current = int(rest.split()[0])
                    key = None
                    continue
                key = name
                line = rest
            if current is None or key is None:
                continue
            tokens = line.split()
            if key == "number of steps":
                counts[current] = int(tokens[0])
            elif key == "filename start number":
                starts[current] = (int(tokens[0]), starts.get(current, (0, 1))[1])
            elif key == "filename increment":
                starts[current] = (starts.get(current, (0, 1))[0], int(tokens[0]))
            elif key == "filename numbers":
                numbers.setdefault(current, []).extend(int(t) for t in tokens)
            elif key == "time values":
                times.setdefault(current, []).extend(float(t) for t in tokens)
    for ts, values in times.items():
        nums = numbers.get(ts)
        if not nums:
            first, inc = starts.get(ts, (0, 1))
            nums = [first + inc * k for k in range(counts.get(ts, len(values)))]
        sets[ts] = (values, nums)
    if not geometry:
        raise ReaderError(f"{Path(path).name} names no geometry (model:) file")
    return _Case(Path(path).parent, geometry, geometry_set, variables, sets)


class _Bytes:
    """EnSight Gold's C Binary (either byte order) or ASCII, read as words."""

    def __init__(self, path: Path, binary: bool | None = None) -> None:
        """`binary`: as the geometry file says (only it starts with "C Binary")."""
        data = path.read_bytes()
        if data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
        self.data, self.pos = data, 0
        self.binary = data[:8].lower() == b"c binary" if binary is None else binary
        self.order = "<"
        if not self.binary and data[:7].lower() == b"fortran":
            raise ReaderError(f"{path.name}: Fortran binary EnSight files can't be read yet; "
                              "write C Binary or ASCII")  # fmt: skip
        self.tokens: list[str] = []

    def text(self) -> str:
        if self.binary:
            raw = self.data[self.pos : self.pos + 80]
            self.pos += 80
            return raw.split(b"\0")[0].decode("ascii", "replace").strip()
        end = self.data.find(b"\n", self.pos)
        end = len(self.data) if end < 0 else end
        line = self.data[self.pos : end].decode("ascii", "replace").strip()
        self.pos = end + 1
        return line

    def _numbers(self, n: int, kind: str) -> np.ndarray:
        if self.binary:
            dtype = np.dtype(self.order + kind + "4")
            out = np.frombuffer(self.data, dtype=dtype, count=n, offset=self.pos)
            self.pos += 4 * n
            return out
        values: list[str] = []
        while len(values) < n:
            values += self.text().split()
        if len(values) > n:
            raise ReaderError("an EnSight ASCII file's numbers run past their line")
        return np.array(values, dtype=float if kind == "f" else np.int64)

    def ints(self, n: int) -> np.ndarray:
        return self._numbers(n, "i").astype(np.int64)

    def floats(self, n: int) -> np.ndarray:
        return self._numbers(n, "f").astype(float)

    def first_part_number(self) -> None:
        """Pick the byte order from the first part number, which is small."""
        if self.binary:
            le = int(np.frombuffer(self.data, "<i4", 1, self.pos)[0])
            if not 0 < le < 1 << 20:
                self.order = ">"

    @property
    def done(self) -> bool:
        return self.pos >= len(self.data) or not self.data[self.pos :].strip(b"\0 \n\r\t")


@dataclass
class _Block:
    kind: str
    count: int
    centres: np.ndarray


@dataclass
class _Part:
    number: int
    name: str
    coords: np.ndarray
    blocks: list[_Block]
    binary: bool = True  # how the geometry was written, so its variables are too

    @property
    def volume(self) -> bool:
        return any(b.kind in VOLUME_TYPES for b in self.blocks)


def _ensight_geometry(path: Path) -> list[_Part]:
    r = _Bytes(path)
    if r.binary:
        r.text()
    r.text(), r.text()  # two description lines
    node_ids, element_ids = r.text().lower(), r.text().lower()
    with_nodes = "given" in node_ids or "ignore" in node_ids
    with_elements = "given" in element_ids or "ignore" in element_ids
    keyword = r.text()
    if keyword.lower() == "extents":
        r.floats(6)
        keyword = r.text()
    parts = []
    while keyword.lower() == "part":
        r.first_part_number()
        number = int(r.ints(1)[0])
        name = r.text()
        keyword = r.text().lower()
        if keyword == "block":
            raise ReaderError(f"{path.name}: structured (block) EnSight parts can't be read yet")
        if keyword != "coordinates":
            raise ReaderError(f"{path.name}: part {number} has no coordinates")
        n = int(r.ints(1)[0])
        if with_nodes:
            r.ints(n)
        xyz = np.column_stack([r.floats(n), r.floats(n), r.floats(n)])
        blocks = []
        keyword = "" if r.done else r.text()
        while keyword and keyword.lower() != "part":
            kind = keyword.lower()
            if kind.startswith("g_"):
                raise ReaderError(f"{path.name}: ghost elements ({kind}) can't be read yet")
            count = int(r.ints(1)[0])
            if with_elements:
                r.ints(count)
            if kind in ENSIGHT_NODES:
                conn = r.ints(count * ENSIGHT_NODES[kind]).reshape(count, -1) - 1
                centres = xyz[conn[:, : ENSIGHT_CORNERS[kind]]].mean(axis=1)
            elif kind == "nsided":
                per = r.ints(count)
                conn = r.ints(int(per.sum())) - 1
                centres = np.add.reduceat(xyz[conn], np.r_[0, np.cumsum(per)[:-1]]) / per[:, None]
            elif kind == "nfaced":
                faces = r.ints(count)
                per_face = r.ints(int(faces.sum()))
                conn = r.ints(int(per_face.sum())) - 1
                face_start = np.r_[0, np.cumsum(per_face)]
                cell_start = np.r_[0, np.cumsum(faces)]
                centres = np.array([
                    xyz[np.unique(conn[face_start[cell_start[c]] : face_start[cell_start[c + 1]]])]
                    .mean(axis=0) for c in range(count)])  # fmt: skip
            else:
                raise ReaderError(f"{path.name}: EnSight element type '{kind}' isn't read yet")
            blocks.append(_Block(kind, count, centres.reshape(count, 3)))
            keyword = "" if r.done else r.text()
        parts.append(_Part(number, name, xyz, blocks, r.binary))
    if not parts:
        raise ReaderError(f"{path.name} holds no EnSight parts")
    return parts


def _sniff_ensight(path: Path) -> Offer:
    case = _ensight_case(path)
    columns = []
    sets = {v.time_set for v in case.variables.values()}
    for name, v in case.variables.items():
        if v.location == "element" and any(
            c.name in _components(name, ENSIGHT_WIDTH[v.kind]) for c in columns
        ):
            continue
        columns += [Column(c, v.location) for c in _components(name, ENSIGHT_WIDTH[v.kind])]
    ts = next((s for s in sets if s is not None), None)
    times, _ = case.sets.get(ts, ([], []))
    steps = [Step(float(t), str(k)) for k, t in enumerate(times, 1)]
    notes = ["variables on different time sets: the first set's times are listed"] if len(
        {s for s in sets if s is not None}) > 1 else []  # fmt: skip
    parts = _ensight_geometry(case.file(case.geometry, case.geometry_set, 0))
    return Offer(columns, steps, [p.name for p in parts], notes)


def _ensight_variable(path: Path, v: _Variable, parts: list[_Part]) -> dict[int, np.ndarray]:
    """Part number -> its values (n, width) for one variable file."""
    width = ENSIGHT_WIDTH[v.kind]
    by_number = {p.number: p for p in parts}
    r = _Bytes(path, parts[0].binary)
    r.text()  # description
    out = {}
    keyword = "" if r.done else r.text()
    while keyword.lower() == "part":
        r.first_part_number()
        number = int(r.ints(1)[0])
        part = by_number.get(number)
        if part is None:
            raise ReaderError(f"{path.name}: part {number} is not in the geometry")
        if v.location == "node":
            keyword = r.text().lower()
            if keyword != "coordinates":
                raise ReaderError(f"{path.name}: '{keyword}' node values (partial or "
                                  "undefined) can't be read yet")  # fmt: skip
            n = len(part.coords)
            out[number] = r.floats(n * width).reshape(width, n).T
        else:
            chunks = []
            for b in part.blocks:
                kind = r.text().lower()
                if kind != b.kind:
                    raise ReaderError(f"{path.name}: part {number} lists '{kind}' values, "
                                      f"its geometry '{b.kind}' (partial or undefined values "
                                      "can't be read yet)")  # fmt: skip
                chunks.append(r.floats(b.count * width).reshape(width, b.count).T)
            out[number] = np.vstack(chunks) if chunks else np.zeros((0, width))
        keyword = "" if r.done else r.text()
    return out


def _read_ensight(path: Path, names, region, index):
    case = _ensight_case(path)
    offer = _sniff_ensight(path)
    location = _one_location(offer, names, path.name)
    owners = {}  # column -> (variable, component position in Puzzle's order)
    for name, v in case.variables.items():
        for k, c in enumerate(_components(name, ENSIGHT_WIDTH[v.kind])):
            if v.location == location:
                owners.setdefault(c, (name, k))
    used = {owners[n][0] for n in names}
    sets = {case.variables[u].time_set for u in used}
    if len(sets) > 1:
        raise ReaderError(f"{path.name}: {', '.join(sorted(used))} are on different time sets; "
                          "import them as separate fields")  # fmt: skip
    ts = sets.pop()
    times = case.sets.get(ts, ([], []))[0]
    steps = [Step(float(t), str(k)) for k, t in enumerate(times, 1)]
    position = _step_position(steps, index, path.name)
    geo_position = position if case.geometry_set is not None else 0
    parts = _ensight_geometry(case.file(case.geometry, case.geometry_set, geo_position))
    if region:
        chosen = [p for p in parts if p.name == region]
        if not chosen:
            have = ", ".join(p.name for p in parts)
            raise ReaderError(f"{path.name} has no part '{region}' ({have})", code="not_found")
    else:  # the volume parts: a boundary part repeats its cells' values at the faces
        chosen = [p for p in parts if p.volume] or parts
    read = {u: _ensight_variable(case.file(case.variables[u].pattern, ts, position),
                                 case.variables[u], parts) for u in used}  # fmt: skip
    points, values = [], {n: [] for n in names}
    for p in chosen:
        if not all(p.number in read[u] for u in used):
            continue  # a part a variable leaves out
        xyz = p.coords if location == "node" else (
            np.vstack([b.centres for b in p.blocks]) if p.blocks else np.zeros((0, 3)))  # fmt: skip
        points.append(xyz)
        for n in names:
            var, k = owners[n]
            data = read[var][p.number]
            if case.variables[var].kind == "tensor symm":
                data = data[:, ENSIGHT_SYMMETRIC]
            values[n].append(data[:, k])
    if not points:
        raise ReaderError(f"{path.name}: no part carries {', '.join(names)}")
    return np.vstack(points), {n: np.concatenate(v) for n, v in values.items()}


# --- OpenFOAM -------------------------------------------------------------------------------

FOAM_WIDTH = {"volScalarField": 1, "volVectorField": 3, "volSymmTensorField": 6,
              "volTensorField": 9}  # fmt: skip
FOAM_SYMMETRIC = [0, 3, 5, 1, 4, 2]  # OpenFOAM's xx xy xz yy yz zz -> Puzzle's order


def openfoam_case(path: Path) -> Path | None:
    """The case folder holding `path` (a folder with constant/polyMesh), or None."""
    path = Path(path).resolve()
    for folder in [path, *path.parents][:4]:
        if (folder / "constant" / "polyMesh" / "points").exists() or (
            folder / "constant" / "polyMesh" / "points.gz"
        ).exists():
            return folder
    return None


def _foam_times(case: Path) -> list[tuple[float, Path]]:
    out = []
    for d in case.iterdir():
        try:
            t = float(d.name)
        except ValueError:
            continue
        if d.is_dir():
            out.append((t, d))
    return sorted(out)


def _foam_text(path: Path) -> str:
    if not path.exists() and path.with_name(path.name + ".gz").exists():
        path = path.with_name(path.name + ".gz")
    raw = path.read_bytes()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    text = raw.decode("latin-1")
    head = re.search(r"FoamFile\s*\{(.*?)\}", text, re.S)
    if head and re.search(r"\bformat\s+binary\s*;", head.group(1)):
        raise ReaderError(f"{path.name} is written in binary; rerun with writeFormat ascii "
                          "(or foamFormatConvert) to import it")  # fmt: skip
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return re.sub(r"//[^\n]*", " ", text)


def _foam_class(text: str) -> str:
    m = re.search(r"FoamFile\s*\{[^}]*\bclass\s+(\w+)\s*;", text, re.S)
    return m.group(1) if m else ""


def _foam_body(text: str) -> str:
    m = re.search(r"FoamFile\s*\{.*?\}", text, re.S)
    return text[m.end() :] if m else text


def _foam_list(text: str, start: int = 0) -> tuple[str, int, int]:
    """The first `N ( ... )` or `N{v}` list from `start`: its inside, N, where it ends."""
    m = re.compile(r"(\d+)\s*([({])").search(text, start)
    if m is None:
        raise ReaderError("an OpenFOAM list was expected")
    n, opening = int(m.group(1)), m.group(2)
    depth, pos = 0, m.end() - 1
    close = ")" if opening == "(" else "}"
    while True:
        c = text[pos]
        if c == opening:
            depth += 1
        elif c == close:
            depth -= 1
            if depth == 0:
                break
        pos += 1
    inner = text[m.end() : pos]
    if opening == "{":  # N{value}: N copies of one value
        inner = " ".join([inner] * n)
    return inner, n, pos + 1


def _numbers(inner: str) -> np.ndarray:
    return np.array(inner.replace("(", " ").replace(")", " ").split(), dtype=float)


def _poly_mesh(case: Path):
    """Points, faces (lists of point indices), owner, neighbour, and the patches
    {name: (start face, faces)}."""
    mesh = case / "constant" / "polyMesh"
    inner, n, _ = _foam_list(_foam_body(_foam_text(mesh / "points")))
    points = _numbers(inner).reshape(n, 3)
    text = _foam_text(mesh / "faces")
    body = _foam_body(text)
    if _foam_class(text) == "faceCompactList":
        inner, _, end = _foam_list(body)
        offsets = _numbers(inner).astype(np.int64)
        inner, _, _ = _foam_list(body, end)
        labels = _numbers(inner).astype(np.int64)
        faces = [labels[a:b] for a, b in zip(offsets[:-1], offsets[1:], strict=True)]
    else:
        inner, n, _ = _foam_list(body)
        flat = np.array(inner.replace("(", " ").replace(")", " ").split(), dtype=np.int64)
        faces, pos = [], 0
        while pos < len(flat):
            k = int(flat[pos])
            faces.append(flat[pos + 1 : pos + 1 + k])
            pos += 1 + k
    owner = _numbers(_foam_list(_foam_body(_foam_text(mesh / "owner")))[0]).astype(np.int64)
    neighbour = _numbers(_foam_list(_foam_body(_foam_text(mesh / "neighbour")))[0]).astype(np.int64)
    patches = {}
    body = _foam_body(_foam_text(mesh / "boundary"))
    for m in re.finditer(r"([\w.:\-]+)\s*\{([^{}]*)\}", body):
        start = re.search(r"startFace\s+(\d+)", m.group(2))
        count = re.search(r"nFaces\s+(\d+)", m.group(2))
        if start and count:
            patches[m.group(1)] = (int(start.group(1)), int(count.group(1)))
    return points, faces, owner, neighbour, patches


def _face_geometry(points: np.ndarray, faces: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """Face centres and area vectors, as OpenFOAM computes them: triangles about the
    points' mean, each weighted by its area."""
    centres, areas = np.zeros((len(faces), 3)), np.zeros((len(faces), 3))
    sizes = np.array([len(f) for f in faces])
    for k in np.unique(sizes):
        rows = np.flatnonzero(sizes == k)
        idx = np.array([faces[r] for r in rows])
        p = points[idx]  # (m, k, 3)
        if k == 3:
            centres[rows] = p.mean(axis=1)
            areas[rows] = 0.5 * np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
            continue
        mean = p.mean(axis=1)
        nxt = np.roll(p, -1, axis=1)
        c = p + nxt + mean[:, None, :]  # 3 x each triangle's centre
        n = np.cross(nxt - p, mean[:, None, :] - p)  # 2 x its area vector
        total = n.sum(axis=1)
        unit = total / np.maximum(np.linalg.norm(total, axis=1), 1e-300)[:, None]
        a = np.einsum("mkj,mj->mk", n, unit)
        weight = np.maximum(a.sum(axis=1), 1e-300)
        centres[rows] = (a[:, :, None] * c).sum(axis=1) / (3 * weight[:, None])
        areas[rows] = 0.5 * total
    return centres, areas


def _cell_centres(points, faces, owner, neighbour) -> np.ndarray:
    """OpenFOAM's cell centres: pyramids on each face from the face centres' mean, their
    centroids weighted by their volumes."""
    fc, fa = _face_geometry(points, faces)
    cells = int(max(owner.max(), neighbour.max() if len(neighbour) else -1)) + 1
    inner = np.arange(len(neighbour))
    count = np.bincount(owner, minlength=cells) + np.bincount(neighbour, minlength=cells)
    estimate = np.zeros((cells, 3))
    np.add.at(estimate, owner, fc)
    np.add.at(estimate, neighbour, fc[inner])
    estimate /= count[:, None]
    volume, centre = np.zeros(cells), np.zeros((cells, 3))
    for cell, face, sign in ((owner, np.arange(len(owner)), 1.0), (neighbour, inner, -1.0)):
        v = sign * np.einsum("ij,ij->i", fa[face], fc[face] - estimate[cell])
        v = np.maximum(v, 1e-300)
        np.add.at(volume, cell, v)
        np.add.at(centre, cell, v[:, None] * (0.75 * fc[face] + 0.25 * estimate[cell]))
    return centre / volume[:, None]


def _foam_field(text: str, cells: int, patches, owner, region: str, path: Path) -> np.ndarray:
    """A field file's values on the internal cells or on one patch: (n, width)."""
    width = FOAM_WIDTH[_foam_class(text)]
    body = _foam_body(text)

    def parse(entry: str, at: int, count: int) -> np.ndarray:
        rest = body[at:]
        m = re.match(r"\s*uniform\s+([^;]+);", rest)
        if m:
            return np.tile(_numbers(m.group(1)), (count, 1))
        m = re.match(r"\s*nonuniform\s+List<\w+>\s*", rest)
        if m is None:
            raise ReaderError(f"{path.name}: can't read its {entry}")
        inner, n, _ = _foam_list(body, at + m.end())
        if n != count:
            raise ReaderError(f"{path.name}: its {entry} holds {n} values, not {count}")
        return _numbers(inner).reshape(n, width) if n else np.zeros((0, width))

    internal = body.index("internalField") + len("internalField")
    cell_values = parse("internalField", internal, cells)
    if not region:
        return cell_values
    start, count = patches[region]
    boundary = body.index("boundaryField")
    for m in re.finditer(r'"?([^\s{}";]+)"?\s*\{', body[boundary:]):
        key = m.group(1)
        if key != region and not (key != "boundaryField" and _foam_matches(key, region)):
            continue
        block_start = boundary + m.end()
        depth, pos = 1, block_start
        while depth:
            depth += {"{": 1, "}": -1}.get(body[pos], 0)
            pos += 1
        found = re.search(r"\bvalue\s+", body[block_start:pos])
        if found:
            return parse(f"value on {region}", block_start + found.end(), count)
        break
    return cell_values[owner[start : start + count]]  # no value written: its cells'


def _foam_matches(key: str, name: str) -> bool:
    try:
        return re.fullmatch(key, name) is not None
    except re.error:
        return False


def _foam_fields(folder: Path) -> dict[str, int]:
    out = {}
    for p in sorted(folder.iterdir()):
        if not p.is_file():
            continue
        try:
            with open(p, "rb") as handle:
                head = handle.read(2048)
            if head[:2] == b"\x1f\x8b":
                head = gzip.decompress(p.read_bytes())[:2048]
        except OSError:
            continue
        m = re.search(rb"\bclass\s+(\w+)\s*;", head)
        if m and m.group(1).decode() in FOAM_WIDTH:
            out[p.name.removesuffix(".gz")] = FOAM_WIDTH[m.group(1).decode()]
    return out


def _sniff_foam(path: Path) -> Offer:
    case = openfoam_case(path)
    if case is None:
        raise ReaderError(f"{Path(path).name} is not in an OpenFOAM case (no constant/polyMesh)")
    times = _foam_times(case)
    if not times:
        raise ReaderError(f"{case.name} has no time directories")
    names = {}
    for _, d in times:
        names.update(_foam_fields(d))
    names.pop("C", None)  # cell centres: where the values sit, not a value
    columns = [Column(c, "element") for n, w in names.items() for c in _components(n, w)]
    body = _foam_body(_foam_text(case / "constant" / "polyMesh" / "boundary"))
    patches = [m.group(1) for m in re.finditer(r"([\w.:\-]+)\s*\{[^{}]*nFaces", body)]
    note = "internal values at the cell centres; a patch's at its face centres"
    return Offer(columns, [Step(t, d.name) for t, d in times], patches, [note])


def _read_foam(path: Path, names, region, index):
    case = openfoam_case(path)
    offer = _sniff_foam(path)
    _one_location(offer, names, case.name)
    times = _foam_times(case)
    folder = times[_step_position(offer.steps, index, case.name)][1]
    points, faces, owner, neighbour, patches = _poly_mesh(case)
    if region and region not in patches:
        raise ReaderError(f"{case.name} has no patch '{region}' ({', '.join(patches)})",
                          code="not_found")  # fmt: skip
    cells = int(owner.max()) + 1
    written = _foam_fields(folder)
    if "C" in written:  # the cell (and patch face) centres OpenFOAM wrote: exact
        centres = _foam_field(_foam_text(folder / "C"), cells, patches, owner, region, folder / "C")
    elif region:
        start, count = patches[region]
        centres = _face_geometry(points, faces[start : start + count])[0]
    else:
        centres = _cell_centres(points, faces, owner, neighbour)
    values = {}
    for n in names:
        base, k = next(
            (b, _components(b, w).index(n)) for b, w in written.items() if n in _components(b, w)
        ) if any(n in _components(b, w) for b, w in written.items()) else (None, 0)  # fmt: skip
        if base is None:
            raise ReaderError(f"time {folder.name} of {case.name} has no {n}", code="not_found")
        data = _foam_field(_foam_text(folder / base), cells, patches, owner, region, folder / base)
        if written[base] == 6:
            data = data[:, FOAM_SYMMETRIC]
        values[n] = data[:, k]
    return centres, values
