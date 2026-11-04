"""Adaptive refinement on CalculiX (ADR 0066 phase 3): the steel cantilever of the
convergence benchmark, second-order tets from 8 mm, adapted to a 1.5 % energy-norm
error.

Measured (CalculiX 2.22): Gmsh 665 → 1227 → 1273 elements, error 3.17 → 1.68 →
1.24 %, converged in three passes, where uniform refinement (sizes ÷ 1.5 twice) took
3037 elements to reach 2.12 %. The tip deflection stays within 0.1 % of the
converged 3.5013 mm. Netgen (its size file restricts only): 524 → 765 elements,
error 3.94 → 1.71 % in four passes."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from optimise_beam import beam_project  # noqa: E402
from solvers import available  # noqa: E402

from puzzle import adapt  # noqa: E402
from puzzle.model.schema import Adapt, DesignOutput, MeshRecipe, Quantity, Study  # noqa: E402
from puzzle.project import save_project  # noqa: E402
from puzzle.results.store import ResultStore  # noqa: E402

pytestmark = pytest.mark.skipif(not available("calculix"), reason="calculix not available")
CONVERGED = 3.5013e-3  # m, the convergence benchmark's extrapolated tip deflection


def adapted(tmp_path, mesher: str, passes: int = 4, **kw):
    p, opt = beam_project()
    del p.objects[opt.id]
    recipe = next(iter(p.of_type(MeshRecipe)))
    recipe.global_size = Quantity(value=8, unit="mm")
    recipe.mesher = mesher
    root = tmp_path / mesher
    save_project(p, root)
    study = next(iter(p.of_type(Study)))
    tip = next(o for o in p.objects.values() if getattr(o, "name", "") == "Tip deflection")
    settings = Adapt(target=0.015, passes=passes, quantities=[DesignOutput(source=tip.id)], **kw)
    store = ResultStore(root / "results")
    return adapt.run(p, study, root, root / "work", store=store, settings=settings), store


def test_gmsh_adapts_to_the_target_in_fewer_elements(tmp_path):
    out, store = adapted(tmp_path, "gmsh")
    assert out.converged and len(out.passes) <= 4, out.ended
    last = out.passes[-1]
    assert last.error <= 0.015 and last.elements < 3037  # uniform: 3037 for 2.12 %
    assert last.values["Tip deflection"] == pytest.approx(CONVERGED, rel=0.002)
    errors = [p.error for p in out.passes]
    assert errors == sorted(errors, reverse=True)


def test_netgen_adapts_through_its_size_file(tmp_path):
    from puzzle.mesh.netgen_mesher import netgen_python

    if netgen_python() is None:
        pytest.skip("netgen not available")
    out, store = adapted(tmp_path, "netgen", passes=3, keep_passes=False)
    errors = [p.error for p in out.passes]
    assert errors[0] > errors[1] > errors[2] and errors[2] < 0.025
    assert [r.run_id for r in store.runs()] == [out.passes[-1].run]  # earlier ones deleted
