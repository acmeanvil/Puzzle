"""The results store: Delta Lake tables of Parquet files (ADR 0008).

Layout under the store root (normally `<project>/results`):

| Table | One row per | Partitioned by |
| --- | --- | --- |
| runs | solver run | - |
| meshes | distinct mesh (content hash) | - |
| nodes | mesh: node ids and coordinates as arrays | mesh_id |
| cells | element block: element ids and connectivity as arrays | mesh_id |
| groups | named group of a mesh | - |
| fields (folder `fields3`) | field at one result step: entity ids and values as arrays | run_id |

Arrays are Parquet lists (format 2, ADR 0008): one row per field and step, values
flattened row-major (entities x components). A field's ids are stored only when they
differ from its previous step's; `ids_from` names the step that holds them. Each fields
row is its own row group, so reading one step touches only that step's pages. Against
one row per entity (format 1) this made a 100-step, 100k-node transient 40x faster to
read whole and 4x faster to open, and a quarter smaller.

Format 3 (ADR 0090 package 14) partitions the fields by run alone: a run's fields are
one Parquet file, field after field, a row group per row as before, so 120 runs are 120
files, not 600 that compaction could never merge. Reads open a run's file footer once
(`_run_index`, kept while its files are the same) and then only the row groups they
need. A format 2 store is migrated in place (`ResultStore.upgrade`): the new table is
written beside the old one, in its own folder, and `store.json` is switched last, so an
interruption leaves a format 2 store that migrates again. Format 1 is refused
(`stale_store`), and so is a format newer than this code knows (`store_too_new`).

A mesh is stored once however many runs use it. A run becomes visible only when its
`runs` row is committed, which happens last, so a crash mid-write never leaves a half
run listed.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
from deltalake import ColumnProperties, DeltaTable, WriterProperties, write_deltalake

from puzzle import __version__
from puzzle.errors import PuzzleError
from puzzle.mesh.mesh import CellBlock, Group, Mesh
from puzzle.results.model import Field, Results, StepResult

FORMAT = 3
_MARKER = json.dumps({"format": FORMAT}) + "\n"  # store.json's text
MIGRATED = (2,)  # older formats this code migrates in place; format 1 is refused
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
            # The sweep point as JSON (ADR 0018), e.g. {"Bar/Body": "Copper"}; added to
            # older stores by schema merge, where earlier runs read it as null.
            ("point", pa.string()),
            # Which solver ran (ADR 0072): its version, and a container's image id;
            # null for runs stored before, or a solver that couldn't be named.
            ("solver_version", pa.string()),
            ("image_digest", pa.string()),
            # The Puzzle that wrote it (ADR 0090): run keys leave versions out, so an
            # upgrade keeps results current; this says what made them.
            ("puzzle_version", pa.string()),
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
    # A run's progress events as JSON lines (ADR 0054): how it converged. Stores made
    # before it simply have no history for their runs.
    "progress": pa.schema([("run_id", pa.string()), ("events", pa.large_string())]),
}
PARTITIONS = {"nodes": ["mesh_id"], "cells": ["mesh_id"], "fields": ["run_id"]}
# Format 3's fields table has a folder of its own, so the migration writes it beside
# format 2's `fields` and nothing is renamed (Windows refuses to rename a folder while
# any reader holds a file in it). Other tables are where they always were.
FOLDERS = {"fields": "fields3"}
FIELD_SCALARS = [n for n in SCHEMAS["fields"].names if n not in ("run_id", "ids", "values")]
# zstd (ADR 0008): about a fifth smaller than Delta's default Snappy for result stores.
# No dictionaries (BE-18): a column of distinct floats tried one for every page and
# fell back, a fifth of the write's time and a quarter more bytes; the values are the
# same either way.
PLAIN = ColumnProperties(dictionary_enabled=False)
WRITER = WriterProperties(compression="ZSTD", compression_level=3,
                          default_column_properties=PLAIN)  # fmt: skip
# A row group per fields row (one field at one step): a step's read prunes the others.
FIELD_WRITER = WriterProperties(compression="ZSTD", compression_level=3, max_row_group_size=1,
                                default_column_properties=PLAIN)  # fmt: skip
WRITERS = {"fields": FIELD_WRITER}


class StaleStore(PuzzleError):
    code = "stale_store"


class StoreTooNew(PuzzleError):
    code = "store_too_new"


class StoreUpgrading(PuzzleError):
    code = "store_upgrading"


# Stores this process is upgrading in the background (the GUI's, `hold_upgrade`): by
# root, None while it works, or why it failed. Others reading such a store meanwhile
# are told so (`store_upgrading`) instead of migrating it themselves on their thread.
_UPGRADES: dict[str, str | None] = {}
_upgrader = threading.local()  # the root this thread is upgrading, if any
log = logging.getLogger(__name__)


def stored_format(root: Path) -> int | None:
    """The format of the store at `root`: from `store.json`, 1 for tables older than
    the marker, None for no store yet. Reads one small file."""
    marker = Path(root) / "store.json"
    if marker.exists():
        found = json.loads(marker.read_text(encoding="utf-8")).get("format")
        return found if isinstance(found, int) else 0
    if any((Path(root) / n / "_delta_log").is_dir() for n in (*SCHEMAS, *FOLDERS.values())):
        return 1  # format 1 stores predate the marker
    return None


def needs_upgrade(root: Path) -> bool:
    """Whether the store at `root` is in a format this code migrates (`upgrade`)."""
    try:
        return stored_format(root) in MIGRATED
    except (OSError, ValueError):
        return False


def hold_upgrade(root: Path) -> None:
    """Mark the store as being upgraded by this process in the background: until
    `release_upgrade`, other readers here get `store_upgrading` at once."""
    with _cache_lock:
        _UPGRADES[str(Path(root).resolve())] = None


def release_upgrade(root: Path, failed: str | None = None) -> None:
    """The background upgrade ended; `failed` (why) keeps readers told, as a stale store
    they may move aside, instead of trying again on their own thread."""
    with _cache_lock:
        key = str(Path(root).resolve())
        if failed is None:
            _UPGRADES.pop(key, None)
        else:
            _UPGRADES[key] = failed


@dataclass(frozen=True)
class FieldInfo:
    """A stored field without its values (`ResultStore.outline`)."""

    name: str
    location: str
    components: list[str]
    count: int


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
    point: str | None = None  # the sweep point as JSON (ADR 0018), labels as people read them
    solver_version: str | None = None  # ADR 0072: the solver that ran, as it names itself
    image_digest: str | None = None  # a container run's image id
    puzzle_version: str | None = None  # the Puzzle that stored it; None: stored before

    def point_labels(self) -> dict[str, str]:
        import json

        return json.loads(self.point) if self.point else {}


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


COMPACT_EVERY = 40
"""Small files in the runs table before a write compacts the store (`maybe_compact`)."""


LOCK_FILE = ".lock"  # writers and Save as, one at a time (a lock file, atomic.Lock)
LOCK_WAIT_S = 600.0
_held = threading.local()  # the stores this thread holds the lock of (it nests)


class ResultStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    @contextlib.contextmanager
    def locked(self):
        """Hold the store's lock: writes (a run, a mesh, a delete, compaction) and a
        copy of the store (Save as, archives) one at a time, across processes."""
        from puzzle.project import atomic

        key = str(Path(self.root).resolve())
        held = getattr(_held, "roots", None)
        if held is None:
            held = _held.roots = set()
        if key in held:
            yield
            return
        Path(self.root).mkdir(parents=True, exist_ok=True)
        with atomic.Lock(Path(self.root) / LOCK_FILE, what="the results store").hold(LOCK_WAIT_S):
            held.add(key)
            try:
                yield
            finally:
                held.discard(key)

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
        point: dict[str, str] | None = None,
        solver_version: str | None = None,
        image_digest: str | None = None,
    ) -> str:
        """Store a completed run and its mesh; return the new run id. `point`: the sweep
        point it ran (readable labels), if any; the solver's version and image id when
        known."""
        with self.locked():
            return self._write_run(
                run_key=run_key, study_id=study_id, study_name=study_name, solver=solver,
                runtime=runtime, mesh=mesh, results=results, started_at=started_at,
                duration_s=duration_s, point=point, solver_version=solver_version,
                image_digest=image_digest,
            )  # fmt: skip

    def _write_run(self, *, run_key, study_id, study_name, solver, runtime, mesh, results,
                   started_at, duration_s, point, solver_version, image_digest) -> str:  # fmt: skip
        run_id = uuid.uuid4().hex
        self._check_format(create=True)
        self._tidy()  # what a migration cut short left behind
        mid = self.write_mesh(mesh)
        if any(step.fields for step in results.steps):
            self._write_fields(run_id, results)
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
            "point": json.dumps(point) if point else None,
            "solver_version": solver_version,
            "image_digest": image_digest,
            "puzzle_version": __version__,
        }
        self._append("runs", pa.Table.from_pylist([record], schema=SCHEMAS["runs"]))
        self.maybe_compact()
        return run_id

    def write_progress(self, run_id: str, events: list) -> None:
        """Keep a run's progress events (ADR 0054) with it."""
        from puzzle.progress import dumps

        if events:
            self._check_format(create=True)
            row = {"run_id": run_id, "events": dumps(events)}
            self._append("progress", pa.Table.from_pylist([row], schema=SCHEMAS["progress"]))

    def read_progress(self, run_id: str) -> list:
        """A stored run's progress events; [] for a run kept without them."""
        from puzzle.progress import loads

        rows = self._rows("progress", filter=pc.field("run_id") == run_id).to_pylist()
        return loads("".join(r["events"] for r in rows))

    def write_mesh(self, mesh: Mesh) -> str:
        """Store a mesh once (BE-10): under the store's lock, and the `meshes` row that
        marks it stored comes last, so rows an interrupted write left (nodes, cells,
        groups without that row) are removed before it is written again."""
        with self.locked():
            return self._write_mesh(mesh)

    def _write_mesh(self, mesh: Mesh) -> str:
        self._check_format(create=True)
        mid = mesh_id(mesh)
        if self._exists("meshes") and self._rows("meshes", pc.field("mesh_id") == mid).num_rows:
            return mid
        for name in ("nodes", "cells", "groups"):  # a write cut short: start again
            if self._exists(name) and self._rows(name, pc.field("mesh_id") == mid,
                                                 ["mesh_id"]).num_rows:  # fmt: skip
                DeltaTable(str(self._path(name))).delete(f"mesh_id = '{mid}'")
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
        return list(self._runs()[0])

    def _runs(self) -> tuple[list[RunRecord], dict[str, RunRecord], dict[str, RunRecord]]:
        """The runs in the order recorded, by id, and the latest completed by key: read
        once per version of the runs table (BE-12: each look-up read and sorted it)."""
        self._check_format()
        if not self._exists("runs"):
            return [], {}, {}
        stamp = _log_stamp(self.root / "runs")
        key = str(Path(self.root).resolve())
        with _cache_lock:
            hit = _RUNS.get(key)
        if hit is not None and hit[0] == stamp:
            return hit[1]
        rows = self._rows("runs").sort_by([("recorded_at", "ascending")]).to_pylist()
        records = [RunRecord(**r) for r in rows]
        by_id: dict[str, RunRecord] = {}
        for r in records:  # the first with an id, as a scan in order found it
            by_id.setdefault(r.run_id, r)
        by_key = {r.run_key: r for r in records if r.status == "completed"}
        got = (records, by_id, by_key)
        with _cache_lock:
            _RUNS[key] = (stamp, got)
            _trim(_RUNS)
        return got

    def find_run(self, run_key: str) -> RunRecord | None:
        """The latest completed run with this key: a cache hit for an unchanged study."""
        return self._runs()[2].get(run_key)

    def get_run(self, run_id: str) -> RunRecord:
        found = self._runs()[1].get(run_id)
        if found is None:
            raise KeyError(f"no run {run_id}")
        return found

    def field_catalog(self, run_id: str) -> list[tuple[str, str, list[str]]]:
        """(field, location, components) of a run's stored fields, in step order, read
        from the metadata columns only (no values): what the model tree lists."""
        index = self._run_index(run_id)
        rows = sorted(index.rows if index else [], key=lambda r: r["ordinal"])
        seen: dict[str, tuple[str, str, list[str]]] = {}
        for r in rows:
            if r["field"] not in seen:
                comps = r["components"].split(",") if r["components"] else [""]
                seen[r["field"]] = (r["field"], r["location"], comps)
        return list(seen.values())

    def outline(self, run_id: str) -> Results:
        """A run's steps and each step's fields as `FieldInfo` (name, location,
        components, count), from the metadata columns alone: what a form's choices
        need, without reading a value (PANEL-13). Steps and fields in `read_results`'
        order."""
        record = self.get_run(run_id)
        index = self._run_index(run_id)
        steps: dict[int, StepResult] = {}
        for r in sorted(index.rows if index else [], key=lambda r: (r["ordinal"], r["field"])):
            if r["ordinal"] not in steps:
                steps[r["ordinal"]] = StepResult(index=r["step"], kind=r["kind"],
                                                 value=r["value"], mode=r["mode"])  # fmt: skip
            steps[r["ordinal"]].fields[r["field"]] = FieldInfo(
                r["field"], r["location"], r["components"].split(","), r["count"]
            )
        return Results(solver=record.solver, steps=[steps[k] for k in sorted(steps)])

    def group_dims(self, mid: str) -> dict[str, int]:
        """A mesh's groups by name, with their dimension, read without their nodes."""
        rows = self._rows("groups", pc.field("mesh_id") == mid, ["name", "dim"]).to_pylist()
        out: dict[str, int] = {}
        for g in rows:
            out.setdefault(g["name"], g["dim"])
        return out

    def bounds(self, mid: str) -> tuple[np.ndarray, np.ndarray]:
        """A mesh's bounding box (lowest and highest corner), from its coordinates."""
        nodes = self._rows("nodes", pc.field("mesh_id") == mid, ["coords"],
                           partitions=[("mesh_id", "=", mid)])  # fmt: skip
        if not nodes.num_rows:
            raise KeyError(f"no mesh {mid}")
        xyz = _array(nodes, "coords", 0).reshape(-1, 3)
        return xyz.min(axis=0), xyz.max(axis=0)

    def read_results(self, run_id: str) -> Results:
        import pyarrow.parquet as pq

        record = self.get_run(run_id)
        files = self._run_files(run_id)
        table = (pa.concat_tables([pq.read_table(f, schema=_stored_fields()) for f in files])
                 if files else _stored_fields().empty_table())  # fmt: skip
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
        0; -1 is the last. The run's index (its files' metadata columns, read once) names
        the row group holding that step, and only that row group's arrays are read
        (and the one holding its ids, when an earlier step's): what a viewer needs for a
        frame.
        """
        index = self._run_index(run_id)
        mine = sorted((r for r in index.rows if r["field"] == field),
                      key=lambda r: r["ordinal"]) if index else []  # fmt: skip
        if not mine:
            raise KeyError(f"run {run_id} has no '{field}' field")
        at = mine[-1 if step is None else step]
        values = index.cell(at, "values")
        if at["ids_from"] == at["ordinal"]:
            ids = index.cell(at, "ids")
        else:
            ids = index.cell(next(r for r in mine if r["ordinal"] == at["ids_from"]), "ids")
        return _field(at, ids, values)

    def read_mesh(self, mid: str) -> Mesh:
        self._check_format()
        on = [("mesh_id", "=", mid)]
        nodes = self._rows("nodes", pc.field("mesh_id") == mid, partitions=on)
        if not nodes.num_rows:
            raise KeyError(f"no mesh {mid}")
        mesh = Mesh(
            node_ids=_array(nodes, "node_ids", 0), coords=_array(nodes, "coords", 0).reshape(-1, 3)
        )
        cells = self._rows("cells", pc.field("mesh_id") == mid, partitions=on).sort_by("block")
        seen: set[int] = set()
        for i, row in enumerate(cells.drop_columns(["element_ids", "connectivity"]).to_pylist()):
            if row["block"] in seen:  # a store an older Puzzle wrote twice (BE-10)
                continue
            seen.add(row["block"])
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
            if g["name"] in mesh.groups:
                continue
            mesh.groups[g["name"]] = Group(
                name=g["name"],
                dim=g["dim"],
                entities=[(g["dim"], t) for t in g["entity_tags"]],
                node_ids=np.asarray(g["node_ids"], dtype=np.int64),
            )
        return mesh

    # --- maintenance -----------------------------------------------------------

    def delete_run(self, run_id: str) -> dict[str, int]:
        """Remove a run: its record, its fields, and its mesh when no other run uses
        it; then vacuum, so the disk space comes back. Results are a cache (ADR 0008):
        the study runs again to make them. Returns what went, for a message."""
        with self.locked():
            return self._delete_run(run_id)

    def _delete_run(self, run_id: str) -> dict[str, int]:
        record = self.get_run(run_id)  # KeyError for an unknown run
        if not re.fullmatch(r"[0-9a-f]+", run_id):  # it goes into a predicate
            raise KeyError(f"no run {run_id}")
        gone = {"runs": 1, "meshes": 0}
        for name in ("runs", "fields", "progress"):
            if self._exists(name):
                DeltaTable(str(self._path(name))).delete(f"run_id = '{run_id}'")
        mid = record.mesh_id
        if re.fullmatch(r"[0-9a-f]+", mid) and not any(r.mesh_id == mid for r in self.runs()):
            for name in ("meshes", "nodes", "cells", "groups"):
                if self._exists(name):
                    DeltaTable(str(self._path(name))).delete(f"mesh_id = '{mid}'")
            gone["meshes"] = 1
        for name in SCHEMAS:  # free the files the deletes left behind
            if self._exists(name):
                DeltaTable(str(self._path(name))).vacuum(
                    retention_hours=0, dry_run=False, enforce_retention_duration=False
                )
        return gone

    def compact(self) -> None:
        """Merge small Parquet files (each run appends a few) into larger ones, checkpoint
        each table's log (so opening it reads one file, not every commit), and remove
        files older than a week that no version reads any more. A reader holding an
        older snapshot keeps working: nothing younger than the retention is removed."""
        with self.locked():
            self._compact()

    def _compact(self) -> None:
        for name in SCHEMAS:
            if self._exists(name):
                table = DeltaTable(str(self._path(name)))
                table.optimize.compact(writer_properties=WRITERS.get(name, WRITER))
                table.create_checkpoint()
                table.vacuum(dry_run=False)  # Delta's default retention: 7 days

    def maybe_compact(self, every: int = COMPACT_EVERY) -> bool:
        """Compact once the runs table holds `every` small files: each stored run adds
        one to most tables, and opening many slows every read (ADR 0008). Called after
        each run is written; True when it compacted."""
        if not self._exists("runs") or len(DeltaTable(str(self.root / "runs")).file_uris()) < every:
            return False
        self.compact()
        return True

    # --- internals ---------------------------------------------------------------

    def _path(self, name: str) -> Path:
        return self.root / FOLDERS.get(name, name)

    def _exists(self, name: str) -> bool:
        return (self._path(name) / "_delta_log").is_dir()

    def _check_format(self, create: bool = False) -> None:
        """Refuse a store in a format this code can't read; migrate a format 2 one (on
        this thread, said in the log, unless this process upgrades it in the background:
        `hold_upgrade`); on a write, mark a new store with this format."""
        found = stored_format(self.root)
        if found == FORMAT:
            return
        if found is None:
            if create:
                from puzzle.project import atomic

                self.root.mkdir(parents=True, exist_ok=True)
                atomic.write_text(self.root / "store.json", _MARKER, encoding="utf-8")
            return
        if found in MIGRATED:
            key = str(Path(self.root).resolve())
            with _cache_lock:
                background = key in _UPGRADES and getattr(_upgrader, "root", None) != key
                why = _UPGRADES.get(key)
            if not background:
                self.upgrade()
                return
            if why is None:
                raise StoreUpgrading(
                    "the results store is being updated to the current format; its results "
                    "show once that is done", path=str(self.root),
                    details={"found": found, "expected": FORMAT})  # fmt: skip
            raise StaleStore(
                f"the results store at {self.root} (format {found}) could not be updated to "
                f"format {FORMAT}: {why}. Move it aside and rerun the studies to rebuild it",
                path=str(self.root), details={"found": found, "expected": FORMAT})  # fmt: skip
        self._refuse(found)

    def _refuse(self, found) -> None:
        if isinstance(found, int) and found > FORMAT:
            raise StoreTooNew(
                f"the results store at {self.root} has format {found}, written by a newer "
                f"Puzzle; this one reads format {FORMAT}. Open the project in that Puzzle, "
                "or move the folder aside and rerun the studies here",
                path=str(self.root), details={"found": found, "expected": FORMAT})  # fmt: skip
        raise StaleStore(
            f"the results store at {self.root} has format {found}; this Puzzle reads "
            f"format {FORMAT}. Delete the folder and rerun the studies to rebuild it",
            path=str(self.root),
            details={"found": found, "expected": FORMAT},
        )

    def upgrade(self, progress=None) -> bool:
        """Migrate a format 2 store to format 3 in place; True when it did.

        The fields are copied run by run, a row (one field at one step) at a time, into
        a new table in its own folder (`FOLDERS`), so memory holds a row, not a run.
        `store.json` is switched last, atomically: an interruption before leaves format
        2 (the half-written table is removed and the copy made again next time), after
        it format 3 with the old folder still to remove (`_tidy`). Values are copied as
        they are stored, bit for bit. `progress(done, total)` gets bytes of old files.
        Needs room for the fields twice until the old folder goes."""
        import time

        with self.locked():
            found = stored_format(self.root)
            if found == FORMAT:
                self._tidy()
                return False
            if found not in MIGRATED:
                self._refuse(found)
            key = str(Path(self.root).resolve())
            old, new = self.root / "fields", self._path("fields")
            total = sum(f.stat().st_size for f in old.rglob("*.parquet")) if old.is_dir() else 0
            log.warning("Updating the results store at %s from format %s to %s (%.0f MB of "
                        "fields)", self.root, found, FORMAT, total / 1e6)  # fmt: skip
            start = time.perf_counter()
            _upgrader.root = key
            try:
                _migrate_fields(old, new, progress)
                from puzzle.project import atomic

                atomic.write_text(self.root / "store.json", _MARKER, encoding="utf-8")
            finally:
                _upgrader.root = None
            self._tidy()
            log.warning("Updated the results store at %s in %.1f s", self.root,
                        time.perf_counter() - start)  # fmt: skip
            return True

    def _tidy(self) -> None:
        """Remove what a finished migration left: format 2's fields folder (a reader on
        Windows may hold a file in it; then it goes next time)."""
        old = self.root / "fields"
        if FOLDERS["fields"] != "fields" and old.is_dir() and stored_format(self.root) == FORMAT:
            import shutil

            shutil.rmtree(old, ignore_errors=True)

    def _append(self, name: str, table: pa.Table) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        write_deltalake(
            str(self._path(name)),
            table.cast(SCHEMAS[name]),
            mode="append",
            partition_by=PARTITIONS.get(name),
            writer_properties=WRITERS.get(name, WRITER),
            schema_mode="merge",  # a column added since (runs.point) joins older tables
        )

    def _write_fields(self, run_id: str, results: Results) -> None:
        """A run's fields as one Parquet file (format 3), written a row at a time
        straight to disk by pyarrow, then added to the table in one commit (BE-18).

        delta-rs's own writer held each partition's file in memory and more: storing
        192 MB of values took 960 MB besides the results, and 1 GB would have needed
        5 GB. Here each row (one field at one step) is a view of the results' arrays
        and its own row group, field after field so a field's steps lie together, with
        statistics on the scalar columns as delta-rs writes them."""
        rows = list(_fields_rows(run_id, results))  # views of the results' arrays
        first: dict[str, int] = {}
        for meta, _, _ in rows:
            first.setdefault(meta["field"], len(first))
        rows.sort(key=lambda r: (first[r[0]["field"]], r[0]["ordinal"]))
        path = self._path("fields")
        out = _PartFile(path, {"run_id": run_id}, _stored_fields())
        try:
            for meta, ids, values in rows:
                out.write(meta, ids, values)
            action = out.close()
        except BaseException:
            out.discard()
            raise
        _commit_fields(path, [action])

    def _table(self, name: str) -> DeltaTable:
        """The table at its latest version, opened once per version (BE-12: each read
        opened it and read its log again)."""
        path = self._path(name)
        stamp = _log_stamp(path)
        key = str(Path(path).resolve())
        with _cache_lock:
            hit = _TABLES.get(key)
            if hit is not None and hit[0] == stamp:
                return hit[1]
        table = DeltaTable(str(path))
        with _cache_lock:
            _TABLES[key] = (stamp, table)
            _trim(_TABLES)
        return table

    def _run_files(self, run_id: str) -> tuple[str, ...]:
        """The fields files of a run, from the table's add actions: listed once per
        version of the table, for every run at once."""
        self._check_format()
        if not self._exists("fields"):
            return ()
        path = self._path("fields")
        stamp = _log_stamp(path)
        key = str(Path(path).resolve())
        with _cache_lock:
            hit = _FILES.get(key)
        if hit is None or hit[0] != stamp:
            from urllib.parse import unquote

            table = self._table("fields")
            with _cache_lock:  # one caller of a cached DeltaTable at a time
                actions = pa.table(table.get_add_actions(flatten=True))
            by_run: dict[str, list[str]] = {}
            paths = actions.column("path").to_pylist()
            runs = actions.column("partition.run_id").to_pylist()
            for rel, rid in zip(paths, runs, strict=True):
                by_run.setdefault(rid, []).append(str(path / unquote(rel)))
            hit = (stamp, {k: tuple(sorted(v)) for k, v in by_run.items()})
            with _cache_lock:
                _FILES[key] = hit
                _trim(_FILES)
        return hit[1].get(run_id, ())

    def _run_index(self, run_id: str) -> _RunIndex | None:
        """A run's fields rows without their arrays, each with the file and row group
        holding it: read from the files' metadata columns once and kept while the run's
        files are the same (they never change; compaction makes new ones)."""
        files = self._run_files(run_id)
        if not files:
            return None
        with _cache_lock:
            hit = _INDEXES.get(files)
        if hit is None:
            hit = _RunIndex(files)
            with _cache_lock:
                _INDEXES[files] = hit
                _trim(_INDEXES, keep=256)
        return hit

    def _dataset(self, name: str, partitions=None):
        """The table as a pyarrow dataset of the partitions given only (a run's or a
        mesh's files, not every run's); None when there is no table."""
        if not self._exists(name):
            return None
        self._check_format()
        table = self._table(name)
        with _cache_lock:  # one caller of a cached DeltaTable at a time
            if partitions:
                own = set(table.metadata().partition_columns)
                partitions = [p for p in partitions if p[0] in own] or None
            if not partitions:
                return table.to_pyarrow_dataset()
            try:
                return table.to_pyarrow_dataset(file_pruning_predicate=partitions)
            except TypeError:  # deltalake before 1.6 (linux-aarch64's lock: 1.5.0)
                return table.to_pyarrow_dataset(partitions=partitions)

    def _rows(self, name: str, filter=None, columns: list[str] | None = None,
              partitions=None) -> pa.Table:  # fmt: skip
        dataset = self._dataset(name, partitions)
        if dataset is None:
            return SCHEMAS[name].empty_table().select(columns or SCHEMAS[name].names)
        return dataset.to_table(filter=filter, columns=columns)


_cache_lock = threading.RLock()
_TABLES: dict[str, tuple[tuple, DeltaTable]] = {}  # table folder -> (log stamp, table)
_RUNS: dict[str, tuple[tuple, tuple]] = {}  # store root -> (runs log stamp, records)
_FILES: dict[str, tuple[tuple, dict]] = {}  # fields folder -> (log stamp, files by run)
_INDEXES: dict[tuple, _RunIndex] = {}  # a run's files -> their index


class _RunIndex:
    """A run's fields files: each row's metadata (`FIELD_SCALARS`) with where it lies,
    and the files' footers, so a cell's read opens one row group of one file."""

    def __init__(self, files: tuple[str, ...]) -> None:
        import pyarrow.parquet as pq

        self.files = files
        self.footers = []
        self.rows: list[dict] = []
        for n, path in enumerate(files):
            with pq.ParquetFile(path) as pf:  # closed: Windows can't delete an open file
                md = pf.metadata
                meta = pf.read(columns=FIELD_SCALARS).to_pylist()
            self.footers.append(md)
            k = 0
            for g in range(md.num_row_groups):
                for at in range(md.row_group(g).num_rows):
                    meta[k].update(_file=n, _group=g, _at=at)
                    self.rows.append(meta[k])
                    k += 1

    def cell(self, row: dict, column: str) -> np.ndarray:
        """The array in one row's `ids` or `values`, reading only its row group."""
        import pyarrow.parquet as pq

        n = row["_file"]
        with pq.ParquetFile(self.files[n], metadata=self.footers[n]) as pf:
            group = pf.read_row_group(row["_group"], columns=[column])
        return _array(group, column, row["_at"])


def _trim(cache: dict, keep: int = 64) -> None:
    while len(cache) > keep:  # the oldest opened first
        cache.pop(next(iter(cache)))


def _log_stamp(table: Path) -> tuple:
    """A table's latest commit, as its log folder lists it: a new commit (any writer,
    any process), or a store made again from nothing, gives another."""
    latest, stamp = "", None
    try:
        with os.scandir(Path(table) / "_delta_log") as entries:
            for e in entries:
                if e.name.endswith(".json") and e.name[:-5].isdigit() and e.name > latest:
                    latest = e.name
                    st = e.stat()
                    stamp = (st.st_size, st.st_mtime_ns, st.st_ctime_ns)
    except FileNotFoundError:
        return ("", None)
    return (latest, stamp)


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


def _fields_rows(run_id: str, results: Results):
    """(metadata, ids or None, values) per field per result step, in the order Results
    keeps its steps; the arrays as the results hold them (flattened views)."""
    held: dict[str, tuple[int, np.ndarray]] = {}  # field -> (ordinal, ids) last stored
    ordered = sorted(results.steps, key=lambda s: (s.index, s.mode or 0, s.value))
    for ordinal, step in enumerate(ordered):
        for f in step.fields.values():
            same = f.name in held and np.array_equal(held[f.name][1], f.ids)
            if not same:
                held[f.name] = (ordinal, f.ids)
            meta = {
                "run_id": run_id,
                "field": f.name,
                "ordinal": ordinal,
                "step": step.index,
                "mode": step.mode,
                "kind": step.kind,
                "value": step.value,
                "location": f.location,
                "components": ",".join(f.components),
                "count": len(f.ids),
                "ids_from": held[f.name][0],
            }
            ids = None if same else np.asarray(f.ids, np.int64)
            yield meta, ids, np.ascontiguousarray(f.values, np.float64).ravel()


_SAFE = re.compile(r"[A-Za-z0-9_.-]+")


class _PartFile:
    """One partition's Parquet file of a run's fields, written a row group per row, and
    what its Delta add action records: size, partition values, statistics."""

    def __init__(self, table: Path, values: dict, schema: pa.Schema) -> None:
        import pyarrow.parquet as pq

        folder = "/".join(f"{k}={v if _SAFE.fullmatch(str(v)) else _hashed(str(v))}"
                          for k, v in values.items())  # fmt: skip
        self.rel = f"{folder}/part-00000-{uuid.uuid4()}-c000.zstd.parquet"
        self.file = Path(table) / self.rel
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.values, self.schema = values, schema
        self.scalars = [f.name for f in schema if f.name not in ("ids", "values")]
        self.rows = 0
        self.low: dict = {}
        self.high: dict = {}
        self.nulls = dict.fromkeys(self.scalars, 0)
        self.writer = pq.ParquetWriter(
            self.file, schema, compression="zstd", compression_level=3,
            use_dictionary=False, write_statistics=True,
        )  # fmt: skip

    def write(self, meta: dict, ids, values: np.ndarray) -> None:
        columns = [pa.array([meta[k]], self.schema.field(k).type) for k in self.scalars]
        columns.append(_lists([ids], pa.int64()))
        columns.append(_lists([values], pa.float64()))
        self.writer.write_batch(pa.RecordBatch.from_arrays(columns, schema=self.schema),
                                row_group_size=1)  # fmt: skip
        self.rows += 1
        for k in self.scalars:
            v = meta[k]
            if v is None:
                self.nulls[k] += 1
                continue
            if k not in self.low or v < self.low[k]:
                self.low[k] = v
            if k not in self.high or v > self.high[k]:
                self.high[k] = v

    def close(self) -> dict:
        self.writer.close()
        st = self.file.stat()
        stats = {"numRecords": self.rows, "minValues": self.low, "maxValues": self.high,
                 "nullCount": self.nulls}  # fmt: skip
        return {"path": self.rel, "size": st.st_size,
                "partition_values": {k: str(v) for k, v in self.values.items()},
                "modification_time": int(st.st_mtime * 1000), "data_change": True,
                "stats": json.dumps(stats)}  # fmt: skip

    def discard(self) -> None:
        with contextlib.suppress(Exception):
            self.writer.close()
        self.file.unlink(missing_ok=True)


def _stored_fields() -> pa.Schema:
    """The fields table's columns as its files hold them: all but the partition's."""
    return pa.schema([f for f in SCHEMAS["fields"] if f.name not in PARTITIONS["fields"]])


def _commit_fields(path: Path, actions: list[dict]) -> None:
    """Files written by `_PartFile` added to the fields table at `path` in one commit
    (the table made first if it is new)."""
    from deltalake.transaction import AddAction

    schema = SCHEMAS["fields"]
    if not (Path(path) / "_delta_log").is_dir():
        DeltaTable.create(str(path), schema=schema, partition_by=PARTITIONS["fields"],
                          mode="ignore")  # fmt: skip
    table = DeltaTable(str(path))
    adds = [AddAction(**a) for a in actions]
    table.create_write_transaction(adds, mode="append", schema=table.schema(),
                                   partition_by=PARTITIONS["fields"])  # fmt: skip


def _migrate_fields(old: Path, new: Path, progress=None) -> None:
    """Format 2's fields table (partitioned by run and field) copied into format 3's at
    `new` (by run): a file per run, the old files' rows in field order, one row (one
    field at one step) in memory at a time; then added in one commit. A `new` left by an
    interrupted copy is removed first. The values and ids are copied as stored."""
    import shutil

    import pyarrow.dataset as pds
    import pyarrow.parquet as pq

    if new.exists():
        shutil.rmtree(new)
    if not (old / "_delta_log").is_dir():
        return
    dataset = DeltaTable(str(old)).to_pyarrow_dataset()
    pieces = []  # (run_id, field, file)
    for fragment in dataset.get_fragments():
        keys = pds.get_partition_keys(fragment.partition_expression)
        pieces.append((str(keys["run_id"]), str(keys["field"]), old / fragment.path))
    pieces.sort(key=lambda p: (p[0], p[1], str(p[2])))
    total = sum(p[2].stat().st_size for p in pieces)
    done = 0
    stored = _stored_fields()
    scalars = [n for n in FIELD_SCALARS if n != "field"]
    actions: list[dict] = []
    out = None
    try:
        for k, piece in enumerate(pieces):
            run_id = piece[0]
            if k == 0 or pieces[k - 1][0] != run_id:
                out = _PartFile(new, {"run_id": run_id}, stored)
            with pq.ParquetFile(piece[2]) as pf:
                for g in range(pf.num_row_groups):
                    rows = pf.read_row_group(g, columns=[*scalars, "ids", "values"])
                    meta = rows.select(scalars).to_pylist()
                    for i, m in enumerate(meta):
                        m["field"] = piece[1]
                        cell = rows.column("ids")[i]
                        ids = cell.values.to_numpy(zero_copy_only=False) if cell.is_valid else None
                        values = rows.column("values")[i].values.to_numpy(zero_copy_only=False)
                        out.write(m, ids, values)
            done += piece[2].stat().st_size
            if k == len(pieces) - 1 or pieces[k + 1][0] != run_id:
                actions.append(out.close())
            if progress is not None:
                progress(done, total)
        if actions:
            _commit_fields(new, actions)
            DeltaTable(str(new)).create_checkpoint()
            _flush_tree(new)  # on disk before store.json says so and the old copy goes
    except BaseException:
        if out is not None:
            out.discard()  # closed first: Windows can't remove an open file
        shutil.rmtree(new, ignore_errors=True)
        raise


def _flush_tree(folder: Path) -> None:
    """Every file under `folder` flushed to disk (and, off Windows, each folder's
    entries), so a power cut after the switch can't find the new table half written."""
    for here, _, names in os.walk(folder):
        for name in names:
            with open(Path(here) / name, "rb+") as f:  # Windows flushes writable handles only
                os.fsync(f.fileno())
        if os.name != "nt":
            fd = os.open(here, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)


def _hashed(value: str) -> str:
    """A folder name for a partition value that isn't plain letters and digits: the
    value itself is in the add action; the folder only has to be distinct and safe."""
    return "h" + hashlib.sha256(value.encode()).hexdigest()[:16]


def _lists(arrays: list, typ: pa.DataType) -> pa.Array:
    """A list column from numpy arrays (None = a null cell), built without Python loops."""
    lengths = [0 if a is None else len(a) for a in arrays]
    offsets = pa.array(np.concatenate([[0], np.cumsum(lengths)]).astype(np.int64))
    present = [a for a in arrays if a is not None]
    if len(present) == 1:  # one array: wrapped as it is, not copied
        flat = pa.array(present[0], typ)
    else:
        flat = pa.array(np.concatenate(present) if present else np.empty(0), typ)
    mask = pa.array([a is None for a in arrays])
    return pa.LargeListArray.from_arrays(offsets, flat, mask=mask)


def _now() -> datetime:
    return datetime.now(UTC)
