"""Body loads (ADR 0037) against closed forms, on every FE solver.

Self weight: the cantilever (fixed at its root) under gravity, against the same mesh
under a tip force. Timoshenko beam theory gives the ratio of the tip deflections,
    (wL⁴/8EI + wL²/2κGA) / (PL³/3EI + PL/κGA),   w = ρgA,
so mesh error largely cancels; within 0.5 %. A linear acceleration a is the same load
as gravity −a, so its tip deflection is the gravity one, reversed.

Spinning disc: a thin steel quarter disc (R = 100 mm, 4 mm thick) on symmetry planes,
spun about its axis; plane-stress theory gives the rim's radial displacement
    u(R) = (1 − ν) ρ ω² R³ / (4E),   within 1 %.
"""

import shutil
from pathlib import Path

import numpy as np
import pytest
from solvers import over

from puzzle.model.schema import (
    Component,
    Feature,
    Geometry,
    HistoryStep,
    MaterialAssignment,
    MeshRecipe,
    NamedSelection,
    PartInstance,
    Physics,
    Project,
    Quantity,
    SolveStep,
    Study,
)
from puzzle.project import load_project, save_project
from puzzle.solve import run_study

EXAMPLE = Path(__file__).parents[2] / "examples" / "cantilever"
SOLVERS = ("calculix", "code_aster", "elmer", "fenicsx")


def _tip_y(run) -> float:
    u = run.results.last("displacement")
    xyz = run.mesh.node_coords(u.ids)
    tip = xyz[:, 0] > xyz[:, 0].max() - 1e-9
    return float(u.values[tip, 1].mean())


@over(*SOLVERS)
def test_self_weight_cantilever(tmp_path, solver):
    root = tmp_path / "cantilever"
    shutil.copytree(
        EXAMPLE, root, ignore=shutil.ignore_patterns("results*", ".puzzle", "history.jsonl")
    )
    p = load_project(root)
    comp = p.of_type(Component)[0]
    study = next(s for s in p.of_type(Study) if s.name == "Tip load")
    study.solver = solver
    force = comp.physics[0].features[1]
    body = comp.materials[0].selection
    material = p.objects[comp.materials[0].material]
    P = abs(force.values["y"].to_si())
    save_project(p, root)
    by_force = run_study(p, study.id, root, tmp_path / "w1")

    g = 9.81 * 1000  # a heavy gravity, so the deflection is well above round-off
    comp.physics[0].features[1] = Feature(
        type="gravity",
        selection=body,
        values={"gx": Quantity(value=0, unit="m/s^2"), "gy": Quantity(value=-g, unit="m/s^2"),
                "gz": Quantity(value=0, unit="m/s^2")},
    )  # fmt: skip
    save_project(p, root)
    by_weight = run_study(p, study.id, root, tmp_path / "w2")

    xyz = by_force.mesh.coords
    L = float(np.ptp(xyz[:, 0]))
    h = float(np.ptp(xyz[:, 1]))
    b = float(np.ptp(xyz[:, 2]))
    A, inertia = b * h, b * h**3 / 12
    E = material.properties["youngs_modulus"].to_si()
    nu = material.properties["poissons_ratio"].to_si()
    G, kappa = E / (2 * (1 + nu)), 10 * (1 + nu) / (12 + 11 * nu)  # rectangle
    w = material.properties["density"].to_si() * g * A
    expected = (w * L**4 / (8 * E * inertia) + w * L**2 / (2 * kappa * G * A)) / (
        P * L**3 / (3 * E * inertia) + P * L / (kappa * G * A)
    )
    ratio = _tip_y(by_weight) / _tip_y(by_force)
    print(f"{solver} self weight: ratio {ratio:.6g}, theory {expected:.6g}, "
          f"{100 * (ratio / expected - 1):+.3f} %")  # fmt: skip
    assert ratio == pytest.approx(expected, rel=5e-3)

    # A linear acceleration up (+y) loads the beam as gravity down (−y) does.
    comp.physics[0].features[1] = Feature(
        type="acceleration",
        selection=body,
        values={"ay": Quantity(value=g, unit="m/s^2")},
    )
    save_project(p, root)
    by_inertia = run_study(p, study.id, root, tmp_path / "w3")
    assert _tip_y(by_inertia) == pytest.approx(_tip_y(by_weight), rel=1e-6)


def _disc_project(solver: str) -> tuple[Project, Study]:
    p = Project(name="disc")
    R, t = 0.1, 0.004
    geo = p.add(
        Geometry(
            name="Disc",
            history=[
                HistoryStep(kind="cylinder", name="Disc", args={"r": R, "h": t}),
                HistoryStep(
                    kind="box",
                    name="Quarter",
                    args={"dx": 0.2, "dy": 0.2, "dz": 0.02, "z0": -0.01, "mode": "intersect"},
                ),
            ],
            selections=[
                NamedSelection(name="Body", entity="body", members=["body-1"]),
                *(
                    NamedSelection(name=n, entity="face", rule={"plane": {"axis": a, "at": 0.0}})
                    for n, a in (("Xsym", "x"), ("Ysym", "y"), ("Bottom", "z"))
                ),
            ],
        )
    )  # fmt: skip
    from puzzle.materials import add_library_material

    steel = add_library_material(p, "structural_steel")
    mesh = p.add(MeshRecipe(name="Tets", global_size=Quantity(value=5, unit="mm")))
    zero = Quantity(value=0, unit="m")
    comp = p.add(
        Component(
            name="Disc",
            parts=[PartInstance(name="D", geometry=geo.id)],
            mesh=mesh.id,
            materials=[MaterialAssignment(material=steel.id, selection="D/Body")],
            physics=[
                Physics(
                    type="solid_mechanics",
                    features=[
                        Feature(type="displacement", selection="D/Xsym", values={"x": zero}),
                        Feature(type="displacement", selection="D/Ysym", values={"y": zero}),
                        Feature(type="displacement", selection="D/Bottom", values={"z": zero}),
                        Feature(
                            type="rotation",
                            selection="D/Body",
                            values={"omega": Quantity(value=1000, unit="rad/s")},
                        ),
                    ],
                    settings={"temperature_dependent_materials": False},
                )
            ],
        )
    )
    study = p.add(
        Study(name="Spin", component=comp.id, solver=solver, steps=[SolveStep(type="stationary")])
    )
    return p, study


@over(*SOLVERS)
def test_spinning_disc(tmp_path, solver):
    pytest.importorskip("OCP")
    p, study = _disc_project(solver)
    root = tmp_path / "disc"
    save_project(p, root)
    run = run_study(p, study.id, root, tmp_path / "w")
    steel = next(iter(m for m in p.objects.values() if m.kind == "material"))
    E = steel.properties["youngs_modulus"].to_si()
    nu = steel.properties["poissons_ratio"].to_si()
    rho = steel.properties["density"].to_si()
    R, omega = 0.1, 1000.0
    expected = (1 - nu) * rho * omega**2 * R**3 / (4 * E)
    u = run.results.last("displacement")
    xyz = run.mesh.node_coords(u.ids)
    rim = np.isclose(np.hypot(xyz[:, 0], xyz[:, 1]), R, atol=1e-6)
    radial = (u.values[rim, 0] * xyz[rim, 0] + u.values[rim, 1] * xyz[rim, 1]) / R
    got = float(radial.mean())
    print(f"{solver} spinning disc: u(R) {got:.6g} m, theory {expected:.6g}, "
          f"{100 * (got / expected - 1):+.3f} %")  # fmt: skip
    assert got == pytest.approx(expected, rel=1e-2)


@over(*SOLVERS)
def test_amplitude_heat_flux_energy_balance(tmp_path, solver):
    """A heat flux into the cantilever's tip, switched on and off by a trapezoid amplitude
    (0 → 1 over 10 s, held 10 s, back to 0 over 10 s), every other face insulated. Time
    stepping at 1 s with backward Euler sums the amplitude at each step's end to 20 s,
    the trapezoid's area, so the mean temperature rise is exactly q·A·20 s / (ρ c V)."""
    from puzzle.model.schema import Amplitude
    from puzzle.results.query import integrate

    root = tmp_path / "cantilever"
    shutil.copytree(
        EXAMPLE, root, ignore=shutil.ignore_patterns("results*", ".puzzle", "history.jsonl")
    )
    p = load_project(root)
    comp = p.of_type(Component)[0]
    amp = p.add(Amplitude(name="Pulse", points=[(0.0, 0.0), (10.0, 1.0), (20.0, 1.0), (30.0, 0.0)]))
    q = 2.0e5
    heat = next(ph for ph in comp.physics if ph.type == "heat_transfer")
    heat.features = [
        Feature(
            type="heat_flux",
            selection="Bar/Tip",
            values={"q": Quantity(value=q, unit="W/m^2")},
            amplitude=amp.id,
        )
    ]
    heat.settings = {
        "temperature_dependent_materials": False,
        "initial_temperature": Quantity(value=20, unit="degC").model_dump(mode="json"),
    }
    study = next(s for s in p.of_type(Study) if s.name == "Conduction")
    study.solver = solver
    study.steps = [
        SolveStep(
            type="transient",
            physics=["heat_transfer"],
            settings={
                "time_step": {"value": 1, "unit": "s"},
                "end_time": {"value": 40, "unit": "s"},
            },
        )
    ]
    save_project(p, root)
    run = run_study(p, study.id, root, tmp_path / "w")
    body = comp.materials[0].selection
    mean = integrate(run.results, run.mesh, "temperature", body)
    steel = p.objects[comp.materials[0].material]
    rho = steel.properties["density"].to_si()
    c = steel.properties["specific_heat"].to_si()
    from puzzle.adapters.common import surface_area

    area = surface_area(run.mesh.groups["Bar/Tip"], run.mesh)
    expected = 293.15 + q * area * 20.0 / (rho * c * mean["volume"])
    print(f"{solver} amplitude energy: mean {mean['mean']:.6g} K, expected {expected:.6g} K")
    assert mean["mean"] == pytest.approx(expected, abs=1e-3 * (expected - 293.15))
