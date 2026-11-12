"""Element choices per body (ADR 0082 step 3) against closed forms.

The steel bar 200 x 10 x 10 mm as a cantilever with 100 N at its tip, against
Timoshenko's δ = PL³/3EI + PL/(κGA), κ = 5/6:
- linear bricks two through the depth: C3D8 shear-locks (far too stiff), C3D8I's
  incompatible modes bend (within 2 %);
- nearly incompressible (ν = 0.4999) quadratic tetrahedra: Code_Aster's mixed 3D_INCO_UPG
  within 2 %;
- a frame cantilever on Code_Aster's Euler-Bernoulli POU_D_E: PL³/3EI exactly, where its
  Timoshenko POU_D_T adds the shear term.
And Code_Aster's lumped heat capacity (3D_DIAG): after a sudden 100 °C at the root, no
node falls below the starting 20 °C (a consistent capacity's first steps undershoot).
"""

import numpy as np
import pytest
from conftest_geometry import BAR, bar_project, write_bar_step
from solvers import over

from puzzle.materials import library_material
from puzzle.model.schema import (
    ElementChoice,
    Feature,
    Material,
    Physics,
    Quantity,
    SolveStep,
    Study,
)
from puzzle.solve import run_study

L, B, H = BAR
FORCE = 100.0
STEEL = library_material("structural_steel").properties
E = STEEL["youngs_modulus"].to_si()
q = lambda v, u: Quantity(value=v, unit=u)  # noqa: E731


def _timoshenko(nu):
    i, a = B * H**3 / 12, B * H
    return FORCE * L**3 / (3 * E * i) + FORCE * L / ((5 / 6) * E / (2 * (1 + nu)) * a)


def _cantilever(tmp_path, solver, formulation=None, nu=None, **recipe):
    tmp_path.mkdir(parents=True, exist_ok=True)
    step = write_bar_step(tmp_path / "bar.step")
    physics = Physics(type="solid_mechanics", settings={"temperature_dependent_materials": False},
                      features=[Feature(type="fixed", selection="Bar/Root"),
                                Feature(type="force", selection="Bar/Tip",
                                        values={"y": q(-FORCE, "N")})])  # fmt: skip
    project, comp = bar_project(step, [physics], recipe.pop("size", 5.0), **recipe)
    if nu is not None:
        steel = next(iter(project.of_type(Material)))
        steel.properties["poissons_ratio"] = q(nu, "dimensionless")
    if formulation:
        comp.elements = [ElementChoice(selection="Bar/Body", formulation=formulation)]
    study = project.add(Study(name="S", component=comp.id, solver=solver,
                              steps=[SolveStep(type="stationary")]))  # fmt: skip
    run = run_study(project, study.id, tmp_path, tmp_path / "w", timeout_s=900)
    tip = run.mesh.groups["Bar/Tip"].node_ids
    return -run.results.last("displacement").at(tip)[:, 1].mean(), run


@over("calculix")
def test_incompatible_modes_unlock_linear_bricks(tmp_path, solver):
    exact = _timoshenko(STEEL["poissons_ratio"].to_si())
    plain, run = _cantilever(tmp_path / "a", solver, element_shape="hexahedra", element_order=1)
    unlocked, run = _cantilever(tmp_path / "b", solver, "incompatible_modes",
                                element_shape="hexahedra", element_order=1)  # fmt: skip
    deck = next((tmp_path / "b" / "w").rglob("*.inp")).read_text(encoding="utf-8")
    print(f"C3D8 {plain / exact:.3f} of Timoshenko's, C3D8I {unlocked / exact:.4f}")
    assert "TYPE=C3D8I" in deck
    assert plain < 0.92 * exact  # shear locking: 0.896 of it
    assert unlocked == pytest.approx(exact, rel=0.02)


@over("code_aster")
def test_mixed_elements_for_nearly_incompressible_bending(tmp_path, solver):
    nu = 0.4999
    exact = _timoshenko(nu)
    got, run = _cantilever(tmp_path, solver, "mixed_up", nu=nu, size=2.5)
    print(f"3D_INCO_UPG at nu {nu}: {got / exact:.4f} of Timoshenko's")
    assert got == pytest.approx(exact, rel=0.02)


@over("code_aster")
def test_euler_bernoulli_beams(tmp_path, solver):
    from puzzle import frames
    from puzzle.materials import add_library_material
    from puzzle.model.schema import (
        BeamSection,
        Component,
        MaterialAssignment,
        MeshRecipe,
        PartInstance,
        Project,
    )
    from puzzle.project import save_project

    pytest.importorskip("OCP")
    length, force = 2.0, 1000.0
    section = BeamSection(selection="F", shape="library", standard="aisc",
                          designation="W12X26", orientation=(0.0, 1.0, 0.0))  # fmt: skip
    p = Project(name="beam")
    geo = p.add(frames.geometry("Frame", {"A": [0, 0, 0], "B": [length, 0, 0]},
                                [["AB", "A", "B"]]))  # fmt: skip
    steel = add_library_material(p, "structural_steel")
    mesh = p.add(MeshRecipe(name="M", global_size=q(0.25, "m"), element_order=1))
    comp = p.add(Component(
        name="Frame", parts=[PartInstance(name="F", geometry=geo.id)], mesh=mesh.id,
        materials=[MaterialAssignment(material=steel.id, selection="F")],
        beam_sections=[section], elements=[ElementChoice(selection="F",
                                                          formulation="euler_bernoulli")],
        physics=[Physics(type="solid_mechanics",
                         settings={"temperature_dependent_materials": False},
                         features=[Feature(type="fixed", selection="F/A"),
                                   Feature(type="force", selection="F/B",
                                           values={"z": q(-force, "N")})])]))  # fmt: skip
    study = p.add(Study(name="S", component=comp.id, solver=solver,
                        steps=[SolveStep(type="stationary")]))  # fmt: skip
    save_project(p, tmp_path / "p")
    run = run_study(p, study.id, tmp_path / "p", tmp_path / "w")
    _a, i1, _i2, _j = frames.constants(section)
    exact = force * length**3 / (3 * steel.properties["youngs_modulus"].to_si() * i1)
    u = run.results.last("displacement")
    k = int(np.argmax(run.mesh.node_coords(u.ids)[:, 0]))
    got = -float(u.values[k, 2])
    print(f"POU_D_E tip {got:.8g} m against PL^3/3EI {exact:.8g}")
    assert got == pytest.approx(exact, rel=1e-6)


@over("code_aster")
def test_lumped_capacity_has_no_undershoot(tmp_path, solver):
    step = write_bar_step(tmp_path / "bar.step")
    heat = Physics(type="heat_transfer", settings={"initial_temperature": q(20, "degC"),
                                                   "temperature_dependent_materials": False},
                   features=[Feature(type="temperature", selection="Bar/Root",
                                     values={"T": q(100, "degC")})])  # fmt: skip
    project, comp = bar_project(step, [heat], 5.0)
    comp.elements = [ElementChoice(selection="Bar/Body", formulation="lumped")]
    transient = SolveStep(type="transient", settings={"time_step": q(0.5, "s"),
                                                      "end_time": q(5, "s")})  # fmt: skip
    study = project.add(Study(name="t", component=comp.id, solver=solver, steps=[transient]))
    run = run_study(project, study.id, tmp_path, tmp_path / "w", timeout_s=900)
    comm = next((tmp_path / "w").rglob("*.comm")).read_text(encoding="utf-8")
    assert "MODELISATION='3D_DIAG'" in comm
    coldest = min(float(s.fields["temperature"].values.min()) for s in run.results.steps
                  if "temperature" in s.fields)  # fmt: skip
    print(f"lumped: coldest {coldest - 273.15:.6f} °C (started at 20)")
    assert coldest >= 293.15 - 1e-6


@over("calculix", "code_aster")
def test_follower_pressure_under_large_deflection(tmp_path, solver):
    """ADR 0082 decision 6: a cantilever bar pressed on its top face to about a quarter
    of its length's deflection, large deflection on. CalculiX's pressure follows the face
    under NLGEOM; Code_Aster's now does too (TYPE_CHARGE='SUIV'). Both land on the same
    tip, and short of the small-deflection wL⁴/8EI."""
    from puzzle.geometry import import_step
    from puzzle.model.schema import NamedSelection

    pytest.importorskip("OCP")
    step = write_bar_step(tmp_path / "bar.step")
    model = import_step(step)
    p_top = 2.4e6  # Pa: w = p b = 24 kN/m, a tip deflection of about L/7
    physics = Physics(type="solid_mechanics", settings={"temperature_dependent_materials": False},
                      features=[Feature(type="fixed", selection="Bar/Root"),
                                Feature(type="pressure", selection="Bar/Top",
                                        values={"p": q(p_top, "Pa")})])  # fmt: skip
    project, comp = bar_project(step, [physics], 5.0)
    geo = project.objects[comp.parts[0].geometry]
    geo.selections.append(NamedSelection(name="Top", entity="face",
                                         members=model.faces_on_plane("y", B)))  # fmt: skip
    study = project.add(Study(name="S", component=comp.id, solver=solver, steps=[
        SolveStep(type="stationary", settings={"geometric_nonlinearity": True})]))  # fmt: skip
    run = run_study(project, study.id, tmp_path, tmp_path / "w", timeout_s=1800)
    tip = run.mesh.groups["Bar/Tip"].node_ids
    u = run.results.last("displacement").at(tip).mean(axis=0)
    small = p_top * B * L**4 / (8 * E * B * H**3 / 12)
    print(f"{solver}: tip u_x {u[0] * 1e3:.4f} mm, u_y {u[1] * 1e3:.4f} mm "
          f"(small deflection {-small * 1e3:.4f} mm)")  # fmt: skip
    assert -u[1] < small and u[0] < 0  # it shortens as it bends
    FOLLOWED[solver] = u
    if len(FOLLOWED) == 2:
        a, b = FOLLOWED["calculix"], FOLLOWED["code_aster"]
        assert b[1] == pytest.approx(a[1], rel=0.02) and b[0] == pytest.approx(a[0], rel=0.05)


FOLLOWED: dict = {}


@over("calculix", "code_aster")
def test_the_elastica(tmp_path, solver):
    """A thin strip (200 x 10 x 2 mm) as a cantilever under a tip load of fixed
    direction, PL²/EI = 1: Bisshopp & Drucker's elastica (Quart. Appl. Math. 3, 1945)
    puts the tip 0.3017 L down and 0.0566 L in. Strains stay below 0.5 %, so Green-
    Lagrange with plain elasticity is right; the first-order answer would be L/3, and
    no draw-in at all."""
    pytest.importorskip("OCP")
    size = (0.2, 0.01, 0.002)
    length, width, depth = size
    step = write_bar_step(tmp_path / "strip.step", size)
    inertia = width * depth**3 / 12
    force = 1.0 * E * inertia / length**2
    physics = Physics(type="solid_mechanics", settings={"temperature_dependent_materials": False},
                      features=[Feature(type="fixed", selection="Bar/Root"),
                                Feature(type="force", selection="Bar/Tip",
                                        values={"z": q(-force, "N")})])  # fmt: skip
    project, comp = bar_project(step, [physics], 2.0)
    study = project.add(Study(name="S", component=comp.id, solver=solver, steps=[
        SolveStep(type="stationary", settings={"geometric_nonlinearity": True,
                                               "increments": 20})]))  # fmt: skip
    run = run_study(project, study.id, tmp_path, tmp_path / "w", timeout_s=3600)
    tip = run.mesh.groups["Bar/Tip"].node_ids
    u = run.results.last("displacement").at(tip).mean(axis=0)
    down, drawn = -u[2] / length, -u[0] / length
    print(f"{solver} elastica: down {down:.4f} L (0.3017), in {drawn:.4f} L (0.0566)")
    assert down == pytest.approx(0.3017, rel=0.02)
    assert drawn == pytest.approx(0.0566, rel=0.04)


@over("code_aster", "calculix")
def test_the_elastica_on_beams(tmp_path, solver):
    """The elastica again, on line elements (ADR 0082 decision 6): a 1 m frame cantilever,
    a 10 × 2 mm rectangle (thin about its bending axis), PL²/EI = 1 at its tip, large
    deflection. Code_Aster writes POU_D_T_GD with GROT_GDEP, CalculiX its beams under
    NLGEOM: the tip 0.3017 L down and 0.0566 L in (Bisshopp & Drucker)."""
    pytest.importorskip("OCP")
    from puzzle import frames
    from puzzle.model.schema import (
        BeamSection,
        Component,
        MaterialAssignment,
        MeshRecipe,
        PartInstance,
        Project,
    )
    from puzzle.project import save_project

    length, width, depth = 1.0, 0.01, 0.002
    p = Project(name="elastica")
    geo = p.add(frames.geometry("Frame", {"A": [0, 0, 0], "B": [length, 0, 0]},
                                [["AB", "A", "B"]]))  # fmt: skip
    steel = p.add(Material(name="Steel", properties={
        "youngs_modulus": q(E, "Pa"), "poissons_ratio": q(0.3, "dimensionless"),
        "density": q(7850, "kg/m^3")}))  # fmt: skip
    mesh = p.add(MeshRecipe(name="M", global_size=q(length / 40, "m"),
                            element_order=1 if solver == "code_aster" else 2))  # fmt: skip
    inertia = width * depth**3 / 12  # about the first axis (y): the height across it, z
    force = E * inertia / length**2
    section = BeamSection(selection="F", orientation=(0.0, 1.0, 0.0), shape="rectangle",
                          width=q(width * 1e3, "mm"), height=q(depth * 1e3, "mm"))  # fmt: skip
    comp = p.add(Component(
        name="Frame", parts=[PartInstance(name="F", geometry=geo.id)], mesh=mesh.id,
        materials=[MaterialAssignment(material=steel.id, selection="F")],
        beam_sections=[section],
        physics=[Physics(type="solid_mechanics",
                         settings={"temperature_dependent_materials": False},
                         features=[Feature(type="fixed", selection="F/A"),
                                   Feature(type="force", selection="F/B",
                                           values={"z": q(-force, "N")})])]))  # fmt: skip
    study = p.add(Study(name="S", component=comp.id, solver=solver, steps=[
        SolveStep(type="stationary", settings={"geometric_nonlinearity": True,
                                               "increments": 20})]))  # fmt: skip
    root = tmp_path / "frame"
    save_project(p, root)
    run = run_study(p, study.id, root, tmp_path / "w", timeout_s=1800)
    u = run.results.last("displacement")
    xyz = run.mesh.node_coords(u.ids)
    k = int(np.argmin(np.linalg.norm(xyz - [length, 0, 0], axis=1)))
    down, drawn = -u.values[k, 2] / length, -u.values[k, 0] / length
    print(f"{solver} beam elastica: down {down:.4f} L (0.3017), in {drawn:.4f} L (0.0566)")
    assert down == pytest.approx(0.3017, rel=0.01)
    assert drawn == pytest.approx(0.0566, rel=0.03)
