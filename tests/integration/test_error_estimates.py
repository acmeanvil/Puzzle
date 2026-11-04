"""Error estimates on CalculiX runs (ADR 0066 phase 2): the steel cantilever of the
convergence benchmark, its ZZ energy-norm estimate on three meshes 1.5 apart.

Measured (CalculiX 2.22): second-order tets from 8 mm, η = 3.17, 2.23, 2.12 %
(flattening: the clamped root's edges are singular); first-order tets from 4 mm,
64, 54, 43 % (they lock in bending). The largest element errors sit at the root."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from optimise_beam import beam_project  # noqa: E402
from solvers import available  # noqa: E402

from puzzle import convergence as conv  # noqa: E402
from puzzle.model.schema import MeshRecipe, Quantity, Study  # noqa: E402
from puzzle.post import estimates  # noqa: E402
from puzzle.project import save_project  # noqa: E402
from puzzle.results.store import ResultStore  # noqa: E402

pytestmark = pytest.mark.skipif(not available("calculix"), reason="calculix not available")


def runs(tmp_path, order, size_mm):
    p, opt = beam_project()
    del p.objects[opt.id]
    recipe = next(iter(p.of_type(MeshRecipe)))
    recipe.element_order = order
    recipe.global_size = Quantity(value=size_mm, unit="mm")
    root = tmp_path / f"beam{order}"
    save_project(p, root)
    study = next(iter(p.of_type(Study)))
    store = ResultStore(root / "results")
    levels = conv.run(p, study, root, root / "work", store=store, count=3, ratio=1.5)
    return root, store, levels, [estimates.of_run(p, store, lv.run) for lv in levels]


def test_the_estimate_falls_and_points_at_the_root(tmp_path):
    root, store, levels, quad = runs(tmp_path, 2, 8)
    eta = [e.relative for e in quad]
    assert eta[0] > eta[1] > eta[2] and 0.01 < eta[2] < 0.04
    *_, lin = runs(tmp_path, 1, 4)
    assert all(e.relative > 0.3 for e in lin) and lin[0].relative > lin[2].relative
    finest = quad[-1]
    record = next(r for r in store.runs() if r.run_id == levels[-1].run)
    mesh = store.read_mesh(record.mesh_id)
    worst = finest.element_ids[np.argsort(finest.error)[-10:]]
    pos = {int(n): i for i, n in enumerate(mesh.node_ids)}
    centres = []
    for b in mesh.blocks:
        if b.dim == 3:
            keep = np.isin(b.ids, worst)
            idx = np.vectorize(pos.__getitem__)(b.connectivity[keep])
            centres.append(mesh.coords[idx].mean(axis=1))
    xs = np.concatenate(centres)[:, 0]
    assert (xs < 0.02).all()  # within 20 mm of the clamped root


def test_the_cli_reads_the_newest_run(tmp_path, capsys):
    import json

    from puzzle.cli import main

    root, _, _, quad = runs(tmp_path, 2, 8)
    main(["error-estimate", str(root), "Bend", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert out["relative_error"] == pytest.approx(quad[-1].relative, rel=1e-9)
    assert out["physics"] == "structural" and out["hot_spots"][0]["centre"][0] < 0.02
    assert "B/Root" in out["hot_spots"][0]["selections"]
