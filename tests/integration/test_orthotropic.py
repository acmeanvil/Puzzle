"""Orthotropic materials in a system's axes (ADR 0049 step 4), on bars pulled along x:

- material axes turned 90° about z (1 along y, 2 along x): the bar stretches by
  F L / (A E2); unturned, by F L / (A E1);
- a round bar in cylindrical axes about its own axis (1 radial, 2 tangential, 3 axial),
  transversely isotropic about it: by F L / (A E3), E3 half of E1;
- conduction along a turned axis: from T0 at the root with q leaving the tip,
  T(L) = T0 - q L / k2.

E1, E2 and E3 differ by factors of two and more, so a wrong mapping can't pass. The root
face is held, its contraction too, so strains are read in the middle half of the bar."""

import math

import pytest
from conftest_geometry import BAR, bar_project, write_bar_step
from solvers import over

from puzzle.model import Feature, Physics, Quantity, SolveStep, Study
from puzzle.model.schema import (
    AnisotropicConductivity,
    CoordinateSystem,
    Geometry,
    HistoryStep,
    Material,
    MaterialModels,
    NamedSelection,
    OrthotropicElasticity,
)
from puzzle.solve import run_study

pytest.importorskip("OCP")

E1, E2, E3 = 200e9, 50e9, 100e9
ORTHO = OrthotropicElasticity(e1=E1, e2=E2, e3=E3, nu12=0.3, nu13=0.25, nu23=0.2,
                              g12=20e9, g13=30e9, g23=25e9)  # fmt: skip
F = 1000.0
CONSTANT = {"temperature_dependent_materials": False}


def qty(v, u):
    return Quantity(value=v, unit=u)


def _orthotropic(project, comp, axes=None, models=None):
    """The component's material made orthotropic, in `axes` (a system, or None)."""
    steel = project.get(comp.materials[0].material, Material)
    steel.models = models or MaterialModels(orthotropic_elasticity=ORTHO)
    comp.materials[0].axes = axes


def _pulled(features_extra=()):
    return Physics(type="solid_mechanics", settings=CONSTANT, features=[
        Feature(type="displacement", selection="Bar/Root", values={"x": qty(0, "mm")}),
        *features_extra,
        Feature(type="force", selection="Bar/Tip", values={"x": qty(F, "N")})])  # fmt: skip


def _strain(tmp_path, project, comp, solver, length):
    """The axial strain in the middle half of the bar: the slope of ux along x there,
    away from the held root and the loaded tip."""
    import numpy as np

    study = project.add(Study(name="s", component=comp.id, solver=solver,
                              steps=[SolveStep(type="stationary")]))  # fmt: skip
    run = run_study(project, study.id, tmp_path, tmp_path / "run", timeout_s=600)
    u = run.results.last("displacement")
    x = run.mesh.node_coords(u.ids)[:, 0]
    middle = (x > 0.25 * length) & (x < 0.75 * length)
    return float(np.polyfit(x[middle], u.values[middle, 0], 1)[0])


def _held_across(project):
    """The root also held in y and z (no rigid motion)."""
    return [Feature(type="displacement", selection="Bar/Root",
                    values={"y": qty(0, "mm"), "z": qty(0, "mm")})]  # fmt: skip


@over("calculix")
@pytest.mark.parametrize("turned", [True, False], ids=["turned", "unturned"])
def test_a_turned_orthotropic_bar_stretches_by_the_axis_along_it(tmp_path, solver, turned):
    L, B, H = BAR
    project, comp = bar_project(write_bar_step(tmp_path / "bar.step"), [], mesh_size_mm=5)
    comp.physics = [_pulled(_held_across(project))]
    axes = project.add(CoordinateSystem(name="Turned", angles=(0.0, 0.0, 90.0 if turned else 0.0)))
    _orthotropic(project, comp, axes.id)
    strain = _strain(tmp_path, project, comp, solver, L)
    E = E2 if turned else E1
    print(f"{solver}: {'turned' if turned else 'unturned'}, strain {strain:.6g} "
          f"(F / A E = {F / (B * H * E):.6g})")  # fmt: skip
    assert strain == pytest.approx(F / (B * H * E), rel=5e-3)


@over("calculix")
def test_cylindrical_material_axes_make_3_the_axial_one(tmp_path, solver):
    r, L = 0.005, 0.1
    from puzzle.materials import add_library_material
    from puzzle.model import Component, MaterialAssignment, MeshRecipe, PartInstance, Project

    project = Project(name="rod")
    geo = project.add(Geometry(name="Bar", history=[
        HistoryStep(kind="cylinder", name="Rod", args={"r": r, "h": L, "axis": "x"})],
        selections=[NamedSelection(name="Body", entity="body", members=["Body"]),
                    NamedSelection(name="Root", entity="face", members=["Rod/start"]),
                    NamedSelection(name="Tip", entity="face", members=["Rod/end"])]))  # fmt: skip
    steel = add_library_material(project, "structural_steel")
    mesh = project.add(MeshRecipe(name="M", global_size=qty(1.5, "mm")))
    comp = project.add(Component(name="Rod", mesh=mesh.id,
                                 parts=[PartInstance(name="Bar", geometry=geo.id)],
                                 materials=[MaterialAssignment(material=steel.id,
                                                               selection="Bar/Body")],
                                 physics=[_pulled(_held_across(project))]))  # fmt: skip
    axes = project.add(CoordinateSystem(name="Axial", type="cylindrical", axis=(1.0, 0.0, 0.0)))
    # Transversely isotropic about the axis (E1 = E2, nu13 = nu23): it contracts freely,
    # so the stretch is exactly F L / (A E3). (With E1 != E2 a cylindrically orthotropic
    # rod builds radial and hoop stresses under axial load, Lekhnitskii: 1.8 % stiffer.)
    rod = ORTHO.model_copy(update={"e2": E1, "nu23": ORTHO.nu13, "g12": E1 / 2.6,
                                   "g23": ORTHO.g13})  # fmt: skip
    _orthotropic(project, comp, axes.id, MaterialModels(orthotropic_elasticity=rod))
    strain = _strain(tmp_path, project, comp, solver, L)
    area = math.pi * r**2
    print(f"{solver}: cylindrical axes, strain {strain:.6g} (F / A E3 = {F / (area * E3):.6g})")
    assert strain == pytest.approx(F / (area * E3), rel=5e-3)


@over("calculix")
def test_conduction_along_a_turned_axis(tmp_path, solver):
    L = BAR[0]
    k1, k2, k3, q, T0 = 10.0, 40.0, 5.0, 2000.0, 300.0
    physics = Physics(type="heat_transfer", settings=CONSTANT, features=[
        Feature(type="temperature", selection="Bar/Root", values={"T": qty(T0, "K")}),
        Feature(type="heat_flux", selection="Bar/Tip",
                values={"q": qty(-q, "W/m^2")})])  # fmt: skip
    project, comp = bar_project(write_bar_step(tmp_path / "bar.step"), [physics], mesh_size_mm=5)
    axes = project.add(CoordinateSystem(name="Turned", angles=(0.0, 0.0, 90.0)))
    _orthotropic(project, comp, axes.id,
                 MaterialModels(anisotropic_conductivity=AnisotropicConductivity(
                     k1=k1, k2=k2, k3=k3)))  # fmt: skip
    study = project.add(Study(name="s", component=comp.id, solver=solver,
                              steps=[SolveStep(type="stationary")]))  # fmt: skip
    run = run_study(project, study.id, tmp_path, tmp_path / "run", timeout_s=600)
    tip = run.results.last("temperature").at(run.mesh.groups["Bar/Tip"].node_ids)[:, 0].mean()
    assert tip == pytest.approx(T0 - q * L / k2, abs=1e-3 * q * L / k2)
