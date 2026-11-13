"""Plasticity (ADR 0020): a bar pulled 1 % past yield, on CalculiX and Code_Aster,
against the closed form of bilinear isotropic hardening (see plastic_bar.py).

Checked at every node, at the last increment: the axial stress, the equivalent plastic
strain, and the directional plastic strains (ep_xx = ep, ep_yy = ep_zz = -ep/2, no
shear); and that each increment is kept, so a stress-strain history can be drawn.
"""

import numpy as np
import pytest
from plastic_bar import EP, NU, STRESS, SY, E, H, plastic_bar
from solvers import over

from puzzle.solve import run_study


@over("calculix", "code_aster")
def test_a_bar_yields_as_bilinear_hardening_says(solver, tmp_path):
    p = plastic_bar(solver, increments=10)
    study = next(s for s in p.objects.values() if s.name == "Pull")
    results = run_study(p, study.id, tmp_path, tmp_path / "run", timeout_s=900).results
    assert [round(s.value, 6) for s in results.steps] == [round(0.1 * i, 6) for i in range(1, 11)]
    last = results.steps[-1].fields
    # CalculiX's .frd keeps 5 digits; Code_Aster's tables 11.
    tol = 2e-5 if solver == "calculix" else 1e-8
    assert last["stress"].values[:, 0] == pytest.approx(STRESS, rel=tol)
    assert last["plastic_strain_equivalent"].values[:, 0] == pytest.approx(EP, rel=tol)
    ep = last["plastic_strain"].values
    assert ep[:, 0] == pytest.approx(EP, rel=tol)
    assert ep[:, 1] == pytest.approx(-EP / 2, rel=tol) and ep[:, 2] == pytest.approx(
        -EP / 2, rel=tol
    )
    assert np.abs(ep[:, 3:]).max() < 1e-9
    # Before yield (0.1 of the pull: 0.1 % strain, 200 MPa) nothing is plastic.
    first = results.steps[0].fields
    assert np.abs(first["plastic_strain_equivalent"].values).max() < 1e-9
    assert first["stress"].values[:, 0] == pytest.approx(200e6, rel=tol)


@over("calculix", "code_aster")
def test_a_bar_drawn_to_large_strain(solver, tmp_path):
    """Large strain plasticity (ADR 0082 decision 6): the bar drawn to 1.3 of its length
    with geometric nonlinearity (CalculiX NLGEOM; Code_Aster GDEF_LOG on its mixed u-p
    3D_INCO_UPG, since plain quadratic tetrahedra stalled at 1.21). In log strain,
    ln 1.3 = s/E + ep with s = sy + H ep, the true stress: ep = 0.2611, s = 380.6 MPa,
    where small strain would give 399 MPa. The force is s times the current area,
    A0 exp(2 (-nu s/E - ep/2))."""
    stretch = 1.3
    p = plastic_bar(solver, increments=30, strain=stretch - 1, large=True)
    if solver == "code_aster":  # plain quadratic tetrahedra lock under plastic flow
        from puzzle.model.schema import Component, ElementChoice

        comp = next(o for o in p.objects.values() if isinstance(o, Component))
        comp.elements = [ElementChoice(selection="Bar/Solid", formulation="mixed_up")]
    study = next(s for s in p.objects.values() if s.name == "Pull")
    run = run_study(p, study.id, tmp_path, tmp_path / "run", timeout_s=1800)
    last = run.results.steps[-1].fields
    ep = (np.log(stretch) - SY / E) / (1 + H / E)
    stress = SY + H * ep
    area = 1e-4 * np.exp(2 * (-NU * stress / E - ep / 2))
    tip = run.mesh.groups["Bar/Tip"].node_ids
    force = float(last["reaction_force"].at(tip)[:, 0].sum())
    got = float(np.mean(last["stress"].values[:, 0]))
    equivalent = float(np.mean(last["plastic_strain_equivalent"].values[:, 0]))
    print(
        f"{solver} drawn to 1.3: stress {got / 1e6:.4f} MPa ({stress / 1e6:.4f}), ep "
        f"{equivalent:.5f} ({ep:.5f}), force {force:.2f} N ({stress * area:.2f})"
    )
    assert got == pytest.approx(stress, rel=5e-3)
    assert equivalent == pytest.approx(ep, rel=1e-2)
    assert force == pytest.approx(stress * area, rel=5e-3)
