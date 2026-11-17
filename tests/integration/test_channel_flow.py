"""Laminar channel flow (Code_Saturne, OpenFOAM): Poiseuille profile and pressure gradient."""

import importlib.util
from pathlib import Path

import numpy as np
import pytest
from solvers import over

from puzzle.model import Material, Study
from puzzle.project import load_project
from puzzle.solve import run_study

BENCHMARK = Path(__file__).parents[2] / "benchmarks" / "channel_flow"


def _constants():
    spec = importlib.util.spec_from_file_location("channel_build", BENCHMARK / "build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@over("code_saturne", "openfoam")
def test_poiseuille_profile_and_pressure_gradient(tmp_path, solver):
    ch = _constants()
    project = load_project(BENCHMARK)
    study = next(s for s in project.of_type(Study) if s.name == "Channel flow")
    study.solver = solver
    study.shootout = None  # one solver here; test_cfd_shootout.py compares them
    mu = project.of_type(Material)[0].properties["dynamic_viscosity"].to_si()
    run = run_study(project, study.id, BENCHMARK, tmp_path / "run", timeout_s=1800)

    velocity = run.results.last("velocity")
    pressure = run.results.last("pressure")
    assert velocity.location == pressure.location == "element"
    blocks = [b for b in run.mesh.blocks if b.dim == 3]
    ids = np.concatenate([b.ids for b in blocks])
    centres = np.vstack(
        [
            run.mesh.node_coords(b.connectivity.ravel())
            .reshape(*b.connectivity.shape, 3)
            .mean(axis=1)
            for b in blocks
        ]
    )
    centres = centres[np.argsort(ids)]
    x, y = centres[:, 0], centres[:, 1]

    at = np.abs(x - 0.07) < 0.3e-3
    u = velocity.at(np.sort(ids)[at])[:, 0]
    exact = 6 * ch.U * y[at] * (ch.H - y[at]) / ch.H**2
    assert u.max() / ch.U == pytest.approx(1.5, rel=0.01)
    assert np.linalg.norm(u - exact) / np.linalg.norm(exact) < 0.01

    def mean_pressure(x0):
        return pressure.at(np.sort(ids)[np.abs(x - x0) < 0.3e-3])[:, 0].mean()

    dpdx = (mean_pressure(0.08) - mean_pressure(0.05)) / 0.03
    assert dpdx == pytest.approx(-12 * mu * ch.U / ch.H**2, rel=0.02)


@over("code_saturne", "openfoam")
def test_hagen_poiseuille_on_hexahedra(tmp_path, solver):
    """Laminar pipe flow on a hexahedral mesh (the butterfly blocks of a cylinder), against
    Hagen–Poiseuille: u = 2 U (1 − r²/R²), dp/dz = −8 μ U / R². Water at 1 mm/s in a
    10 mm bore (Re 10, developed within 6 mm), 50 mm long, 1 mm cells. The blocks' cells
    are not parallelepipeds, so OpenFOAM's volume centres are not the node averages:
    up to 2.1e-5 m apart here, past the old match's 1e-5 of the model, and the solve's
    results could not be read (ADR 0090 OF-2). Five cells across the radius leave about
    2 % of discretisation error, so both are checked within 4 %. Measured 2026-11-17:
    dp/dz −1.7 % (OpenFOAM), −2.0 % (Code_Saturne); the profile 1.8 % and 1.9 % (L2)."""
    pytest.importorskip("OCP")
    from puzzle.materials import add_library_material
    from puzzle.model import (
        Component,
        Feature,
        Geometry,
        MaterialAssignment,
        MeshRecipe,
        NamedSelection,
        PartInstance,
        Physics,
        Project,
        Quantity,
        SolveStep,
    )
    from puzzle.model.schema import HistoryStep

    radius, length, speed = 0.005, 0.05, 1e-3

    def plane(name, z):
        return NamedSelection(name=name, entity="face", rule={"plane": {"axis": "z", "at": z}})

    p = Project(name="pipe")
    geo = p.add(Geometry(name="Pipe", history=[
        HistoryStep(kind="cylinder", name="C", args={"r": radius, "h": length})], selections=[
        NamedSelection(name="Fluid", entity="body", members=["Body"]),
        plane("Inlet", 0.0), plane("Outlet", length),
        NamedSelection(name="Wall", entity="face", members=["C/wall"])]))  # fmt: skip
    water = add_library_material(p, "water")
    mu = water.properties["dynamic_viscosity"].to_si()
    mesh = p.add(MeshRecipe(name="M", global_size=Quantity(value=1, unit="mm"),
                            element_order=1, element_shape="hexahedra"))  # fmt: skip
    comp = p.add(Component(name="Pipe", parts=[PartInstance(name="P", geometry=geo.id)],
                           mesh=mesh.id, materials=[MaterialAssignment(material=water.id,
                                                                       selection="P/Fluid")],
                           physics=[Physics(type="fluid_flow", features=[
                               Feature(type="velocity_inlet", selection="P/Inlet",
                                       values={"U": Quantity(value=speed, unit="m/s")}),
                               Feature(type="pressure_outlet", selection="P/Outlet",
                                       values={"p": Quantity(value=0, unit="Pa")}),
                               Feature(type="wall", selection="P/Wall")])]))  # fmt: skip
    study = p.add(
        Study(
            name="Pipe",
            component=comp.id,
            solver=solver,
            steps=[SolveStep(type="stationary", settings={"iterations": 1000})],
        )
    )
    run = run_study(p, study.id, tmp_path, tmp_path / "run", timeout_s=3600)
    blocks = [b for b in run.mesh.blocks if b.dim == 3]
    assert {b.element for b in blocks} == {"hex8"}
    ids = np.concatenate([b.ids for b in blocks])
    c = np.vstack([run.mesh.node_coords(b.connectivity.ravel()).reshape(
        *b.connectivity.shape, 3).mean(axis=1) for b in blocks])  # fmt: skip
    pressure = run.results.last("pressure").at(ids)[:, 0]
    w = run.results.last("velocity").at(ids)[:, 2]

    def mean_pressure(z0):
        return pressure[np.abs(c[:, 2] - z0) < 1.5e-3].mean()

    dpdz = (mean_pressure(0.04) - mean_pressure(0.025)) / 0.015
    at = np.abs(c[:, 2] - 0.035) < 0.6e-3
    r2 = c[at, 0] ** 2 + c[at, 1] ** 2
    exact = 2 * speed * (1 - r2 / radius**2)
    profile = np.linalg.norm(w[at] - exact) / np.linalg.norm(exact)
    print(f"{solver}: dp/dz {dpdz:.5g} Pa/m (Hagen-Poiseuille {-8 * mu * speed / radius**2:.5g}), "
          f"profile {profile:.2%} (L2)")  # fmt: skip
    assert dpdz == pytest.approx(-8 * mu * speed / radius**2, rel=0.04)
    assert profile < 0.04
