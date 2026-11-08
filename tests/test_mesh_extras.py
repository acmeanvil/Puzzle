"""The rest of ADR 0019: Gmsh's option list, the element-types setting, edge seeds and
method detection. The mesh shoot-out is in tests/integration/test_mesh_shootout.py.
"""

import os

import numpy as np
import pytest

pytest.importorskip("OCP")

from puzzle.mesh.generate import generate_mesh, preferred_order  # noqa: E402
from puzzle.mesh.gmsh_options import mesh_options, search  # noqa: E402
from puzzle.model.schema import (  # noqa: E402
    Component,
    Geometry,
    HistoryStep,
    MeshRecipe,
    NamedSelection,
    PartInstance,
    Project,
    Quantity,
    SolveStep,
    Study,
    TransfiniteControl,
)


def _part(history, controls=()):
    p = Project(name="p")
    geo = p.add(
        Geometry(
            name="G",
            history=history,
            selections=[NamedSelection(name="Edge", entity="edge", members=["B/+x|B/+y"])],
        )
    )
    mesh = p.add(
        MeshRecipe(
            name="M",
            global_size=Quantity(value=4, unit="mm"),
            element_order=1,
            controls=list(controls),
        )
    )
    comp = p.add(Component(name="C", parts=[PartInstance(name="K", geometry=geo.id)], mesh=mesh.id))
    return p, comp


BOX = [HistoryStep(kind="box", name="B", args={"dx": 0.04, "dy": 0.02, "dz": 0.01})]


def test_gmsh_option_list():
    options = {o.name: o for o in mesh_options()}
    if not options:
        pytest.skip("no gmsh program on PATH")
    assert options["Mesh.Algorithm"].default == 6
    assert "2D mesh algorithm" in options["Mesh.Algorithm"].description
    assert any(o.name == "Mesh.HighOrderOptimize" for o in search("high order optim"))


def test_gmsh_options_dialog():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from puzzle.gui.mesh_bench import GmshOptionsDialog

    _app = QApplication.instance() or QApplication([])
    if not mesh_options():
        pytest.skip("no gmsh program on PATH")
    dialog = GmshOptionsDialog({"Mesh.Smoothing": 3})
    dialog.set_value("Mesh.AnisoMax", "1e30")
    assert dialog.chosen() == {"Mesh.Smoothing": 3.0, "Mesh.AnisoMax": 1e30}
    dialog.set_value("Mesh.Smoothing", "")
    dialog.set_value("Mesh.AnisoMax", "lots")
    with pytest.raises(ValueError, match="takes a number"):
        dialog.chosen()
    dialog.search.setText("smoothing")
    shown = [o.name for r, o in enumerate(dialog.options) if not dialog.table.isRowHidden(r)]
    assert "Mesh.Smoothing" in shown and "Mesh.Algorithm" not in shown


def test_element_types_setting():
    from puzzle import settings

    p, comp = _part(BOX)
    s = settings.current()
    assert preferred_order(p) == 2  # no studies yet
    p.add(
        Study(
            name="Flow", component=comp.id, solver="openfoam", steps=[SolveStep(type="stationary")]
        )
    )
    assert preferred_order(p) == 1  # finite volumes only
    s.set("meshing.element_types", "neutral")
    assert preferred_order(p) == 2


def test_edge_seeds(tmp_path):
    p, comp = _part(BOX, [TransfiniteControl(selection="K/Edge", count=7)])
    result = generate_mesh(p, comp.id, tmp_path, tmp_path / "m", seeds=True)
    seeds = result.seeds
    on_edge = np.isclose(seeds[:, 0], 0.04) & np.isclose(seeds[:, 1], 0.02)
    assert on_edge.sum() == 8  # 7 elements: 8 nodes, its ends included


def test_method_detection(tmp_path):
    p, comp = _part(BOX)
    result = generate_mesh(p, comp.id, tmp_path, tmp_path / "a", preview=True)
    ((method, why),) = result.methods.values()
    assert method == "hexahedra" and "six four-sided faces" in why
    holed = [*BOX, HistoryStep(kind="hole", name="H", args={"r": 0.003, "x": 0.02, "y": 0.01})]
    p, comp = _part(holed)
    result = generate_mesh(p, comp.id, tmp_path, tmp_path / "b", preview=True)
    ((method, why),) = result.methods.values()
    assert method == "tetrahedra" and "faces" in why


def test_folded_curved_elements_become_straight_sided(tmp_path, monkeypatch):
    """A curved second-order element the optimiser can't untangle (a hole's wall on Apple
    Silicon's Gmsh) gives straight sides there and a warning, not no mesh: every
    midside node then sits halfway along its edge."""
    from puzzle.mesh import generate

    p, comp = _part([HistoryStep(kind="cylinder", name="S", args={"r": 0.01, "h": 0.02})])
    p.objects[comp.mesh].element_order = 2
    real, calls = generate._min_quality, []

    def folded_first(dim=3):
        calls.append(dim)
        return -0.2 if len(calls) == 1 else real(dim)

    def midsides_halfway(result):
        block = next(b for b in result.mesh.blocks if b.connectivity.shape[1] == 10)
        xyz = result.mesh.node_coords(block.connectivity.ravel()).reshape(-1, 10, 3)
        corners, middles = xyz[:, :4], xyz[:, 4:]
        pairs = [(0, 1), (1, 2), (2, 0), (0, 3), (2, 3), (1, 3)]  # Gmsh's tet10 edges
        halfway = np.stack([(corners[:, a] + corners[:, b]) / 2 for a, b in pairs], axis=1)
        return np.allclose(middles, halfway, atol=1e-9)

    assert not midsides_halfway(generate_mesh(p, comp.id, tmp_path, tmp_path / "curved"))
    monkeypatch.setattr(generate, "_min_quality", folded_first)
    result = generate_mesh(p, comp.id, tmp_path, tmp_path / "m")
    assert any("sides are straight" in w for w in result.warnings)
    assert midsides_halfway(result)
