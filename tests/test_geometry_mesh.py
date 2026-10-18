from pathlib import Path

import gmsh
import numpy as np
import pytest

from puzzle.geometry import gmsh_session, import_step
from puzzle.mesh import MeshError, generate_mesh
from puzzle.model import (
    Component,
    Contact,
    Geometry,
    MeshRecipe,
    NamedSelection,
    PartInstance,
    Project,
    Quantity,
)


def _write_step(path: Path, build) -> Path:
    with gmsh_session("fixture"):
        build(gmsh.model.occ)
        gmsh.model.occ.synchronize()
        gmsh.write(str(path))
    return path


@pytest.fixture
def plate_step(tmp_path) -> Path:
    """A 100 x 20 x 10 mm plate with a 4 mm radius through-hole at x = 80 mm."""

    def build(occ):
        box = occ.addBox(0, 0, 0, 0.1, 0.02, 0.01)
        hole = occ.addCylinder(0.08, 0.01, 0, 0, 0, 0.01, 0.004)
        occ.cut([(3, box)], [(3, hole)])

    return _write_step(tmp_path / "plate.step", build)


@pytest.fixture
def block_step(tmp_path) -> Path:
    """A 20 x 20 x 10 mm block."""
    return _write_step(tmp_path / "block.step", lambda occ: occ.addBox(0, 0, 0, 0.02, 0.02, 0.01))


def _plate_project(plate_step: Path, **recipe) -> tuple[Project, Component]:
    model = import_step(plate_step)
    p = Project(name="plate")
    geo = p.add(
        Geometry(
            name="Plate",
            source=plate_step.name,
            selections=[
                NamedSelection(name="Fixed", entity="face", members=model.faces_on_plane("x", 0.0)),
                NamedSelection(name="Hole", entity="face", members=model.faces_of_kind("Cylinder")),
                NamedSelection(name="End", entity="face", members=model.faces_on_plane("x", 0.1)),
            ],
        )
    )
    mesh = p.add(MeshRecipe(name="Tets", global_size=Quantity(value=5, unit="mm"), **recipe))
    comp = p.add(
        Component(name="Plate", parts=[PartInstance(name="Plate", geometry=geo.id)], mesh=mesh.id)
    )
    return p, comp


def test_import_names_and_measures_entities(plate_step):
    model = import_step(plate_step)
    assert len(model.of_dim(3)) == 1
    assert len(model.of_dim(2)) == 7
    assert model.size == pytest.approx(
        np.sqrt(0.1**2 + 0.02**2 + 0.01**2), rel=1e-5
    )  # boxes carry OCC's 1e-7 m padding
    assert model.faces_on_plane("x", 0.0) == ["face-1"]
    (hole,) = model.faces_of_kind("Cylinder")
    assert model[hole].measure == pytest.approx(2 * np.pi * 0.004 * 0.01, rel=1e-6)


def test_import_is_repeatable(plate_step):
    assert import_step(plate_step).entities == import_step(plate_step).entities


def test_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        import_step(tmp_path / "nope.step")


def test_single_part_mesh_has_groups_in_metres(plate_step, tmp_path):
    project, comp = _plate_project(plate_step)
    result = generate_mesh(project, comp.id, plate_step.parent, tmp_path / "mesh")

    assert result.path.exists()
    assert result.warnings == []
    assert result.min_quality > 0.1
    assert "tet10" in result.mesh.element_counts()
    assert set(result.mesh.groups) == {"Plate", "Plate/Fixed", "Plate/Hole", "Plate/End"}
    assert result.mesh.coords[:, 0].max() == pytest.approx(0.1, rel=1e-9)

    fixed = result.mesh.node_coords(result.mesh.groups["Plate/Fixed"].node_ids)
    assert np.allclose(fixed[:, 0], 0.0)
    hole = result.mesh.node_coords(result.mesh.groups["Plate/Hole"].node_ids)
    radius = np.hypot(hole[:, 0] - 0.08, hole[:, 1] - 0.01)
    assert np.allclose(radius, 0.004, rtol=1e-3)


def test_first_order_and_local_refinement(plate_step, tmp_path):
    coarse, comp = _plate_project(plate_step, element_order=1)
    base = generate_mesh(coarse, comp.id, plate_step.parent, tmp_path / "a")
    assert "tet4" in base.mesh.element_counts()

    fine, comp = _plate_project(
        plate_step, element_order=1, local_sizes={"Plate/Hole": Quantity(value=0.5, unit="mm")}
    )
    refined = generate_mesh(fine, comp.id, plate_step.parent, tmp_path / "b")
    hole = lambda r: len(r.mesh.groups["Plate/Hole"].node_ids)  # noqa: E731
    assert hole(refined) > 3 * hole(base)

    # A body size fills the volume, not just the body's corners.
    filled, comp = _plate_project(
        plate_step, element_order=1, local_sizes={"Plate": Quantity(value=1, unit="mm")}
    )
    body = generate_mesh(filled, comp.id, plate_step.parent, tmp_path / "c")
    assert body.mesh.element_counts()["tet4"] > 5 * base.mesh.element_counts()["tet4"]


def test_placement_moves_the_part_and_keeps_selections(plate_step, tmp_path):
    project, comp = _plate_project(plate_step)
    comp.parts[0].translation = (1.0, 0.0, 0.0)
    comp.parts[0].rotation = (0.0, 0.0, 90.0)
    result = generate_mesh(project, comp.id, plate_step.parent, tmp_path / "m")
    assert result.warnings == []
    fixed = result.mesh.node_coords(result.mesh.groups["Plate/Fixed"].node_ids)
    # x = 0 face rotated 90 degrees about z lands on y = 0, then shifted to x = 1.
    assert np.allclose(fixed[:, 1], 0.0, atol=1e-12)
    assert np.allclose(fixed[:, 0].mean(), 1.0 - 0.01, atol=1e-9)


def _stack(plate_step: Path, block_step: Path, meshing: str) -> tuple[Project, Component]:
    p, comp = _plate_project(plate_step)
    block = import_step(block_step)
    geo = p.add(
        Geometry(
            name="Block",
            source=block_step.name,
            selections=[
                NamedSelection(name="Bottom", entity="face", members=block.faces_on_plane("z", 0.0))
            ],
        )
    )
    comp.parts.append(PartInstance(name="Block", geometry=geo.id, translation=(0.0, 0.0, 0.01)))
    comp.contacts.append(
        Contact(
            name="Glue", type="bonded", source="Block/Bottom", target="Plate/Fixed", meshing=meshing
        )
    )
    return p, comp


def test_conformal_bond_shares_nodes(plate_step, block_step, tmp_path):
    project, comp = _stack(plate_step, block_step, "conformal")
    mesh = generate_mesh(project, comp.id, plate_step.parent, tmp_path / "c").mesh
    shared = np.intersect1d(mesh.groups["Plate"].node_ids, mesh.groups["Block"].node_ids)
    assert len(shared) > 0
    assert np.allclose(mesh.node_coords(shared)[:, 2], 0.01)


def test_nonconformal_bond_keeps_parts_separate(plate_step, block_step, tmp_path):
    project, comp = _stack(plate_step, block_step, "nonconformal")
    mesh = generate_mesh(project, comp.id, plate_step.parent, tmp_path / "n").mesh
    assert len(np.intersect1d(mesh.groups["Plate"].node_ids, mesh.groups["Block"].node_ids)) == 0
    assert "Block/Bottom" in mesh.groups


def test_unknown_local_size_is_a_warning(plate_step, tmp_path):
    project, comp = _plate_project(
        plate_step, local_sizes={"Plate/Nowhere": Quantity(value=1, unit="mm")}
    )
    result = generate_mesh(project, comp.id, plate_step.parent, tmp_path / "w")
    assert result.warnings == ["local size on unknown selection 'Plate/Nowhere' ignored"]


def test_2d_is_not_implemented_yet(plate_step, tmp_path):
    project, comp = _plate_project(plate_step)
    comp.dimension = "2d_planar"
    with pytest.raises(MeshError, match="2d_planar"):
        generate_mesh(project, comp.id, plate_step.parent, tmp_path / "x")


def test_hexahedral_mesh_of_a_block(tmp_path):
    from conftest_geometry import bar_project, write_bar_step

    step = write_bar_step(tmp_path / "bar.step")
    project, comp = bar_project(
        step, [], mesh_size_mm=2.5, element_shape="hexahedra", element_order=1
    )
    mesh = generate_mesh(project, comp.id, tmp_path, tmp_path / "m").mesh
    counts = mesh.element_counts()
    assert set(e for e in counts if e in ("tet4", "tet10", "hex8")) == {"hex8"}
    tip = mesh.groups["Bar/Tip"]
    assert {b.element for b in tip.blocks(mesh)} == {"quad4"}
    assert np.allclose(mesh.node_coords(tip.node_ids)[:, 0], 0.2)


def test_second_order_hexahedra_are_refused(tmp_path):
    from conftest_geometry import bar_project, write_bar_step

    step = write_bar_step(tmp_path / "bar.step")
    project, comp = bar_project(step, [], element_shape="hexahedra", element_order=2)
    with pytest.raises(MeshError, match="second-order hexahedra"):
        generate_mesh(project, comp.id, tmp_path, tmp_path / "m")


def test_meshing_from_short_lived_threads(tmp_path):
    """Worker threads (MCP tools, GUI jobs) mesh, exit, and meshing still works."""
    import subprocess
    import sys
    import textwrap

    script = textwrap.dedent(f"""
        import sys, threading
        sys.path.insert(0, {str(Path(__file__).parent)!r})
        from pathlib import Path
        from conftest_geometry import bar_project, write_bar_step
        from puzzle.mesh import generate_mesh
        from puzzle.model import Physics

        def mesh(i):
            d = Path({str(tmp_path)!r}) / str(i)
            d.mkdir()
            project, comp = bar_project(write_bar_step(d / "bar.step"),
                                        [Physics(type="heat_transfer")])
            generate_mesh(project, comp.id, d, d / "m")

        for i in range(2):
            t = threading.Thread(target=mesh, args=(i,))
            t.start()
            t.join()
        mesh("main")
        print("ok")
    """)
    # In a subprocess: the failure mode is a segfault.
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), done.stderr[-2000:]


def test_identical_touching_parts_keep_their_own_faces(tmp_path):
    """Two copies of one box, end to end: each part's end face is its own, though the
    two faces have the same kind, area and centre."""
    with gmsh_session("half"):
        gmsh.model.occ.addBox(0, 0, 0, 0.1, 0.01, 0.01)
        gmsh.model.occ.synchronize()
        gmsh.write(str(tmp_path / "half.step"))
    model = import_step(tmp_path / "half.step")
    project = Project(name="halves")
    geo = project.add(
        Geometry(
            name="Half",
            source="half.step",
            selections=[
                NamedSelection(name="Body", entity="body", members=["body-1"]),
                NamedSelection(name="Start", entity="face", members=model.faces_on_plane("x", 0)),
                NamedSelection(name="End", entity="face", members=model.faces_on_plane("x", 0.1)),
            ],
        )
    )
    recipe = project.add(MeshRecipe(name="m", global_size=Quantity(value=3, unit="mm")))
    comp = project.add(
        Component(
            name="c",
            parts=[
                PartInstance(name="Left", geometry=geo.id),
                PartInstance(name="Right", geometry=geo.id, translation=(0.1, 0.0, 0.0)),
            ],
            mesh=recipe.id,
            contacts=[
                Contact(
                    name="j",
                    type="bonded",
                    source="Right/Start",
                    target="Left/End",
                    meshing="nonconformal",
                )
            ],
        )
    )
    mesh = generate_mesh(project, comp.id, tmp_path, tmp_path / "m").mesh
    left, right = (mesh.groups[g].node_ids for g in ("Left/End", "Right/Start"))
    assert len(np.intersect1d(left, right)) == 0
    assert np.allclose(mesh.node_coords(right)[:, 0], 0.1)
    assert np.allclose(mesh.node_coords(left)[:, 0], 0.1)
