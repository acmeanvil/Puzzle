"""Mesh convergence (ADR 0066 phase 1) on a stand-in study whose answer converges as
f(h) = f∞ (1 − c h²) by construction, h the element size: three levels 1.5 apart
(element counts 1 000, 3 375, 11 390.625 → 11 391), so the observed order comes out 2,
the extrapolated value f∞, and the GCI as Roache's formula gives by hand."""

from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pytest

from puzzle import convergence
from puzzle.mesh import Mesh
from puzzle.mesh.mesh import CellBlock
from puzzle.model.schema import (
    Component,
    Geometry,
    MeshRecipe,
    PartInstance,
    Project,
    Quantity,
    Study,
)
from puzzle.results.model import Field, Results, StepResult
from puzzle.results.store import RunRecord

F_INF, C = 2.0e-3, 0.4  # m, and the error's coefficient per (h / h0)²


class Store:
    root = "memory"

    def __init__(self):
        self.records, self.results, self.meshes = [], {}, {}

    def runs(self):
        return self.records

    def read_results(self, run_id):
        return self.results[run_id]

    def read_mesh(self, mesh_id):
        return self.meshes[mesh_id]


def project():
    p = Project("p")
    geo = p.add(Geometry(name="G", source="g.step"))
    mesh = p.add(MeshRecipe(name="M", global_size=Quantity(value=6, unit="mm")))
    comp = p.add(Component(name="C", parts=[PartInstance(name="B", geometry=geo.id)],
                           mesh=mesh.id))  # fmt: skip
    study = p.add(Study(name="S", component=comp.id, solver="calculix", steps=[]))
    return p, study


def stand_in(store, oscillate=False):
    def solve(project, study_id, root, workdir, *, store=store, _point, **_):
        recipe = next(iter(project.of_type(MeshRecipe)))
        h = recipe.global_size.to_si() / 0.006  # 1, 1/1.5, 1/2.25
        n = round(1000 / h**3)
        sign = (-1) ** len(store.records) if oscillate else 1
        tip = F_INF * (1 - sign * C * h**2)
        rid = f"r{len(store.records)}-{uuid4().hex[:6]}"
        ids = np.arange(1, 3)
        store.meshes[f"m{n}"] = Mesh(ids, np.zeros((2, 3)), [CellBlock(
            "tet4", 3, (3, 1), np.arange(1, n + 1), np.ones((n, 4), np.int64))])  # fmt: skip
        u = np.array([[0.0, 0, 0], [0, 0, -tip]])
        store.results[rid] = Results("calculix", [StepResult(1, "stationary", 1.0, fields={
            "displacement": Field("displacement", "node", ["x", "y", "z"], ids, u)})])  # fmt: skip
        when = datetime(2026, 11, 4, 12, len(store.records))
        store.records.append(RunRecord(rid, _point[0], str(study_id), "S", "calculix",
                                       "native", f"m{n}", "completed", when, 1.0, 1, when,
                                       json.dumps(_point[1])))  # fmt: skip
        return SimpleNamespace(run_id=rid)

    return solve


def test_three_levels_give_order_two_and_the_answer(tmp_path):
    p, study = project()
    store = Store()
    got = convergence.run(p, study, tmp_path, tmp_path / "w", store=store, count=3,
                          ratio=1.5, solve=stand_in(store))  # fmt: skip
    assert [lv.label for lv in got] == ["level 1: 6 mm", "level 2: 4 mm",
                                        "level 3: 2.667 mm"]  # fmt: skip
    assert [lv.elements for lv in got] == [1000, 3375, 11391]
    (v,) = convergence.verdicts(got, target=0.02)
    assert v.order == pytest.approx(2.0, abs=0.01)
    assert v.extrapolated == pytest.approx(F_INF, rel=1e-3)
    # Roache by hand: e_a = |f1 − f2| / f1 and GCI = 1.25 e_a / (r21^p − 1)
    f1, f2 = (lv.values[v.quantity] for lv in (got[2], got[1]))
    r = (11391 / 3375) ** (1 / 3)
    assert v.gci == pytest.approx(1.25 * abs((f1 - f2) / f1) / (r**v.order - 1), rel=1e-6)
    assert v.state == "not converged" and v.words.startswith("GCI")  # c = 0.4: coarse
    assert convergence.verdicts(got, target=0.2)[0].state == "converged"
    again = convergence.run(p, study, tmp_path, tmp_path / "w", store=store, count=3,
                            ratio=1.5, read_only=True)  # fmt: skip
    assert [lv.run for lv in again] == [lv.run for lv in got]  # read back, not solved


def test_an_oscillating_sequence_is_not_called_converged(tmp_path):
    p, study = project()
    store = Store()
    got = convergence.run(p, study, tmp_path, tmp_path / "w", store=store, count=3,
                          ratio=1.5, solve=stand_in(store, oscillate=True))  # fmt: skip
    (v,) = convergence.verdicts(got)
    assert v.state == "oscillating" and "asymptotic" in v.words
    two = convergence.verdicts(got[:2])
    assert two[0].state == "too few levels"


def test_scaled_recipes():
    from puzzle.model.schema import SizeControl

    p, study = project()
    recipe = next(iter(p.of_type(MeshRecipe)))
    recipe.controls = [SizeControl(selection="B/Fillet", size=Quantity(value=1, unit="mm"))]
    finer = next(iter(convergence.scaled(p, study, 2.0).of_type(MeshRecipe)))
    assert finer.global_size.value == 3 and finer.controls[0].size.value == 0.5
    recipe.global_size = None
    finer = next(iter(convergence.scaled(p, study, 2.0).of_type(MeshRecipe)))
    assert finer.gmsh_options["Mesh.MeshSizeFactor"] == 0.5
    assert recipe.global_size is None  # the project itself is never changed


def test_settings_report_and_the_advisor(tmp_path, monkeypatch):
    from puzzle import advisor
    from puzzle.model.schema import Convergence, MeshStudy

    p, study = project()
    assert convergence.settings(p, "S")[1] == Convergence()  # the defaults
    p.add(MeshStudy(name="Fine check", study=study.id, levels=3, ratio=1.5, target=0.2))
    got_study, c = convergence.settings(p, "Fine check")
    assert got_study.id == study.id and c.target == 0.2
    store = Store()
    monkeypatch.setattr("puzzle.results.store.ResultStore", lambda *_: store)
    said = []
    advisor._convergence(p, study, tmp_path, lambda *a: said.append(a))
    assert said[0][:3] == ("convergence", "optional", "0 of 3 meshes run.")
    lv = convergence.run(p, study, tmp_path, tmp_path / "w", store=store, count=3,
                         ratio=1.5, solve=stand_in(store))  # fmt: skip
    said.clear()
    advisor._convergence(p, study, tmp_path, lambda *a: said.append(a))
    assert said[0][1] == "done" and "converged within" in said[0][2]  # target 20 %
    vs = convergence.verdicts(lv, 0.02)
    out = convergence.report(lv, vs, 0.02)
    assert out["converged"] is False and len(out["levels"]) == 3
    text = convergence.lines(lv, vs)
    assert text[0].startswith("level 1: 6 mm: 1000 elements") and "extrapolated" in text[-1]


def test_the_cli_reads_what_ran(tmp_path, capsys):
    from puzzle.cli import main
    from puzzle.project import save_project

    p, _ = project()
    save_project(p, tmp_path / "p")
    assert main(["convergence", str(tmp_path / "p"), "S", "--points", "--json"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert [lv["reason"] for lv in out["levels"]] == ["not run yet"] * 3
    assert out["converged"] is False and out["target"] == 0.02


def test_the_outline_and_schematic_say_the_check(tmp_path):
    from puzzle import schematic
    from puzzle.commands.outline import outline
    from puzzle.model.schema import Convergence, MeshStudy

    p, study = project()
    study.convergence = Convergence()
    ms = p.add(MeshStudy(name="Fine check", study=study.id, levels=3, target=0.2))
    store = Store()
    convergence.run(p, study, tmp_path, tmp_path / "w", store=store, count=2, ratio=1.5,
                    solve=stand_in(store))  # fmt: skip
    text = outline(p, runs=store.runs())
    assert "Study S mesh convergence: 3 meshes, each finer by 1.5, target GCI 2 %; 2 of 3" in text
    assert "Mesh study Fine check of S: 3 meshes" in text
    node = schematic.build(p, store.runs(), store=store).nodes[str(ms.id)]
    assert node.badge == "2 of 3 meshes" and node.value == ""
