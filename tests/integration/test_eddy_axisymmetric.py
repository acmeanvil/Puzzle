"""Axisymmetric magnetic fields (ADR 0047 item 7): induction heating of a long billet.

A copper billet of radius a inside a winding of N turns carrying I, the model a slice of
height h with the natural condition (flux normal: magnetic symmetry) on every edge, so it
is an infinitely long solenoid: H0 = N I / h inside the winding, none outside. In the
billet, with k = (1 − j)/δ: H(r) = H0 J0(kr) / J0(ka), J_φ = H0 k J1(kr) / J0(ka), and the
loss is ½ ∫ |J|² / σ 2π r h dr.
"""

import math

import numpy as np
import pytest
from scipy.integrate import quad
from scipy.special import jv
from solvers import over

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
A, GAP, COIL, OUT, H = 0.010, 0.012, 0.014, 0.020, 0.005  # m
TURNS, CURRENT, HZ = 10.0, 10.0, 500.0


def _rect(name, x0, x1):
    pts = [[x0, 0.0], [x1, 0.0], [x1, H], [x0, H]]
    return HistoryStep(kind="face", name=name, args={"profile": {"points": pts}})


def _billet(solver):
    p = Project(name="billet")
    spans = {"Billet": (0.0, A), "Gap": (A, GAP), "Coil": (GAP, COIL), "Air": (COIL, OUT)}
    parts, contacts = [], []
    for name, (x0, x1) in spans.items():  # one part each, bonded edge to edge
        geo = p.add(
            Geometry(
                name=name,
                history=[_rect("R", x0, x1)],
                selections=[
                    NamedSelection(name="Region", entity="face", members=["R/face"]),
                    NamedSelection(name="Inner", entity="edge", members=["R/side-4"]),
                    NamedSelection(name="Outer", entity="edge", members=["R/side-2"]),
                ],
            )
        )
        parts.append(PartInstance(name=name, geometry=geo.id))
    names = list(spans)
    for a, b in zip(names[:-1], names[1:], strict=True):
        contacts.append(
            Contact(name=f"{a}-{b}", type="bonded", source=f"{a}/Outer", target=f"{b}/Inner")
        )
    copper, air = add_library_material(p, "copper"), add_library_material(p, "air")
    mesh = p.add(
        MeshRecipe(
            name="Mesh",
            element_order=1,
            global_size=Quantity(value=0.5, unit="mm"),
            local_sizes={"Billet/Region": Quantity(value=0.3, unit="mm")},
        )
    )
    coil = Feature(
        type="stranded_coil",
        selection="Coil/Region",
        values={
            "I": Quantity(value=CURRENT, unit="A"),
            "phase": Quantity(value=0, unit="deg"),
            "turns": Quantity(value=TURNS, unit="dimensionless"),
            "fill": Quantity(value=1, unit="dimensionless"),
        },
    )
    materials = [
        MaterialAssignment(
            material=(copper if n in ("Billet", "Coil") else air).id, selection=f"{n}/Region"
        )
        for n in ("Billet", "Gap", "Coil", "Air")
    ]
    comp = p.add(
        Component(
            name="Billet",
            dimension="2d_axisymmetric",
            mesh=mesh.id,
            parts=parts,
            contacts=contacts,
            materials=materials,
            physics=[Physics(type="magnetic_fields", features=[coil])],
        )
    )
    study = p.add(
        Study(
            name="AC",
            component=comp.id,
            solver=solver,
            steps=[SolveStep(type="frequency_domain", settings={"frequencies": f"{HZ:g}"})],
        )
    )
    sigma = copper.properties["electrical_conductivity"].to_si()
    return p, study, sigma


@over("elmer", "fenicsx")
def test_induction_heating_of_a_long_billet(tmp_path, solver):
    project, study, sigma = _billet(solver)
    run = run_study(project, study.id, tmp_path, tmp_path / "run", timeout_s=1800)
    (step,) = run.results.steps
    h0 = TURNS * CURRENT / H
    delta = math.sqrt(2 / (2 * math.pi * HZ * MU0 * sigma))
    k = (1 - 1j) / delta

    def density(r):
        return abs(h0 * k * jv(1, k * r) / jv(0, k * A)) ** 2 / sigma * math.pi * r * H

    exact = quad(density, 0, A, limit=200)[0]
    loss = step.fields["joule_loss"]
    got = loss.values[0, loss.components.index("Billet/Region")]
    b = step.fields["magnetic_flux_density_amplitude"]
    xy = run.mesh.node_coords(b.ids)
    centre = b.values[np.hypot(xy[:, 0], xy[:, 1] - H / 2) < 0.6e-3, 1].mean()
    b_exact = MU0 * h0 / abs(jv(0, k * A))
    print(
        f"{solver}: a/δ = {A / delta:.2f}, billet loss {got:.5g} W (exact {exact:.5g} W), "
        f"centre B {centre:.4g} T (exact {b_exact:.4g} T)"
    )
    assert got == pytest.approx(exact, rel=0.02)
    assert centre == pytest.approx(b_exact, rel=0.03)
    gap = (xy[:, 0] > A + 0.4e-3) & (xy[:, 0] < GAP - 0.4e-3)
    assert b.values[gap, 1].mean() == pytest.approx(MU0 * h0, rel=0.01)  # H0 in the gap


R_IN, R_OUT, RING = 0.010, 0.014, 100.0  # m, m, A: a solid copper tube, one turn


def _tube(solver):
    """A solid copper tube carrying RING amperes round it (a massive conductor, its
    voltage per turn the circuit's unknown), air inside and out, natural edges."""
    p = Project(name="tube")
    spans = {"Bore": (0.0, R_IN), "Tube": (R_IN, R_OUT), "Air": (R_OUT, OUT)}
    parts, contacts = [], []
    for name, (x0, x1) in spans.items():
        geo = p.add(
            Geometry(
                name=name,
                history=[_rect("R", x0, x1)],
                selections=[
                    NamedSelection(name="Region", entity="face", members=["R/face"]),
                    NamedSelection(name="Inner", entity="edge", members=["R/side-4"]),
                    NamedSelection(name="Outer", entity="edge", members=["R/side-2"]),
                ],
            )
        )
        parts.append(PartInstance(name=name, geometry=geo.id))
    for a, b in (("Bore", "Tube"), ("Tube", "Air")):
        contacts.append(
            Contact(name=f"{a}-{b}", type="bonded", source=f"{a}/Outer", target=f"{b}/Inner")
        )
    copper, air = add_library_material(p, "copper"), add_library_material(p, "air")
    mesh = p.add(
        MeshRecipe(
            name="Mesh",
            element_order=1,
            global_size=Quantity(value=0.5, unit="mm"),
            local_sizes={"Tube/Region": Quantity(value=0.15, unit="mm")},
        )
    )
    ring = Feature(
        type="coil_current",
        selection="Tube/Region",
        values={"I": Quantity(value=RING, unit="A"), "phase": Quantity(value=0, unit="deg")},
    )
    materials = [
        MaterialAssignment(material=(copper if n == "Tube" else air).id, selection=f"{n}/Region")
        for n in spans
    ]
    comp = p.add(
        Component(
            name="Tube",
            dimension="2d_axisymmetric",
            mesh=mesh.id,
            parts=parts,
            contacts=contacts,
            materials=materials,
            physics=[Physics(type="magnetic_fields", features=[ring])],
        )
    )
    study = p.add(
        Study(
            name="AC",
            component=comp.id,
            solver=solver,
            steps=[SolveStep(type="frequency_domain", settings={"frequencies": f"{HZ:g}"})],
        )
    )
    return p, study, copper.properties["electrical_conductivity"].to_si()


@over("elmer", "fenicsx")
def test_a_solid_ring_carries_its_current(tmp_path, solver):
    """The tube's wall, κ = (1 + j)/δ: H = a I0(κr) + b K0(κr) with H(r_i) = I/h and
    H(r_o) = 0; J = −dH/dr; its loss ½ ∫ |J|²/σ 2πr h dr; its voltage per turn by
    Faraday at r_o, V = 2π r_o J(r_o)/σ + jω Φ(r_o), so Z = V / I."""
    from scipy.special import iv, kv

    project, study, sigma = _tube(solver)
    run = run_study(project, study.id, tmp_path, tmp_path / "run", timeout_s=1800)
    (step,) = run.results.steps
    w = 2 * math.pi * HZ
    kappa = (1 + 1j) * math.sqrt(w * MU0 * sigma / 2)
    hin = RING / H
    m = np.array(
        [[iv(0, kappa * R_IN), kv(0, kappa * R_IN)], [iv(0, kappa * R_OUT), kv(0, kappa * R_OUT)]]
    )
    a, b = np.linalg.solve(m, [hin, 0.0])

    def h_of(r):
        return a * iv(0, kappa * r) + b * kv(0, kappa * r)

    def j_of(r):
        return -kappa * (a * iv(1, kappa * r) - b * kv(1, kappa * r))

    loss = quad(
        lambda r: abs(j_of(r)) ** 2 / (2 * sigma) * 2 * math.pi * r * H, R_IN, R_OUT, limit=200
    )[0]
    flux = MU0 * (
        math.pi * R_IN**2 * hin
        + quad(lambda r: (h_of(r) * 2 * math.pi * r).real, R_IN, R_OUT)[0]
        + 1j * quad(lambda r: (h_of(r) * 2 * math.pi * r).imag, R_IN, R_OUT)[0]
    )
    z = (2 * math.pi * R_OUT * j_of(R_OUT) / sigma + 1j * w * flux) / RING
    got = step.fields["joule_loss"].values[0, 0]
    r_got = step.fields["coil_resistance"].values[0, 0]
    l_got = step.fields["coil_inductance"].values[0, 0]
    print(
        f"{solver}: loss {got:.5g} W (exact {loss:.5g}), R {r_got:.5g} Ω (exact {z.real:.5g}),"
        f" L {l_got:.5g} H (exact {z.imag / w:.5g})"
    )
    assert got == pytest.approx(loss, rel=0.01)
    assert r_got == pytest.approx(z.real, rel=0.01)
    assert l_got == pytest.approx(z.imag / w, rel=0.01)
    b_field = step.fields["magnetic_flux_density_amplitude"]
    xy = run.mesh.node_coords(b_field.ids)
    bore = xy[:, 0] < 0.8 * R_IN
    assert b_field.values[bore, 1].mean() == pytest.approx(MU0 * hin, rel=0.01)


SPHERE, Z, R_COIL, SEGMENTS, B_FIELD = 0.010, 0.060, 0.060, 96, 1e-3  # m, m, m, -, T


def _sphere_case(solver, hz):
    """A copper sphere in a long solenoid's uniform AC field B0: the sphere a half-disc on
    the axis (a 96-sided arc), the winding a thin sheet far out, natural edges."""
    p = Project(name="sphere")
    arc = [
        [SPHERE * math.cos(t), SPHERE * math.sin(t)]
        for t in np.linspace(-math.pi / 2, math.pi / 2, SEGMENTS + 1)
    ]
    around = [[0.0, -Z], [R_COIL, -Z], [R_COIL, Z], [0.0, Z], *arc[::-1]]
    shapes = {
        "Sphere": arc,  # closes along the axis
        "Inside": around,  # sides 5 to 4 + SEGMENTS are the arc
        "Coil": [[R_COIL, -Z], [R_COIL + 0.002, -Z], [R_COIL + 0.002, Z], [R_COIL, Z]],
        "Outside": [[R_COIL + 0.002, -Z], [OUT + 0.05, -Z], [OUT + 0.05, Z], [R_COIL + 0.002, Z]],
    }
    edges = {
        "Sphere": {"Arc": [f"R/side-{k}" for k in range(1, SEGMENTS + 1)]},
        "Inside": {"Arc": [f"R/side-{k}" for k in range(5, 5 + SEGMENTS)], "Outer": ["R/side-2"]},
        "Coil": {"Inner": ["R/side-4"], "Outer": ["R/side-2"]},
        "Outside": {"Inner": ["R/side-4"]},
    }
    parts = []
    for name, points in shapes.items():
        step = HistoryStep(kind="face", name="R", args={"profile": {"points": points}})
        selections = [NamedSelection(name="Region", entity="face", members=["R/face"])]
        selections += [
            NamedSelection(name=n, entity="edge", members=m) for n, m in edges[name].items()
        ]
        geo = p.add(Geometry(name=name, history=[step], selections=selections))
        parts.append(PartInstance(name=name, geometry=geo.id))
    contacts = [
        Contact(name="Surface", type="bonded", source="Sphere/Arc", target="Inside/Arc"),
        Contact(name="Coil in", type="bonded", source="Inside/Outer", target="Coil/Inner"),
        Contact(name="Coil out", type="bonded", source="Coil/Outer", target="Outside/Inner"),
    ]
    copper, air = add_library_material(p, "copper"), add_library_material(p, "air")
    mesh = p.add(
        MeshRecipe(
            name="Mesh",
            element_order=1,
            global_size=Quantity(value=3, unit="mm"),
            local_sizes={"Sphere/Region": Quantity(value=0.4, unit="mm")},
        )
    )
    turns_current = B_FIELD * 2 * Z / MU0
    coil = Feature(
        type="stranded_coil",
        selection="Coil/Region",
        values={
            "I": Quantity(value=turns_current, unit="A"),
            "phase": Quantity(value=0, unit="deg"),
            "turns": Quantity(value=1, unit="dimensionless"),
            "fill": Quantity(value=1, unit="dimensionless"),
        },
    )
    materials = [
        MaterialAssignment(material=(copper if n == "Sphere" else air).id, selection=f"{n}/Region")
        for n in shapes
    ]
    comp = p.add(
        Component(
            name="Sphere",
            dimension="2d_axisymmetric",
            mesh=mesh.id,
            parts=parts,
            contacts=contacts,
            materials=materials,
            physics=[Physics(type="magnetic_fields", features=[coil])],
        )
    )
    study = p.add(
        Study(
            name="AC",
            component=comp.id,
            solver=solver,
            steps=[SolveStep(type="frequency_domain", settings={"frequencies": f"{hz:g}"})],
        )
    )
    return p, study, copper.properties["electrical_conductivity"].to_si()


@over("elmer", "fenicsx")
def test_a_conducting_sphere_in_an_ac_field(tmp_path, solver):
    """The induced loss in a sphere of radius a in a uniform field B0 (peak) at any a/δ,
    from its magnetic polarisability (Landau & Lifshitz, Electrodynamics of Continuous
    Media §59): α = −(3/8π)(1 − 3/(ka)² + 3 cot(ka)/(ka)), k = (1 + j)/δ, and
    P = (ω μ0 / 2) 4π |Im α| H0² (4/3) π a³; at small a/δ it is π σ ω² B0² a⁵ / 15."""
    hz = 200.0
    project, study, sigma = _sphere_case(solver, hz)
    run = run_study(project, study.id, tmp_path, tmp_path / "run", timeout_s=1800)
    (step,) = run.results.steps
    w = 2 * math.pi * hz
    delta = math.sqrt(2 / (w * MU0 * sigma))
    k = (1 + 1j) / delta
    alpha = -(3 / (8 * math.pi)) * (
        1 - 3 / (SPHERE * k) ** 2 + 3 / (SPHERE * k) / np.tan(SPHERE * k)
    )
    exact = (
        (w * MU0 / 2)
        * 4
        * math.pi
        * abs(alpha.imag)
        * (B_FIELD / MU0) ** 2
        * (4 / 3 * math.pi * SPHERE**3)
    )
    loss = step.fields["joule_loss"]
    got = loss.values[0, loss.components.index("Sphere/Region")]
    print(f"{solver}: a/δ = {SPHERE / delta:.2f}, sphere loss {got:.5g} W (exact {exact:.5g} W)")
    assert got == pytest.approx(exact, rel=0.02)
