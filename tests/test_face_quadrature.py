"""Face integration over quadrilaterals (`adapters.common._quadrature`).

A modelled bolt's revolved mesh (ADR 0077) has quadrilateral faces: hex8 and wedge6 on
a first-order mesh, hex20's quad8 on a second-order one. Contact results and remote
loads integrate over them, as over triangles. Each shape function is 1 at its own node
and 0 at the others; a quarter annulus (r 1 to 2) mapped through its nodes has the
annulus's area, 3π/4, to the order of the element: exactly the chords' area for quad4,
within 1.2 % for quad8 and quad9 (one element over a quarter turn).
"""

import math

import numpy as np
import pytest

from puzzle.adapters.common import _face_rule, _shape

NODES = {
    "quad4": [(-1, -1), (1, -1), (1, 1), (-1, 1)],
    "quad8": [(-1, -1), (1, -1), (1, 1), (-1, 1), (0, -1), (1, 0), (0, 1), (-1, 0)],
    "quad9": [(-1, -1), (1, -1), (1, 1), (-1, 1), (0, -1), (1, 0), (0, 1), (-1, 0), (0, 0)],
}


def _annulus(u, v):
    r, t = 1.5 + 0.5 * u, (v + 1) * math.pi / 4
    return np.array([r * math.cos(t), r * math.sin(t), 0.0])


@pytest.mark.parametrize("element", sorted(NODES))
def test_each_shape_function_is_one_at_its_node(element):
    points = NODES[element]
    for i, p in enumerate(points):
        assert _shape(element, *p)[0] == pytest.approx(np.eye(len(points))[i], abs=1e-12)


@pytest.mark.parametrize(("element", "area", "rel"), [
    ("quad4", 1.5, 1e-12),  # the chords: a trapezium 1 .. 2 at 0° and 90°
    ("quad8", 3 * math.pi / 4, 0.012),
    ("quad9", 3 * math.pi / 4, 0.012),
])  # fmt: skip
def test_a_quarter_annulus_has_its_area(element, area, rel):
    nodes = np.array([_annulus(*p) for p in NODES[element]])
    total = 0.0
    for xi, eta, w in _face_rule(element):
        _, dxi, deta = _shape(element, xi, eta)
        total += w * np.linalg.norm(np.cross(dxi @ nodes, deta @ nodes))
    assert total == pytest.approx(area, rel=rel)
