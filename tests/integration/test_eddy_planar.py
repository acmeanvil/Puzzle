"""Planar 2D magnetic fields (ADR 0048 round 5, ADR 0047): A_z, currents along z, every
total per metre of depth.

- A round copper wire of radius a carrying I at DC in an air disc of radius R (A = 0 on
  its rim): B = μ0 I r / (2π a²) inside, μ0 I / (2π r) outside; energy per metre
  μ0 I² / (4π) (1/4 + ln(R/a)); resistance per metre 1 / (σ π a²).
- The same wire at a/δ = 2.5: its internal impedance per metre is
  k J0(ka) / (2π a σ J1(ka)), k = (1 − j)/δ, so R_ac/R_dc follows Bessel's solution; its
  inductance adds μ0 ln(R/a) / 2π outside.
- A two-wire line (go and return, centres d apart) at a low frequency: its loop
  inductance per metre is μ0/π (ln(d/a) + 1/4) with uniform currents (the mean of the
  other wire's A over a disc is its value at the centre; arccosh(d/2a) is the
  high-frequency limit), less μ0/π ln((R² + h²)/(R² − h²))
  for the images in the rim at R (A = 0 there acts as a shield; h = d/2).
- Elmer and FEniCSx on the same mesh agree with each other.
"""

import math

import numpy as np
import pytest
from scipy.special import jv
from solvers import available, over

from puzzle.materials import add_library_material
from puzzle.model import (
    Component,
    Contact,
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
    Study,
)
from puzzle.model.schema import HistoryStep
from puzzle.solve import run_study

pytest.importorskip("OCP")

MU0 = 4e-7 * math.pi
WIRE, AIR, CURRENT = 0.005, 0.05, 100.0  # m, m, A
SPACING, LINE_AIR = 0.03, 0.5  # m: the two-wire line's centres apart, its air's radius


def _disc(name, centre, r, holes=()):
    profile = {"circle": [centre[0], centre[1], r]}
    if holes:
        profile["holes"] = [{"circle": [x, y, rh]} for x, y, rh in holes]
    return HistoryStep(kind="face", name=name, args={"profile": profile})


def _wire(p, name, centre):
    return p.add(
        Geometry(
            name=name,
            history=[_disc("D", centre, WIRE)],
            selections=[
                NamedSelection(name="Region", entity="face", members=["D/face"]),
                NamedSelection(name="Rim", entity="edge", members=["D/side-1"]),
            ],
        )
    )


def _project(solver, wires, step, phases=None, air_r=AIR, size=4.0, kind="coil_current"):
    """Copper wires (name → centre) in an air disc, each a massive conductor carrying
    CURRENT at its phase (degrees), A = 0 on the air's rim."""
    p = Project(name="wires")
    holes = [(c[0], c[1], WIRE) for c in wires.values()]
    air = p.add(
        Geometry(
            name="Air",
            history=[_disc("D", (0.0, 0.0), air_r, holes)],
            selections=[
                NamedSelection(name="Region", entity="face", members=["D/face"]),
                NamedSelection(name="Rim", entity="edge", members=["D/side-1"]),
                *(
                    NamedSelection(name=f"Hole{k}", entity="edge", members=[f"D/side-{k + 2}"])
                    for k in range(len(wires))
                ),
            ],
        )
    )
    parts = [PartInstance(name="Air", geometry=air.id)]
    contacts = []
    for k, (name, centre) in enumerate(wires.items()):
        geo = _wire(p, name, centre)
        parts.append(PartInstance(name=name, geometry=geo.id))
        contacts.append(
            Contact(
                name=f"{name}-Air", type="bonded", source=f"{name}/Rim", target=f"Air/Hole{k}"
            )  # fmt: skip
        )
    copper, gas = add_library_material(p, "copper"), add_library_material(p, "air")
    mesh = p.add(
        MeshRecipe(
            name="Mesh",
            element_order=1,
            global_size=Quantity(value=size, unit="mm"),
            local_sizes={f"{n}/Region": Quantity(value=0.3, unit="mm") for n in wires},
        )
    )
    phases = phases or {}
    turns = (
        {
            "turns": Quantity(value=4, unit="dimensionless"),
            "fill": Quantity(value=1, unit="dimensionless"),
        }
        if kind == "stranded_coil"
        else {}
    )
    current = CURRENT / 4 if turns else CURRENT  # a winding: 4 turns of I/4
    features = [
        Feature(
            type=kind,
            selection=f"{n}/Region",
            values={
                "I": Quantity(value=current, unit="A"),
                "phase": Quantity(value=phases.get(n, 0.0), unit="deg"),
                **turns,
            },
        )  # fmt: skip
        for n in wires
    ]
    features.append(Feature(type="magnetic_insulation", selection="Air/Rim"))
    materials = [MaterialAssignment(material=gas.id, selection="Air/Region")] + [
        MaterialAssignment(material=copper.id, selection=f"{n}/Region") for n in wires
    ]
    comp = p.add(
        Component(
            name="Line",
            dimension="2d_planar",
            mesh=mesh.id,
            parts=parts,
            contacts=contacts,
            materials=materials,
            physics=[Physics(type="magnetic_fields", features=features)],
        )
    )
    study = p.add(Study(name="S", component=comp.id, solver=solver, steps=[step]))
    sigma = copper.properties["electrical_conductivity"].to_si()
    return p, study, sigma


def _ring_mean(run, field, r, width=0.3e-3, component=None):
    """A nodal field's mean (magnitude, or one component) on the ring of radius r."""
    xy = run.mesh.node_coords(field.ids)
    near = np.abs(np.hypot(xy[:, 0], xy[:, 1]) - r) < width
    v = field.values[near]
    return (np.linalg.norm(v, axis=1) if component is None else v[:, component]).mean()


@over("elmer", "fenicsx")
def test_a_round_wire_at_dc(tmp_path, solver):
    project, study, sigma = _project(solver, {"Wire": (0.0, 0.0)},
                                     SolveStep(type="stationary"))  # fmt: skip
    run = run_study(project, study.id, tmp_path, tmp_path / "run", timeout_s=900)
    (step,) = run.results.steps
    b = step.fields["magnetic_flux_density"]
    inside = _ring_mean(run, b, WIRE / 2)
    outside = _ring_mean(run, b, 4 * WIRE, width=1e-3)
    energy = step.fields["magnetic_energy"].values[0, 0]
    resistance = step.fields["coil_resistance"].values[0, 0]
    exact_energy = MU0 * CURRENT**2 / (4 * math.pi) * (0.25 + math.log(AIR / WIRE))
    print(f"{solver}: B(a/2) {inside:.5g} T (exact {MU0 * CURRENT / (4 * math.pi * WIRE):.5g}), "
          f"B(4a) {outside:.5g} T (exact {MU0 * CURRENT / (8 * math.pi * WIRE):.5g}), "
          f"W {energy:.5g} J/m (exact {exact_energy:.5g}), R {resistance:.5g} Ω/m "
          f"(exact {1 / (sigma * math.pi * WIRE**2):.5g})")  # fmt: skip
    assert inside == pytest.approx(MU0 * CURRENT / (4 * math.pi * WIRE), rel=0.02)
    assert outside == pytest.approx(MU0 * CURRENT / (8 * math.pi * WIRE), rel=0.02)
    assert energy == pytest.approx(exact_energy, rel=0.01)
    assert resistance == pytest.approx(1 / (sigma * math.pi * WIRE**2), rel=0.01)
    j = step.fields["current_density"]  # uniform, along z
    assert j.values[:, 2].max() == pytest.approx(CURRENT / (math.pi * WIRE**2), rel=0.02)


@over("elmer", "fenicsx")
def test_a_stranded_winding_at_dc(tmp_path, solver):
    """A winding (4 turns of I/4, no eddy currents) gives the wire's field, and its
    inductance per metre Ψ/I = μ0/(2π) (1/4 + ln(R/a)) (per turn²: N² of it)."""
    project, study, _ = _project(solver, {"Wire": (0.0, 0.0)}, SolveStep(type="stationary"),
                                 kind="stranded_coil")  # fmt: skip
    run = run_study(project, study.id, tmp_path, tmp_path / "run", timeout_s=900)
    (step,) = run.results.steps
    b = step.fields["magnetic_flux_density"]
    inductance = step.fields["coil_inductance"].values[0, 0]
    exact = 16 * MU0 / (2 * math.pi) * (0.25 + math.log(AIR / WIRE))
    print(f"{solver}: winding L {inductance:.5g} H/m (exact {exact:.5g})")
    assert _ring_mean(run, b, 4 * WIRE, width=1e-3) == pytest.approx(
        MU0 * CURRENT / (8 * math.pi * WIRE), rel=0.02
    )
    assert inductance == pytest.approx(exact, rel=0.01)


@over("elmer", "fenicsx")
def test_the_ac_resistance_of_a_round_wire(tmp_path, solver):
    sigma = 5.8e7
    delta = WIRE / 2.5
    hz = 1 / (math.pi * MU0 * sigma * delta**2)
    project, study, sigma = _project(
        solver, {"Wire": (0.0, 0.0)},
        SolveStep(type="frequency_domain", settings={"frequencies": f"{hz:.6g}"}),
    )  # fmt: skip
    delta = math.sqrt(2 / (2 * math.pi * hz * MU0 * sigma))  # the library copper's own σ
    k = (1 - 1j) / delta
    z_int = k * jv(0, k * WIRE) / (2 * math.pi * WIRE * sigma * jv(1, k * WIRE))
    r_dc = 1 / (sigma * math.pi * WIRE**2)
    omega = 2 * math.pi * hz
    l_exact = z_int.imag / omega + MU0 * math.log(AIR / WIRE) / (2 * math.pi)
    run = run_study(project, study.id, tmp_path, tmp_path / "run", timeout_s=900)
    (step,) = run.results.steps
    resistance = step.fields["coil_resistance"].values[0, 0]
    inductance = step.fields["coil_inductance"].values[0, 0]
    loss = step.fields["joule_loss"].values[0, 0]
    print(f"{solver}: a/δ = {WIRE / delta:.2f}, R/Rdc {resistance / r_dc:.4f} "
          f"(exact {z_int.real / r_dc:.4f}), L {inductance:.5g} H/m (exact {l_exact:.5g}), "
          f"loss {loss:.5g} W/m (½ R I² {0.5 * z_int.real * CURRENT**2:.5g})")  # fmt: skip
    assert resistance == pytest.approx(z_int.real, rel=0.02)
    assert inductance == pytest.approx(l_exact, rel=0.01)
    assert loss == pytest.approx(0.5 * resistance * CURRENT**2, rel=0.01)
    from puzzle import magnetics  # the skin-depth checks read 2D elements too

    assert magnetics._element_size(run.mesh, "Wire/Region") == pytest.approx(0.3e-3, rel=0.25)
    assert magnetics.mesh_warnings(project, study, run.mesh) == []  # 0.3 mm < δ/3


def _line(solver):
    half = SPACING / 2
    return _project(
        solver, {"Go": (-half, 0.0), "Return": (half, 0.0)},
        SolveStep(type="frequency_domain", settings={"frequencies": "1"}),
        phases={"Return": 180.0}, air_r=LINE_AIR, size=25.0,
    )  # fmt: skip


@over("elmer", "fenicsx")
def test_the_inductance_of_a_two_wire_line(tmp_path, solver):
    project, study, _ = _line(solver)
    run = run_study(project, study.id, tmp_path, tmp_path / "run", timeout_s=900)
    (step,) = run.results.steps
    per_coil = step.fields["coil_inductance"].values[0]
    loop = float(per_coil.sum())  # Z_go + Z_return: each its V over its own current
    h2, r2 = (SPACING / 2) ** 2, LINE_AIR**2
    shield = math.log((r2 + h2) / (r2 - h2))
    exact = MU0 / math.pi * (math.log(SPACING / WIRE) + 0.25 - shield)
    energy = step.fields["magnetic_energy"].values[0, 0]  # ¼ L I² (peak phasors)
    print(f"{solver}: loop L {loop:.5g} H/m (exact {exact:.5g}), "
          f"from the energy {4 * energy / CURRENT**2:.5g}")  # fmt: skip
    assert loop == pytest.approx(exact, rel=0.01)
    assert 4 * energy / CURRENT**2 == pytest.approx(exact, rel=0.01)
    lorentz = step.fields["lorentz_force_density"]  # the wires repel: Go pushed to −x
    assert lorentz.values[:, 0].min() < 0 < lorentz.values[:, 0].max()


@pytest.mark.skipif(not (available("elmer") and available("fenicsx")),
                    reason="needs Elmer and FEniCSx")  # fmt: skip
def test_elmer_and_fenicsx_agree_on_one_mesh(tmp_path):
    got = {}
    for solver in ("elmer", "fenicsx"):
        project, study, _ = _line(solver)
        run = run_study(project, study.id, tmp_path / solver, tmp_path / solver / "run",
                        timeout_s=900)  # fmt: skip
        (step,) = run.results.steps
        got[solver] = (step.fields["joule_loss"].values[0, 0],
                       step.fields["magnetic_energy"].values[0, 0],
                       step.fields["coil_inductance"].values[0].sum())  # fmt: skip
    print(got)
    for a, b in zip(got["elmer"], got["fenicsx"], strict=True):
        assert a == pytest.approx(b, rel=2e-3)
