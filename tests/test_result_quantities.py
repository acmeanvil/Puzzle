"""The results a run can show (post.quantities), against closed forms."""

import numpy as np
import pytest

from puzzle.post.quantities import catalog, compute, default

STRESS = ["xx", "yy", "zz", "xy", "yz", "zx"]


def _item(fields, key):
    return next(i for i in catalog(fields) if i.key == key)


def test_the_catalog_lists_components_and_derived_results():
    fields = [
        ("displacement", "node", ["x", "y", "z"]),
        ("stress", "node", STRESS),
        ("temperature", "node", [""]),
        ("stored_energy", "global", [""]),
    ]
    items = catalog(fields)
    labels = [i.label for i in items]
    assert labels[:4] == ["Total deformation", "Deformation X", "Deformation Y", "Deformation Z"]
    assert "Equivalent (von Mises) stress" in labels and "Maximum shear stress" in labels
    assert "Temperature" in labels
    assert not any(i.field == "stored_energy" for i in items)  # not drawn on the mesh
    assert default(items).key == "displacement:total"
    assert _item(fields, "stress:xy").label == "Shear stress XY"


def test_stress_results_match_closed_forms():
    fields = [("stress", "node", STRESS)]
    # Pure shear tau in xy: principal +tau, 0, -tau; von Mises sqrt(3) tau; max shear tau.
    tau = 50e6
    s = np.array([[0, 0, 0, tau, 0, 0]], float)
    assert compute(_item(fields, "stress:p1"), s, STRESS) == pytest.approx([tau])
    assert compute(_item(fields, "stress:p3"), s, STRESS) == pytest.approx([-tau])
    assert compute(_item(fields, "stress:von_mises"), s, STRESS) == pytest.approx([3**0.5 * tau])
    assert compute(_item(fields, "stress:max_shear"), s, STRESS) == pytest.approx([tau])
    # Uniaxial: von Mises is the stress itself.
    u = np.array([[200e6, 0, 0, 0, 0, 0]], float)
    assert compute(_item(fields, "stress:von_mises"), u, STRESS) == pytest.approx([200e6])


def test_equivalent_strain_of_uniaxial_incompressible_strain():
    """e = diag(e, -e/2, -e/2): the equivalent strain is e."""
    fields = [("strain", "node", STRESS)]
    e = 1e-3
    v = np.array([[e, -e / 2, -e / 2, 0, 0, 0]], float)
    assert compute(_item(fields, "strain:equivalent"), v, STRESS) == pytest.approx([e])


def test_vectors_and_nan_rows():
    fields = [("displacement", "node", ["x", "y", "z"])]
    v = np.array([[3.0, 4.0, 0.0], [np.nan, np.nan, np.nan]])
    total = compute(_item(fields, "displacement:total"), v, ["x", "y", "z"])
    assert total[0] == pytest.approx(5.0) and np.isnan(total[1])
    stress = [("stress", "node", STRESS)]
    p1 = compute(_item(stress, "stress:p1"), np.full((1, 6), np.nan), STRESS)
    assert np.isnan(p1[0])  # a node without values stays empty, not an error
