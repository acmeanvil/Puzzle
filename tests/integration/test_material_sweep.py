"""A study that runs several materials (ADR 0018): the cantilever's tip load on steel
and on aluminium, one identical run each, on one mesh.

With the same Poisson's ratio, tip deflection scales as 1/E, so the two runs' tip
deflections are in the ratio of the moduli exactly (the same mesh, the same load); each
run records its material.
"""

import shutil
from pathlib import Path

import numpy as np
import pytest
from solvers import available

from puzzle.commands import Session
from puzzle.materials import add_library_material
from puzzle.model.schema import Component, Material, Study, SweepParameter, SweepStep
from puzzle.project import load_project, save_project
from puzzle.results.store import ResultStore

EXAMPLE = Path(__file__).parents[2] / "examples" / "cantilever"


@pytest.mark.skipif(not available("calculix"), reason="calculix not available")
def test_a_study_runs_each_material_on_one_mesh(tmp_path):
    root = tmp_path / "cantilever"
    shutil.copytree(
        EXAMPLE, root, ignore=shutil.ignore_patterns("results*", ".puzzle", "history.jsonl")
    )
    p = load_project(root)
    steel = next(m for m in p.of_type(Material))
    aluminium = add_library_material(p, "aluminium_6061_t6")
    aluminium.properties["poissons_ratio"] = steel.properties["poissons_ratio"]  # 1/E exactly
    comp = next(iter(p.of_type(Component)))
    location = comp.materials[0].selection
    study = next(s for s in p.of_type(Study) if s.name == "Tip load")
    study.solver = "calculix"
    study.steps = [
        SweepStep(
            parameters=[
                SweepParameter(
                    name="Bar material", location=location, materials=[steel.id, aluminium.id]
                )
            ]
        ),
        *study.solve_steps(),
    ]
    save_project(p, root)
    s = Session(root)
    out = s.submit({"command": "run_study", "study": "Tip load"})
    points = out.data["points"]
    assert [pt["point"][location] for pt in points] == [steel.name, aluminium.name]
    store = ResultStore(root / "results")
    runs = {r.point_labels()[location]: r for r in store.runs()}
    tip = {}
    for name, record in runs.items():
        results = store.read_results(record.run_id)
        u = results.last("displacement").values
        tip[name] = np.abs(u).max()
    e_steel = steel.properties["youngs_modulus"].to_si()
    e_al = aluminium.properties["youngs_modulus"].to_si()
    # CalculiX results keep 5 digits.
    assert tip[aluminium.name] / tip[steel.name] == pytest.approx(e_steel / e_al, rel=1e-5)
    assert runs[steel.name].mesh_id == runs[aluminium.name].mesh_id  # one mesh
    again = s.submit({"command": "run_study", "study": "Tip load"})
    assert all(pt["cached"] for pt in again.data["points"])  # each point reused
