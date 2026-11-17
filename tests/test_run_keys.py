"""One definition of what a run depends on (ADR 0090 package 1).

- BE-1: the files a model reads are hashed by content, so replacing a STEP file (or a
  history step's import, a frame's DXF, a profile's DXF) makes its studies out of date;
  a missing file is a problem, not a crash, with a stable stand-in hash.
- BE-2, OP-2: `run_study` keys a run as status does, so a study with a value wired to a
  parameter, a suppressed geometry or a feature switched by a toggle reads "up to
  date" after its own run: a run stored under the key status gives is the one
  `run_study` finds (a cache hit, no solve).
- OP-3: a study's key covers only the parameters its model reads: adding a Number no
  study reads changes no key, editing one a history argument names does.
- ADR-47: convergence levels and adaptive passes are keyed as variants of the study's
  own key, so they are current while the study is.
- A stale run is kept: only its state changes (ADR 0090 decision 4).
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from puzzle.commands import Session
from puzzle.mesh.mesh import CellBlock, Group, Mesh
from puzzle.model.schema import Geometry, Study
from puzzle.project import load_project
from puzzle.project.files import MISSING
from puzzle.project.graph import base_key, content_hashes, run_hashes, variant
from puzzle.project.status import run_state, status
from puzzle.results import Field, Results, ResultStore, StepResult

EXAMPLE = Path(__file__).parents[1] / "examples" / "cantilever"


def copy(tmp_path) -> Path:
    root = tmp_path / "cantilever"
    shutil.copytree(
        EXAMPLE, root, ignore=shutil.ignore_patterns("results*", ".puzzle", "history.jsonl")
    )
    return root


def tip(p) -> Study:
    return next(s for s in p.of_type(Study) if s.name == "Tip load")


def _store_fake_run(root: Path, study: Study, key: str) -> ResultStore:
    """A run stored under `key`, as a solve would store it (a one-element mesh)."""
    mesh = Mesh(node_ids=np.arange(1, 5, dtype=np.int64),
                coords=np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], float))  # fmt: skip
    mesh.blocks = [CellBlock("tet4", 3, (3, 1), np.array([1]), np.array([[1, 2, 3, 4]]))]
    mesh.groups = {"Bar": Group("Bar", 3, [(3, 1)], mesh.node_ids)}
    disp = Field("displacement", "node", ["x", "y", "z"], mesh.node_ids, np.zeros((4, 3)))
    results = Results("calculix", [StepResult(1, "stationary", 1.0, None, {"displacement": disp})])
    store = ResultStore(root / "results")
    store.write_run(run_key=key, study_id=str(study.id), study_name=study.name,
                    solver=study.solver, runtime="native", mesh=mesh, results=results,
                    started_at=datetime(2026, 11, 16, tzinfo=UTC), duration_s=1.0)  # fmt: skip
    return store


def _status_key(p, study) -> str:
    return run_hashes(p)[study.id][study.solver][0]


def _solve_finds_it(p, study, root, tmp_path) -> bool:
    """Whether `run_study` takes the run stored under status's key (a cache hit)."""
    from puzzle.solve import run_study

    store = _store_fake_run(root, study, _status_key(p, study))
    got = run_study(p, study.id, root, tmp_path / "work", store=store)
    return got.cached and status(p, store.runs())[str(study.id)].state == "up_to_date"


# --- BE-1 ------------------------------------------------------------------------------


def test_replacing_a_geometry_file_stales_its_studies_and_keeps_the_run(tmp_path):
    root = copy(tmp_path)
    p = load_project(root)
    study = tip(p)
    store = _store_fake_run(root, study, _status_key(p, study))
    assert status(p, store.runs())[str(study.id)].state == "up_to_date"
    step = root / "bar.step"
    step.write_text(step.read_text(encoding="utf-8").replace("1", "2", 1), encoding="utf-8")
    p = load_project(root)  # as a reopened project, or the next refresh
    assert status(p, store.runs())[str(study.id)].state == "out_of_date"
    assert run_state(store.runs()[0], p).state == "out_of_date"
    assert len(store.runs()) == 1  # the stale run is kept and stays readable


def test_import_steps_frame_and_profile_drawings_are_hashed_by_content(tmp_path):
    from puzzle.model.schema import HistoryStep, Project

    p = Project("p")
    p.root = tmp_path
    files = {"part.step": "a", "frame.dxf": "b", "outline.dxf": "c"}
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    geos = [
        p.add(Geometry(name="I", history=[HistoryStep(kind="import", name="I",
                                                      args={"file": "part.step"})])),
        p.add(Geometry(name="F", history=[HistoryStep(kind="frame", name="F",
                                                      args={"dxf": "frame.dxf"})])),
        p.add(Geometry(name="E", history=[HistoryStep(kind="extrude", name="E", args={
            "profile": {"dxf": "outline.dxf"}, "distance": 0.01})])),
    ]  # fmt: skip
    for geo, name in zip(geos, files, strict=True):
        before = content_hashes(p)
        (tmp_path / name).write_text(files[name] + " changed", encoding="utf-8")
        after = content_hashes(p)
        assert after[geo.id] != before[geo.id], name
        assert all(after[g.id] == before[g.id] for g in geos if g is not geo)


def test_a_missing_file_is_a_problem_with_a_stable_hash(tmp_path):
    root = copy(tmp_path)
    (root / "bar.step").unlink()
    p = load_project(root)
    first = content_hashes(p)
    assert content_hashes(p) == first  # no crash, and the same each time
    problems = [x for x in p.problems() if x.code == "missing_file"]
    assert problems and "bar.step" in problems[0].message
    from puzzle.project.files import digests

    assert digests(p.of_type(Geometry)[0], root) == {"bar.step": MISSING}


def test_an_imported_fields_file_is_hashed_by_content(tmp_path):
    from puzzle.model.schema import ImportedField, Project

    p = Project("p")
    p.root = tmp_path
    (tmp_path / "fields").mkdir()
    (tmp_path / "fields" / "t.csv").write_text("x,y,z,T\n0,0,0,1\n", encoding="utf-8")
    f = p.add(ImportedField(name="T", file="fields/t.csv", file_hash="as imported"))
    before = content_hashes(p)[f.id]
    (tmp_path / "fields" / "t.csv").write_text("x,y,z,T\n0,0,0,2\n", encoding="utf-8")
    assert content_hashes(p)[f.id] != before  # the stored file_hash alone didn't see it


# --- BE-2, OP-2 ------------------------------------------------------------------------


def test_a_value_wired_to_a_parameter_is_up_to_date_after_its_run(tmp_path):
    root = copy(tmp_path)
    s = Session(root)
    s.submit(
        {
            "command": "create",
            "kind": "parameters",
            "data": {"name": "P", "values": {"F": {"value": -250.0, "unit": "N"}}},
        }
    )
    s.submit({"command": "edit", "object": "Cantilever", "ops": [{
        "op": "set", "path": "physics.0.features.1.values.y",
        "value": {"value": -100.0, "unit": "N", "parameter": "F"}}]})  # fmt: skip
    p = s.project
    assert _solve_finds_it(p, tip(p), root, tmp_path)
    before = _status_key(p, tip(p))
    s.submit(
        {
            "command": "edit",
            "object": "P",
            "ops": [{"op": "set", "path": "values.F", "value": {"value": -300.0, "unit": "N"}}],
        }
    )
    assert _status_key(s.project, tip(s.project)) != before  # the parameter is read


def test_a_suppressed_geometry_is_up_to_date_after_its_run(tmp_path):
    root = copy(tmp_path)
    s = Session(root)
    bar = s.project.of_type(Geometry)[0].model_dump(mode="json")
    bar.pop("id")
    s.submit(
        {
            "command": "create",
            "kind": "geometry",
            "data": bar | {"name": "Bar 2", "suppressed": True},
        }
    )
    gid = next(str(g.id) for g in s.project.of_type(Geometry) if g.name == "Bar 2")
    s.submit(
        {
            "command": "edit",
            "object": "Cantilever",
            "ops": [{"op": "append", "path": "parts", "value": {"name": "B2", "geometry": gid}}],
        }
    )
    p = s.project
    assert _solve_finds_it(p, tip(p), root, tmp_path)


def test_a_feature_switched_by_a_toggle_is_up_to_date_after_its_run(tmp_path):
    root = copy(tmp_path)
    s = Session(root)
    s.submit(
        {
            "command": "create",
            "kind": "operator",
            "data": {  # switched off
                "name": "Loaded",
                "type": "toggle",
                "inputs": {"value": {"value": {"value": 0}}},
            },
        }
    )
    s.submit(
        {
            "command": "edit",
            "object": "Cantilever",
            "ops": [{"op": "set", "path": "physics.0.features.1.when", "value": "Loaded"}],
        }
    )
    p = s.project
    assert _solve_finds_it(p, tip(p), root, tmp_path)


# --- OP-3 ------------------------------------------------------------------------------


def test_a_number_no_study_reads_changes_no_key(tmp_path):
    s = Session(copy(tmp_path))
    before = run_hashes(s.project)
    s.submit({"command": "create", "kind": "operator", "data": {
        "name": "Sy", "type": "number", "inputs": {"value": {"value": {
            "value": 235, "unit": "MPa"}}}}})  # fmt: skip
    assert run_hashes(s.project) == before


def test_a_number_a_history_argument_names_stales_only_its_study(tmp_path):
    from puzzle.model.schema import (
        Component,
        HistoryStep,
        MeshRecipe,
        Operator,
        OperatorInput,
        PartInstance,
        Project,
        Quantity,
    )

    p = Project("p")
    width = p.add(Operator(name="width", type="number", inputs={
        "value": OperatorInput(value=Quantity(value=20, unit="mm"))}))  # fmt: skip
    geo = p.add(Geometry(name="Plate", history=[HistoryStep(kind="box", name="B", args={
        "dx": "width", "dy": 0.01, "dz": 0.01})]))  # fmt: skip
    mesh = p.add(MeshRecipe(name="M"))
    comp = p.add(Component(name="C", mesh=mesh.id,
                           parts=[PartInstance(name="Plate", geometry=geo.id)]))  # fmt: skip
    reads = p.add(Study(name="S", component=comp.id, solver="calculix", steps=[]))
    fixed = p.add(Geometry(name="Block", history=[HistoryStep(kind="box", name="B", args={
        "dx": 0.02, "dy": 0.01, "dz": 0.01})]))  # fmt: skip
    block = p.add(Component(name="D", mesh=mesh.id,
                            parts=[PartInstance(name="Block", geometry=fixed.id)]))  # fmt: skip
    other = p.add(Study(name="T", component=block.id, solver="calculix", steps=[]))
    before = run_hashes(p)
    width.inputs["value"] = OperatorInput(value=Quantity(value=30, unit="mm"))
    after = run_hashes(p)
    assert after[reads.id] != before[reads.id]
    assert after[other.id] == before[other.id]


def test_a_parameter_set_value_stales_only_the_studies_naming_it(tmp_path):
    root = copy(tmp_path)
    s = Session(root)
    s.submit(
        {
            "command": "create",
            "kind": "parameters",
            "data": {
                "name": "P",
                "values": {
                    "F": {"value": -250.0, "unit": "N"},
                    "unused": {"value": 1.0, "unit": "m"},
                },
            },
        }
    )
    s.submit({"command": "edit", "object": "Cantilever", "ops": [{
        "op": "set", "path": "physics.0.features.1.values.y",
        "value": {"value": -100.0, "unit": "N", "parameter": "F"}}]})  # fmt: skip
    before = run_hashes(s.project)
    s.submit(
        {
            "command": "edit",
            "object": "P",
            "ops": [{"op": "set", "path": "values.unused", "value": {"value": 2.0, "unit": "m"}}],
        }
    )
    assert run_hashes(s.project) == before


# --- ADR-47 ----------------------------------------------------------------------------


def test_levels_and_passes_are_current_while_their_study_is(tmp_path):
    from dataclasses import replace

    from test_tree_status import record

    p = load_project(copy(tmp_path))
    study = tip(p)
    key = _status_key(p, study)
    level = replace(record(study, variant(key, "mesh scaled", 1.5)), run_id="r2")
    assert base_key(level.run_key) == key
    assert run_state(level, p).state == "up_to_date"
    # Only a finer level kept (pass 1 deleted): the study is still up to date.
    assert status(p, [level])[str(study.id)].state == "up_to_date"
    p.of_type(Geometry)[0].selections[0].members = ["body-2"]  # the model changes
    assert run_state(level, p).state == "out_of_date"


def test_a_real_run_of_a_wired_and_suppressed_model_is_up_to_date(tmp_path):
    """The same invariant through a CalculiX solve: run it, and status says current."""
    import pytest
    from solvers import available

    if not available("calculix"):
        pytest.skip("calculix not available")
    from puzzle.solve import run_study

    root = copy(tmp_path)
    s = Session(root)
    s.submit(
        {
            "command": "create",
            "kind": "parameters",
            "data": {"name": "P", "values": {"F": {"value": -250.0, "unit": "N"}}},
        }
    )
    s.submit({"command": "edit", "object": "Cantilever", "ops": [{
        "op": "set", "path": "physics.0.features.1.values.y",
        "value": {"value": -100.0, "unit": "N", "parameter": "F"}}]})  # fmt: skip
    bar = s.project.of_type(Geometry)[0].model_dump(mode="json")
    bar.pop("id")
    s.submit(
        {
            "command": "create",
            "kind": "geometry",
            "data": bar | {"name": "Bar 2", "suppressed": True},
        }
    )
    gid = next(str(g.id) for g in s.project.of_type(Geometry) if g.name == "Bar 2")
    s.submit(
        {
            "command": "edit",
            "object": "Cantilever",
            "ops": [{"op": "append", "path": "parts", "value": {"name": "B2", "geometry": gid}}],
        }
    )
    p, study = s.project, tip(s.project)
    p.objects[study.id] = study.model_copy(update={"solver": "calculix"})
    store = ResultStore(root / "results")
    got = run_study(p, study.id, root, tmp_path / "work", store=store)
    assert not got.cached
    assert status(p, store.runs())[str(study.id)].state == "up_to_date"
    from puzzle.results.query import total

    held = total(got.results, got.mesh, "reaction_force", "Bar/Root")
    # The parameter's 250 N was solved, not the stored 100 N: the root holds it.
    assert held["total"][1] == pytest.approx(250.0, rel=1e-6)
