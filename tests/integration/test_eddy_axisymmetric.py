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
