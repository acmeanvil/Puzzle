"""Transient flow on Code_Saturne against a closed form (ADR 0072: its capability matrix
claims transient fluid_flow, and the maintainer chose a benchmark, 2026-11-15).

A temperature front carried by plug flow: a 2D channel 200 mm long and 10 mm wide with
symmetry planes for walls, so the flow is uniform at U = 5 mm/s, air at 20 °C, and the
inlet held at 30 °C from t = 0. Along the channel the temperature then solves
∂T/∂t + U ∂T/∂x = α ∂²T/∂x² on a half-line, whose solution is Ogata and Banks' (1961):

    (T - T0) / (T1 - T0) = ½ [erfc((x - U t) / s) + exp(U x / α) erfc((x + U t) / s)]

with s = 2 √(α t) and α = k / (ρ cp) of air (2.1e-5 m²/s): at 20 s the front is at
100 mm and 41 mm wide (s), well clear of the outlet. Cells are 1 mm (cell Péclet 0.24), steps 50 ms
(Courant 0.25) and first order in time, so the front's numerical diffusion is about
U² Δt / 2 = 3 % of α. The test reads the field at 10 and 20 s and the initial state,
one step per output time. Measured (Code_Saturne 9.2, 2026-11-15): the largest error
anywhere 0.53 % of the rise at 10 s and 0.50 % at 20 s; the velocity U to 1e-3.
"""

from __future__ import annotations

import numpy as np
import pytest
import test_mesh_layer as base
from scipy.special import erfc
from solvers import over

from puzzle.model import Feature, Physics, Quantity, SolveStep, Study
from puzzle.solve import run_study

pytest.importorskip("OCP")

U, LENGTH, T0, T1 = 0.005, 0.2, 293.15, 303.15
DT, END, EVERY = 0.05, 20.0, 200  # output every 10 s


def ogata_banks(x, t, alpha):
    s = 2 * np.sqrt(alpha * t)
    # exp(U x / α) erfc(...) overflows separately far downstream; it is tiny there
    tail = np.exp(np.minimum(U * x / alpha, 700.0)) * erfc((x + U * t) / s)
    return 0.5 * (erfc((x - U * t) / s) + tail)


@over("code_saturne")
def test_a_temperature_front_in_plug_flow(tmp_path, solver):
    from puzzle.materials import library_material

    air = library_material("air").properties
    rho, cp, k = (air[n].to_si() for n in ("density", "specific_heat", "thermal_conductivity"))
    alpha = k / (rho * cp)
    p, comp = base.cfd_2d("2d_planar", "quadrilaterals", 1.0, length=LENGTH, fluid="air",
                          speed=U)  # fmt: skip
    flow = comp.physics[0]
    flow.features = [f if f.type != "wall" else Feature(type="symmetry", selection=f.selection)
                     for f in flow.features]  # fmt: skip
    comp.physics.append(Physics(
        type="heat_transfer",
        settings={"initial_temperature": {"value": T0, "unit": "K"}},
        features=[Feature(type="temperature", selection="Duct/Inlet",
                          values={"T": Quantity(value=T1, unit="K")})],
    ))  # fmt: skip
    settings = {"time_step": Quantity(value=DT, unit="s"),
                "end_time": Quantity(value=END, unit="s"), "output_every": EVERY}  # fmt: skip
    study = p.add(Study(name="front", component=comp.id, solver=solver,
                        steps=[SolveStep(type="transient", settings=settings)]))  # fmt: skip
    run = run_study(p, study.id, tmp_path, tmp_path / "run", timeout_s=1800)

    steps = run.results.steps
    assert [s.kind for s in steps] == ["transient"] * len(steps)
    times = [s.value for s in steps]
    assert times == pytest.approx([0.0, 10.0, 20.0], abs=1e-6), times
    blocks = run.mesh.region_blocks()
    ids = np.concatenate([b.ids for b in blocks])
    x = np.concatenate([run.mesh.node_coords(b.connectivity.ravel())
                        .reshape(*b.connectivity.shape, 3).mean(axis=1)[:, 0]
                        for b in blocks])  # fmt: skip
    first = steps[0].fields["temperature"].at(ids)[:, 0]
    assert first == pytest.approx(T0, abs=1e-4)  # the start (EnSight is single precision)
    for step in steps[1:]:
        u = step.fields["velocity"].at(ids)
        assert u[:, 0] == pytest.approx(U, rel=1e-3) and np.abs(u[:, 1]).max() < 1e-3 * U
        got = (step.fields["temperature"].at(ids)[:, 0] - T0) / (T1 - T0)
        want = ogata_banks(x, step.value, alpha)
        error = np.abs(got - want).max()
        print(f"t = {step.value:g} s: largest error {error:.4f} of the rise")
        # 0.53 % (10 s) and 0.50 % (20 s) of the rise measured 2026-11-18, as CLAUDE.md
        assert error < 0.006, step.value
