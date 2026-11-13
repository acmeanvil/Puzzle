"""Adaptive refinement's loop (ADR 0066 phase 3) on a stand-in study: each pass's
"mesh" is the cube of tets with twice the divisions of the pass its sizes came from,
its displacement u = (10⁻³ x², 0, 0), whose ZZ error halves with h (η 12.7, 6.4,
3.2 %). With a 4 % target it stops at pass 3, converged; each pass is a stored run
labelled `adapt: pass k`, read back without solving; `keep_passes = False` deletes
the earlier runs and keeps the table; the quantity criterion stops on a small change."""

from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pytest
from test_estimates import cube

from puzzle import adapt
from puzzle.materials import add_library_material
from puzzle.mesh.mesh import Group
from puzzle.model.schema import (
    Adapt,
    Component,
    DesignOutput,
    Geometry,
    MaterialAssignment,
    MeshRecipe,
    PartInstance,
    Project,
    Quantity,
    Study,
)
from puzzle.results.model import Field, Results, StepResult
from puzzle.results.store import RunRecord


class Store:
    def __init__(self):
        self.records, self.results, self.meshes, self.n = [], {}, {}, {}

    def runs(self):
        return self.records

    def read_results(self, run_id):
        return self.results[run_id]

    def read_mesh(self, mesh_id):
        return self.meshes[mesh_id]

    def delete_run(self, run_id):
        self.records = [r for r in self.records if r.run_id != run_id]
        return {}


def project():
    p = Project("p")
    geo = p.add(Geometry(name="G", source="g.step"))
    steel = add_library_material(p, "structural_steel")
    recipe = p.add(MeshRecipe(name="M", global_size=Quantity(value=250, unit="mm")))
    comp = p.add(Component(name="C", parts=[PartInstance(name="B", geometry=geo.id)],
                           mesh=recipe.id,
                           materials=[MaterialAssignment(material=steel.id,
                                                         selection="B/Body")]))  # fmt: skip
    return p, p.add(Study(name="S", component=comp.id, solver="calculix", steps=[]))


def stand_in(store):
    def solve(project, study_id, root, workdir, *, store=store, _point, **_):
        recipe = next(iter(project.of_type(MeshRecipe)))
        source = next((c.run for c in recipe.controls if c.kind == "size_field"), None)
        n = 4 if source is None else 2 * store.n[source]
        mesh = cube(n)
        mesh.groups["B/Body"] = Group("B/Body", 3, [(3, 1)], mesh.node_ids)
        u = np.zeros((len(mesh.coords), 3))
        u[:, 0] = 1e-3 * mesh.coords[:, 0] ** 2
        rid = f"r{len(store.records)}"
        store.n[rid] = n
        store.meshes[f"m{rid}"] = mesh
        store.results[rid] = Results(
            "calculix",
            [
                StepResult(
                    1,
                    "stationary",
                    1.0,
                    fields={
                        "displacement": Field(
                            "displacement", "node", ["x", "y", "z"], mesh.node_ids, u
                        )
                    },
                )
            ],
        )
        when = datetime(2026, 11, 4, 12, len(store.records))
        store.records.append(RunRecord(rid, _point[0], str(study_id), "S", "calculix", "native",
                                       f"m{rid}", "completed", when, 1.0, 1, when,
                                       json.dumps(_point[1])))  # fmt: skip
        return SimpleNamespace(run_id=rid)

    return solve


def test_passes_until_the_error_is_at_the_target(tmp_path):
    p, study = project()
    store = Store()
    out = adapt.run(p, study, tmp_path, tmp_path / "w", store=store,
                    settings=Adapt(target=0.04, passes=5), solve=stand_in(store))  # fmt: skip
    errors = [x.error for x in out.passes]
    assert len(out.passes) == 3 and out.converged, out.ended
    assert errors[0] == pytest.approx(0.127, abs=0.003) and errors[2] < 0.04
    assert [json.loads(r.point)["adapt"] for r in store.records] == ["pass 1", "pass 2",
                                                                     "pass 3"]  # fmt: skip
    again = adapt.run(p, study, tmp_path, tmp_path / "w", store=store,
                      settings=Adapt(target=0.04, passes=5), read_only=True)  # fmt: skip
    assert [x.run for x in again.passes] == [x.run for x in out.passes]  # by run key
    kept = json.loads(
        (tmp_path / "results" / "adapt" / f"{study.id}.json").read_text(encoding="utf-8")
    )
    assert len(kept["passes"]) == 3 and kept["converged"]
    assert adapt.lines(out)[-1].startswith("converged: error 3.")


def test_a_budget_deleting_passes_and_the_quantity_criterion(tmp_path):
    p, study = project()
    store = Store()
    out = adapt.run(p, study, tmp_path, tmp_path / "w", store=store,
                    settings=Adapt(target=0.01, passes=2, keep_passes=False),
                    solve=stand_in(store))  # fmt: skip
    assert not out.converged and "budget of 2 passes" in out.ended
    assert [r.run_id for r in store.records] == [out.passes[-1].run]  # the last kept
    watch = Adapt(criterion="quantity", target=0.02, quantities=[DesignOutput(source=uuid4())])
    assert adapt._done(watch, [
        adapt.Pass(1, values={"Tip": 1.00}), adapt.Pass(2, values={"Tip": 1.01})]).startswith(
        "converged: the quantities changed 0.99 %")  # fmt: skip


def test_the_cli_reads_the_passes(tmp_path, capsys):
    from puzzle.cli import main
    from puzzle.project import save_project

    p, _ = project()
    save_project(p, tmp_path / "p")
    assert main(["adapt", str(tmp_path / "p"), "S", "--points", "--json"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["passes"][0]["reason"] == "not run yet" and not out["converged"]
