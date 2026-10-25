"""Mesh controls and settings (ADR 0019) on a 40 × 20 × 10 mm block.

Each control is checked by what it does to the mesh: a size control and refinement near
a face make elements there smaller; a box or a ball makes them smaller inside it; a
transfinite edge gets exactly its count of elements. Conflicting sizes, an unknown Gmsh
option, boundary layers on Gmsh and a quality floor the mesh misses are refused with a
reason. The quality report has its measures and the worst elements.
"""

import numpy as np
import pytest

pytest.importorskip("OCP")

from puzzle.mesh.generate import MeshError, generate_mesh  # noqa: E402
from puzzle.model.schema import (  # noqa: E402
    BallControl,
    BoundaryLayerControl,
    BoxControl,
    Component,
    Geometry,
    HistoryStep,
    MeshRecipe,
    NamedSelection,
    PartInstance,
    Project,
    Quantity,
    RefineNearControl,
    SizeControl,
    TransfiniteControl,
)

mm = lambda v: Quantity(value=v, unit="mm")  # noqa: E731


def _block(tmp_path, controls=(), **recipe):
    p = Project(name="block")
    geo = p.add(
        Geometry(
            name="Block",
            history=[HistoryStep(kind="box", name="B", args={"dx": 0.04, "dy": 0.02, "dz": 0.01})],
            selections=[
                NamedSelection(name="Body", entity="body", members=["Body"]),
                NamedSelection(name="End", entity="face", members=["B/+x"]),
                NamedSelection(name="Side", entity="face", members=["B/-y"]),
                NamedSelection(name="Edge", entity="edge", members=["B/+x|B/+y"]),
            ],
        )
    )
    mesh = p.add(
        MeshRecipe(
            name="M", global_size=mm(4), controls=list(controls), **{"element_order": 1, **recipe}
        )
    )
    comp = p.add(Component(name="C", parts=[PartInstance(name="K", geometry=geo.id)], mesh=mesh.id))
    return generate_mesh(p, comp.id, tmp_path, tmp_path / "m")


def _edge_lengths_near(result, x: float) -> float:
    """Mean edge length of the tets with a corner within 1 mm of the plane at x."""
    mesh = result.mesh
    order = np.argsort(mesh.node_ids)
    tets = np.concatenate([b.connectivity[:, :4] for b in mesh.blocks if b.dim == 3])
    xyz = mesh.coords[order[np.searchsorted(mesh.node_ids, tets.ravel(), sorter=order)]]
    xyz = xyz.reshape(-1, 4, 3)
    near = (np.abs(xyz[:, :, 0] - x) < 1e-3).any(axis=1)
    edges = np.linalg.norm(xyz[near, 1:] - xyz[near, :1], axis=2)
    return float(edges.mean())


def test_size_and_refinement_make_elements_smaller_there(tmp_path):
    plain = _edge_lengths_near(_block(tmp_path / "a"), 0.04)
    sized = _edge_lengths_near(
        _block(tmp_path / "b", [SizeControl(selection="K/End", size=mm(1))]), 0.04
    )
    near = RefineNearControl(selection="K/End", size=mm(1), near=mm(2), far=mm(10))
    refined = _block(tmp_path / "c", [near])
    assert sized < 0.5 * plain
    assert _edge_lengths_near(refined, 0.04) < 0.5 * plain
    assert _edge_lengths_near(refined, 0.0) > 0.7 * plain  # far from it: as before


def test_box_and_ball_regions(tmp_path):
    plain = _edge_lengths_near(_block(tmp_path / "a"), 0.0)
    box = BoxControl(low=(-0.001, -0.001, -0.001), high=(0.005, 0.021, 0.011), size=mm(1))
    assert _edge_lengths_near(_block(tmp_path / "b", [box]), 0.0) < 0.5 * plain
    ball = BallControl(centre=(0.0, 0.01, 0.005), radius=mm(8), size=mm(1))
    assert _edge_lengths_near(_block(tmp_path / "c", [ball]), 0.0) < 0.6 * plain


def test_transfinite_edge_count(tmp_path):
    result = _block(tmp_path, [TransfiniteControl(selection="K/Edge", count=7)])
    (block,) = [
        b
        for b in result.mesh.blocks
        if b.dim == 1 and b.entity in result.mesh.groups["K/Edge"].entities
    ]
    assert len(block.ids) == 7


def test_refusals_say_why(tmp_path):
    a, b = SizeControl(selection="K/End", size=mm(1)), SizeControl(selection="K/End", size=mm(2))
    with pytest.raises(MeshError, match="different sizes"):
        _block(tmp_path / "a", [a, b])
    with pytest.raises(MeshError, match="no number option 'Mesh.NoSuchThing'"):
        _block(tmp_path / "b", gmsh_options={"Mesh.NoSuchThing": 1})
    layer = BoundaryLayerControl(selection="K/Side", first_height=mm(0.1))
    with pytest.raises(MeshError, match="Netgen"):
        _block(tmp_path / "c", [layer])
    with pytest.raises(MeshError, match="below the recipe's floor"):
        _block(tmp_path / "d", quality_floor=0.999)


def test_options_and_quality_report(tmp_path):
    result = _block(tmp_path, algorithm_3d="hxt", gmsh_options={"Mesh.AnisoMax": 1e30})
    q = result.quality
    assert q["elements"] == sum(len(b.ids) for b in result.mesh.blocks if b.dim == 3)
    sj = q["measures"]["scaled_jacobian"]
    assert 0 < sj["min"] <= sj["mean"] <= 1 and sum(sj["counts"]) == q["elements"]
    assert q["measures"]["aspect_ratio"]["min"] >= 1.0
    shape = q["measures"]["shape"]
    assert len(q["worst"]) == 10 and q["worst"][0]["shape"] == pytest.approx(shape["min"])
    assert (tmp_path / "m" / "quality.json").exists()


def test_agents_get_the_quality_report(tmp_path):
    pytest.importorskip("mcp")
    import asyncio
    import shutil
    from pathlib import Path

    from puzzle.agent.mcp import build_server

    root = tmp_path / "cantilever"
    example = Path(__file__).parents[1] / "examples" / "cantilever"
    shutil.copytree(example, root, ignore=shutil.ignore_patterns("results*", ".puzzle"))
    server = build_server(root, "test")
    result = asyncio.run(server.call_tool("mesh_quality", {"component": "Cantilever"}))
    report = result.structured_content.get("result", result.structured_content)
    assert report["elements"] > 0 and report["element_types"]
    assert set(report["measures"]) == {"shape", "scaled_jacobian", "gamma", "aspect_ratio"}
    assert len(report["worst"]) == 5


def _netgen():
    from puzzle.mesh.netgen_mesher import netgen_python

    if netgen_python() is None:
        pytest.skip("Netgen is not installed (pixi install -e netgen)")


def test_netgen_meshes_with_local_sizes(tmp_path):
    _netgen()
    plain = _block(tmp_path / "a", mesher="netgen")
    sized = _block(tmp_path / "b", [SizeControl(selection="K/End", size=mm(1))], mesher="netgen")
    assert _edge_lengths_near(sized, 0.04) < 0.5 * _edge_lengths_near(plain, 0.04)
    second = _block(tmp_path / "c", mesher="netgen", element_order=2)
    assert set(second.mesh.element_counts()) == {"line3", "tri6", "tet10"}
    assert len(second.mesh.groups["K/Edge"].node_ids) > 2  # edge groups find their nodes
    assert second.min_quality > 0


def test_netgen_boundary_layers(tmp_path):
    """Three layers off the -y side, 0.1 mm first, growing 1.5 times: prisms whose node
    planes sit at y = 0.1, 0.25 and 0.475 mm."""
    _netgen()
    layer = BoundaryLayerControl(selection="K/Side", first_height=mm(0.1), growth=1.5, layers=3)
    result = _block(tmp_path, [layer], mesher="netgen")
    assert result.mesh.element_counts().get("wedge6", 0) > 0
    y = result.mesh.coords[:, 1]
    for plane in (1e-4, 2.5e-4, 4.75e-4):
        assert np.isclose(y, plane, atol=1e-9).sum() > 10
    assert result.quality["measures"]["shape"]["min"] > 0


def test_netgen_refuses_what_it_cannot_do(tmp_path):
    _netgen()
    near = RefineNearControl(selection="K/End", size=mm(1), near=mm(2), far=mm(10))
    with pytest.raises(MeshError, match="needs Gmsh"):
        _block(tmp_path / "a", [near], mesher="netgen")
    with pytest.raises(MeshError, match="use Gmsh for hexahedra"):
        _block(tmp_path / "b", mesher="netgen", element_shape="hexahedra")
