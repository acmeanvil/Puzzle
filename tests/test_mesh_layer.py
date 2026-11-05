"""A 2D model's mesh one cell thick for the finite-volume solvers (ADR 0048 round 5):
every cell the right way out, the volume the swept area's, groups carried over, the
axis left without faces, and results mapped back to the 2D elements."""

import math

import numpy as np
import pytest
from scipy.spatial import ConvexHull

from puzzle.mesh import layer
from puzzle.mesh.generate import generate_mesh
from puzzle.results.model import Field, Results, StepResult

pytest.importorskip("OCP")

H, L, R = 0.01, 0.05, 0.005


def cfd_2d(
    dimension: str,
    shape: str = "triangles",
    size_mm: float = 1.0,
    *,
    length: float = L,
    fluid: str = "water",
    speed: float = 0.001,
):
    """A channel (planar) or a pipe (axisymmetric, X the radius) as a 2D fluid region:
    Inlet, Outlet, Walls, and the Axis of a pipe."""
    from puzzle.materials import add_library_material
    from puzzle.model import (
        Component,
        Feature,
        Geometry,
        MaterialAssignment,
        MeshRecipe,
        NamedSelection,
        PartInstance,
        Physics,
        Project,
        Quantity,
    )
    from puzzle.model.schema import HistoryStep

    axi = dimension == "2d_axisymmetric"
    w, h = (R, length) if axi else (length, H)
    rect = [[0, 0], [w, 0], [w, h], [0, h]]
    step = HistoryStep(kind="face", name="S", args={"profile": {"points": rect}})
    # side-1 bottom (y = 0), side-2 right, side-3 top, side-4 left (x = 0)
    edges = (
        {"Inlet": ["S/side-1"], "Outlet": ["S/side-3"], "Walls": ["S/side-2"],
         "Axis": ["S/side-4"]}
        if axi
        else {"Inlet": ["S/side-4"], "Outlet": ["S/side-2"], "Walls": ["S/side-1", "S/side-3"]}
    )  # fmt: skip
    p = Project(name="cfd 2d")
    sel = [NamedSelection(name="Fluid", entity="face", members=["S/face"])]
    sel += [NamedSelection(name=n, entity="edge", members=m) for n, m in edges.items()]
    geo = p.add(Geometry(name="Duct", history=[step], selections=sel))
    water = add_library_material(p, fluid)
    mesh = p.add(
        MeshRecipe(name="Mesh", global_size=Quantity(value=size_mm, unit="mm"),
                   element_order=1,
                   element_shape="hexahedra" if shape == "quadrilaterals" else "tetrahedra")
    )  # fmt: skip
    features = [
        Feature(type="velocity_inlet", selection="Duct/Inlet",
                values={"U": Quantity(value=speed, unit="m/s")}),
        Feature(type="pressure_outlet", selection="Duct/Outlet",
                values={"p": Quantity(value=0, unit="Pa")}),
        Feature(type="wall", selection="Duct/Walls"),
    ]  # fmt: skip
    if axi:
        features.append(Feature(type="symmetry", selection="Duct/Axis"))
    comp = p.add(
        Component(name="Duct", dimension=dimension, mesh=mesh.id,
                  parts=[PartInstance(name="Duct", geometry=geo.id)],
                  materials=[MaterialAssignment(material=water.id, selection="Duct/Fluid")],
                  physics=[Physics(type="fluid_flow", features=features)])
    )  # fmt: skip
    return p, comp


def _cells(mesh):
    return [b for b in mesh.blocks if b.dim == 3]


def _check_cells(mesh) -> float:
    total = 0.0
    for b in _cells(mesh):
        for row in b.connectivity:
            p = mesh.node_coords(row)
            assert layer._volume(b.element, p) > 0, b.element
            total += ConvexHull(p).volume
    return total


def _area(mesh2d) -> tuple[float, float]:
    """The regions' area and its first moment about the axis (∫ x dA)."""
    area = moment = 0.0
    for b in mesh2d.region_blocks():
        for row in b.connectivity[:, : 3 if b.element == "tri3" else 4]:
            p = mesh2d.node_coords(row)[:, :2]
            x, y = p[:, 0], p[:, 1]
            cross = x * np.roll(y, -1) - np.roll(x, -1) * y
            a = 0.5 * abs(cross.sum())
            area += a
            moment += abs(((x + np.roll(x, -1)) * cross).sum()) / 6
    return area, moment


@pytest.mark.parametrize("shape", ["triangles", "quadrilaterals"])
def test_planar_layer(tmp_path, shape):
    p, comp = cfd_2d("2d_planar", shape)
    mesh2d = generate_mesh(p, comp.id, tmp_path, tmp_path / "m").mesh
    assert mesh2d.planar
    lay = layer.extrude(mesh2d)
    m = lay.mesh
    t = float(m.coords[:, 2].max() - m.coords[:, 2].min())
    area, _ = _area(mesh2d)
    assert _check_cells(m) == pytest.approx(area * t, rel=1e-9)
    n2d = sum(len(b.ids) for b in mesh2d.region_blocks())
    assert sum(len(b.ids) for b in _cells(m)) == n2d == len(lay.to_2d)
    assert m.groups["Duct/Fluid"].dim == 3 and m.groups["Duct/Inlet"].dim == 2
    for side in (layer.FRONT, layer.BACK):
        faces = sum(len(b.ids) for b in m.groups[side].blocks(m))
        assert faces == n2d
    assert lay.diameters["Duct/Inlet"] == pytest.approx(2 * H)
    assert not lay.on_axis


@pytest.mark.parametrize("shape", ["triangles", "quadrilaterals"])
def test_axisymmetric_wedge(tmp_path, shape):
    p, comp = cfd_2d("2d_axisymmetric", shape)
    mesh2d = generate_mesh(p, comp.id, tmp_path, tmp_path / "m").mesh
    lay = layer.extrude(mesh2d)
    m = lay.mesh
    _, moment = _area(mesh2d)
    theta = math.radians(layer.WEDGE_DEG)
    # Each cell is a flat-sided wedge: its volume is ∫ 2 r sin(θ/2) cos(θ/2) dA.
    assert _check_cells(m) == pytest.approx(moment * math.sin(theta), rel=1e-6)
    assert lay.on_axis == {"Duct/Axis"} and "Duct/Axis" not in m.groups
    kinds = {b.element for b in _cells(m)}
    assert kinds & ({"pyramid5", "tet4"} if shape == "triangles" else {"wedge6"})
    assert lay.diameters["Duct/Inlet"] == pytest.approx(2 * R)
    assert set(lay.to_2d.values()) == {int(i) for b in mesh2d.region_blocks() for i in b.ids}
    # The case writer's Gmsh export takes it.
    from puzzle.mesh.med import write_med

    tags = write_med(m, tmp_path / "w.msh", {g: g for g in m.groups}, msh_version=2.2)
    assert {layer.FRONT, layer.BACK, "Duct/Fluid"} <= set(tags)


def test_results_come_back_to_the_2d_elements(tmp_path):
    p, comp = cfd_2d("2d_axisymmetric", "quadrilaterals")
    mesh2d = generate_mesh(p, comp.id, tmp_path, tmp_path / "m").mesh
    lay = layer.extrude(mesh2d)
    lay.save(tmp_path)
    ids3d = np.array(sorted(lay.to_2d))
    values = np.array([[float(lay.to_2d[i]), 1.0, 0.0] for i in ids3d])
    step = StepResult(1, "stationary", 1.0)
    step.fields["velocity"] = Field("velocity", "element", ["x", "y", "z"], ids3d, values)
    back = layer.to_2d(tmp_path, Results(solver="x", steps=[step])).steps[0].fields["velocity"]
    assert np.array_equal(back.values[:, 0], back.ids.astype(float))
    assert len(back.ids) == sum(len(b.ids) for b in mesh2d.region_blocks())


def test_cut_quadrilaterals_on_the_axis():
    """A quadrilateral with one corner on the axis is cut into two pyramids."""
    on = {1}
    pieces = layer._pieces([1, 2, 3, 4], lambda n: n in on)
    assert pieces == [[1, 2, 3], [1, 3, 4]]
    back = {n: n if n in on else n + 10 for n in range(1, 5)}
    assert [layer._cell(q, lambda n: n in on, back)[0] for q in pieces] == ["pyramid5"] * 2
