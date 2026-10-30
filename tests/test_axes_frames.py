"""Coordinate systems (ADR 0049): their axes at points, by every definition, and results
read in them.

Each type's frames are right-handed and orthonormal; rotation angles turn about x,
then the turned y, then the turned z; three points place a system; vectors and tensors
rotate there and back. A spherical system's r, θ, φ at a point on its equator are the
global axes rearranged. The labels name a system's components (hoop stress)."""

import numpy as np
import pytest

from puzzle.model.schema import CoordinateSystem
from puzzle.post.axes import rotate_tensors, rotate_vectors
from puzzle.post.quantities import catalog

POINTS = np.array([[1.0, 0.2, 0.3], [-0.5, 2.0, -1.0], [0.1, -0.7, 0.4]])


@pytest.mark.parametrize("kind", ["cartesian", "cylindrical", "spherical"])
def test_frames_are_right_handed_and_orthonormal(kind):
    cs = CoordinateSystem(name="c", type=kind, origin=(0.2, -0.1, 0.5), axis=(0.3, 0.4, 1.0),
                          x_axis=(1.0, 0.5, 0.0), xy_direction=(0.0, 1.0, 0.2))  # fmt: skip
    for frame in cs.frames(POINTS):
        assert np.allclose(frame.T @ frame, np.eye(3), atol=1e-12)
        assert np.linalg.det(frame) == pytest.approx(1.0)
    v = np.random.default_rng(1).normal(size=(3, 3))
    assert np.allclose(cs.to_global(cs.to_local(v, POINTS)[0], POINTS[:1]), v[:1])


def test_rotation_angles_turn_about_the_turned_axes():
    turned = CoordinateSystem(name="z90", angles=(0.0, 0.0, 90.0)).orientation()
    assert np.allclose(turned[:, 0], (0, 1, 0)) and np.allclose(turned[:, 2], (0, 0, 1))
    # 90° about x puts y on global z; 90° about that turned y then puts x on global y.
    two = CoordinateSystem(name="xy", angles=(90.0, 90.0, 0.0)).orientation()
    assert np.allclose(two[:, 0], (0, 1, 0), atol=1e-12)
    assert np.allclose(two[:, 1], (0, 0, 1), atol=1e-12)


def test_three_points_place_a_system():
    cs = CoordinateSystem(name="p", points=[(1, 1, 0), (2, 1, 0), (1, 5, 0)])
    assert np.allclose(cs.centre(), (1, 1, 0))
    assert np.allclose(cs.orientation(), np.eye(3))
    tilted = CoordinateSystem(name="t", points=[(0, 0, 0), (0, 0, 1), (1, 0, 0)])
    assert np.allclose(tilted.orientation()[:, 0], (0, 0, 1))
    assert np.allclose(tilted.orientation()[:, 2], (0, 1, 0))


def test_spherical_axes_on_its_equator():
    cs = CoordinateSystem(name="s", type="spherical")  # axis along global z
    r, theta, phi = cs.frames([[2.0, 0.0, 0.0]])[0].T
    assert np.allclose(r, (1, 0, 0)) and np.allclose(phi, (0, 1, 0))
    assert np.allclose(theta, (0, 0, -1))  # the polar angle grows away from +z


def test_cylindrical_hoop_of_a_uniaxial_stress():
    # σyy = 5 at a point on +x: in a cylindrical system about z it is the hoop stress.
    cs = CoordinateSystem(name="c", type="cylindrical")
    frames = cs.frames([[1.0, 0.0, 0.0]])
    local = rotate_tensors(frames, np.array([[0.0, 5.0, 0.0, 0.0, 0.0, 0.0]]))
    assert local[0, 1] == pytest.approx(5.0) and abs(local[0, 0]) < 1e-12
    v = rotate_vectors(cs.frames([[0.0, 2.0, 0.0]]), np.array([[0.0, 1.0, 0.0]]))
    assert np.allclose(v, [[1.0, 0.0, 0.0]])  # along +y at a point on +y: radial


def test_labels_name_the_system_axes():
    fields = [("stress", "node", ["xx", "yy", "zz", "xy", "yz", "zx"]),
              ("displacement", "node", ["x", "y", "z"]),
              ("displacement_global", "node", ["x", "y", "z"])]  # fmt: skip
    labels = {i.key: i.label for i in catalog(fields, "cylindrical")}
    assert labels["stress:yy"] == "Normal stress θ (hoop)"
    assert labels["displacement:x"] == "Deformation r (radial)"
    assert not any(k.startswith("displacement_global") for k in labels)  # the warp's copy
    assert {i.key: i.label for i in catalog(fields, "spherical")}["stress:zz"] == (
        "Normal stress φ (hoop)"
    )
