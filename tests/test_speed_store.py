"""A results store that reads and writes by field and step (ADR 0090 package 14).

BE-18, done when a 1 GB result stores without doubling memory. Storing a run wrote
every value into one Arrow table first, then delta-rs held each partition's file in
memory besides: 192 MB of values took 1.4 GB of extra memory (7.2x) and 16 s. Now each
partition's Parquet file is written a row (a field at a step) at a time, the rows
views of the results' arrays, and added to the table in one commit: about 100 MB
extra whatever the run's size, on the maintainer's machine 106, 95, 97 and 73 MB for
48 MB, 192 MB, 595 MB and 1.0 GB of values (the 1 GB by hand: 2.6 s, 0.07 times the
values). The test stores 19 MB and 192 MB in fresh processes and holds the extra's
growth from one to the other under a quarter of the values added (0.08 measured; the
old writer's grew by seven times them).

BE-12: look-ups and reads open only what they need: the runs table once per version
of it, a field's read only its run's file (format 3: one per run), its footer once,
then the one row group holding the step.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from datetime import UTC, datetime

import numpy as np
import pytest

from puzzle.mesh.mesh import CellBlock, Mesh
from puzzle.results import Field, Results, ResultStore, StepResult
from puzzle.results import store as store_module

MEMORY = textwrap.dedent(
    """
    import ctypes, os, sys, threading, time
    from datetime import UTC, datetime
    from pathlib import Path
    import numpy as np
    from puzzle.mesh.mesh import CellBlock, Mesh
    from puzzle.results import Field, Results, ResultStore, StepResult

    def now():
        if os.name == "nt":
            class PMC(ctypes.Structure):
                _fields_ = [("cb", ctypes.c_ulong), ("faults", ctypes.c_ulong)] + [
                    (n, ctypes.c_size_t) for n in ("pws", "ws", "qpp", "qp", "qpnp", "qnp",
                                                   "private", "peak")]
            c = PMC(); c.cb = ctypes.sizeof(PMC)
            k32 = ctypes.WinDLL("kernel32"); k32.GetCurrentProcess.restype = ctypes.c_void_p
            psapi = ctypes.WinDLL("psapi")
            psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                                   ctypes.c_ulong]
            psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb)
            return c.private
        with open("/proc/self/statm") as f:
            return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")

    nodes, steps = 100_000, int(sys.argv[2])
    ids = np.arange(1, nodes + 1, dtype=np.int64)
    mesh = Mesh(node_ids=ids, coords=np.random.rand(nodes, 3))
    mesh.blocks = [CellBlock("tet4", 3, (3, 1), np.array([1]), np.array([[1, 2, 3, 4]]))]
    run = [StepResult(k + 1, "transient", float(k), None,
                      {n: Field(n, "node", ["x", "y", "z"], ids, np.random.rand(nodes, 3))
                       for n in ("displacement", "stress")}) for k in range(steps)]
    store = ResultStore(Path(sys.argv[1]))
    store.write_mesh(mesh)
    store.write_run(run_key="warm", study_id="s", study_name="S", solver="calculix",
                    runtime="native", mesh=mesh, results=Results("calculix", run[:1]),
                    started_at=datetime.now(UTC), duration_s=1.0)
    base = high = now(); done = threading.Event()
    def sample():
        global high
        while not done.is_set():
            high = max(high, now()); time.sleep(0.002)
    t = threading.Thread(target=sample); t.start()
    store.write_run(run_key="k", study_id="s", study_name="S", solver="calculix",
                    runtime="native", mesh=mesh, results=Results("calculix", run),
                    started_at=datetime.now(UTC), duration_s=1.0)
    done.set(); t.join()
    size = sum(f.values.nbytes for s in run for f in s.fields.values())
    print(size, max(high, now()) - base)
    """
)


def _extra(tmp_path, steps: int) -> tuple[int, int]:
    """(bytes of values, extra bytes while storing them), in a fresh process."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    out = subprocess.run([sys.executable, "-c", MEMORY, str(tmp_path / f"s{steps}"), str(steps)],
                         capture_output=True, text=True, encoding="utf-8", env=env,
                         timeout=600, check=True)  # fmt: skip
    size, extra = out.stdout.split()
    return int(size), int(extra)


@pytest.mark.skipif(sys.platform == "darwin", reason="no current memory to read here")
def test_storing_a_run_takes_memory_by_step_not_by_run(tmp_path):
    small, small_extra = _extra(tmp_path, 4)  # 19 MB of values
    big, big_extra = _extra(tmp_path, 40)  # 192 MB
    growth = (big_extra - small_extra) / (big - small)
    assert growth < 0.25, (f"storing {big / 1e6:.0f} MB took {big_extra / 1e6:.0f} MB extra, "
                           f"{small / 1e6:.0f} MB took {small_extra / 1e6:.0f} MB")  # fmt: skip
    assert big_extra < 0.6 * big  # and never near doubling it


def _mesh(n: int = 50) -> Mesh:
    ids = np.arange(1, n + 1, dtype=np.int64)
    mesh = Mesh(node_ids=ids, coords=np.arange(3 * n, dtype=float).reshape(n, 3))
    mesh.blocks = [CellBlock("tet4", 3, (3, 1), np.array([1]), np.array([[1, 2, 3, 4]]))]
    return mesh


def _run(n: int = 50, seed: int = 0, names=("displacement", "stress")) -> Results:
    rng = np.random.default_rng(seed)
    ids = np.arange(1, n + 1, dtype=np.int64)
    steps = []
    for k in range(3):
        fields = {name: Field(name, "node", ["x", "y", "z"], ids, rng.random((n, 3)))
                  for name in names}  # fmt: skip
        if k == 2:  # fewer entities in the last step: its ids are stored again
            fields["stress"] = Field("stress", "node", ["x", "y", "z"], ids[:10],
                                     rng.random((10, 3)))  # fmt: skip
        steps.append(StepResult(k + 1, "transient", float(k), None, fields))
    return Results("calculix", steps)


def _write(store: ResultStore, results: Results, key: str = "k") -> str:
    return store.write_run(run_key=key, study_id="s", study_name="S", solver="calculix",
                           runtime="native", mesh=_mesh(), results=results,
                           started_at=datetime.now(UTC), duration_s=1.0)  # fmt: skip


def _same(a: Results, b: Results) -> None:
    assert [(s.index, s.kind, s.value, s.mode) for s in a.steps] == [
        (s.index, s.kind, s.value, s.mode) for s in b.steps]  # fmt: skip
    for sa, sb in zip(a.steps, b.steps, strict=True):
        assert sa.fields.keys() == sb.fields.keys()
        for name, fa in sa.fields.items():
            fb = sb.fields[name]
            assert fa.location == fb.location and fa.components == fb.components
            assert np.array_equal(fa.ids, fb.ids) and np.array_equal(fa.values, fb.values)


def test_a_run_reads_back_as_stored_and_compacts_and_deletes(tmp_path):
    store = ResultStore(tmp_path / "results")
    written = _run()
    rid = _write(store, written)
    _same(store.read_results(rid), written)
    assert np.array_equal(store.read_field(rid, "stress", 2).values,
                          written.steps[2].fields["stress"].values)  # fmt: skip
    assert np.array_equal(store.read_field(rid, "stress", 1).ids,
                          written.steps[0].fields["stress"].ids)  # fmt: skip
    other = _write(store, _run(seed=1), "k2")
    store.compact()
    _same(store.read_results(rid), written)
    store.delete_run(other)
    _same(store.read_results(rid), written)
    assert [r.run_id for r in store.runs()] == [rid]


def test_a_field_named_with_any_characters(tmp_path):
    store = ResultStore(tmp_path / "results")
    written = _run(names=("displacement", "stress", "a field/with spaces?"))
    rid = _write(store, written)
    _same(store.read_results(rid), written)
    assert store.read_field(rid, "a field/with spaces?", 0).values.shape == (50, 3)


def test_look_ups_and_reads_open_only_what_they_need(tmp_path, monkeypatch):
    store = ResultStore(tmp_path / "results")
    ids = [_write(store, _run(n=20, seed=i), f"key{i}") for i in range(12)]
    opened = []
    real = store_module.DeltaTable

    class Counting(real):
        def __init__(self, *a, **k):
            opened.append(a[0])
            super().__init__(*a, **k)

    monkeypatch.setattr(store_module, "DeltaTable", Counting)
    store.find_run("key3")
    store.read_field(ids[3], "displacement", 1)
    opened.clear()
    t = time.perf_counter()
    for i in range(12):
        assert store.find_run(f"key{i}").run_id == ids[i]
        assert store.get_run(ids[i]).run_key == f"key{i}"
    looked = (time.perf_counter() - t) / 24
    assert looked < 0.005, f"a look-up took {looked * 1000:.1f} ms"
    store.read_field(ids[5], "displacement", 1)
    assert opened == []  # nothing written since: no table opened again
    assert len(store._run_files(ids[5])) == 1  # format 3: this run's one file, no other's

    # A field's step reads its run's footer once, then one row group: no other field's
    # or step's arrays (BE-12).
    import pyarrow.parquet as pq

    groups = []
    real_group = pq.ParquetFile.read_row_group

    def counting(self, i, columns=None, **k):
        groups.append((i, tuple(columns or ())))
        return real_group(self, i, columns=columns, **k)

    monkeypatch.setattr(pq.ParquetFile, "read_row_group", counting)
    with store_module._cache_lock:
        store_module._INDEXES.clear()
    store.read_field(ids[6], "stress", 1)
    assert groups == [(4, ("values",)), (3, ("ids",))]  # stress after displacement's 3
    groups.clear()
    store.read_field(ids[6], "displacement", 2)
    assert groups == [(2, ("values",)), (0, ("ids",))]  # its ids held by step 0
    t = time.perf_counter()
    for _ in range(20):
        store.read_field(ids[7], "displacement", 1)
        store.outline(ids[7])
        store.field_catalog(ids[7])
    each = (time.perf_counter() - t) / 20  # 2.0 ms here (format 2, 120 runs: 35)
    assert each < 0.006, f"a field, an outline and a catalog took {each * 1000:.1f} ms"

    rid = _write(store, _run(n=20, seed=99), "new")  # a write is seen at once
    assert store.find_run("new").run_id == rid


def test_a_cache_hit_and_earlier_sweep_points_read_their_arrays_when_used(tmp_path):
    """BE-18: a cached run read its whole mesh and results only to say it was cached;
    now they are read when first used (and a sweep lets go of each point's once it is
    stored, reading them again if asked)."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent))
    import schematic_project as sp

    from puzzle.commands import Session
    from puzzle.model.schema import Study
    from puzzle.project.graph import run_hashes
    from puzzle.solve import StudyRun, run_study

    root = sp.copy(tmp_path)
    project = Session(root).project
    study = next(s for s in project.of_type(Study) if s.name == "Tip load")
    store = sp.store_run(root, study, run_hashes(project, root)[study.id][study.solver][0])
    run = run_study(project, study.id, root, tmp_path / "w", store=store, reuse=True)
    held = run.__dict__
    assert run.cached and held.get("_results") is None and held.get("_mesh") is None
    assert run.results.steps[0].fields["displacement"].values.shape == (5, 3)
    assert len(run.mesh.node_ids) == 5

    fresh = StudyRun(mesh=run.mesh, results=run.results, run_key="k", run_id=run.run_id)
    fresh.release(store)
    assert fresh.__dict__.get("_results") is None
    assert fresh.results.steps[0].fields["displacement"].values.shape == (5, 3)
    assert StudyRun(run_key="x").results is None  # nothing stored, nothing to read
