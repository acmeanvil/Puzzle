"""Plasticity (ADR 0020): a bar pulled 1 % past yield, on CalculiX and Code_Aster,
against the closed form of bilinear isotropic hardening (see plastic_bar.py).

Checked at every node, at the last increment: the axial stress, the equivalent plastic
strain, and the directional plastic strains (ep_xx = ep, ep_yy = ep_zz = -ep/2, no
shear); and that each increment is kept, so a stress-strain history can be drawn.
"""

import numpy as np
import pytest
from plastic_bar import EP, STRESS, plastic_bar
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
