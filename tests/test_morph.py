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


def volume(mesh, coords):
    vols, _ = _shape(mesh, coords)
    return float(np.abs(vols).sum())


def test_a_thicker_beam_by_morphing(beam):
    p, mesh = beam
    from puzzle.model.schema import ParameterSet

    p.of_type(ParameterSet)[0].values["dt"] = Quantity(value=2, unit="mm")
    study = next(iter(p.of_type(Study))).model_copy(update={"morph": Morph(
        handles=[MorphHandle(selection="B/Top", distance="dt", direction=(0, 0, 1))],
        fixed=["B/Bottom"])})  # fmt: skip
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
