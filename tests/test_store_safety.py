"""A store that survives crashes and two writers (ADR 0090 package 2).

Each command, proposal accept, undo and version restore is stopped by a failure at
every write it makes (the n-th temporary file, replace or history append), and the
project must then open, wholly as it was before or wholly after, with history.jsonl
agreeing. Two sessions on one folder never lose an edit; the lock is taken from a
dead holder at once and never from a live one. Then the findings one by one: a torn
history line, half-accepted proposals, undo that strands a reference, a mesh stored
twice, projects from a newer Puzzle and migrations, restores from missing blobs,
exports in versions, archives that copy what they then drop, the jobs and trust files,
a new project over an old one, and a group inside itself.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import tomllib
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest

from puzzle.commands import Actor, Session
from puzzle.errors import CODES, PuzzleError
from puzzle.model.schema import Component, Material, Project, TreeGroup
from puzzle.project import atomic, history, store, versions
from puzzle.project.layout import HISTORY, JOURNAL, LOCK, TORN

EXAMPLE = Path(__file__).parents[1] / "examples" / "cantilever"
FORCE = "physics.0.features.1.values.y.value"
AGENT = Actor("agent", "test-agent")


@pytest.fixture(autouse=True)
def _no_auto_versions():
    from puzzle.settings import current

    current().set("versions.auto", False)  # fewer writes to count; restore forces its own


def _copy(tmp_path: Path, name: str = "p") -> Path:
    dest = tmp_path / name
    shutil.copytree(EXAMPLE, dest, ignore=shutil.ignore_patterns(
        "results*", ".puzzle", "history.jsonl", "versions"))  # fmt: skip
    return dest


def _files(root: Path) -> dict[str, dict]:
    """The project's own files, parsed (so formatting never matters)."""
    out = {"project.toml": tomllib.loads((root / "project.toml").read_text(encoding="utf-8"))}
    for path in sorted(root.glob("*/*.toml")):
        if path.parent.name in ("results", "versions"):
            continue
        out[f"{path.parent.name}/{path.name}"] = tomllib.loads(path.read_text(encoding="utf-8"))
    return out


def _entries(root: Path) -> list[dict]:
    return history.entries(root)


def _edit(value: float) -> dict:
    return {"command": "edit", "object": "Cantilever",
            "ops": [{"op": "set", "path": FORCE, "value": value}]}  # fmt: skip


def _material(name: str, oid: str | None = None) -> dict:
    data = {"name": name, "properties": {}}
    if oid:
        data["id"] = oid
    return {"command": "create", "kind": "material", "data": data}


class _Fail:
    """Raise OSError on the n-th write call (temporary file, replace or append)."""

    def __init__(self, monkeypatch, at: int | None) -> None:
        self.calls, self.at = 0, at
        for name in ("write_temp", "replace", "append_line"):
            real = getattr(atomic, name)
            monkeypatch.setattr(atomic, name, self._wrap(real))

    def _wrap(self, real):
        def call(*args, **kwargs):
            self.calls += 1
            if self.at is not None and self.calls == self.at:
                raise OSError(f"injected failure at write {self.at}")
            return real(*args, **kwargs)

        return call


def _crash_at_every_write(tmp_path, monkeypatch, setup, act, *, entries_after: int):
    """Run `act` once to count its writes, then again on a fresh copy failing at each
    one in turn; after each, reopen and check: before or after, never a mix, and the
    history has the new entries exactly when the files changed."""
    base = _copy(tmp_path, "base")
    setup(base)
    probe = tmp_path / "probe"
    shutil.copytree(base, probe)
    before_files, before_n = _files(probe), len(_entries(probe))
    with monkeypatch.context() as m:
        counter = _Fail(m, None)
        act(probe)
        total = counter.calls
    after_files = _files(probe)
    assert after_files != before_files and len(_entries(probe)) == before_n + entries_after
    assert total >= 3
    seen = set()
    for n in range(1, total + 1):
        root = tmp_path / f"crash{n}"
        shutil.copytree(base, root)
        with monkeypatch.context() as m:
            _Fail(m, n)
            with contextlib.suppress(OSError, PuzzleError):
                act(root)
        Session(root)  # opening finishes a change cut short
        files, count = _files(root), len(_entries(root))
        assert not (root / JOURNAL).exists()
        if files == before_files:
            assert count == before_n, f"failure at write {n}: history moved, files didn't"
            seen.add("before")
        else:
            assert files == after_files, f"failure at write {n} left a mix"
            assert count == before_n + entries_after, f"failure at write {n}: history behind"
            seen.add("after")
        assert not list(root.glob("*/.*.tmp")), f"failure at write {n} left temporary files"
    assert seen == {"before", "after"}  # some failures were before the commit point


# --- BE-4: atomic saves of only what changed ---------------------------------------------


def test_a_batch_stopped_at_any_write_is_before_or_after(tmp_path, monkeypatch):
    a, b = str(uuid4()), str(uuid4())

    def setup(root):
        Session(root).submit(_material("Tmp"))

    def act(root):
        Session(root).submit_batch([_material("A", a), _material("B", b), _edit(-7),
                                    {"command": "delete", "object": "Tmp"}])  # fmt: skip

    _crash_at_every_write(tmp_path, monkeypatch, setup, act, entries_after=1)


def test_only_changed_files_are_written(tmp_path, monkeypatch):
    root = _copy(tmp_path)
    s = Session(root)
    s.submit(_edit(-1))
    written = []
    real = atomic.write_temp

    def write_temp(path, *a, **k):
        written.append(Path(path))
        return real(path, *a, **k)

    monkeypatch.setattr(atomic, "write_temp", write_temp)
    s.submit(_edit(-2))
    objects = [p for p in written if p.suffix == ".toml" and p.name != "project.toml"]
    assert [p.parent.name for p in objects] == ["component"]  # one file of seven


def test_an_emptied_object_file_names_itself(tmp_path):
    root = _copy(tmp_path)
    victim = next((root / "component").glob("*.toml"))
    victim.write_text("", encoding="utf-8")
    with pytest.raises(PuzzleError) as err:
        Session(root)
    assert err.value.code == "invalid" and victim.name in err.value.message
    assert "Restore a version" in err.value.message


# --- BE-5: a torn history line -----------------------------------------------------------


def test_a_torn_last_history_line_is_set_aside(tmp_path):
    root = _copy(tmp_path)
    s = Session(root)
    s.submit(_edit(-1))
    with open(root / HISTORY, "a", encoding="utf-8") as f:
        f.write('{"seq": 2, "type": "comm')
    out = Session(root).submit(_edit(-2))
    assert out.seq == 2
    seqs = [e["seq"] for e in _entries(root)]
    assert seqs == [1, 2]
    assert '{"seq": 2, "type": "comm' in (root / TORN).read_text(encoding="utf-8")
    assert all(
        json.loads(line) for line in (root / HISTORY).read_text(encoding="utf-8").splitlines()
    )


def test_a_damaged_middle_line_is_a_problem_not_a_crash(tmp_path):
    root = _copy(tmp_path)
    s = Session(root)
    s.submit(_edit(-1))
    good = (root / HISTORY).read_text(encoding="utf-8")
    (root / HISTORY).write_text('{"seq": 9, "ty\n' + good, encoding="utf-8")
    s = Session(root)
    out = s.submit(_edit(-2))
    assert out.status == "applied"
    assert "damaged_history" in [p.code for p in s.project.problems()]
    s.undo()  # the history still works


# --- BE-6: the lock ------------------------------------------------------------------------


def _dead_pid() -> int:
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def _hold(root: Path, pid: int, host: str = atomic.HOST, age: float = 0.0) -> None:
    path = root / LOCK
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": pid, "host": host, "token": "theirs"}), encoding="utf-8")
    if age:
        old = time.time() - age
        os.utime(path, (old, old))


def test_a_dead_holders_lock_is_taken_at_once(tmp_path, monkeypatch):
    root = _copy(tmp_path)
    _hold(root, _dead_pid(), age=1.0)
    t = time.monotonic()
    Session(root).submit(_edit(-3))
    assert time.monotonic() - t < 5.0 and not (root / LOCK).exists()


def test_a_live_slow_holder_keeps_its_lock(tmp_path, monkeypatch):
    import puzzle.commands.session as session_module

    monkeypatch.setattr(session_module, "LOCK_WAIT_S", 0.3)
    root = _copy(tmp_path)
    _hold(root, os.getpid(), age=10_000)  # alive here, however old the file
    with pytest.raises(PuzzleError) as err:
        Session(root).submit(_edit(-3))
    assert err.value.code == "busy"
    assert (
        json.loads((root / LOCK).read_text(encoding="utf-8"))["token"] == "theirs"
    )  # never removed


def test_another_hosts_lock_goes_stale_by_age_only(tmp_path, monkeypatch):
    import puzzle.commands.session as session_module

    monkeypatch.setattr(session_module, "LOCK_WAIT_S", 0.3)
    root = _copy(tmp_path)
    _hold(root, 1, host="elsewhere", age=10)
    with pytest.raises(PuzzleError):
        Session(root).submit(_edit(-3))
    _hold(root, 1, host="elsewhere", age=atomic.STALE_S + 5)
    assert Session(root).submit(_edit(-3)).status == "applied"


def test_a_holder_that_lost_its_lock_writes_nothing(tmp_path):
    root = _copy(tmp_path)
    a, b = Session(root), Session(root)
    a.submit(_material("X"))
    before = _files(root)
    from puzzle.commands.model import parse_command
    from puzzle.commands.session import _execute, _snapshot

    with pytest.raises(PuzzleError) as err, a._locked():  # noqa: PT012
        _hold(root, 1, host="elsewhere")  # someone took it from us meanwhile
        snap = _snapshot(a.project)
        _execute(a.project, parse_command(_material("Y")))
        a._record({"command": "create"}, Actor("user"), snap)
    assert err.value.code == "lock_lost"
    assert _files(root) == before
    assert (
        json.loads((root / LOCK).read_text(encoding="utf-8"))["token"] == "theirs"
    )  # theirs, kept
    (root / LOCK).unlink()
    b.submit(_edit(-4))


def test_two_sessions_never_lose_an_edit(tmp_path):
    root = _copy(tmp_path)
    sessions = [Session(root) for _ in range(2)]
    errors = []

    def work(s, tag):
        try:
            for k in range(8):
                s.submit(_material(f"{tag}{k}"))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [
        threading.Thread(target=work, args=(s, t)) for s, t in zip(sessions, "AB", strict=True)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    names = {m.name for m in Session(root).project.of_type(Material)}
    assert {f"{t}{k}" for t in "AB" for k in range(8)} <= names
    assert [e["seq"] for e in _entries(root)] == list(range(1, 17))


# --- BE-7: accepting a proposal --------------------------------------------------------


def test_accept_is_all_or_nothing(tmp_path):
    root = _copy(tmp_path)
    s = Session(root)
    s.submit(_material("Tmp"))
    proposal = s.propose([_material("NewMat"), {"command": "delete", "object": "Tmp"}], AGENT)
    s.submit({"command": "delete", "object": "Tmp"})  # the user deletes it by hand
    for _ in range(2):
        with pytest.raises(PuzzleError) as err:
            s.accept(proposal.proposal)
        assert err.value.code == "not_found"
    names = [m.name for m in Session(root).project.of_type(Material)]
    assert "NewMat" not in names
    assert [p["id"] for p in s.proposals()] == [proposal.proposal]  # still open


def test_an_accept_is_one_undo(tmp_path):
    root = _copy(tmp_path)
    s = Session(root)
    proposal = s.propose([_material("P1"), _material("P2"), _edit(-9)], AGENT)
    s.accept(proposal.proposal)
    assert not s.proposals()
    s.undo()
    names = {m.name for m in Session(root).project.of_type(Material)}
    assert not names & {"P1", "P2"}


def test_an_accept_stopped_at_any_write_is_before_or_after(tmp_path, monkeypatch):
    ids = [str(uuid4()), str(uuid4())]
    state = {}

    def setup(root):
        s = Session(root)
        state["p"] = s.propose([_material("P1", ids[0]), _material("P2", ids[1]), _edit(-9)],
                               AGENT).proposal  # fmt: skip

    def act(root):
        Session(root).accept(state["p"])

    _crash_at_every_write(tmp_path, monkeypatch, setup, act, entries_after=2)


# --- BE-8: undo that would strand a reference -------------------------------------------


def test_undo_refuses_to_remove_what_later_changes_use(tmp_path):
    root = _copy(tmp_path)
    s = Session(root)
    s.submit({"command": "set_autonomy", "level": "act_in_budget"})
    made = s.submit(_material("Agent steel"), AGENT)
    comp = next(iter(s.project.of_type(Component)))
    use = {"op": "set", "path": "materials.0.material", "value": made.created[0]["id"]}
    s.submit({"command": "edit", "object": "Cantilever", "ops": [use]})
    with pytest.raises(PuzzleError) as err:
        s.undo(AGENT)  # its own latest: the material, which the user has used since
    assert err.value.code == "not_undoable" and err.value.details["objects"] == [comp.name]
    assert not [p for p in Session(root).project.problems() if p.code == "missing_reference"]


def test_an_undo_stopped_at_any_write_is_before_or_after(tmp_path, monkeypatch):
    def setup(root):
        s = Session(root)
        s.submit(_material("Tmp"))
        s.submit(_edit(-12))

    def act(root):
        Session(root).undo()

    _crash_at_every_write(tmp_path, monkeypatch, setup, act, entries_after=1)


# --- BE-15: restore ---------------------------------------------------------------------


def test_a_restore_with_a_missing_blob_changes_nothing(tmp_path):
    root = _copy(tmp_path)
    s = Session(root)
    v = s.save_version(label="start")
    s.submit(_edit(-5))
    s.submit(_material("Later"))
    changed = next(rel for rel, d in v.files.items() if versions.hashes(root).get(rel) != d)
    (root / "versions" / "blobs" / v.files[changed]).unlink()
    before, count = _files(root), len(_entries(root))
    with pytest.raises(PuzzleError) as err:
        s.restore_version(v.number)
    assert err.value.code == "invalid" and "missing" in err.value.message
    assert _files(root) == before and len(_entries(root)) == count
    assert not list(root.glob("**/.*.tmp"))


def test_a_restore_with_a_damaged_blob_changes_nothing(tmp_path):
    import zlib

    root = _copy(tmp_path)
    s = Session(root)
    v = s.save_version(label="start")
    s.submit(_edit(-5))
    changed = next(rel for rel, d in v.files.items() if versions.hashes(root).get(rel) != d)
    blob = root / "versions" / "blobs" / v.files[changed]
    blob.write_bytes(zlib.compress(b"something else"))
    before = _files(root)
    with pytest.raises(PuzzleError):
        s.restore_version(v.number)
    assert _files(root) == before


def test_a_restore_stopped_at_any_write_is_before_or_after(tmp_path, monkeypatch):
    state = {}

    def setup(root):
        s = Session(root)
        state["v"] = s.save_version(label="start").number
        s.submit(_edit(-5))
        s.submit(_material("Later"))

    def act(root):
        Session(root).restore_version(state["v"])

    _crash_at_every_write(tmp_path, monkeypatch, setup, act, entries_after=1)


def test_undoing_a_restore_is_atomic_too(tmp_path, monkeypatch):
    state = {}

    def setup(root):
        s = Session(root)
        state["v"] = s.save_version(label="start").number
        s.submit(_material("Later"))
        s.restore_version(state["v"])

    def act(root):
        Session(root).undo()

    _crash_at_every_write(tmp_path, monkeypatch, setup, act, entries_after=1)


# --- BE-13: exports stay out of versions -------------------------------------------------


def test_exports_are_left_out_of_versions_and_kept_by_a_restore(tmp_path):
    root = _copy(tmp_path)
    s = Session(root)
    v = s.save_version(label="start")
    (root / "exports" / "paraview" / "r1").mkdir(parents=True)
    (root / "exports" / "paraview" / "r1" / "shown.vtu").write_bytes(b"x" * 1000)
    w = s.save_version(label="with an export")
    assert not [f for f in w.files if f.startswith("exports/")]
    s.restore_version(v.number)
    assert (root / "exports" / "paraview" / "r1" / "shown.vtu").exists()


# --- BE-10: a mesh stored twice ------------------------------------------------------------


def _mesh():
    from puzzle.mesh.mesh import CellBlock, Group, Mesh

    coords = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 1, 1]], float)
    mesh = Mesh(node_ids=np.arange(1, 6, dtype=np.int64), coords=coords)
    mesh.blocks = [
        CellBlock("tet4", 3, (3, 1), np.array([10, 11]), np.array([[1, 2, 3, 4], [2, 3, 4, 5]])),
        CellBlock("tri3", 2, (2, 7), np.array([20]), np.array([[1, 2, 3]])),
    ]
    mesh.groups = {"Part": Group("Part", 3, [(3, 1)], np.arange(1, 6, dtype=np.int64))}
    return mesh


def test_an_interrupted_mesh_write_is_written_once_again(tmp_path, monkeypatch):
    from puzzle.results.store import ResultStore

    store_ = ResultStore(tmp_path / "results")
    real = store_._append

    def fail_on_meshes(name, table):
        if name == "meshes":
            raise OSError("disk full")
        return real(name, table)

    monkeypatch.setattr(store_, "_append", fail_on_meshes)
    with pytest.raises(OSError):
        store_.write_mesh(_mesh())
    monkeypatch.setattr(store_, "_append", real)
    mid = store_.write_mesh(_mesh())
    got = store_.read_mesh(mid)
    assert len(got.blocks) == 2 and list(got.groups) == ["Part"]


def test_a_store_holding_doubled_blocks_reads_each_once(tmp_path):
    import pyarrow as pa

    from puzzle.results.store import SCHEMAS, ResultStore, _cells_table, mesh_id

    store_ = ResultStore(tmp_path / "results")
    mesh = _mesh()
    mid = store_.write_mesh(mesh)
    store_._append("cells", pa.concat_tables([_cells_table(mid, i, b)
                                               for i, b in enumerate(mesh.blocks)]))  # fmt: skip
    store_._append("groups", pa.Table.from_pylist(
        [{"mesh_id": mid, "name": "Part", "dim": 3, "entity_tags": [1],
          "node_ids": [1, 2, 3, 4, 5]}], schema=SCHEMAS["groups"]))  # fmt: skip
    assert mid == mesh_id(mesh)
    assert len(store_.read_mesh(mid).blocks) == 2


def test_two_writers_store_a_mesh_once(tmp_path):
    from puzzle.results.store import ResultStore

    errors = []

    def write():
        try:
            ResultStore(tmp_path / "results").write_mesh(_mesh())
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=write) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    store_ = ResultStore(tmp_path / "results")
    mid = store_.write_mesh(_mesh())
    assert store_._rows("meshes").num_rows == 1
    assert store_._rows("cells").num_rows == 2 and len(store_.read_mesh(mid).blocks) == 2


# --- BE-14 / ADR-E-18: schema versions ----------------------------------------------------


def test_a_project_from_a_newer_puzzle_says_so(tmp_path):
    root = _copy(tmp_path)
    head = tomllib.loads((root / "project.toml").read_text(encoding="utf-8"))
    import tomli_w

    from puzzle.model.schema import SCHEMA_VERSION

    head["schema_version"] = SCHEMA_VERSION + 1
    f = next((root / "study").glob("*.toml"))
    f.write_text("an_option_from_the_future = true\n" + f.read_text(encoding="utf-8"),
                 encoding="utf-8")  # fmt: skip
    (root / "project.toml").write_text(tomli_w.dumps(head), encoding="utf-8")
    with pytest.raises(PuzzleError) as err:
        Session(root)
    assert err.value.code == "newer_schema"
    assert (
        f"schema {SCHEMA_VERSION + 1}; this build reads up to {SCHEMA_VERSION}" in err.value.message
    )


def test_an_unknown_field_names_its_file_and_field(tmp_path):
    root = _copy(tmp_path)
    f = next((root / "study").glob("*.toml"))
    f.write_text("an_option_from_the_future = true\n" + f.read_text(encoding="utf-8"),
                 encoding="utf-8")  # fmt: skip
    with pytest.raises(PuzzleError) as err:
        store.load_project(root)
    assert err.value.code == "unknown_field"
    assert f.name in err.value.message and "an_option_from_the_future" in err.value.message
    assert "newer Puzzle" in err.value.message


def test_an_older_project_is_migrated_kept_and_written_at_the_new_version(tmp_path, monkeypatch):
    from puzzle.model import schema
    from puzzle.project import migrations

    root = _copy(tmp_path)
    old = schema.SCHEMA_VERSION
    # Version old + 1 renamed a material's `name`... from `label`: an old file has `label`.
    mat = Session(root).submit(_material("Old steel")).created[0]["id"]
    path = root / "material" / f"{mat}.toml"
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    data["label"] = data.pop("name")
    import tomli_w

    path.write_text(tomli_w.dumps(data), encoding="utf-8")

    def rename(header, objects):
        for o in objects:
            if o["kind"] == "material" and "label" in o["data"]:
                o["data"]["name"] = o["data"].pop("label")

    for module in (schema, store, migrations):
        monkeypatch.setattr(module, "SCHEMA_VERSION", old + 1)
    monkeypatch.setattr(migrations, "MIGRATIONS", {old: rename})
    import puzzle.commands.session as session_module

    monkeypatch.setattr(session_module, "SCHEMA_VERSION", old + 1)
    s = Session(root)
    assert s.project.migrated_from == old
    assert any(m.name == "Old steel" for m in s.project.of_type(Material))
    s.submit(_edit(-2))
    head = tomllib.loads((root / "project.toml").read_text(encoding="utf-8"))
    assert head["schema_version"] == old + 1
    assert "label" not in tomllib.loads(path.read_text(encoding="utf-8"))
    kept = [v for v in versions.versions(root) if v.trigger == "migrated"]
    assert len(kept) == 1 and kept[0].words == {"found": old}


# --- BE-16: archives copy only what they keep -----------------------------------------------


def test_an_archive_without_results_never_copies_them(tmp_path, monkeypatch):
    from puzzle.project import archive

    root = _copy(tmp_path)
    (root / "results" / "fields").mkdir(parents=True)
    (root / "results" / "fields" / "big.parquet").write_bytes(b"x" * 100)
    visited = []
    real_tree, real_copy = shutil.copytree, shutil.copy2

    def copy(s, d, **k):
        visited.append(str(s))
        return real_copy(s, d, **k)

    def copytree(src, dst, *args, **kwargs):
        if args:  # copytree's own recursion: the recorder is passed on already
            return real_tree(src, dst, *args, **kwargs)
        return real_tree(src, dst, **{**kwargs, "copy_function": copy})

    monkeypatch.setattr(shutil, "copytree", copytree)
    archive.write(root, tmp_path / "out.puzzle", archive.Include(results="none"))
    assert visited and not [v for v in visited if f"{os.sep}results{os.sep}" in v]


# --- BE-19: the jobs file and the trust list ---------------------------------------------


def test_jobs_file_writers_wait_for_each_other(tmp_path):
    from puzzle.jobs import pending

    root = tmp_path / "p"
    (root / ".puzzle").mkdir(parents=True)
    ctx = pending.RunContext(root, "s", root / ".puzzle" / "runs" / "w")
    done = threading.Event()
    with atomic.locked(root / pending.FILE):  # another process changing it
        t = threading.Thread(target=lambda: (pending.add(ctx, "j2", "local", root, {}),
                                             done.set()))  # fmt: skip
        t.start()
        time.sleep(0.3)
        assert not done.is_set()
        jobs = [{"job": "j1", "study": "s", "workdir": "w", "folder": "f"}]
        (root / pending.FILE).write_text(json.dumps({"jobs": jobs}), encoding="utf-8")
    t.join(5)
    assert {e["job"] for e in pending.entries(root)} == {"j1", "j2"}


def test_a_failed_trust_write_keeps_the_list(tmp_path, monkeypatch):
    from puzzle import trust

    trust.trust(tmp_path / "one")
    real = atomic.replace

    def replace(src, dst):
        if Path(dst).name == trust.FILE:
            raise OSError("disk full")
        return real(src, dst)

    monkeypatch.setattr(atomic, "replace", replace)
    with pytest.raises(OSError):
        trust.trust(tmp_path / "two")
    monkeypatch.undo()
    assert trust.is_trusted(tmp_path / "one") and not trust.is_trusted(tmp_path / "two")


# --- GUI-19: a new project over an old one ---------------------------------------------


def test_a_new_project_never_replaces_one(tmp_path):
    root = _copy(tmp_path)
    before = _files(root)
    with pytest.raises(PuzzleError) as err:
        store.create_project(Project(name="Fresh"), root)
    assert err.value.code == "exists" and "Cantilever" in err.value.message
    assert _files(root) == before
    other = tmp_path / "notes"
    other.mkdir()
    (other / "readme.txt").write_text("mine", encoding="utf-8")
    store.create_project(Project(name="Fresh"), other)  # a folder of other things is fine
    assert (other / "readme.txt").read_text(encoding="utf-8") == "mine"


# --- GUI-21: a group inside a group it holds ---------------------------------------------


def test_a_group_cannot_go_inside_a_group_it_holds(tmp_path):
    root = _copy(tmp_path)
    s = Session(root)
    p = s.submit({"command": "create", "kind": "group",
                  "data": {"name": "P", "section": "Studies"}}).created[0]["id"]  # fmt: skip
    data = {"name": "C", "section": "Studies", "members": [p]}
    c = s.submit({"command": "create", "kind": "group", "data": data}).created[0]["id"]
    with pytest.raises(PuzzleError) as err:
        s.submit({"command": "edit", "object": p, "ops": [
            {"op": "set", "path": "members", "value": [c]}]})  # fmt: skip
    assert err.value.code == "group_cycle" and "P" in err.value.message
    groups = {g.name: g for g in Session(root).project.of_type(TreeGroup)}
    assert groups["P"].members == []


def test_a_group_loop_on_disk_is_a_problem(tmp_path):
    root = _copy(tmp_path)
    project = store.load_project(root)
    a, b = uuid4(), uuid4()
    project.add(TreeGroup(id=a, name="A", section="Studies", members=[str(b)]))
    project.add(TreeGroup(id=b, name="B", section="Studies", members=[str(a)]))
    store.save_project(project, root)
    problems = [p for p in Session(root).project.problems() if p.code == "group_cycle"]
    assert problems and set(problems[0].details["groups"]) == {"A", "B"}


def test_new_codes_are_listed():
    for code in ("lock_lost", "write_failed", "newer_schema", "unknown_field", "group_cycle",
                 "damaged_history"):  # fmt: skip
        assert code in CODES
