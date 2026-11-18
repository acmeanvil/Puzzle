"""A schematic of 55 nodes for the review's measurements (ADR 0090 package 13).

The cantilever example (12 nodes) with a run stored for each of its three studies (3
results nodes), then ten chains of four operators on the Tip load run: a Number (the
limit), a Result summary (the largest deformation), a Check of one against the other
and a Formula of the summary: 40 operator nodes, 55 in all. Runs are stored as a solve
stores them (a two-tet mesh with a displacement field) under each study's run key now,
so every study is up to date until the model changes.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from puzzle.commands import Session
from puzzle.mesh.mesh import CellBlock, Group, Mesh
from puzzle.model.schema import Study
from puzzle.project.graph import run_hashes
from puzzle.results import Field, Results, ResultStore, StepResult

EXAMPLE = Path(__file__).parents[1] / "examples" / "cantilever"
CHAINS = 10


def copy(tmp_path: Path) -> Path:
    root = tmp_path / "cantilever"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns(
        "results*", ".puzzle", "history.jsonl"))  # fmt: skip
    return root


def store_run(root: Path, study: Study, key: str, scale: float = 1.0) -> ResultStore:
    """A run of `study` stored under `key`: two tets, the tip moving by `scale` mm."""
    xyz = np.array([[0.0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 1, 1]])
    ids = np.arange(1, 6, dtype=np.int64)
    mesh = Mesh(node_ids=ids, coords=xyz)
    mesh.blocks = [
        CellBlock("tet4", 3, (3, 1), np.array([1]), np.array([[1, 2, 3, 4]])),
        CellBlock("tet4", 3, (3, 2), np.array([2]), np.array([[2, 3, 4, 5]])),
    ]
    mesh.groups = {"Bar": Group("Bar", 3, [(3, 1), (3, 2)], ids)}
    u = np.zeros((5, 3))
    u[:, 1] = -1e-3 * scale * xyz[:, 0]
    fields = {"displacement": Field("displacement", "node", ["x", "y", "z"], ids, u)}
    results = Results(study.solver, [StepResult(1, "stationary", 1.0, None, fields)])
    store = ResultStore(root / "results")
    store.write_run(run_key=key, study_id=str(study.id), study_name=study.name,
                    solver=study.solver, runtime="native", mesh=mesh, results=results,
                    started_at=datetime(2026, 11, 16, tzinfo=UTC), duration_s=1.0)  # fmt: skip
    return store


def study(project, name: str) -> Study:
    return next(s for s in project.of_type(Study) if s.name == name)


def build(tmp_path: Path, chains: int = CHAINS) -> tuple[Path, Session]:
    """The 55-node project, saved, its runs stored; the session that made it."""
    root = copy(tmp_path)
    s = Session(root)
    commands = []
    for i in range(chains):
        commands += [
            {"command": "create", "kind": "operator", "data": {
                "name": f"Allowed {i}", "type": "number",
                "inputs": {"value": {"value": {"value": 1 + i, "unit": "mm"}}}}},
            {"command": "create", "kind": "operator", "data": {
                "name": f"Peak {i}", "type": "reduce",
                "inputs": {"result": {"source": "Tip load", "port": "result"}}}},
            {"command": "create", "kind": "operator", "data": {
                "name": f"Check {i}", "type": "check",
                "inputs": {"value": {"source": f"Peak {i}"},
                           "limit": {"source": f"Allowed {i}"}}}},
            {"command": "create", "kind": "operator", "data": {
                "name": f"Twice {i}", "type": "expression", "expression": "2 * x",
                "inputs": {"x": {"source": f"Peak {i}"}}}},
        ]  # fmt: skip
    from puzzle import operators

    for c in commands:  # sources by name, as an agent gives them
        c["data"]["inputs"] = operators.inputs_from(s.project, c["data"].get("inputs", {}))
        s.submit(c)
    keys = run_hashes(s.project, root)
    for st in s.project.of_type(Study):
        store_run(root, st, keys[st.id][st.solver][0])
    return root, s
