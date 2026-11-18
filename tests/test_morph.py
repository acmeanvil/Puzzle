"""Mesh morphing (ADR 0065 phase 2) on the beam stand-in, a steel box 200 × 20 × t
(t = 6 mm), fixed at x = 0. Its top face morphed up by `dt` with the bottom held makes a
box 6 + dt thick: the mesh's volume L W (t + dt) by hand, every node's x and y kept
(they move only up), and no element inverted. Too far, and the morph is refused."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("OCP")

from puzzle.errors import PuzzleError  # noqa: E402
from puzzle.mesh.morph import _shape, morphed  # noqa: E402
from puzzle.model.schema import Morph, MorphHandle, NamedSelection, Quantity, Study  # noqa: E402


@pytest.fixture(scope="module")
def beam(tmp_path_factory):
    from optimise_beam import beam_project

    from puzzle.mesh import generate_mesh
    from puzzle.model.schema import Component, Geometry
    from puzzle.project import save_project

    p, _ = beam_project()
    geo = next(iter(p.of_type(Geometry)))
    geo.selections += [
        NamedSelection(name="Top", entity="face", rule={"plane": {"axis": "z", "at": 0.006}}),
        NamedSelection(name="Bottom", entity="face", rule={"plane": {"axis": "z", "at": 0.0}}),
    ]
    root = tmp_path_factory.mktemp("morph") / "beam"
    save_project(p, root)
    comp = next(iter(p.of_type(Component)))
    mesh = generate_mesh(p, comp.id, root, root / "mesh").mesh
    return p, mesh


# Apple Silicon's Gmsh meshes this beam with a flatter worst element, which the stretch
# took to 0.036, under the default floor of 0.05; the stretch itself is what's tested.
LOW_FLOOR = 0.02


def volume(mesh, coords):
    vols, _ = _shape(mesh, coords)
    return float(np.abs(vols).sum())


def test_a_thicker_beam_by_morphing(beam):
    p, mesh = beam
    from puzzle.model.schema import ParameterSet

    p.of_type(ParameterSet)[0].values["dt"] = Quantity(value=2, unit="mm")
    study = next(iter(p.of_type(Study))).model_copy(update={"morph": Morph(
        handles=[MorphHandle(selection="B/Top", distance="dt", direction=(0, 0, 1))],
        fixed=["B/Bottom"], quality_floor=LOW_FLOOR)})  # fmt: skip
    got = morphed(p, study, mesh)
    assert volume(mesh, got.coords) == pytest.approx(0.2 * 0.02 * 0.008, rel=1e-6)
    assert np.allclose(got.coords[:, :2], mesh.coords[:, :2])  # only z moves
    assert got.coords[:, 2].max() == pytest.approx(0.008)
    assert (got.node_ids == mesh.node_ids).all() and len(got.blocks) == len(mesh.blocks)
    normal = study.morph.model_copy(
        update={"handles": [MorphHandle(selection="B/Top", distance=0.001)]}
    )  # along its outward normal: +z
    got = morphed(p, study.model_copy(update={"morph": normal}), mesh)
    assert got.coords[:, 2].max() == pytest.approx(0.007)


def test_a_morph_too_far_is_refused(beam):
    p, mesh = beam
    study = next(iter(p.of_type(Study))).model_copy(update={"morph": Morph(
        handles=[MorphHandle(selection="B/Top", distance=-0.0059, direction=(0, 0, 1))],
        fixed=["B/Bottom"], quality_floor=0.2)})  # fmt: skip
    with pytest.raises(PuzzleError, match="quality falls|inverts"):
        morphed(p, study, mesh)
    with pytest.raises(PuzzleError, match="no parameter 'nope'"):
        morphed(p, study.model_copy(update={"morph": Morph(handles=[
            MorphHandle(selection="B/Top", distance="nope")])}), mesh)  # fmt: skip


def test_an_element_the_mesher_left_poor_is_not_the_morphs(beam, monkeypatch):
    """Below the floor already and kept about as it was: accepted; halved: refused."""
    from puzzle.mesh import morph as morph_module

    p, mesh = beam
    shape = morph_module._shape

    def with_a_sliver(m, coords, factor):
        vols, quals = shape(m, coords)
        quals = quals.copy()
        moved = coords is not mesh.coords
        quals[0] = 0.001 * (factor if moved else 1)  # the mesher's sliver, then the morph's
        return vols, quals

    study = next(iter(p.of_type(Study))).model_copy(update={"morph": Morph(
        handles=[MorphHandle(selection="B/Top", distance=0.001, direction=(0, 0, 1))],
        fixed=["B/Bottom"], quality_floor=LOW_FLOOR)})  # fmt: skip
    monkeypatch.setattr(morph_module, "_shape", lambda m, c: with_a_sliver(m, c, 0.9))
    morphed(p, study, mesh)  # 0.001 to 0.0009: the mesh's, not the morph's
    monkeypatch.setattr(morph_module, "_shape", lambda m, c: with_a_sliver(m, c, 0.4))
    with pytest.raises(PuzzleError, match=r"falls to 0\.000 \(it was 0\.001\)"):
        morphed(p, study, mesh)


def _plain(blocks, xyz, groups):
    from puzzle.mesh.mesh import CellBlock, Group, Mesh

    ids = np.arange(1, len(xyz) + 1)
    return Mesh(ids, np.asarray(xyz, float),
                [CellBlock(e, d, (d, 1), np.arange(1, len(c) + 1), np.asarray(c))
                 for e, d, c in blocks],
                {n: Group(n, 2, [], np.asarray(g)) for n, g in groups.items()})  # fmt: skip


def _pushed(mesh, distance, direction):
    from puzzle.model.schema import Project

    study = Study(name="S", component=__import__("uuid").uuid4(), solver="calculix", steps=[],
                  morph=Morph(handles=[MorphHandle(selection="B/Top", distance=distance,
                                                   direction=direction)],
                              fixed=["B/Bottom"]))  # fmt: skip
    return morphed(Project("p"), study, mesh)


def test_bricks_and_quadrilaterals_are_checked_too():
    """ADR-50: a column of two unit hex8 bricks, its top pushed down 2.5 past its base
    (the middle follows linearly to -0.25: both turned inside out), is refused; pushed
    down 0.5 it is two bricks of 0.75 by hand. Two quad4 shells, the far edge of the
    second pushed back past its near edge, are refused the same way."""
    square = [[0, 0], [1, 0], [1, 1], [0, 1]]
    xyz = [[x, y, z] for z in (0.0, 1.0, 2.0) for x, y in square]
    bricks = [("hex8", 3, [[0, 1, 2, 3, 4, 5, 6, 7], [4, 5, 6, 7, 8, 9, 10, 11]])]
    col = _plain([(e, d, np.asarray(c) + 1) for e, d, c in bricks], xyz,
                 {"B/Bottom": [1, 2, 3, 4], "B/Top": [9, 10, 11, 12]})  # fmt: skip
    with pytest.raises(PuzzleError, match="inverts elements"):
        _pushed(col, -2.5, (0, 0, 1))
    got = _pushed(col, -0.5, (0, 0, 1))
    assert np.sort(np.unique(got.coords[:, 2])) == pytest.approx([0.0, 0.75, 1.5])
    # a sheet folded along x = 1 (the RBF's linear part needs points off one plane)
    xyz = [[x, y, max(x - 1.0, 0.0)] for y in (0.0, 1.0) for x in (0.0, 1.0, 2.0)]
    quads = [("quad4", 2, [[1, 2, 5, 4], [2, 3, 6, 5]])]
    flat = _plain(quads, xyz, {"B/Bottom": [1, 2, 4], "B/Top": [3, 6]})
    with pytest.raises(PuzzleError, match="inverts elements"):
        _pushed(flat, -3.0, (1, 0, 0))
    assert _pushed(flat, 0.5, (1, 0, 0)).coords[:, 0].max() == pytest.approx(2.5)


def test_a_flat_morph_says_why_it_cant():
    """Every control point in one plane (a 2D model): a coded refusal, not numpy's."""
    xyz = [[x, y, 0.0] for y in (0.0, 1.0) for x in (0.0, 1.0, 2.0)]
    flat = _plain([("quad4", 2, [[1, 2, 5, 4], [2, 3, 6, 5]])], xyz,
                  {"B/Bottom": [1, 4], "B/Top": [3, 6]})  # fmt: skip
    with pytest.raises(PuzzleError, match="one plane") as err:
        _pushed(flat, 0.5, (1, 0, 0))
    assert err.value.code == "unsupported"
