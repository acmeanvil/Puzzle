"""Adaptive refinement on CalculiX (ADR 0066 phase 3): the steel cantilever of the
convergence benchmark, second-order tets from 8 mm, adapted to a 1.5 % energy-norm
error.

Measured (CalculiX 2.22): Gmsh 665 → 1227 → 1273 elements, error 3.17 → 1.68 →
1.24 %, converged in three passes, where uniform refinement (sizes ÷ 1.5 twice) took
3037 elements to reach 2.12 %. The tip deflection stays within 0.1 % of the
converged 3.5013 mm. Netgen (its size file restricts only): 524 → 765 elements,
error 3.94 → 1.71 % in four passes. And MMG's anisotropic passes on a heat layer
(ADR 0066 phase 4), against isotropic ones."""

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


def heated_bar(tmp_path, method: str):
    """Steel bar 200 × 10 × 10 mm from 20 °C, its end held at 100 °C for 2 s: a thin
    layer of heat (erfc), from 10 mm tets, adapted to a 0.2 % energy-norm error."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from conftest_geometry import bar_project, write_bar_step

    from puzzle.model import Feature, Physics, SolveStep

    root = tmp_path / method
    root.mkdir(parents=True)
    hot = Feature(type="temperature", selection="Bar/Root",
                  values={"T": Quantity(value=100, unit="degC")})  # fmt: skip
    heat = Physics(type="heat_transfer", features=[hot],
                   settings={"initial_temperature": Quantity(value=20, unit="degC"),
                             "temperature_dependent_materials": False})  # fmt: skip
    p, comp = bar_project(write_bar_step(root / "bar.step"), [heat], mesh_size_mm=10)
    study = p.add(Study(name="t", component=comp.id, solver="calculix", steps=[SolveStep(
        type="transient", settings={"time_step": Quantity(value=0.1, unit="s"),
                                    "end_time": Quantity(value=2, unit="s")})]))  # fmt: skip
    save_project(p, root)
    store = ResultStore(root / "results")
    settings = Adapt(target=0.002, passes=4, method=method, field="temperature")
    return adapt.run(p, study, root, root / "work", store=store, settings=settings), store


def test_mmg_follows_a_heat_layer_better_than_isotropic(tmp_path):
    """Measured: both from 403 elements at 1.75 %; MMG 1647 elements at 0.053 %,
    isotropic Gmsh 1710 at 0.14 %, each in two passes. Within 0.6 K of the erfc
    solution either way (the rest is backward Euler's time error)."""
    from puzzle.mesh.mmg import mmg_binary

    if mmg_binary() is None:
        pytest.skip("mmg not available")
    import math

    import numpy as np
    from scipy.special import erfc

    from puzzle.materials import library_material

    ani, store = heated_bar(tmp_path, "anisotropic")
    iso, _ = heated_bar(tmp_path, "isotropic")
    assert ani.converged and iso.converged
    a, i = ani.passes[-1], iso.passes[-1]
    assert a.error < i.error / 2 and a.elements <= i.elements * 1.1
    steel = library_material("structural_steel").properties
    k, rho, cp = (steel[q].to_si() for q in ("thermal_conductivity", "density", "specific_heat"))
    record = next(r for r in store.runs() if r.run_id == a.run)
    mesh = store.read_mesh(record.mesh_id)
    temp = store.read_results(a.run).steps[-1].fields["temperature"]
    x = mesh.node_coords(temp.ids)[:, 0]
    exact = 293.15 + 80 * erfc(x / (2 * math.sqrt(k / (rho * cp) * 2.0)))
    assert np.abs(temp.values[:, 0] - exact).max() < 0.7
    assert "Bar/Root" in mesh.groups and len(mesh.groups["Bar/Root"].node_ids) > 4
