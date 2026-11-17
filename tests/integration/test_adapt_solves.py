"""Adaptive refinement on CalculiX (ADR 0066 phase 3): the steel cantilever of the
convergence benchmark, second-order tets from 8 mm, adapted to a 1.5 % energy-norm
error.

Measured (CalculiX 2.22): Gmsh 665 → 1227 → 1273 elements, error 3.17 → 1.68 →
1.24 %, converged in three passes, where uniform refinement (sizes ÷ 1.5 twice) took
3037 elements to reach 2.12 %. The tip deflection stays within 0.1 % of the
converged 3.5013 mm. Netgen (its size file restricts only): 524 → 765 elements,
error 3.94 → 1.71 % (the test runs three passes). And MMG's anisotropic passes on a heat layer
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
    """Measured (2026-11-16, with the 3D metric constant 9/32): MMG 2056 elements at
    0.041 %, isotropic Gmsh 1711 at 0.180 %, each in two passes: a quarter of the error
    for a fifth more elements (at second order that many more isotropic elements would
    take about a tenth off its error). Within 0.6 K of the erfc solution either way (the
    rest is backward Euler's time error)."""
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
    assert a.error < i.error / 3 and a.elements <= i.elements * 1.25
    steel = library_material("structural_steel").properties
    k, rho, cp = (steel[q].to_si() for q in ("thermal_conductivity", "density", "specific_heat"))
    record = next(r for r in store.runs() if r.run_id == a.run)
    mesh = store.read_mesh(record.mesh_id)
    temp = store.read_results(a.run).steps[-1].fields["temperature"]
    x = mesh.node_coords(temp.ids)[:, 0]
    exact = 293.15 + 80 * erfc(x / (2 * math.sqrt(k / (rho * cp) * 2.0)))
    assert np.abs(temp.values[:, 0] - exact).max() < 0.7
    assert "Bar/Root" in mesh.groups and len(mesh.groups["Bar/Root"].node_ids) > 4


def test_a_plate_with_a_hole_adapts_where_the_stress_concentrates(tmp_path):
    """ADR 0066 decision 7: a quarter of a 200 x 200 x 5 mm steel plate with a 20 mm hole,
    pulled at 10 MPa, symmetric on x = 0 and y = 0. A plate in tension is mostly uniform
    stress, so 2 % comes on the first mesh (12 mm: 1.55 %, peak 3.27 by nodal overshoot);
    adapted to 0.5 % energy-norm error, the peak σxx at the hole's top is Heywood's
    K_t = (2 + (1 - d/W)³) / (1 - d/W) = 3.03 of the gross stress (a finite-width strip,
    as in Peterson's; Kirsch's 3 for an infinite plate) within 5 %: a 5 mm plate's
    mid-plane peak sits a few per cent above plane stress, and this one is as long as it
    is wide. A uniform mesh with at least as many elements stays above 0.5 %: the passes
    put the elements at the hole. Measured (CalculiX, 2026-11-15): 802, 2214, 2786, 3305
    elements at 1.55, 0.62, 0.50, 0.46 %; the peak 3.27 on the first mesh, 3.14 adapted
    (+3.7 %); uniform 4.53 mm, 3998 elements, 0.89 %."""
    from puzzle.materials import add_library_material
    from puzzle.model.schema import (
        Component,
        Feature,
        Geometry,
        HistoryStep,
        MaterialAssignment,
        NamedSelection,
        PartInstance,
        Physics,
        Project,
        SolveStep,
    )
    from puzzle.post import estimates
    from puzzle.solve import run_study

    if not available("calculix"):
        pytest.skip("calculix not available")
    pytest.importorskip("OCP")
    half, thick, r, stress = 0.1, 0.005, 0.01, 10e6

    def plate(size_mm):
        p = Project(name="kirsch")
        geo = p.add(Geometry(name="Plate", history=[
            HistoryStep(kind="box", name="B", args={"dx": half, "dy": half, "dz": thick}),
            HistoryStep(kind="hole", name="H", args={"r": r, "x": 0.0, "y": 0.0})],
            selections=[NamedSelection(name="Body", entity="body", members=["body-1"]),
                        NamedSelection(name="Hole", entity="face", members=["H/wall"]),
                        NamedSelection(name="X0", entity="face",
                                       rule={"plane": {"axis": "x", "at": 0.0}}),
                        NamedSelection(name="Y0", entity="face",
                                       rule={"plane": {"axis": "y", "at": 0.0}}),
                        NamedSelection(name="Pull", entity="face",
                                       rule={"plane": {"axis": "x", "at": half}})]))  # fmt: skip
        steel = add_library_material(p, "structural_steel")
        mesh = p.add(MeshRecipe(name="Tets", global_size=Quantity(value=size_mm, unit="mm")))
        comp = p.add(Component(
            name="Plate", parts=[PartInstance(name="P", geometry=geo.id)], mesh=mesh.id,
            materials=[MaterialAssignment(material=steel.id, selection="P/Body")],
            physics=[Physics(type="solid_mechanics", settings={
                "temperature_dependent_materials": False}, features=[
                Feature(type="symmetry", selection="P/X0"),
                Feature(type="symmetry", selection="P/Y0"),
                Feature(type="displacement", selection="P/Y0",
                        values={"z": Quantity(value=0, unit="m")}),
                Feature(type="pressure", selection="P/Pull",
                        values={"p": Quantity(value=-stress, unit="Pa")})])]))  # fmt: skip
        study = p.add(Study(name="Pull", component=comp.id, solver="calculix",
                            steps=[SolveStep(type="stationary")]))  # fmt: skip
        return p, study

    p, study = plate(12.0)
    root = tmp_path / "adapt"
    save_project(p, root)
    store = ResultStore(root / "results")
    out = adapt.run(p, study, root, root / "work", store=store,
                    settings=Adapt(target=0.005, passes=6))  # fmt: skip
    print(adapt.lines(out))
    assert out.converged and len(out.passes) > 1, out.ended
    last = out.passes[-1]
    run = next(r for r in store.runs() if r.run_id == last.run)
    mesh, results = store.read_mesh(run.mesh_id), store.read_results(run.run_id)
    s = results.steps[-1].fields["stress"]
    top = s.at(mesh.groups["P/Hole"].node_ids)[:, s.components.index("xx")].max()
    heywood = (2 + (1 - r / half) ** 3) / (1 - r / half)
    print(f"peak σxx {top / stress:.4f} of the gross stress (Heywood {heywood:.4f})")
    assert top / stress == pytest.approx(heywood, rel=0.05)

    size, count = 12.0, 0
    while count < last.elements:  # a uniform mesh with at least as many elements
        size *= 0.85
        p2, study2 = plate(size)
        root2 = tmp_path / f"uniform-{size:.2f}"
        save_project(p2, root2)
        store2 = ResultStore(root2 / "results")
        even = run_study(p2, study2.id, root2, root2 / "w", store=store2, timeout_s=1800)
        count = sum(len(b.ids) for b in even.mesh.blocks if b.dim == 3)
    eta = estimates.of_run(p2, store2, even.run_id).relative
    print(
        f"adapted: {last.elements} elements, {last.error:.2%}; uniform at {size:.2f} mm: "
        f"{count} elements, {eta:.2%}"
    )
    assert eta > 0.005
