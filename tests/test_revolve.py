"""2D results in the view (ADR 0048 round 4): an axisymmetric section drawn revolved
about Y (Settings > Results > Axisymmetric results), probes read on the section, the
pick bar's words for 2D models, and what a 2D cut's length stands for.

The section is a 1 x 1 m square at x = 1..2 m (two triangles and, beside it, a
quadrilateral at x = 2..3 m), its displacement purely radial (u_x = 1 mm).
"""

import math
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from puzzle.mesh.mesh import CellBlock, Group, Mesh  # noqa: E402
from puzzle.post.revolve import HEX, WEDGE, revolve, segments_for, to_section  # noqa: E402
from puzzle.results.model import Field, Results, StepResult  # noqa: E402


def _section(axisymmetric=True):
    xyz = np.array([[1, 0, 0], [2, 0, 0], [2, 1, 0], [1, 1, 0], [3, 0, 0], [3, 1, 0]], float)
    ids = np.arange(1, 7)
    tris = CellBlock("tri3", 2, (2, 1), np.array([1, 2]), np.array([[1, 2, 3], [1, 3, 4]]))
    quad = CellBlock("quad4", 2, (2, 2), np.array([3]), np.array([[2, 5, 6, 3]]))
    mesh = Mesh(ids, xyz, [tris, quad], {"Part": Group("Part", 2, [(2, 1), (2, 2)], ids)},
                planar=True, axisymmetric=axisymmetric)  # fmt: skip
    u = np.zeros((6, 3))
    u[:, 0] = 1e-3
    temperature = xyz[:, 0:1] * 100.0  # 100 K per metre of radius
    step = StepResult(1, "stationary", 1.0, fields={
        "displacement": Field("displacement", "node", ["x", "y", "z"], ids, u),
        "temperature": Field("temperature", "node", [""], ids, temperature),
    })  # fmt: skip
    return mesh, Results("calculix", [step])


def test_the_section_is_turned_into_wedges_and_hexahedra():
    from puzzle.post.vtk import to_grid

    mesh, results = _section()
    grid = to_grid(mesh, results.steps[0])
    assert grid.n_cells == 3  # 2D regions are drawn (ADR 0048), not dropped
    out = revolve(grid, 90.0, segments=2)
    assert out.n_points == 6 * 3
    kinds = np.asarray(out.celltypes)
    assert (kinds == WEDGE).sum() == 2 * 2 and (kinds == HEX).sum() == 2
    # At 90° the radial direction is -z: the outward displacement turns with it.
    last = slice(2 * 6, 3 * 6)
    assert np.asarray(out.points)[last][1] == pytest.approx([0, 0, -2], abs=1e-12)
    assert np.asarray(out.point_data["displacement"])[last][1] == pytest.approx(
        [0, 0, -1e-3], abs=1e-15)  # fmt: skip
    assert np.asarray(out.point_data["temperature"])[last][1] == pytest.approx(200.0)
    assert to_section((0.0, 0.5, -2.0)) == pytest.approx([2.0, 0.5, 0.0])
    assert segments_for(270.0) == 54 and segments_for(1.0) == 4


def _viewport():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from puzzle.gui.viewport import Viewport

    QApplication.instance() or QApplication([])
    return Viewport()


def _item(results, key):
    from puzzle.post.quantities import catalog

    fields = [(f.name, f.location, f.components) for f in results.steps[0].fields.values()]
    return next(i for i in catalog(fields) if i.key == key)


def test_the_view_draws_axisymmetric_results_revolved_and_probes_the_section():
    from puzzle.settings import current

    view = _viewport()
    mesh, results = _section()
    shown = view.show_result(mesh, results, "temperature", item=_item(results, "temperature:value"))
    assert shown["revolved"] == 270.0  # the default
    assert view.result_state["grid"].n_points == 6 * (54 + 1)
    assert shown["max_at"][0] == pytest.approx(3.0)  # extremes on the section (angle 0)
    # A probe where the view shows the part turned 90°: read at r = 1.5 on the section.
    probe = view.probe_undeformed((0.0, 0.5, -1.5))
    assert probe["value"] == pytest.approx(150.0, rel=1e-6)
    current().set("results.axisymmetric_view", "flat")
    item = _item(results, "temperature:value")
    assert view.show_result(mesh, results, "temperature", item=item)["revolved"] is None
    current().set("results.axisymmetric_view", "revolved")
    current().set("results.sweep_angle", 90.0)
    view.show_result(mesh, results, "temperature", item=_item(results, "temperature:value"))
    assert view.shown["revolved"] == 90.0
    flat, _ = _section(axisymmetric=False)  # a planar model stays flat
    assert view.show_result(flat, results, "temperature", item=item)["revolved"] is None


def test_the_pick_bar_speaks_of_regions_in_2d():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from puzzle.gui.window import MainWindow

    QApplication.instance() or QApplication([])
    w = MainWindow()
    try:
        bar = w.pick_bar
        words = lambda: [bar.filter.itemText(i) for i in range(bar.filter.count())]  # noqa: E731
        assert words() == ["Faces", "Edges", "Bodies", "Points"]
        w.viewport.set_pick_kind("body")
        bar.set_planar(True)
        assert words() == ["Regions", "Edges", "Points"]
        assert w.viewport.pick_kind == "face"  # no bodies in 2D: regions
        bar.update_picks([])
        assert "Click regions to pick" in bar.label.text()
        bar.set_planar(False)
        assert words() == ["Faces", "Edges", "Bodies", "Points"]
    finally:
        w.close()


def test_a_2d_cut_stands_for_the_ring_or_the_depth():
    """A cut across the section at y = 0.5 with σ_yy = 1 Pa: the axial force is
    ∫ 2 π r dr over r = 1..3 (8 π N), per metre in plane strain (2 N/m)."""
    from uuid import uuid4

    from puzzle.model.schema import Component, PartInstance
    from puzzle.post.probes import depth_of, section_force, totals_basis

    PARTS = [PartInstance(name="Part", geometry=uuid4())]  # noqa: N806
    mesh, results = _section()
    stress = np.zeros((6, 6))
    stress[:, 1] = 1.0
    step = results.steps[0]
    step.fields["stress"] = Field("stress", "node", ["xx", "yy", "zz", "xy", "yz", "zx"],
                                  mesh.node_ids, stress)  # fmt: skip
    axi = Component(name="C", parts=PARTS, mesh=uuid4(), dimension="2d_axisymmetric")
    found = section_force(mesh, step, (0, 0.5, 0), (0, 1, 0), depth_of(axi))
    assert found["components"]["y"] == pytest.approx(8 * math.pi, rel=1e-9)
    assert totals_basis(axi) == "for the full 360°"
    strain = Component(
        name="C", parts=PARTS, mesh=uuid4(), dimension="2d_planar", planar_model="plane_strain"
    )
    found = section_force(mesh, step, (0, 0.5, 0), (0, 1, 0), depth_of(strain))
    assert found["components"]["y"] == pytest.approx(2.0, rel=1e-9)
    assert totals_basis(strain) == "per metre of depth"
    solid = Component(name="C", parts=PARTS, mesh=uuid4())
    assert totals_basis(solid) == "" and depth_of(solid) is None
