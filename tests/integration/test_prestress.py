"""Prestressed modal analysis through a transfer: a stretched clamped-clamped bar.

The steel bar (200 x 10 x 10 mm) is clamped at its root and its tip is pulled 0.2 mm
along its axis, then held. The tension P = E A d / L stiffens bending, raising the first
frequency. The reference is a 1D Euler-Bernoulli beam with the geometric stiffness of P,
solved here with 200 Hermite elements; the 3D model is compared by the frequency
ratio f(P) / f(0), in which shear and 3D effects largely cancel.
"""

import numpy as np
import pytest
from conftest_geometry import BAR, bar_project, write_bar_step
from solvers import over

from puzzle.materials import library_material
from puzzle.model import Feature, Physics, Quantity, SolveStep, Study, Transfer
from puzzle.solve import run_study

L, B, H = BAR
STRETCH = 2e-4  # m
STEEL = library_material("structural_steel").properties
E, RHO = STEEL["youngs_modulus"].to_si(), STEEL["density"].to_si()


def _beam_frequency(tension: float, elements: int = 200) -> float:
    """First bending frequency (Hz) of a clamped-clamped beam under axial tension."""
    area, inertia, le = B * H, B * H**3 / 12, L / elements
    k = (
        E
        * inertia
        / le**3
        * np.array(
            [
                [12, 6 * le, -12, 6 * le],
                [6 * le, 4 * le**2, -6 * le, 2 * le**2],
                [-12, -6 * le, 12, -6 * le],
                [6 * le, 2 * le**2, -6 * le, 4 * le**2],
            ]
        )
    )
    g = (
        tension
        / (30 * le)
        * np.array(
            [
                [36, 3 * le, -36, 3 * le],
                [3 * le, 4 * le**2, -3 * le, -(le**2)],
                [-36, -3 * le, 36, -3 * le],
                [3 * le, -(le**2), -3 * le, 4 * le**2],
            ]
        )
    )
    m = (
        RHO
        * area
        * le
        / 420
        * np.array(
            [
                [156, 22 * le, 54, -13 * le],
                [22 * le, 4 * le**2, 13 * le, -3 * le**2],
                [54, 13 * le, 156, -22 * le],
                [-13 * le, -3 * le**2, -22 * le, 4 * le**2],
            ]
        )
    )
    n = 2 * (elements + 1)
    K, M = np.zeros((n, n)), np.zeros((n, n))
    for e in range(elements):
        idx = slice(2 * e, 2 * e + 4)
        K[idx, idx] += k + g
        M[idx, idx] += m
    free = np.arange(2, n - 2)  # both ends clamped
    omega2 = np.linalg.eigvals(np.linalg.solve(M[np.ix_(free, free)], K[np.ix_(free, free)]))
    return float(np.sqrt(np.min(omega2.real))) / (2 * np.pi)


@over("calculix")
def test_tension_raises_the_first_frequency(tmp_path, solver):
    step = write_bar_step(tmp_path / "bar.step")
    solid = Physics(
        type="solid_mechanics",
        features=[
            Feature(type="fixed", selection="Bar/Root"),
            Feature(
                type="displacement",
                selection="Bar/Tip",
                values={
                    "x": Quantity(value=STRETCH, unit="m"),
                    "y": Quantity(value=0, unit="m"),
                    "z": Quantity(value=0, unit="m"),
                },
            ),
        ],
    )
    project, comp = bar_project(step, [solid])
    modal = SolveStep(type="modal", settings={"modes": 3})
    static = project.add(
        Study(
            name="Stretch", component=comp.id, solver=solver, steps=[SolveStep(type="stationary")]
        )
    )
    plain = project.add(Study(name="Modes", component=comp.id, solver=solver, steps=[modal]))
    tense = project.add(
        Study(name="Prestressed modes", component=comp.id, solver=solver, steps=[modal])
    )
    project.add(
        Transfer(
            name="Prestress", source=static.id, field="stress", target=tense.id, role="prestress"
        )
    )

    f0 = run_study(project, plain.id, tmp_path, tmp_path / "plain").results.modes()[0].value
    run = run_study(project, tense.id, tmp_path, tmp_path / "tense")
    modes = run.results.modes()
    assert len(modes) == 3 and all(m.index == 1 for m in modes)  # the replayed step is hidden
    ratio = modes[0].value / f0

    tension = E * B * H * STRETCH / L
    expected = _beam_frequency(tension) / _beam_frequency(0.0)
    print(
        f"{solver}: f0 {f0:.2f} Hz, prestressed {modes[0].value:.2f} Hz, ratio {ratio:.4f} "
        f"(beam {expected:.4f})"
    )
    assert expected > 1.05  # the tension matters
    assert ratio == pytest.approx(expected, rel=5e-3)
