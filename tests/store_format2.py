"""Format 2 results stores for the migration's tests (ADR 0090 package 14).

`downgrade` turns a store this code wrote (format 3) into the format 2 a Puzzle before
it left: the fields table in `fields`, partitioned by run and field, a file per
partition written as package 14 wrote it (`_PartFile`, hashed folders for names that
aren't plain), or, with `delta_rs`, by delta-rs's own writer as earlier stores were.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pyarrow as pa
from deltalake import DeltaTable, write_deltalake

from puzzle.results import store as sm

PARTITIONS = ["run_id", "field"]


def rows(root: Path) -> list[dict]:
    """Every fields row of a store, either format, as Python values with the arrays as
    bytes, in (run, field, ordinal) order: what must be the same after a migration."""
    folder = root / ("fields3" if (root / "fields3" / "_delta_log").is_dir() else "fields")
    table = DeltaTable(str(folder)).to_pyarrow_table()
    out = []
    for r in range(table.num_rows):
        row = {k: table.column(k)[r].as_py() for k in table.column_names
               if k not in ("ids", "values")}  # fmt: skip
        for k in ("ids", "values"):
            cell = table.column(k)[r]
            row[k] = cell.values.to_numpy(zero_copy_only=False).tobytes() if cell.is_valid else None
        out.append(row)
    return sorted(out, key=lambda r: (r["run_id"], r["field"], r["ordinal"]))


def downgrade(root: Path, delta_rs: bool = False) -> None:
    root = Path(root)
    new = root / "fields3"
    table = DeltaTable(str(new)).to_pyarrow_table()
    old = root / "fields"
    schema = sm.SCHEMAS["fields"]
    if delta_rs:
        write_deltalake(str(old), table.select(schema.names).cast(schema), mode="append",
                        partition_by=PARTITIONS, writer_properties=sm.FIELD_WRITER)  # fmt: skip
    else:
        stored = pa.schema([f for f in schema if f.name not in PARTITIONS])
        writers: dict[tuple, sm._PartFile] = {}
        for r in range(table.num_rows):
            meta = {k: table.column(k)[r].as_py() for k in schema.names
                    if k not in ("ids", "values")}  # fmt: skip
            key = (meta["run_id"], meta["field"])
            out = writers.get(key)
            if out is None:
                out = writers[key] = sm._PartFile(old, dict(zip(PARTITIONS, key, strict=True)),
                                                  stored)  # fmt: skip
            ids = table.column("ids")[r]
            out.write(
                meta,
                ids.values.to_numpy() if ids.is_valid else None,
                table.column("values")[r].values.to_numpy(),
            )
        from deltalake.transaction import AddAction

        DeltaTable.create(str(old), schema=schema, partition_by=PARTITIONS)
        DeltaTable(str(old)).create_write_transaction(
            [AddAction(**w.close()) for w in writers.values()], mode="append",
            schema=DeltaTable(str(old)).schema(), partition_by=PARTITIONS)  # fmt: skip
    shutil.rmtree(new)
    (root / "store.json").write_text(json.dumps({"format": 2}) + "\n", encoding="utf-8")
    with sm._cache_lock:
        sm._TABLES.clear()
        sm._FILES.clear()
        sm._INDEXES.clear()
        sm._RUNS.clear()


def file_count(folder: Path) -> int:
    return sum(1 for _ in Path(folder).rglob("*.parquet")) if Path(folder).exists() else 0


def memory_now() -> int:
    """This process's memory now: private bytes on Windows, resident on Linux (as
    `test_speed_store`'s memory test reads it)."""
    import ctypes
    import os

    if os.name == "nt":

        class PMC(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_ulong), ("faults", ctypes.c_ulong)] + [
                (n, ctypes.c_size_t)
                for n in ("pws", "ws", "qpp", "qp", "qpnp", "qnp", "private", "peak")
            ]

        c = PMC()
        c.cb = ctypes.sizeof(PMC)
        k32 = ctypes.WinDLL("kernel32")
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        psapi = ctypes.WinDLL("psapi")
        psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
        psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb)
        return c.private
    with open("/proc/self/statm", encoding="utf-8") as f:
        return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
