"""The results store: Delta Lake tables of Parquet files (ADR 0008).

Layout under the store root (normally `<project>/results`):

| Table | One row per | Partitioned by |
| --- | --- | --- |
| runs | solver run | - |
| meshes | distinct mesh (content hash) | - |
| nodes | mesh: node ids and coordinates as arrays | mesh_id |
| cells | element block: element ids and connectivity as arrays | mesh_id |
| groups | named group of a mesh | - |
| fields | field at one result step: entity ids and values as arrays | run_id, field |

Arrays are Parquet lists (format 2, ADR 0008): one row per field and step, values
flattened row-major (entities x components). A field's ids are stored only when they
differ from its previous step's; `ids_from` names the step that holds them. Each fields
row is its own row group, so reading one step touches only that step's pages. Against
one row per entity (format 1) this made a 100-step, 100k-node transient 40x faster to
read whole and 4x faster to open, and a quarter smaller.

A mesh is stored once however many runs use it. A run becomes visible only when its
`runs` row is committed, which happens last, so a crash mid-write never leaves a half
run listed. `store.json` records the format; an older store is refused (`stale_store`).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
from deltalake import DeltaTable, WriterProperties, write_deltalake

from puzzle.errors import PuzzleError
from puzzle.mesh.mesh import CellBlock, Group, Mesh
from puzzle.results.model import Field, Results, StepResult

FORMAT = 2
INTS = pa.large_list(pa.int64())
FLOATS = pa.large_list(pa.float64())

SCHEMAS = {
    "runs": pa.schema(
        [
            ("run_id", pa.string()),
            ("run_key", pa.string()),
            ("study_id", pa.string()),
            ("study_name", pa.string()),
            ("solver", pa.string()),
            ("runtime", pa.string()),
            ("mesh_id", pa.string()),
            ("status", pa.string()),
            ("started_at", pa.timestamp("us", tz="UTC")),
            ("duration_s", pa.float64()),
            ("steps", pa.int32()),
            ("recorded_at", pa.timestamp("us", tz="UTC")),
        ]
    ),
    "meshes": pa.schema(
        [
            ("mesh_id", pa.string()),
            ("node_count", pa.int64()),
            ("element_count", pa.int64()),
            ("created_at", pa.timestamp("us", tz="UTC")),
        ]
    ),
    "nodes": pa.schema([("mesh_id", pa.string()), ("node_ids", INTS), ("coords", FLOATS)]),
    "cells": pa.schema(
        [
            ("mesh_id", pa.string()),
            ("block", pa.int32()),
            ("element", pa.string()),
            ("dim", pa.int8()),
            ("entity_dim", pa.int8()),
            ("entity_tag", pa.int32()),
            ("nodes_per_element", pa.int32()),
            ("element_ids", INTS),
            ("connectivity", INTS),
        ]
    ),
    "groups": pa.schema(
        [
            ("mesh_id", pa.string()),
            ("name", pa.string()),
            ("dim", pa.int8()),
            ("entity_tags", pa.list_(pa.int32())),
            ("node_ids", INTS),
        ]
    ),
    "fields": pa.schema(
        [
            ("run_id", pa.string()),
            ("field", pa.string()),
            ("ordinal", pa.int32()),  # position among the run's result steps, from 0
            ("step", pa.int32()),
            ("mode", pa.int32()),
            ("kind", pa.string()),
            ("value", pa.float64()),
            ("location", pa.string()),
            ("components", pa.string()),
            ("count", pa.int64()),
            ("ids_from", pa.int32()),  # the ordinal whose row holds this field's ids
            ("ids", INTS),  # null when the same as at `ids_from`
            ("values", FLOATS),
        ]
    ),
}
PARTITIONS = {"nodes": ["mesh_id"], "cells": ["mesh_id"], "fields": ["run_id", "field"]}
# zstd (ADR 0008): about a fifth smaller than Delta's default Snappy for result stores.
WRITER = WriterProperties(compression="ZSTD", compression_level=3)
# A row group per fields row (one field at one step): a step's read prunes the others.
FIELD_WRITER = WriterProperties(compression="ZSTD", compression_level=3, max_row_group_size=1)
WRITERS = {"fields": FIELD_WRITER}


class StaleStore(PuzzleError):
    code = "stale_store"


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    run_key: str
    study_id: str
    study_name: str
    solver: str
    runtime: str
    mesh_id: str
    status: str
    started_at: datetime
    duration_s: float
    steps: int
    recorded_at: datetime


def mesh_id(mesh: Mesh) -> str:
    """A content hash of the mesh, so identical meshes are stored once."""
    h = hashlib.sha256()
    h.update(mesh.node_ids.tobytes())
    h.update(np.ascontiguousarray(mesh.coords).tobytes())
    for b in mesh.blocks:
        h.update(f"{b.element}|{b.entity}".encode())
        h.update(b.ids.tobytes())
        h.update(np.ascontiguousarray(b.connectivity).tobytes())
    for name in sorted(mesh.groups):
        g = mesh.groups[name]
        h.update(f"{name}|{g.dim}|{g.entities}".encode())
        h.update(g.node_ids.tobytes())
    return h.hexdigest()[:32]


class ResultStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    # --- writing ---------------------------------------------------------------

    def write_run(
        self,
        *,
        run_key: str,
        study_id: str,
        study_name: str,
        solver: str,
        runtime: str,
        mesh: Mesh,
        results: Results,
        started_at: datetime,
        duration_s: float,
    ) -> str:
        """Store a completed run and its mesh; return the new run id."""
        run_id = uuid.uuid4().hex
        self._check_format(create=True)
        mid = self.write_mesh(mesh)
        fields = _fields_table(run_id, results)
        if fields.num_rows:
            self._append("fields", fields)
        record = {
            "run_id": run_id,
            "run_key": run_key,
            "study_id": study_id,
            "study_name": study_name,
            "solver": solver,
            "runtime": runtime,
            "mesh_id": mid,
            "status": "completed",
            "started_at": started_at,
            "duration_s": duration_s,
            "steps": len(results.steps),
            "recorded_at": _now(),
        }
        self._append("runs", pa.Table.from_pylist([record], schema=SCHEMAS["runs"]))
        return run_id

    def write_mesh(self, mesh: Mesh) -> str:
        self._check_format(create=True)
        mid = mesh_id(mesh)
        if self._exists("meshes") and self._rows("meshes", pc.field("mesh_id") == mid).num_rows:
            return mid
        n = len(mesh.node_ids)
        nodes = {"mesh_id": [mid], "node_ids": _list(mesh.node_ids), "coords": _list(mesh.coords)}
        self._append("nodes", pa.table(nodes, schema=SCHEMAS["nodes"]))
        cells = [_cells_table(mid, i, b) for i, b in enumerate(mesh.blocks)]
        if cells:
            self._append("cells", pa.concat_tables(cells))
        groups = [
            {
                "mesh_id": mid,
                "name": g.name,
                "dim": g.dim,
                "entity_tags": [t for _, t in g.entities],
                "node_ids": g.node_ids.tolist(),
            }
            for g in mesh.groups.values()
        ]
        if groups:
            self._append("groups", pa.Table.from_pylist(groups, schema=SCHEMAS["groups"]))
        count = sum(len(b.ids) for b in mesh.blocks)
        meta = {"mesh_id": mid, "node_count": n, "element_count": count, "created_at": _now()}
        self._append("meshes", pa.Table.from_pylist([meta], schema=SCHEMAS["meshes"]))
        return mid

    # --- reading ---------------------------------------------------------------

    def runs(self) -> list[RunRecord]:
        self._check_format()
        if not self._exists("runs"):
            return []
        rows = self._rows("runs").sort_by([("recorded_at", "ascending")]).to_pylist()
        return [RunRecord(**r) for r in rows]

    def find_run(self, run_key: str) -> RunRecord | None:
        """The latest completed run with this key: a cache hit for an unchanged study."""
        hits = [r for r in self.runs() if r.run_key == run_key and r.status == "completed"]
        return hits[-1] if hits else None

    def get_run(self, run_id: str) -> RunRecord:
        for r in self.runs():
            if r.run_id == run_id:
                return r
        raise KeyError(f"no run {run_id}")

    def read_results(self, run_id: str) -> Results:
        record = self.get_run(run_id)
        table = self._rows("fields", pc.field("run_id") == run_id)
        results = Results(solver=record.solver)
        steps: dict[int, StepResult] = {}
        ids: dict[tuple[str, int], np.ndarray] = {}  # (field, ordinal) -> ids stored there
        # Sort the small metadata only; sorting the table would copy every array.
        meta = table.drop_columns(["ids", "values"]).to_pylist()
        id_arrays, value_arrays = _Lists(table, "ids"), _Lists(table, "values")
        for i in sorted(range(len(meta)), key=lambda i: (meta[i]["ordinal"], meta[i]["field"])):
            row = meta[i]
            if row["ordinal"] not in steps:
                steps[row["ordinal"]] = StepResult(
                    index=row["step"], kind=row["kind"], value=row["value"], mode=row["mode"]
                )
            if row["ids_from"] == row["ordinal"]:
                ids[row["field"], row["ordinal"]] = id_arrays[i]
            steps[row["ordinal"]].fields[row["field"]] = _field(
                row, ids[row["field"], row["ids_from"]], value_arrays[i]
            )
        results.steps = [steps[k] for k in sorted(steps)]
        return results

    def read_field(self, run_id: str, field: str, step: int | None = None) -> Field:
        """One field at one result step (default: the last), read without the others.

        `step` counts the field's stored result steps (increments, times, modes) from
        0; -1 is the last. Partitions prune the files to this run and field, and row
        group statistics on `ordinal` to the one step: what a viewer needs for a frame.
        """
        where = (pc.field("run_id") == run_id) & (pc.field("field") == field)
        index = self._rows("fields", where, columns=["ordinal", "ids_from"]).sort_by("ordinal")
        if not index.num_rows:
            raise KeyError(f"run {run_id} has no '{field}' field")
        at = index.to_pylist()[-1 if step is None else step]
        table = self._rows("fields", where & (pc.field("ordinal") == at["ordinal"]))
        meta = table.drop_columns(["ids", "values"]).to_pylist()[0]
        if at["ids_from"] == at["ordinal"]:
            ids = _array(table, "ids", 0)
        else:
            held = self._rows(
                "fields", where & (pc.field("ordinal") == at["ids_from"]), columns=["ids"]
            )
            ids = _array(held, "ids", 0)
        return _field(meta, ids, _array(table, "values", 0))

    def read_mesh(self, mid: str) -> Mesh:
        self._check_format()
        nodes = self._rows("nodes", pc.field("mesh_id") == mid)
        if not nodes.num_rows:
            raise KeyError(f"no mesh {mid}")
        mesh = Mesh(
            node_ids=_array(nodes, "node_ids", 0), coords=_array(nodes, "coords", 0).reshape(-1, 3)
        )
        cells = self._rows("cells", pc.field("mesh_id") == mid).sort_by("block")
        for i, row in enumerate(cells.drop_columns(["element_ids", "connectivity"]).to_pylist()):
            mesh.blocks.append(
                CellBlock(
                    element=row["element"],
                    dim=row["dim"],
                    entity=(row["entity_dim"], row["entity_tag"]),
                    ids=_array(cells, "element_ids", i),
                    connectivity=_array(cells, "connectivity", i).reshape(
                        -1, row["nodes_per_element"]
                    ),
                )
            )
        for g in self._rows("groups", pc.field("mesh_id") == mid).to_pylist():
            mesh.groups[g["name"]] = Group(
                name=g["name"],
                dim=g["dim"],
                entities=[(g["dim"], t) for t in g["entity_tags"]],
                node_ids=np.asarray(g["node_ids"], dtype=np.int64),
            )
        return mesh

    # --- maintenance -----------------------------------------------------------

    def compact(self) -> None:
        """Merge small Parquet files (each run appends a few) into larger ones."""
        for name in SCHEMAS:
            if self._exists(name):
                DeltaTable(str(self.root / name)).optimize.compact(
                    writer_properties=WRITERS.get(name, WRITER)
                )

    # --- internals ---------------------------------------------------------------

    def _exists(self, name: str) -> bool:
        return (self.root / name / "_delta_log").is_dir()

    def _check_format(self, create: bool = False) -> None:
        """Refuse a store in another format; on a write, mark a new one with this format."""
        marker = self.root / "store.json"
        if marker.exists():
            found = json.loads(marker.read_text()).get("format")
        elif any(self._exists(name) for name in SCHEMAS):
            found = 1  # format 1 stores predate the marker
        else:
            if not create:
                return
            self.root.mkdir(parents=True, exist_ok=True)
            marker.write_text(json.dumps({"format": FORMAT}) + "\n")
            return
        if found != FORMAT:
            raise StaleStore(
                f"the results store at {self.root} has format {found}; this Puzzle reads "
                f"format {FORMAT}. Delete the folder and rerun the studies to rebuild it",
                path=str(self.root),
                details={"found": found, "expected": FORMAT},
            )

    def _append(self, name: str, table: pa.Table) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        write_deltalake(
            str(self.root / name),
            table.cast(SCHEMAS[name]),
            mode="append",
            partition_by=PARTITIONS.get(name),
            writer_properties=WRITERS.get(name, WRITER),
        )

    def _rows(self, name: str, filter=None, columns: list[str] | None = None) -> pa.Table:
        if not self._exists(name):
            return SCHEMAS[name].empty_table().select(columns or SCHEMAS[name].names)
        self._check_format()
        dataset = DeltaTable(str(self.root / name)).to_pyarrow_dataset()
        return dataset.to_table(filter=filter, columns=columns)


def _list(values: np.ndarray) -> pa.Array:
    """One list cell holding a whole array, flattened row-major, without copying it twice."""
    integer = np.issubdtype(np.asarray(values).dtype, np.integer)
    flat = np.ascontiguousarray(values, np.int64 if integer else np.float64).ravel()
    offsets = pa.array([0, len(flat)], pa.int64())
    return pa.LargeListArray.from_arrays(offsets, pa.array(flat))


def _array(table: pa.Table, column: str, row: int) -> np.ndarray:
    """The array in one list cell, as numpy."""
    return table.column(column)[row].values.to_numpy(zero_copy_only=False)


class _Lists:
    """The arrays of a list column as numpy views: one flatten, then offsets, no copies."""

    def __init__(self, table: pa.Table, column: str) -> None:
        lists = table.column(column).combine_chunks()
        self.flat = lists.values.to_numpy(zero_copy_only=False)
        self.offsets = lists.offsets.to_numpy()

    def __getitem__(self, row: int) -> np.ndarray:
        return self.flat[self.offsets[row] : self.offsets[row + 1]]


def _field(row: dict, ids: np.ndarray, flat: np.ndarray) -> Field:
    components = row["components"].split(",")
    return Field(
        name=row["field"],
        location=row["location"],
        components=components,
        ids=ids,
        values=flat.reshape(row["count"], len(components)),
    )


def _cells_table(mid: str, index: int, b: CellBlock) -> pa.Table:
    return pa.table(
        {
            "mesh_id": [mid],
            "block": pa.array([index], pa.int32()),
            "element": [b.element],
            "dim": pa.array([b.dim], pa.int8()),
            "entity_dim": pa.array([b.entity[0]], pa.int8()),
            "entity_tag": pa.array([b.entity[1]], pa.int32()),
            "nodes_per_element": pa.array([b.connectivity.shape[1]], pa.int32()),
            "element_ids": _list(b.ids),
            "connectivity": _list(b.connectivity),
        },
        schema=SCHEMAS["cells"],
    )


def _fields_table(run_id: str, results: Results) -> pa.Table:
    """One row per field per result step, in the order Results keeps its steps."""
    rows: dict[str, list] = {name: [] for name in SCHEMAS["fields"].names}
    ids_list, values_list = [], []
    held: dict[str, tuple[int, np.ndarray]] = {}  # field -> (ordinal, ids) last stored
    ordered = sorted(results.steps, key=lambda s: (s.index, s.mode or 0, s.value))
    for ordinal, step in enumerate(ordered):
        for f in step.fields.values():
            same = f.name in held and np.array_equal(held[f.name][1], f.ids)
            if not same:
                held[f.name] = (ordinal, f.ids)
            for key, value in (
                ("run_id", run_id),
                ("field", f.name),
                ("ordinal", ordinal),
                ("step", step.index),
                ("mode", step.mode),
                ("kind", step.kind),
                ("value", step.value),
                ("location", f.location),
                ("components", ",".join(f.components)),
                ("count", len(f.ids)),
                ("ids_from", held[f.name][0]),
            ):
                rows[key].append(value)
            ids_list.append(None if same else np.asarray(f.ids, np.int64))
            values_list.append(np.ascontiguousarray(f.values, np.float64).ravel())
    if not values_list:
        return SCHEMAS["fields"].empty_table()
    columns = {
        k: pa.array(v, SCHEMAS["fields"].field(k).type)
        for k, v in rows.items()
        if k not in ("ids", "values")
    }
    columns["ids"] = _lists(ids_list, pa.int64())
    columns["values"] = _lists(values_list, pa.float64())
    return pa.table(columns, schema=SCHEMAS["fields"])


def _lists(arrays: list, typ: pa.DataType) -> pa.Array:
    """A list column from numpy arrays (None = a null cell), built without Python loops."""
    lengths = [0 if a is None else len(a) for a in arrays]
    offsets = pa.array(np.concatenate([[0], np.cumsum(lengths)]).astype(np.int64))
    present = [a for a in arrays if a is not None]
    flat = pa.array(np.concatenate(present) if present else np.empty(0), typ)
    mask = pa.array([a is None for a in arrays])
    return pa.LargeListArray.from_arrays(offsets, flat, mask=mask)


def _now() -> datetime:
    return datetime.now(UTC)
