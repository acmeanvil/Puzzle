"""Faces' parents and normals in the shared adapter code (ADR 0090 COMMON-1, COMMON-3),
on meshes small enough to check by hand."""

import numpy as np
import pytest

from puzzle.adapters.common import FaceLookup, InputError, support_nodes, whole_pairs
from puzzle.mesh.mesh import CellBlock, Group, Mesh

# Tet B: corners at x = 1 (a triangle) and its apex at x = 2, so its face at x = 1 looks
# toward -x; tet A shares that face, apex at x = 0. Tet C is far off at x = -10, which
# puts the model's centre at x < 1.
COORDS = {1: (1, 0, 0), 2: (1, 1, 0), 3: (1, 0, 1), 4: (2, 0, 0), 5: (0, 0, 0),
          6: (-10, 0, 0), 7: (-10, 1, 0), 8: (-10, 0, 1), 9: (-11, 0, 0)}  # fmt: skip


def _mesh(with_a: bool) -> Mesh:
    ids = np.array(sorted(COORDS))
    blocks = [CellBlock("tet4", 3, (3, 2), np.array([2]), np.array([[1, 2, 3, 4]])),
              CellBlock("tet4", 3, (3, 3), np.array([3]), np.array([[6, 7, 8, 9]])),
              CellBlock("tri3", 2, (2, 9), np.array([10]), np.array([[1, 2, 3]]))]  # fmt: skip
    if with_a:
        blocks.append(CellBlock("tet4", 3, (3, 1), np.array([1]), np.array([[1, 3, 2, 5]])))
    face = np.array([1, 2, 3])
    groups = {
        "B": Group("B", 3, [(3, 2)], np.array([1, 2, 3, 4])),
        "C": Group("C", 3, [(3, 3)], np.array([6, 7, 8, 9])),
        "B/Face": Group("B/Face", 2, [(2, 9)], face),
    }
    if with_a:
        groups["A"] = Group("A", 3, [(3, 1)], np.array([1, 2, 3, 5]))
        groups["A/Face"] = Group("A/Face", 2, [(2, 9)], face)
        groups["Face"] = Group("Face", 2, [(2, 9)], face)
    return Mesh(ids, np.array([COORDS[i] for i in ids], float), blocks, groups)


def test_support_normal_points_out_of_its_own_solid():
    """COMMON-1: the face at x = 1 bounds tet B (x >= 1), so its outward normal is -x;
    turned away from the model's centre (x < 1) it was +x, into the solid, and a
    compression-only support held in tension there."""
    mesh = _mesh(with_a=False)
    ids, areas, normals = support_nodes(mesh.groups["B/Face"], mesh)
    assert ids.tolist() == [1, 2, 3]
    assert areas.sum() == pytest.approx(0.5)
    assert normals == pytest.approx(np.tile([-1.0, 0, 0], (3, 1)))


def test_a_shared_face_is_loaded_on_its_own_part():
    """COMMON-3: the face shared by A's and B's tets belongs to whichever part the
    selection names (the last element written won before); a selection naming neither
    is refused, never guessed. Its normal follows: out of B is -x, out of A +x."""
    mesh = _mesh(with_a=True)
    faces = FaceLookup.build([b for b in mesh.blocks if b.dim == 3])
    surface = mesh.blocks[2]
    assert faces.of(surface, mesh.groups["B/Face"], mesh) == [(2, 1)]
    assert faces.of(surface, mesh.groups["A/Face"], mesh)[0][0] == 1
    with pytest.raises(InputError) as err:
        faces.of(surface, mesh.groups["Face"], mesh)
    assert err.value.code == "ambiguous"
    for name, x in (("B/Face", -1.0), ("A/Face", 1.0)):
        assert support_nodes(mesh.groups[name], mesh)[2][:, 0] == pytest.approx([x] * 3)


class _Step:
    def __init__(self, index, mode, value):
        self.index, self.mode, self.value, self.kind = index, mode, value, "modal"


def test_a_modal_count_never_splits_a_pair():
    """Asked for 4 modes of a round bar, the 4th (17,135.5 Hz) has a partner at
    17,138.8 Hz (+0.02 %): both are kept, and the margin's distinct 33,556 Hz goes."""
    from types import SimpleNamespace

    from puzzle.model import SolveStep

    values = [3404.0, 3404.6, 16041.2, 17135.5, 17138.8, 33556.0, 40174.5]
    results = SimpleNamespace(steps=[_Step(1, k + 1, f) for k, f in enumerate(values)])
    study = SimpleNamespace(
        solver="code_aster", solve_steps=lambda: [SolveStep(type="modal", settings={"modes": 4})]
    )
    whole_pairs(results, study)
    assert [s.value for s in results.steps] == values[:5]
    results = SimpleNamespace(steps=[_Step(1, k + 1, f) for k, f in enumerate(values)])
    study = SimpleNamespace(
        solver="calculix", solve_steps=lambda: [SolveStep(type="modal", settings={"modes": 3})]
    )
    whole_pairs(results, study)
    assert [s.value for s in results.steps] == values[:3]


def test_code_aster_results_follow_node_ids_with_gaps(tmp_path):
    """ASTER-3: Code_Aster numbers nodes 1..N as written and its tables print those
    numbers, which were taken as Puzzle's ids. Ids 1, 2, 5 (a gap) put node 5's values
    on node 3; the ids are now left beside the job and the reader maps them back."""
    from types import SimpleNamespace

    from puzzle.adapters.code_aster import _node_ids
    from puzzle.adapters.code_aster.comm import JOB, _node_numbers
    from puzzle.results.model import Field

    gap = SimpleNamespace(node_ids=np.array([1, 2, 5]))
    _node_numbers(gap, tmp_path)
    u = Field("displacement", "node", ["x", "y", "z"], np.array([1, 2, 3]), np.eye(3))
    results = SimpleNamespace(steps=[SimpleNamespace(fields={"displacement": u})])
    _node_ids(results, tmp_path / f"{JOB}.comm")
    got = results.steps[0].fields["displacement"]
    assert got.ids.tolist() == [1, 2, 5]
    assert got.at(np.array([5]))[0].tolist() == [0.0, 0.0, 1.0]
    _node_numbers(SimpleNamespace(node_ids=np.array([1, 2, 3])), tmp_path)  # 1..N: no map
    assert not list(tmp_path.glob("*.nodeids.json"))
    with pytest.raises(InputError):
        _node_numbers(SimpleNamespace(node_ids=np.array([1, 3, 2])), tmp_path)
