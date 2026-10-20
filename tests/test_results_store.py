from datetime import UTC, datetime

import numpy as np
import pytest
from deltalake import DeltaTable

from puzzle.mesh.mesh import CellBlock, Group, Mesh
from puzzle.results import Field, Results, ResultStore, StepResult


def _mesh(shift: float = 0.0) -> Mesh:
    coords = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 1, 1]], float) + shift
    mesh = Mesh(node_ids=np.arange(1, 6, dtype=np.int64), coords=coords)
    mesh.blocks = [
        CellBlock("tet4", 3, (3, 1), np.array([10, 11]), np.array([[1, 2, 3, 4], [2, 3, 4, 5]])),
        CellBlock("tri3", 2, (2, 7), np.array([20]), np.array([[1, 2, 3]])),
    ]
    mesh.groups = {
        "Part": Group("Part", 3, [(3, 1)], np.arange(1, 6, dtype=np.int64)),
        "Part/Base": Group("Part/Base", 2, [(2, 7)], np.array([1, 2, 3], dtype=np.int64)),
    }
    return mesh


def _results() -> Results:
    ids = np.arange(1, 6, dtype=np.int64)
    disp = Field(
        "displacement", "node", ["x", "y", "z"], ids, np.arange(15, dtype=float).reshape(5, 3)
    )
    stress = Field("stress", "node", ["xx", "yy", "zz", "xy", "yz", "zx"], ids, np.ones((5, 6)))
    temperature = Field("temperature", "node", [""], ids, np.full((5, 1), 293.15))
    static = StepResult(1, "stationary", 1.0, None, {"displacement": disp, "stress": stress})
    heat = StepResult(2, "stationary", 1.0, None, {"temperature": temperature})
    modes = [
        StepResult(
            3,
            "modal",
            f,
            m,
            {
                "displacement": Field(
                    "displacement", "node", ["x", "y", "z"], ids, np.full((5, 3), m)
                )
            },
        )
        for m, f in ((1, 203.9), (2, 204.0))
    ]
    return Results("calculix", [static, heat, *modes])


def _write(store: ResultStore, key="k1", mesh=None, results=None) -> str:
    return store.write_run(
        run_key=key,
        study_id="s1",
        study_name="Static",
        solver="calculix",
        runtime="native",
        mesh=mesh or _mesh(),
        results=results or _results(),
        started_at=datetime(2026, 10, 17, tzinfo=UTC),
        duration_s=2.5,
    )


def test_results_round_trip(tmp_path):
    store = ResultStore(tmp_path)
    original = _results()
    run_id = _write(store, results=original)
    back = store.read_results(run_id)

    assert back.solver == "calculix"
    assert [(s.index, s.kind, s.value, s.mode) for s in back.steps] == [
        (s.index, s.kind, s.value, s.mode) for s in original.steps
    ]
    for a, b in zip(original.steps, back.steps, strict=True):
        assert a.fields.keys() == b.fields.keys()
        for name in a.fields:
            fa, fb = a.fields[name], b.fields[name]
            assert fa.components == fb.components
            assert np.array_equal(fa.ids, fb.ids)
            assert np.array_equal(fa.values, fb.values)
    assert [m.mode for m in back.modes()] == [1, 2]


def test_mesh_round_trip(tmp_path):
    store = ResultStore(tmp_path)
    run_id = _write(store)
    mesh = store.read_mesh(store.get_run(run_id).mesh_id)
    original = _mesh()
    assert np.array_equal(mesh.node_ids, original.node_ids)
    assert np.array_equal(mesh.coords, original.coords)
    assert [(b.element, b.dim, b.entity) for b in mesh.blocks] == [
        (b.element, b.dim, b.entity) for b in original.blocks
    ]
    assert np.array_equal(mesh.blocks[0].connectivity, original.blocks[0].connectivity)
    assert mesh.groups.keys() == original.groups.keys()
    assert np.array_equal(mesh.groups["Part/Base"].node_ids, [1, 2, 3])


def test_a_mesh_is_stored_once(tmp_path):
    store = ResultStore(tmp_path)
    a, b = _write(store, "k1"), _write(store, "k2")
    c = _write(store, "k3", mesh=_mesh(shift=1.0))
    ids = {store.get_run(r).mesh_id for r in (a, b, c)}
    assert len(ids) == 2
    assert DeltaTable(str(tmp_path / "meshes")).to_pyarrow_table().num_rows == 2


def test_runs_and_cache_lookup(tmp_path):
    store = ResultStore(tmp_path)
    assert store.runs() == [] and store.find_run("k1") is None
    first = _write(store, "k1")
    _write(store, "k2")
    latest = _write(store, "k1")
    assert [r.run_key for r in store.runs()] == ["k1", "k2", "k1"]
    assert store.find_run("k1").run_id == latest != first
    assert store.get_run(first).duration_s == 2.5


def test_fields_are_partitioned_by_run_and_field(tmp_path):
    store = ResultStore(tmp_path)
    run_id = _write(store)
    assert (tmp_path / "fields" / f"run_id={run_id}" / "field=displacement").is_dir()


def test_compaction_keeps_the_data(tmp_path):
    store = ResultStore(tmp_path)
    runs = [_write(store, f"k{i}") for i in range(3)]
    store.compact()
    assert len(store.runs()) == 3
    assert np.array_equal(
        store.read_results(runs[1]).steps[0].fields["displacement"].values,
        _results().steps[0].fields["displacement"].values,
    )


def test_unknown_run(tmp_path):
    with pytest.raises(KeyError):
        ResultStore(tmp_path).read_results("nope")


def test_read_one_field_at_one_step(tmp_path):
    from puzzle.mesh.mesh import CellBlock, Mesh
    from puzzle.results.model import Field, Results, StepResult
    from puzzle.results.store import ResultStore

    mesh = Mesh(node_ids=np.arange(1, 5), coords=np.eye(4, 3))
    mesh.blocks.append(CellBlock("tet4", 3, (3, 1), np.array([1]), np.array([[1, 2, 3, 4]])))
    steps = [
        StepResult(
            1,
            "transient",
            t,
            fields={
                "temperature": Field(
                    "temperature", "node", [""], mesh.node_ids, np.full((4, 1), t)
                ),
                "flux": Field("flux", "node", ["x", "y", "z"], mesh.node_ids, np.ones((4, 3))),
            },
        )
        for t in (1.0, 2.0, 3.0)
    ]
    store = ResultStore(tmp_path / "r")
    run = store.write_run(
        run_key="k",
        study_id="s",
        study_name="s",
        solver="x",
        runtime="r",
        mesh=mesh,
        results=Results("x", steps),
        started_at=datetime.now(UTC),
        duration_s=1.0,
    )
    assert store.read_field(run, "temperature").values[:, 0].tolist() == [3.0] * 4
    assert store.read_field(run, "temperature", 0).values[0, 0] == 1.0
    assert store.read_field(run, "flux").components == ["x", "y", "z"]


def _transient(store, ids_by_step):
    """A run whose 'u' field has the given ids at each step (values = step time)."""
    mesh = Mesh(node_ids=np.arange(1, 5), coords=np.eye(4, 3))
    mesh.blocks.append(CellBlock("tet4", 3, (3, 1), np.array([1]), np.array([[1, 2, 3, 4]])))
    steps = [
        StepResult(
            1,
            "transient",
            float(t),
            fields={"u": Field("u", "node", ["x", "y"], np.array(ids), np.full((len(ids), 2), t))},
        )
        for t, ids in enumerate(ids_by_step, start=1)
    ]
    return store.write_run(
        run_key="k",
        study_id="s",
        study_name="s",
        solver="x",
        runtime="r",
        mesh=mesh,
        results=Results("x", steps),
        started_at=datetime.now(UTC),
        duration_s=1.0,
    )


def test_ids_are_stored_once_per_field(tmp_path):
    store = ResultStore(tmp_path)
    run = _transient(store, [[1, 2, 3, 4]] * 3 + [[2, 4]] + [[2, 4]])
    table = DeltaTable(str(tmp_path / "fields")).to_pyarrow_table().sort_by("ordinal")
    assert table.column("ids_from").to_pylist() == [0, 0, 0, 3, 3]
    assert [v is not None for v in table.column("ids").to_pylist()] == [1, 0, 0, 1, 0]

    steps = store.read_results(run).steps
    assert [s.value for s in steps] == [1.0, 2.0, 3.0, 4.0, 5.0]
    assert steps[2].fields["u"].ids.tolist() == [1, 2, 3, 4]
    assert steps[4].fields["u"].ids.tolist() == [2, 4]
    assert steps[4].fields["u"].values.tolist() == [[5.0, 5.0], [5.0, 5.0]]
    # One step, its ids held by an earlier row:
    u = store.read_field(run, "u", step=2)
    assert u.ids.tolist() == [1, 2, 3, 4] and u.values[0].tolist() == [3.0, 3.0]
    assert store.read_field(run, "u").ids.tolist() == [2, 4]


def test_an_older_store_is_refused(tmp_path):
    from puzzle.results.store import StaleStore

    (tmp_path / "fields" / "_delta_log").mkdir(parents=True)  # a format 1 store
    with pytest.raises(StaleStore, match="format 1") as err:
        ResultStore(tmp_path).runs()
    assert err.value.code == "stale_store"


def test_a_new_store_records_its_format(tmp_path):
    import json

    _transient(ResultStore(tmp_path / "r"), [[1, 2]])
    assert json.loads((tmp_path / "r" / "store.json").read_text()) == {"format": 2}
