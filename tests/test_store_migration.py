"""Results store format 3 and the migration from format 2 (ADR 0090 package 14).

Format 2 partitioned the fields by run and field: a file per field per run that
compaction could never merge (604 files for 120 runs). Format 3 partitions by run
alone, a run's fields one file. A format 2 store is migrated in place: the same rows,
bit for bit, copied a row at a time into a new folder, `store.json` switched last.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from puzzle.mesh.mesh import CellBlock, Mesh
from puzzle.results import Field, Results, ResultStore, StepResult
from puzzle.results import store as sm

sys.path.insert(0, str(Path(__file__).parent))
import store_format2 as f2  # noqa: E402


def _mesh(n: int = 30) -> Mesh:
    ids = np.arange(1, n + 1, dtype=np.int64)
    mesh = Mesh(node_ids=ids, coords=np.arange(3 * n, dtype=float).reshape(n, 3))
    mesh.blocks = [CellBlock("tet4", 3, (3, 1), np.array([1]), np.array([[1, 2, 3, 4]]))]
    return mesh


def _results(seed: int, n: int = 30) -> Results:
    """Steps whose ids change part way, a modal pair, odd values (NaN, -0, tiny) and a
    field name that isn't plain, so every path of the copy is taken."""
    rng = np.random.default_rng(seed)
    ids = np.arange(1, n + 1, dtype=np.int64)
    steps = []
    for k in range(4):
        u = rng.standard_normal((n, 3))
        u[0] = [np.nan, -0.0, 5e-324]
        fields = {"displacement": Field("displacement", "node", ["x", "y", "z"], ids, u),
                  "a field/with spaces?": Field("a field/with spaces?", "element", [""],
                                                ids[: 5 + k], rng.random((5 + k, 1)))}  # fmt: skip
        steps.append(StepResult(k + 1, "transient", float(k), None, fields))
    steps += [StepResult(5, "modal", f, m, {"displacement": Field(
        "displacement", "node", ["x", "y", "z"], ids, rng.random((n, 3)))})
        for m, f in ((1, 10.5), (2, 11.25))]  # fmt: skip
    return Results("calculix", steps)


def _store(root: Path, runs: int = 3) -> tuple[ResultStore, dict[str, Results]]:
    store = ResultStore(root)
    written = {}
    for i in range(runs):
        res = _results(i)
        rid = store.write_run(run_key=f"k{i}", study_id="s", study_name="S", solver="calculix",
                              runtime="native", mesh=_mesh(), results=res,
                              started_at=datetime.now(UTC), duration_s=1.0)  # fmt: skip
        written[rid] = res
    return store, written


def _same_bits(a: np.ndarray, b: np.ndarray) -> None:
    assert a.dtype == b.dtype and a.shape == b.shape and a.tobytes() == b.tobytes()


def _reads_as_written(store: ResultStore, written: dict[str, Results]) -> None:
    for rid, res in written.items():
        back = store.read_results(rid)
        assert [(s.index, s.kind, s.value, s.mode) for s in back.steps] == [
            (s.index, s.kind, s.value, s.mode) for s in res.steps]  # fmt: skip
        for sa, sb in zip(res.steps, back.steps, strict=True):
            assert sa.fields.keys() == sb.fields.keys()
            for name, fa in sa.fields.items():
                fb = sb.fields[name]
                assert (fa.location, fa.components) == (fb.location, fb.components)
                _same_bits(np.asarray(fa.ids, np.int64), fb.ids)
                _same_bits(np.asarray(fa.values, float), fb.values)
        for name in ("displacement", "a field/with spaces?"):
            for step in range(4):
                one = store.read_field(rid, name, step)
                _same_bits(one.values, np.asarray(res.steps[step].fields[name].values, float))
                _same_bits(one.ids, np.asarray(res.steps[step].fields[name].ids, np.int64))


@pytest.mark.parametrize("delta_rs", [False, True], ids=["package 14", "delta-rs"])
def test_a_format_2_store_is_migrated_bit_for_bit(tmp_path, delta_rs):
    store, written = _store(tmp_path / "r")
    meshes = {r.mesh_id for r in store.runs()}
    f2.downgrade(tmp_path / "r", delta_rs=delta_rs)
    before = f2.rows(tmp_path / "r")
    assert sm.needs_upgrade(tmp_path / "r")
    assert len({(r["run_id"], r["field"]) for r in before}) == 6  # a file each in format 2

    seen = []
    assert ResultStore(tmp_path / "r").upgrade(progress=lambda d, t: seen.append((d, t)))
    assert seen and seen[-1][0] == seen[-1][1] > 0  # bytes of old files, to the end
    assert f2.rows(tmp_path / "r") == before  # every row and array, bit for bit
    assert json.loads((tmp_path / "r" / "store.json").read_text(encoding="utf-8")) == {"format": 3}
    assert not (tmp_path / "r" / "fields").exists()
    assert f2.file_count(tmp_path / "r" / "fields3") == 3 + 1  # a file per run, a checkpoint
    store = ResultStore(tmp_path / "r")
    assert [r.run_id for r in store.runs()] == list(written)
    assert {r.mesh_id for r in store.runs()} == meshes
    _reads_as_written(store, written)
    assert not store.upgrade()  # once only


def test_a_format_2_store_migrates_when_first_read(tmp_path, caplog):
    """Outside the GUI (CLI, MCP, solves) the first touch migrates, said in the log."""
    _, written = _store(tmp_path / "r", runs=2)
    f2.downgrade(tmp_path / "r")
    with caplog.at_level("WARNING", logger="puzzle.results.store"):
        _reads_as_written(ResultStore(tmp_path / "r"), written)
    assert "from format 2 to 3" in caplog.text
    store = ResultStore(tmp_path / "r")
    rid = store.write_run(run_key="new", study_id="s", study_name="S", solver="calculix",
                          runtime="native", mesh=_mesh(), results=_results(9),
                          started_at=datetime.now(UTC), duration_s=1.0)  # fmt: skip
    _reads_as_written(store, {**written, rid: _results(9)})


def _interrupted(tmp_path, monkeypatch, where: str):
    store, written = _store(tmp_path / "r")
    f2.downgrade(tmp_path / "r")
    before = f2.rows(tmp_path / "r")

    class Cut(Exception):
        pass

    def cut(*a, **k):
        raise Cut(where)

    if where == "copying":  # part way through the rows
        calls = []
        real = sm._PartFile.write

        def write(self, *a):
            calls.append(1)
            if len(calls) == 7:
                raise Cut(where)
            return real(self, *a)

        monkeypatch.setattr(sm._PartFile, "write", write)
    elif where == "committing":
        monkeypatch.setattr(sm, "_commit_fields", cut)
    elif where == "switching":
        from puzzle.project import atomic

        monkeypatch.setattr(atomic, "write_text", cut)
    elif where == "tidying":
        monkeypatch.setattr(ResultStore, "_tidy", cut)
    with pytest.raises(Cut):
        ResultStore(tmp_path / "r").upgrade()
    monkeypatch.undo()
    return written, before


@pytest.mark.parametrize("where", ["copying", "committing", "switching", "tidying"])
def test_an_interrupted_migration_leaves_a_store_that_opens(tmp_path, monkeypatch, where):
    written, before = _interrupted(tmp_path, monkeypatch, where)
    root = tmp_path / "r"
    found = json.loads((root / "store.json").read_text(encoding="utf-8"))["format"]
    assert found == (3 if where == "tidying" else 2)  # switched last, or not at all
    if found == 2:
        assert (root / "fields" / "_delta_log").is_dir()  # the old table untouched
    store = ResultStore(root)  # opens: migrated again, or read as it is
    _reads_as_written(store, written)
    assert f2.rows(root) == before
    store.write_run(run_key="after", study_id="s", study_name="S", solver="calculix",
                    runtime="native", mesh=_mesh(), results=_results(5),
                    started_at=datetime.now(UTC), duration_s=1.0)  # fmt: skip
    assert not (root / "fields").exists()  # a write tidies what a migration left


def test_a_copy_killed_midway_is_made_again(tmp_path):
    """A process killed while copying leaves a half `fields3` (no exception ran): the
    store still says format 2, so the next open removes it and copies again."""
    _, written = _store(tmp_path / "r")
    f2.downgrade(tmp_path / "r")
    half = tmp_path / "r" / "fields3" / "run_id=abc"
    half.mkdir(parents=True)
    (half / "part-0.parquet").write_bytes(b"PAR1 torn")
    (tmp_path / "r" / "fields3" / "_delta_log").mkdir()
    (tmp_path / "r" / "fields3" / "_delta_log" / "00000000000000000000.json").write_text(
        "{", encoding="utf-8")  # fmt: skip
    _reads_as_written(ResultStore(tmp_path / "r"), written)
    assert not half.exists()


@pytest.mark.parametrize("found", [4, 99])
def test_a_newer_store_is_refused_and_left_alone(tmp_path, found):
    _store(tmp_path / "r", runs=1)
    (tmp_path / "r" / "store.json").write_text(json.dumps({"format": found}), encoding="utf-8")
    files = sorted(p for p in (tmp_path / "r").rglob("*") if p.is_file())
    store = ResultStore(tmp_path / "r")
    for call in (store.runs, store.upgrade,
                 lambda: store.write_run(run_key="x", study_id="s", study_name="S",
                                         solver="calculix", runtime="native", mesh=_mesh(),
                                         results=_results(1), started_at=datetime.now(UTC),
                                         duration_s=1.0)):  # fmt: skip
        with pytest.raises(sm.StoreTooNew, match=f"format {found}, written by a newer") as err:
            call()
        assert err.value.code == "store_too_new" and err.value.details["found"] == found
    assert sorted(p for p in (tmp_path / "r").rglob("*") if p.is_file() and p.name != ".lock") \
        == [p for p in files if p.name != ".lock"]  # fmt: skip
    assert not sm.needs_upgrade(tmp_path / "r")


def test_a_store_upgraded_in_the_background_says_so_meanwhile(tmp_path):
    """The GUI upgrades on a worker (`hold_upgrade`): its other readers are told at
    once (`store_upgrading`), never left waiting on the lock or migrating themselves."""
    _, written = _store(tmp_path / "r", runs=2)
    f2.downgrade(tmp_path / "r")
    sm.hold_upgrade(tmp_path / "r")
    try:
        with pytest.raises(sm.StoreUpgrading) as err:
            ResultStore(tmp_path / "r").runs()
        assert err.value.code == "store_upgrading"
        assert ResultStore(tmp_path / "r").upgrade()
    finally:
        sm.release_upgrade(tmp_path / "r")
    _reads_as_written(ResultStore(tmp_path / "r"), written)

    _store(tmp_path / "s", runs=1)
    f2.downgrade(tmp_path / "s")
    sm.hold_upgrade(tmp_path / "s")
    sm.release_upgrade(tmp_path / "s", failed="the disk is full")
    try:  # a failed upgrade reads as a stale store, which the GUI offers to move aside
        with pytest.raises(sm.StaleStore, match="the disk is full"):
            ResultStore(tmp_path / "s").runs()
    finally:
        sm.release_upgrade(tmp_path / "s")


def test_format_3_keeps_a_file_per_run(tmp_path):
    """120 runs: 120 fields files (format 2 had 600, one per run and field), and the
    whole store about one file per run once compacted."""
    store = ResultStore(tmp_path / "r")
    for i in range(120):
        store.write_run(run_key=f"k{i}", study_id="s", study_name="S", solver="calculix",
                        runtime="native", mesh=_mesh(), results=_results(i, n=5),
                        started_at=datetime.now(UTC), duration_s=1.0)  # fmt: skip
    from deltalake import DeltaTable

    live = {t: len(DeltaTable(str(tmp_path / "r" / t)).file_uris())
            for t in ("runs", "meshes", "nodes", "cells", "fields3")}  # fmt: skip
    assert live["fields3"] == 120
    store.compact()
    live = {t: len(DeltaTable(str(tmp_path / "r" / t)).file_uris()) for t in live}
    assert live == {"runs": 1, "meshes": 1, "nodes": 1, "cells": 1, "fields3": 120}


MIGRATE = textwrap.dedent(
    """
    import sys, threading, time
    from pathlib import Path
    sys.path.insert(0, sys.argv[3])
    from store_format2 import memory_now as now
    from puzzle.results import ResultStore

    store = ResultStore(Path(sys.argv[1]))
    from deltalake import DeltaTable  # delta-rs's runtime started (40 MB whatever the size)
    DeltaTable(sys.argv[1] + "/runs").to_pyarrow_table()
    base = high = now(); done = threading.Event()
    def sample():
        global high
        while not done.is_set():
            high = max(high, now()); time.sleep(0.002)
    t = threading.Thread(target=sample); t.start()
    start = time.perf_counter()
    store.upgrade()
    took = time.perf_counter() - start
    done.set(); t.join()
    print(max(high, now()) - base, took)
    """
)
BUILD = textwrap.dedent(
    """
    import sys
    from datetime import UTC, datetime
    from pathlib import Path
    import numpy as np
    sys.path.insert(0, sys.argv[3])
    import store_format2 as f2
    from puzzle.mesh.mesh import CellBlock, Mesh
    from puzzle.results import Field, Results, ResultStore, StepResult

    nodes, steps = 100_000, int(sys.argv[2])
    ids = np.arange(1, nodes + 1, dtype=np.int64)
    mesh = Mesh(node_ids=ids, coords=np.random.rand(nodes, 3))
    mesh.blocks = [CellBlock("tet4", 3, (3, 1), np.array([1]), np.array([[1, 2, 3, 4]]))]
    run = [StepResult(k + 1, "transient", float(k), None,
                      {n: Field(n, "node", ["x", "y", "z"], ids, np.random.rand(nodes, 3))
                       for n in ("displacement", "stress")}) for k in range(steps)]
    store = ResultStore(Path(sys.argv[1]))
    store.write_run(run_key="k", study_id="s", study_name="S", solver="calculix",
                    runtime="native", mesh=mesh, results=Results("calculix", run),
                    started_at=datetime.now(UTC), duration_s=1.0)
    f2.downgrade(Path(sys.argv[1]))
    print(sum(f.values.nbytes for s in run for f in s.fields.values()))
    """
)


def _migration_extra(tmp_path, steps: int) -> tuple[int, int]:
    """(bytes of values, extra bytes while migrating them), each in a fresh process."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    if sys.platform.startswith("linux"):
        # Resident memory there counts what the allocators keep after a free (Arrow's
        # jemalloc for a second, glibc's heap until trimmed): on GitHub's Linux 164 MB
        # extra for 192 MB of values against 101 for 19. Hand freed memory back at once,
        # so what is read is what the migration holds.
        env |= {"ARROW_DEFAULT_MEMORY_POOL": "system", "MALLOC_TRIM_THRESHOLD_": "0",
                "MALLOC_MMAP_THRESHOLD_": "131072", "MALLOC_ARENA_MAX": "1"}  # fmt: skip
    here, root = str(Path(__file__).parent), str(tmp_path / f"m{steps}")
    run = dict(capture_output=True, text=True, encoding="utf-8", env=env, timeout=600, check=True)
    size = int(subprocess.run([sys.executable, "-c", BUILD, root, str(steps), here], **run).stdout)
    extra, _ = subprocess.run([sys.executable, "-c", MIGRATE, root, str(steps), here],
                              **run).stdout.split()  # fmt: skip
    return size, int(extra)


@pytest.mark.skipif(sys.platform == "darwin", reason="no current memory to read here")
def test_a_migration_holds_a_row_not_a_store(tmp_path):
    """About 120 MB extra whatever the store's size (Arrow's and delta-rs's pools): 128,
    131 and 112 MB for 19, 192 and 576 MB of values on the maintainer's machine."""
    small, small_extra = _migration_extra(tmp_path, 4)  # 19 MB of values
    big, big_extra = _migration_extra(tmp_path, 40)  # 192 MB
    growth = (big_extra - small_extra) / (big - small)
    assert growth < 0.25, (f"migrating {big / 1e6:.0f} MB took {big_extra / 1e6:.0f} MB extra, "
                           f"{small / 1e6:.0f} MB took {small_extra / 1e6:.0f} MB")  # fmt: skip
    assert big_extra < 300e6
