"""Modelled bolts' results and paths (ADR 0077), without a solve.

A bolt drawn as parts carries its loads in its stress field, not in the solvers'
`fastener` rows: `bolts.section_loads` integrates the traction over the bolt part's own
elements cut at its `Section` face (ADR 0042's section tool). On a 10 mm square bar of
hex8 elements with σzz = σ0 + k x (exact for trilinear elements) the axial force is
σ0 A and the bending moment k I, I = b h³ / 12, with a plate beside it at 1 GPa left out.
`bolt_results` keeps the `fastener` rows on the beam bolts when a modelled bolt comes
first, and gives the modelled one its threaded contact's per-thread loads. The fatigue
tool takes the bolt's thread Kf at its thread's nodes; the bolt table, the bolt type's
form and MCP reach it all.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import numpy as np
import pytest

from puzzle.mesh import Mesh
from puzzle.mesh.mesh import CellBlock, Group
from puzzle.model import Contact
from puzzle.model.schema import (
    BeamFastener,
    BoltType,
    Component,
    Geometry,
    HistoryStep,
    MaterialAssignment,
    MeshRecipe,
    PartInstance,
    Project,
    SolveStep,
    Study,
    Thread,
)
from puzzle.results.model import Field, Results, StepResult

MM = 1e-3
SIGMA0, K = 200e6, 2e9  # Pa, Pa/m
B = 10 * MM


def _box(x0, x1, nx, nz, start_node, start_elem, entity):
    """A box [x0, x1] x [-5, 5] mm x [0, 40] mm of hex8: (ids, coords, block, faces)."""
    xs, ys, zs = np.linspace(x0, x1, nx + 1), np.linspace(-B / 2, B / 2, nx + 1), np.linspace(
        0, 0.04, nz + 1)  # fmt: skip
    ids = {}
    coords = []
    for k, z in enumerate(zs):
        for j, y in enumerate(ys):
            for i, x in enumerate(xs):
                ids[i, j, k] = start_node + len(coords)
                coords.append((x, y, z))
    conn = []
    for k in range(nz):
        for j in range(nx):
            for i in range(nx):
                bottom = [ids[i, j, k], ids[i + 1, j, k], ids[i + 1, j + 1, k], ids[i, j + 1, k]]
                top = [ids[i, j, k + 1], ids[i + 1, j, k + 1], ids[i + 1, j + 1, k + 1],
                       ids[i, j + 1, k + 1]]  # fmt: skip
                conn.append(bottom + top)
    block = CellBlock("hex8", 3, (3, entity), np.arange(start_elem, start_elem + len(conn)),
                      np.array(conn, np.int64))  # fmt: skip

    def layer(k):
        return np.array(sorted(v for (_i, _j, kk), v in ids.items() if kk == k), np.int64)

    def tris(k, tag, first):
        t = [tri for j in range(nx) for i in range(nx) for tri in (
            [ids[i, j, k], ids[i + 1, j, k], ids[i + 1, j + 1, k]],
            [ids[i, j, k], ids[i + 1, j + 1, k], ids[i, j + 1, k]])]  # fmt: skip
        return CellBlock("tri3", 2, (2, tag), np.arange(first, first + len(t)),
                         np.array(t, np.int64))  # fmt: skip

    return np.array(list(ids.values()), np.int64), np.array(coords, float), block, layer, tris


def _mesh() -> Mesh:
    s_ids, s_xyz, s_block, s_layer, _ = _box(-B / 2, B / 2, 4, 8, 1, 1, 1)
    p_ids, p_xyz, p_block, _p_layer, p_tris = _box(6 * MM, 16 * MM, 4, 8, 1001, 1001, 2)
    top, bottom = p_tris(8, 11, 5001), p_tris(0, 12, 6001)
    mesh = Mesh(np.concatenate([s_ids, p_ids]), np.vstack([s_xyz, p_xyz]),
                [s_block, p_block, top, bottom])  # fmt: skip
    nodes = lambda b: np.unique(b.connectivity)  # noqa: E731
    mesh.groups = {
        "Screw": Group("Screw", 3, [(3, 1)], np.sort(s_ids)),
        "Screw/Section": Group("Screw/Section", 2, [(2, 5)], s_layer(4)),
        "Screw/Bearing": Group("Screw/Bearing", 2, [(2, 6)], s_layer(8)),
        "Screw/Thread": Group("Screw/Thread", 2, [(2, 7)], s_layer(1)),
        "Plate": Group("Plate", 3, [(3, 2)], np.sort(p_ids)),
        "Plate/Top": Group("Plate/Top", 2, [(2, 11)], nodes(top)),
        "Plate/Bottom": Group("Plate/Bottom", 2, [(2, 12)], nodes(bottom)),
    }
    return mesh


def _stress(mesh: Mesh) -> Field:
    values = np.zeros((len(mesh.node_ids), 6))
    x = mesh.coords[:, 0]
    plate = np.isin(mesh.node_ids, mesh.groups["Plate"].node_ids)
    values[:, 2] = np.where(plate, 1e9, SIGMA0 + K * x)
    return Field("stress", "node", ["xx", "yy", "zz", "xy", "yz", "zx"], mesh.node_ids, values)


def test_section_loads_integrate_the_bolts_own_section():
    from puzzle.bolts import section_loads

    mesh = _mesh()
    loads = section_loads(mesh, _stress(mesh), "Screw")
    assert loads.axial[0] == pytest.approx(SIGMA0 * B * B, rel=1e-6)  # tension, +
    assert loads.bending[0] == pytest.approx(K * B**4 / 12, rel=1e-6)
    assert abs(loads.shear[0]) < 1e-6 * SIGMA0 * B * B and abs(loads.torsion[0]) < 1e-3
    assert loads.s[0] == pytest.approx(0.02 - B / 50, rel=1e-6)  # from under the head
    assert section_loads(mesh, _stress(mesh), "Nothing") is None


def _project(root):
    from puzzle.materials import add_library_material
    from puzzle.project import save_project

    p = Project(name="bolts")
    grade = add_library_material(p, "iso_898_1_class_8_8")
    geo = p.add(
        Geometry(
            name="Plate",
            history=[HistoryStep(kind="box", name="P", args={"dx": 0.01, "dy": 0.01, "dz": 0.04})],
        )
    )
    btype = p.add(BoltType(name="M10 8.8", size="M10", material=grade.id, preload="none",
                           joint="tapped", thread_kf=3.5))  # fmt: skip
    recipe = p.add(MeshRecipe(name="Mesh"))
    comp = p.add(Component(
        name="Joint", mesh=recipe.id, parts=[PartInstance(name="Plate", geometry=geo.id)],
        materials=[MaterialAssignment(material=grade.id, selection="Plate")],
        contacts=[Contact(name="Screw thread", type="bonded", source="Screw/Thread",
                          target="Plate/Top", thread=Thread(form="iso", size="M10"))],
        connectors=[
            BeamFastener(name="Screw", source="Plate/Bottom", target="Plate/Top",
                         type=btype.id, formulation="modelled", parts=["Screw"]),
            BeamFastener(name="Pin", source="Plate/Bottom", target="Plate/Top",
                         type=btype.id),
        ],
    ))  # fmt: skip
    study = p.add(
        Study(
            name="Load", component=comp.id, solver="calculix", steps=[SolveStep(type="stationary")]
        )
    )
    save_project(p, root)
    return p, comp, study


def _run(root, study):
    from puzzle.project.layout import RESULTS_DIR
    from puzzle.results.store import ResultStore

    mesh = _mesh()
    row = np.array([[123.0, 0, 0, 0, 0, 0, 123.0, 0, 0, 0]])
    fields = {
        "stress": _stress(mesh),
        "fastener": Field("fastener", "global", [str(i) for i in range(10)], np.array([0]), row),
        "thread_load": Field(
            "thread_load",
            "global",
            ["Screw thread: 1", "Screw thread: 2"],
            np.array([0]),
            np.array([[600.0, 400.0]]),
        ),  # fmt: skip
    }
    store = ResultStore(root / RESULTS_DIR)
    run_id = store.write_run(
        run_key="k", study_id=str(study.id), study_name=study.name, solver="calculix",
        runtime="native", mesh=mesh, results=Results("calculix", [
            StepResult(1, "stationary", 1.0, None, fields)]),
        started_at=datetime.now(UTC), duration_s=1.0)  # fmt: skip
    return store, run_id


def test_bolt_results_keep_the_rows_on_the_beam_bolts(tmp_path):
    from puzzle.bolts import bolt_results

    p, _comp, study = _project(tmp_path)
    store, run_id = _run(tmp_path, study)
    found = {b.name: b for b in bolt_results(p, store, run_id)}
    assert found["Pin"].loads.axial[0] == pytest.approx(123.0) and not found["Pin"].modelled
    screw = found["Screw"]
    assert screw.modelled and screw.loads.axial[0] == pytest.approx(SIGMA0 * B * B, rel=1e-6)
    assert screw.threads == [600.0, 400.0] and screw.first_thread == pytest.approx(0.6)
    area = 58.0e-6  # M10's stress area
    d = np.sqrt(4 * area / np.pi)
    by_hand = SIGMA0 * B * B / area + K * B**4 / 12 / (np.pi * d**3 / 32)
    assert screw.stress == pytest.approx(by_hand, rel=2e-3)
    assert screw.proof == pytest.approx(by_hand / 580e6, rel=2e-3)


def test_the_fatigue_tool_takes_the_bolts_thread_kf(tmp_path):
    from puzzle.post.fatigue_tool import _bolt_threads

    p, _comp, study = _project(tmp_path)
    mesh = _mesh()
    kf = np.ones(len(mesh.node_ids))
    notes = _bolt_threads(p, mesh, study.id, kf)
    thread = np.isin(mesh.node_ids, mesh.groups["Screw/Thread"].node_ids)
    assert np.all(kf[thread] == 3.5) and np.all(kf[~thread] == 1.0)  # the type's thread_kf
    assert notes and "Screw: Kf = 3.5" in notes[0]


def test_mcp_makes_and_unmakes_a_modelled_bolt(tmp_path):
    """bolt_make_modelled and bolt_make_beam return batches; bolt_results says which
    bolt is modelled."""
    pytest.importorskip("OCP")
    pytest.importorskip("mcp")
    import asyncio

    from puzzle.agent.mcp import build_server
    from puzzle.commands.session import Session

    _joint(tmp_path)
    Session(tmp_path).submit({"command": "set_autonomy", "level": "act_in_budget"})
    server = build_server(tmp_path)

    def call(name, **args):
        result = asyncio.run(server.call_tool(name, args))
        if result.is_error:
            raise AssertionError(result)
        return result.structured_content.get("result", result.structured_content)

    made = call("bolt_make_modelled", component="Joint", bolt="Screw")
    assert made["bolts"] == ["Screw"] and made["batch"]
    call("submit", command=made["batch"])
    from puzzle.project import load_project

    comp = load_project(tmp_path).of_type(Component)[0]
    assert comp.connectors[0].formulation == "modelled" and comp.connectors[0].parts
    back = call("bolt_make_beam", component="Joint", bolt="Screw")
    call("submit", command=back["batch"])
    comp = load_project(tmp_path).of_type(Component)[0]
    assert comp.connectors[0].formulation == "beam"


def _joint(root):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent / "integration"))
    from test_solid_bolts import _joint as joint

    return joint(root)


# --- the GUI ------------------------------------------------------------------------


@pytest.fixture(scope="module")
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_the_bolt_table_models_a_bolt_and_takes_it_back(app, tmp_path):
    """The table's Thread Kf column edits the bolt's own (the type's shown faded); the
    formulation's Modelled draws the bolt as parts in one batch (one undo), Back to a
    beam bolt takes it back."""
    pytest.importorskip("OCP")
    from puzzle.gui.bolt_table import COLUMNS
    from puzzle.gui.window import MainWindow

    _joint(tmp_path)
    window = MainWindow(tmp_path)
    window.open_bolts()
    table = window.bolts.properties
    col = {c.title: i for i, c in enumerate(COLUMNS)}
    kf = table.table.item(0, col["Thread Kf"])
    assert kf.text() == "3" and "M16 8.8" in kf.toolTip()  # the type's default
    table.edit(0, COLUMNS[col["Thread Kf"]], "0.5")  # refused: below 1
    assert window.session.project.of_type(Component)[0].connectors[0].thread_kf is None
    table.edit(0, COLUMNS[col["Thread Kf"]], "4")
    assert window.session.project.of_type(Component)[0].connectors[0].thread_kf == 4.0

    table.edit(0, COLUMNS[col["Formulation"]], "modelled")
    comp = window.session.project.of_type(Component)[0]
    assert comp.connectors[0].formulation == "modelled" and "Screw" in [x.name for x in comp.parts]
    assert window.session.history(limit=1)[0]["command"]["command"] == "batch"
    assert table.table.item(0, col["Formulation"]).text() == "Modelled"
    table.table.selectRow(0)
    table.beam_bolts()
    comp = window.session.project.of_type(Component)[0]
    assert comp.connectors[0].formulation == "beam" and [x.name for x in comp.parts] == [
        "Boss", "Plate"]  # fmt: skip
    window.close()


def test_the_bolt_type_form_asks_for_the_thread_kf(app, tmp_path):
    from forms import fill

    from puzzle.gui.bolt_types import new_bolt_type
    from puzzle.gui.window import MainWindow
    from puzzle.project import save_project

    p = Project(name="types")
    save_project(p, tmp_path)
    window = MainWindow(tmp_path)
    fill(window, standard="iso_coarse", size="M12", material="library:iso_898_1_class_8_8",
         preload="none", formulation="modelled", head="socket", thread_kf="3.5")  # fmt: skip
    new_bolt_type(window)
    (btype,) = window.session.project.of_type(BoltType)
    assert (btype.formulation, btype.head, btype.thread_kf) == ("modelled", "socket", 3.5)
    window.close()


def test_the_bolt_generator_models_the_bolts_it_makes(app, tmp_path):
    """Its "Model the bolts" option draws each new bolt as parts (a second batch, on the
    bolts the first made)."""
    pytest.importorskip("OCP")
    from PySide6.QtCore import Qt

    from puzzle.gui.bolt_generator_panel import generate_bolts
    from puzzle.gui.window import MainWindow
    from puzzle.project import save_project

    p = _joint(tmp_path)
    comp = p.of_type(Component)[0]
    comp.connectors = []
    save_project(p, tmp_path)
    window = MainWindow(tmp_path)
    comp = window.session.project.of_type(Component)[0]
    panel = generate_bolts(window, comp)
    btype = window.session.project.of_type(BoltType)[0]
    panel.btype.setCurrentIndex(panel.btype.findData(str(btype.id)))
    panel.find()
    for r in range(panel.table.rowCount()):  # the bore and the hole (the boss's outside
        if "H/wall" not in panel.table.item(r, 2).text():  # is a cylinder too)
            panel.table.item(r, 0).setCheckState(Qt.CheckState.Unchecked)
    panel.modelled.setChecked(True)
    panel.make()
    comp = window.session.project.of_type(Component)[0]
    (bolt,) = comp.connectors
    assert bolt.formulation == "modelled" and bolt.parts and bolt.parts[0] in [
        x.name for x in comp.parts]  # fmt: skip
    window.close()
