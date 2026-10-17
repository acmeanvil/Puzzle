"""The results store: Delta Lake tables of Parquet files (ADR 0008).

Layout under the store root (normally `<project>/results`):

| Table | One row per | Partitioned by |
| --- | --- | --- |
| runs | solver run | - |
| meshes | distinct mesh (content hash) | - |
| nodes | mesh node | mesh_id |
| cells | mesh element | mesh_id |
| groups | named group of a mesh | - |
| fields | result value at one node or element, one step or mode | run_id, field |

Field values are wide: components go in columns c0..c8 (unused ones null), and the
component names are stored once per row group in `components`. A mesh is stored
once however many runs use it. A run becomes visible only when its `runs` row is
committed, which happens last, so a crash mid-write never leaves a half run listed.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
from deltalake import DeltaTable, WriterProperties, write_deltalake

from puzzle.mesh.mesh import CellBlock, Group, Mesh
from puzzle.results.model import Field, Results, StepResult

MAX_COMPONENTS = 9  # a full 3 x 3 tensor
COMPONENT_COLUMNS = [f"c{i}" for i in range(MAX_COMPONENTS)]

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
    "nodes": pa.schema(
        [("mesh_id", pa.string()), ("node_id", pa.int64())] + [(a, pa.float64()) for a in "xyz"]
    ),
    "cells": pa.schema(
        [
            ("mesh_id", pa.string()),
            ("block", pa.int32()),
            ("element", pa.string()),
            ("dim", pa.int8()),
            ("entity_dim", pa.int8()),
            ("entity_tag", pa.int32()),
            ("element_id", pa.int64()),
            ("connectivity", pa.list_(pa.int64())),
        ]
    ),
    "groups": pa.schema(
        [
            ("mesh_id", pa.string()),
            ("name", pa.string()),
            ("dim", pa.int8()),
            ("entity_tags", pa.list_(pa.int32())),
            ("node_ids", pa.list_(pa.int64())),
        ]
    ),
    "fields": pa.schema(
        [
            ("run_id", pa.string()),
            ("field", pa.string()),
            ("step", pa.int32()),
            ("mode", pa.int32()),
            ("kind", pa.string()),
            ("value", pa.float64()),
            ("location", pa.string()),
            ("components", pa.string()),
            ("entity_id", pa.int64()),
        ]
        + [(c, pa.float64()) for c in COMPONENT_COLUMNS]
    ),
}
PARTITIONS = {"nodes": ["mesh_id"], "cells": ["mesh_id"], "fields": ["run_id", "field"]}
# zstd (ADR 0008): about a fifth smaller than Delta's default Snappy for result stores.
WRITER = WriterProperties(compression="ZSTD", compression_level=3)


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
        mid = mesh_id(mesh)
        if self._exists("meshes") and self._rows("meshes", pc.field("mesh_id") == mid).num_rows:
            return mid
        n = len(mesh.node_ids)
        self._append(
            "nodes",
            pa.table(
                {
                    "mesh_id": pa.array([mid] * n, pa.string()),
                    "node_id": mesh.node_ids,
                    "x": mesh.coords[:, 0],
                    "y": mesh.coords[:, 1],
                    "z": mesh.coords[:, 2],
                },
                schema=SCHEMAS["nodes"],
            ),
        )
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
        steps: dict[tuple, StepResult] = {}
        keys = ["step", "mode", "value", "kind", "field", "location", "components"]
        for key in table.select(keys).group_by(keys).aggregate([]).to_pylist():
            sel = table.filter(
                (pc.field("step") == key["step"])
                & (pc.field("field") == key["field"])
                & (pc.field("value") == key["value"])
                & (
                    pc.field("mode").is_null()
                    if key["mode"] is None
                    else pc.field("mode") == key["mode"]
                )
            ).sort_by("entity_id")
            components = key["components"].split(",")
            values = np.column_stack(
                [
                    sel.column(c).to_numpy(zero_copy_only=False)
                    for c in COMPONENT_COLUMNS[: len(components)]
                ]
            )
            step_key = (key["step"], key["mode"], key["value"])
            if step_key not in steps:
                steps[step_key] = StepResult(
                    index=key["step"], kind=key["kind"], value=key["value"], mode=key["mode"]
                )
            steps[step_key].fields[key["field"]] = Field(
                name=key["field"],
                location=key["location"],
                components=components,
                ids=sel.column("entity_id").to_numpy(),
                values=values,
            )
        results.steps = sorted(steps.values(), key=lambda s: (s.index, s.mode or 0, s.value))
        return results

    def read_mesh(self, mid: str) -> Mesh:
        nodes = self._rows("nodes", pc.field("mesh_id") == mid).sort_by("node_id")
        mesh = Mesh(
            node_ids=nodes.column("node_id").to_numpy(),
            coords=np.column_stack([nodes.column(a).to_numpy() for a in "xyz"]),
        )
        cells = self._rows("cells", pc.field("mesh_id") == mid)
        for block in sorted(set(cells.column("block").to_pylist())):
            rows = cells.filter(pc.field("block") == block).sort_by("element_id")
            first = rows.slice(0, 1).to_pylist()[0]
            flat = pc.list_flatten(rows.column("connectivity")).to_numpy()
            mesh.blocks.append(
                CellBlock(
                    element=first["element"],
                    dim=first["dim"],
                    entity=(first["entity_dim"], first["entity_tag"]),
                    ids=rows.column("element_id").to_numpy(),
                    connectivity=flat.reshape(rows.num_rows, -1),
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
                DeltaTable(str(self.root / name)).optimize.compact()

    # --- internals ---------------------------------------------------------------

    def _exists(self, name: str) -> bool:
        return (self.root / name / "_delta_log").is_dir()

    def _append(self, name: str, table: pa.Table) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        write_deltalake(
            str(self.root / name),
            table.cast(SCHEMAS[name]),
            mode="append",
            partition_by=PARTITIONS.get(name),
            writer_properties=WRITER,
        )

    def _rows(self, name: str, filter=None) -> pa.Table:
        if not self._exists(name):
            return SCHEMAS[name].empty_table()
        dataset = DeltaTable(str(self.root / name)).to_pyarrow_dataset()
        return dataset.to_table(filter=filter)


def _cells_table(mid: str, index: int, b: CellBlock) -> pa.Table:
    n = len(b.ids)
    return pa.table(
        {
            "mesh_id": pa.array([mid] * n, pa.string()),
            "block": pa.array([index] * n, pa.int32()),
            "element": pa.array([b.element] * n, pa.string()),
            "dim": pa.array([b.dim] * n, pa.int8()),
            "entity_dim": pa.array([b.entity[0]] * n, pa.int8()),
            "entity_tag": pa.array([b.entity[1]] * n, pa.int32()),
            "element_id": b.ids,
            "connectivity": pa.array(b.connectivity.tolist(), pa.list_(pa.int64())),
        },
        schema=SCHEMAS["cells"],
    )


def _fields_table(run_id: str, results: Results) -> pa.Table:
    parts = []
    for step in results.steps:
        for f in step.fields.values():
            n, k = f.values.shape
            if k > MAX_COMPONENTS:
                raise ValueError(
                    f"field {f.name} has {k} components; the store holds {MAX_COMPONENTS}"
                )
            columns = {
                "run_id": pa.array([run_id] * n, pa.string()),
                "field": pa.array([f.name] * n, pa.string()),
                "step": pa.array([step.index] * n, pa.int32()),
                "mode": pa.array([step.mode] * n, pa.int32()),
                "kind": pa.array([step.kind] * n, pa.string()),
                "value": pa.array([step.value] * n, pa.float64()),
                "location": pa.array([f.location] * n, pa.string()),
                "components": pa.array([",".join(f.components)] * n, pa.string()),
                "entity_id": f.ids,
            }
            for i, c in enumerate(COMPONENT_COLUMNS):
                columns[c] = f.values[:, i] if i < k else pa.nulls(n, pa.float64())
            parts.append(pa.table(columns, schema=SCHEMAS["fields"]))
    return pa.concat_tables(parts) if parts else SCHEMAS["fields"].empty_table()


def _now() -> datetime:
    return datetime.now(UTC)
