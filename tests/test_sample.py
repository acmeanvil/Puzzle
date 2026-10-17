import numpy as np
import pytest

from puzzle.mesh.mesh import Mesh
from puzzle.post import component, sample
from puzzle.results import Field

COORDS = np.array([[0, 0, 0], [0, 0, 1], [1, 0, 0], [1, 0, 1], [2, 0, 0.5]], float)
MESH = Mesh(node_ids=np.array([10, 11, 12, 13, 14]), coords=COORDS)
FIELD = Field(
    "stress",
    "node",
    ["xx", "yy"],
    MESH.node_ids,
    np.array([[1, 10], [3, 30], [5, 50], [7, 70], [9, 90]], float),
)


def test_exact_node():
    s = sample(MESH, FIELD, (2, 0, 0.5))
    assert s.distance == 0 and list(s.node_ids) == [14] and list(s.values) == [9, 90]


def test_ignored_coordinate_averages_a_line():
    s = sample(MESH, FIELD, (1, 0, None))
    assert sorted(s.node_ids) == [12, 13]
    assert s.values[component(FIELD, "yy")] == pytest.approx(60)


def test_falls_back_to_the_nearest_node():
    s = sample(MESH, FIELD, (0.1, 0, 0))
    assert list(s.node_ids) == [10] and s.distance == pytest.approx(0.1)


def test_component_errors():
    with pytest.raises(KeyError, match="'zz'"):
        component(FIELD, "zz")
    with pytest.raises(ValueError):
        sample(MESH, FIELD, (None, None, None))
