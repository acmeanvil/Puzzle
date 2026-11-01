"""2D models (ADR 0048), the first round: faces in the XY plane meshed in 2D, their
sides named, the plane and radius checked, and loads spread over the right measure."""

import math

import numpy as np
import pytest

pytest.importorskip("OCP")

from puzzle.adapters.common import load_area, plane_thickness  # noqa: E402
from puzzle.mesh import MeshError, generate_mesh  # noqa: E402
from puzzle.model import (  # noqa: E402
    Component,
    Geometry,
    MeshRecipe,
    NamedSelection,
    PartInstance,
    Project,
    Quantity,
)
from puzzle.model.schema import HistoryStep, ShellSection  # noqa: E402

W, H = 0.04, 0.02  # a 40 x 20 mm rectangle, corner at (x0, 0)


def _plate(dimension="2d_planar", x0=0.0, z=0.0, shape="tetrahedra", order=2, **comp):
    rect = [[x0, 0.0], [x0 + W, 0.0], [x0 + W, H], [x0, H]]
    p = Project(name="plate")
    geo = p.add(Geometry(name="Plate", history=[
        HistoryStep(kind="face", name="Plate", args={"profile": {"points": rect}, "offset": z}),
    ], selections=[
        NamedSelection(name="Region", entity="face", members=["Plate/face"]),
        NamedSelection(name="Bottom", entity="edge", members=["Plate/side-1"]),
        NamedSelection(name="Right", entity="edge", members=["Plate/side-2"]),
    ]))  # fmt: skip
    mesh = p.add(MeshRecipe(name="Mesh", global_size=Quantity(value=4, unit="mm"),
                            element_shape=shape, element_order=order))  # fmt: skip
    c = p.add(Component(name="Plate", dimension=dimension, mesh=mesh.id,
                        parts=[PartInstance(name="Plate", geometry=geo.id)], **comp))  # fmt: skip
    return p, c


def test_a_face_meshes_in_2d_with_its_sides_named(tmp_path):
    p, c = _plate()
    result = generate_mesh(p, c.id, tmp_path, tmp_path / "m")
    mesh = result.mesh
    assert result.warnings == []
    assert set(mesh.element_counts()) >= {"tri6", "line3"}
    assert "tet10" not in mesh.element_counts()
    bottom = mesh.node_coords(mesh.groups["Plate/Bottom"].node_ids)
    assert np.allclose(bottom[:, 1], 0.0) and np.ptp(bottom[:, 0]) == pytest.approx(W)
    right = mesh.node_coords(mesh.groups["Plate/Right"].node_ids)
    assert np.allclose(right[:, 0], W)
    assert mesh.groups["Plate/Region"].dim == 2
    assert result.quality["elements"] == mesh.element_counts()["tri6"]


def test_quadrilaterals_are_eight_noded(tmp_path):
    p, c = _plate(shape="hexahedra")
    mesh = generate_mesh(p, c.id, tmp_path, tmp_path / "m").mesh
    assert "quad8" in mesh.element_counts() and "quad9" not in mesh.element_counts()


def test_faces_off_the_xy_plane_are_refused(tmp_path):
    p, c = _plate(z=0.01)
    with pytest.raises(MeshError, match="XY plane") as err:
        generate_mesh(p, c.id, tmp_path, tmp_path / "m")
    assert err.value.code == "not_planar"


def test_an_axisymmetric_face_must_not_cross_the_axis(tmp_path):
    p, c = _plate("2d_axisymmetric", x0=-0.01)
    with pytest.raises(MeshError, match="x >= 0") as err:
        generate_mesh(p, c.id, tmp_path, tmp_path / "m")
    assert err.value.code == "negative_radius"


def test_a_solid_part_is_refused_in_2d(tmp_path):
    p, c = _plate()
    geo = p.get(c.parts[0].geometry, Geometry)
    geo.history = [HistoryStep(kind="box", name="B", args={"dx": 0.01, "dy": 0.01, "dz": 0.01})]
    geo.selections = []
    with pytest.raises(MeshError, match="parts are faces in the XY plane"):
        generate_mesh(p, c.id, tmp_path, tmp_path / "m")


def test_a_force_spreads_over_length_times_thickness_or_the_revolution(tmp_path):
    t = Quantity(value=2, unit="mm")
    p, c = _plate(shell_sections=[ShellSection(selection="Plate", thickness=t)])
    mesh = generate_mesh(p, c.id, tmp_path, tmp_path / "m").mesh
    assert plane_thickness(c, "Plate") == pytest.approx(0.002)
    assert load_area(mesh.groups["Plate/Bottom"], mesh, c) == pytest.approx(W * 0.002)
    c.planar_model = "plane_strain"  # per metre of depth
    assert load_area(mesh.groups["Plate/Bottom"], mesh, c) == pytest.approx(W)
    # Axisymmetric, the rectangle at x = 10..50 mm: the bottom sweeps an annulus and the
    # right side a cylinder.
    p, c = _plate("2d_axisymmetric", x0=0.01)
    mesh = generate_mesh(p, c.id, tmp_path, tmp_path / "a").mesh
    annulus = math.pi * ((0.01 + W) ** 2 - 0.01**2)
    assert load_area(mesh.groups["Plate/Bottom"], mesh, c) == pytest.approx(annulus, rel=1e-9)
    cylinder = 2 * math.pi * (0.01 + W) * H
    assert load_area(mesh.groups["Plate/Right"], mesh, c) == pytest.approx(cylinder, rel=1e-9)


def test_plane_stress_needs_a_thickness():
    from puzzle.errors import PuzzleError

    _p, c = _plate()
    with pytest.raises(PuzzleError, match="needs its thickness"):
        plane_thickness(c, "Plate")


def test_the_default_plane_model_keeps_older_hashes():
    from puzzle.project.graph import content_hashes

    p, c = _plate()
    before = content_hashes(p)[c.id]
    c.planar_model = "plane_strain"
    assert content_hashes(p)[c.id] != before
    c.planar_model = "plane_stress"
    assert content_hashes(p)[c.id] == before
