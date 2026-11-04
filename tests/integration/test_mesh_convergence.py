"""Mesh convergence on CalculiX (ADR 0066 phase 1): the steel cantilever of the
optimisation benchmark (200 × 20 × 6 mm, 100 N at the tip), its tip deflection on three
meshes 1.5 apart.

Measured (CalculiX 2.22, Gmsh): second-order tets from 8 mm give 3.4944, 3.4984, 3.5004
mm, observed order 4.1, GCI 0.03 %, extrapolated 3.5013 mm, 0.8 % under Timoshenko's
3.5297 mm (a wide beam: the plate's Poisson stiffening). First-order tets from 4 mm lock
(1.04, 2.18, 2.74 mm): GCI 26 %, "not converged", and the extrapolated 3.31 mm is
nearer the answer than the finest mesh, as Richardson extrapolation should be."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from optimise_beam import F, L, W, beam_project  # noqa: E402
from solvers import available  # noqa: E402

from puzzle import convergence as conv  # noqa: E402
from puzzle.model.schema import DesignOutput, MeshRecipe, Quantity, Study  # noqa: E402
from puzzle.project import save_project  # noqa: E402
from puzzle.results.store import ResultStore  # noqa: E402

pytestmark = pytest.mark.skipif(not available("calculix"), reason="calculix not available")
T = 0.006


def timoshenko(p) -> float:
    from puzzle.model.schema import Material

    steel = next(iter(p.of_type(Material)))
    E = steel.properties["youngs_modulus"].to_si()
    nu = steel.properties["poissons_ratio"].to_si()
    inertia, area = W * T**3 / 12, W * T
    return F * L**3 / (3 * E * inertia) + F * L / (5 / 6 * E / (2 * (1 + nu)) * area)


def check(tmp_path, order: int, size_mm: float):
    p, opt = beam_project()
    del p.objects[opt.id]
    recipe = next(iter(p.of_type(MeshRecipe)))
    recipe.element_order = order
    recipe.global_size = Quantity(value=size_mm, unit="mm")
    root = tmp_path / f"beam{order}"
    save_project(p, root)
    study = next(iter(p.of_type(Study)))
    tip = next(o for o in p.objects.values() if getattr(o, "name", "") == "Tip deflection")
    store = ResultStore(root / "results")
    levels = conv.run(p, study, root, root / "work", store=store, count=3, ratio=1.5,
                      outputs=[DesignOutput(source=tip.id)])  # fmt: skip
    assert all(lv.run for lv in levels), [lv.reason for lv in levels]
    again = conv.run(p, study, root, root / "work", store=store, count=3, ratio=1.5,
                     outputs=[DesignOutput(source=tip.id)], read_only=True)  # fmt: skip
    assert [lv.run for lv in again] == [lv.run for lv in levels]  # stored, so reused
    (v,) = conv.verdicts(levels, 0.02)
    return p, levels, v


def test_second_order_tets_converge_to_the_beam(tmp_path):
    p, levels, v = check(tmp_path, 2, 8)
    assert v.state == "converged" and v.gci < 0.002
    assert v.extrapolated == pytest.approx(timoshenko(p), rel=0.015)
    finest = levels[-1].values["Tip deflection"]
    assert abs(v.extrapolated - finest) / finest < 0.001


def test_first_order_tets_are_not_converged(tmp_path):
    p, levels, v = check(tmp_path, 1, 4)
    assert v.state == "not converged" and v.gci > 0.1
    exact, finest = timoshenko(p), levels[-1].values["Tip deflection"]
    assert abs(v.extrapolated - exact) < abs(finest - exact)  # Richardson helps
