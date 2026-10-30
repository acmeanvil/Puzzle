"""ADR 0038 section cuts on placed planes: a face (picked, or a face selection) or a
construction plane, moved along its normal by an offset.

On a 200 x 10 x 10 mm bar: the Tip face's plane is x = L facing +x, a cut 50 mm in from
it sits at x = 150 mm; a round face, whose normals cancel, still gives a plane; the bar
lists X/Y/Z, the picked face, each face selection and each construction plane.
"""

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from conftest_geometry import BAR, bar_project, write_bar_step  # noqa: E402

from puzzle.mesh import generate_mesh  # noqa: E402
from puzzle.post.cuts import Cut, face_at, face_plane  # noqa: E402


@pytest.fixture
def bar_mesh(tmp_path):
    step = write_bar_step(tmp_path / "bar.step")
    project, comp = bar_project(step, [], mesh_size_mm=5.0)
    return project, generate_mesh(project, comp.id, tmp_path, tmp_path / "mesh").mesh


def test_face_plane_and_offset(bar_mesh):
    _, mesh = bar_mesh
    origin, normal = face_plane(mesh, mesh.groups["Bar/Tip"].entities)
    assert origin == pytest.approx([BAR[0], BAR[1] / 2, BAR[2] / 2], abs=1e-9)
    assert abs(normal[0]) == pytest.approx(1.0)
    cut = Cut(origin=tuple(origin), normal=tuple(-normal * np.sign(normal[0])), offset=0.05)
    at, n = cut.plane(None)
    assert at[0] == pytest.approx(BAR[0] - 0.05) and n[0] == pytest.approx(-1.0)
    at, n = Cut(origin=(0, 0, 0), normal=(0, 0, 2), offset=0.01, flip=True).plane(None)
    assert at == pytest.approx([0, 0, 0.01]) and n == pytest.approx([0, 0, -1])


def test_face_at_finds_the_tip(bar_mesh):
    _, mesh = bar_mesh
    tip = mesh.groups["Bar/Tip"].entities
    assert face_at(mesh, (BAR[0], 0.005, 0.005)) in [tuple(e) for e in tip]


def test_curved_face_plane_fits_its_points(tmp_path):
    import gmsh

    from puzzle.geometry import gmsh_session

    with gmsh_session("cyl"):
        gmsh.model.occ.addCylinder(0, 0, 0, 0, 0, 0.1, 0.02)
        gmsh.model.occ.synchronize()
        gmsh.option.setNumber("Mesh.MeshSizeMax", 0.01)
        gmsh.model.mesh.generate(2)
        from puzzle.mesh.mesh import CellBlock, Mesh

        tags, xyz, _ = gmsh.model.mesh.getNodes()
        side = [t for d, t in gmsh.model.getEntities(2)
                if gmsh.model.getType(2, t) == "Cylinder"][0]  # fmt: skip
        _, _, conn = gmsh.model.mesh.getElements(2, side)
        tri = np.asarray(conn[0], np.int64).reshape(-1, 3)
        mesh = Mesh(np.asarray(tags, np.int64), np.asarray(xyz).reshape(-1, 3),
                    [CellBlock("tri3", 2, (2, side), np.arange(len(tri)), tri)])  # fmt: skip
    origin, normal = face_plane(mesh, [(2, side)])
    assert np.isfinite(origin).all() and np.linalg.norm(normal) == pytest.approx(1.0)
    assert origin[2] == pytest.approx(0.05, abs=2e-3)  # half way up the side


def test_cut_bar_lists_faces_selections_and_planes(bar_mesh):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from puzzle.gui.viewport import CutBar, Viewport

    QApplication.instance() or QApplication([])
    _, mesh = bar_mesh
    view = Viewport()
    view.construction_planes = lambda: [("Mid", "p1", (0.1, 0, 0), (1, 0, 0))]
    view.show_mesh(mesh)
    bar = CutBar(view)
    names = [bar.axis.itemText(i) for i in range(bar.axis.count())]
    assert names[:4] == ["Across X", "Across Y", "Across Z", "Picked face"]
    assert "Face selection: Tip (Bar)" in names and "Construction plane: Mid" in names
    assert not any("Body" in n for n in names)  # a body selection is no plane

    seen = []
    view.set_cut = seen.append
    bar.axis.setCurrentIndex(names.index("Construction plane: Mid"))
    bar.offset.setValue(0.02)  # SI by default: metres
    origin, normal = seen[-1].plane(None)
    assert origin == pytest.approx([0.12, 0, 0]) and seen[-1].source == "plane"
    assert bar.position.isHidden() and not bar.offset.isHidden()

    bar.offset.setValue(0.0)
    bar.axis.setCurrentIndex(names.index("Face selection: Tip (Bar)"))
    assert seen[-1].plane(None)[0][0] == pytest.approx(BAR[0])

    bar.axis.setCurrentIndex(names.index("Picked face"))
    assert seen[-1] is None and not bar.pick.isHidden()  # nothing picked yet
    bar._face_at((BAR[0], 0.005, 0.005))  # the double-click lands on the tip
    assert seen[-1].plane(None)[0][0] == pytest.approx(BAR[0])

    bar.axis.setCurrentIndex(0)
    assert seen[-1].axis == "x" and seen[-1].origin is None and bar.offset.isHidden()


def test_saved_sections_recall_rename_delete_undo(bar_mesh, tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from puzzle.gui.window import MainWindow
    from puzzle.model.schema import SectionCut
    from puzzle.project import save_project

    app = QApplication.instance() or QApplication([])
    project, mesh = bar_mesh
    root = tmp_path / "proj"
    save_project(project, root)
    w = MainWindow(root)
    try:
        w.viewport.show_mesh(mesh)
        panel = w.sections
        assert w.sections_dock.windowTitle() == "Sections"
        assert panel.save() is None  # no cut yet: says so, saves nothing
        w.viewport.view_bar.toggle_cut_bar(True)
        bar = w.viewport.view_bar.cut_bar
        names = [bar.axis.itemText(i) for i in range(bar.axis.count())]
        bar.axis.setCurrentIndex(names.index("Face selection: Tip (Bar)"))
        bar.offset.setValue(0.05)
        tip = panel.save()
        assert (tip.name, tip.plane, tip.selection) == ("Section 1", "selection", "Bar/Tip")
        assert tip.offset == pytest.approx(0.05)
        bar.axis.setCurrentIndex(names.index("Across Y"))
        bar.position.setValue(30)
        across = panel.save("Quarter Y")
        assert (across.plane, across.fraction) == ("y", pytest.approx(0.3))
        assert [panel.list.item(i).text() for i in range(panel.list.count())] == [
            "Quarter Y", "Section 1"]  # fmt: skip
        assert panel.list.item(1).toolTip().startswith("along Tip (Bar), offset 0.05")

        panel.recall(panel.list.item(1))  # back to the tip, 50 mm along its normal
        assert bar.axis.currentText() == "Face selection: Tip (Bar)"
        origin, _ = w.viewport.cut.plane(None)
        assert abs(origin[0] - BAR[0]) == pytest.approx(0.05)
        panel.recall(panel.list.item(0))
        assert w.viewport.cut.axis == "y" and w.viewport.cut.fraction == pytest.approx(0.3)

        panel.list.item(1).setText("Tip cut")  # renamed in place: a command
        assert {c.name for c in w.session.project.of_type(SectionCut)} == {"Quarter Y", "Tip cut"}
        panel.list.setCurrentRow(0)
        panel.delete()
        assert {c.name for c in w.session.project.of_type(SectionCut)} == {"Tip cut"}
        w.session.undo()
        w.refresh()
        assert panel.list.count() == 2
    finally:
        w.close()
        app.processEvents()
