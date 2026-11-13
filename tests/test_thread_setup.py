"""A thread's other paths (ADR 0076 step 6): the agent's tools, the skill's tools, the
contact's Properties and menus, Find contacts' suggestion, the Advisor's note and the
schematic's wire all set the same `Contact.thread` through one Edit, checked alike.

Hand values: an ISO M16×2 has d2 = 16 − 0.649519·2 = 14.701 mm and d3 = 16 −
1.226869·2 = 13.546 mm, so a cylinder of 13.546 mm suggests M16 (its screw minor) and
one of 10 mm M10 (its major)."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime

import numpy as np
import pytest

from puzzle import thread_setup as ts
from puzzle.errors import PuzzleError
from puzzle.model.schema import (
    Component,
    Contact,
    CoordinateSystem,
    Geometry,
    MeshRecipe,
    NamedSelection,
    PartInstance,
    Project,
    Quantity,
    Study,
)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _project(root=None):
    from puzzle.project import save_project

    p = Project(name="joint")
    geo = p.add(Geometry(name="Boss", source="boss.step", selections=[
        NamedSelection(name="Bore", entity="face", members=["face-1"]),
        NamedSelection(name="Shank", entity="face", members=["face-2"])]))  # fmt: skip
    mesh = p.add(MeshRecipe(name="Tets", global_size=Quantity(value=2, unit="mm")))
    p.add(CoordinateSystem(name="Screw axis", type="cylindrical"))
    comp = p.add(Component(
        name="Joint", mesh=mesh.id, parts=[PartInstance(name="Boss", geometry=geo.id)],
        contacts=[Contact(name="Thread", type="bonded", source="Boss/Shank",
                          target="Boss/Bore", meshing="conformal")]))  # fmt: skip
    p.add(Study(name="Pull", component=comp.id, solver="calculix"))
    if root is not None:
        save_project(p, root)
    return p, comp


# --- the shared rules ------------------------------------------------------------------


def test_forms_sizes_and_suggestions():
    forms = {f["form"]: f for f in ts.forms_info()}
    assert forms["iso"]["flank_angles_deg"] == [30.0, 30.0]
    assert forms["buttress"]["flank_angles_deg"] == [7.0, 45.0]
    assert forms["npt"]["taper"] == pytest.approx(1 / 16)
    assert "custom" in forms
    assert "M16" in ts.sizes_for("iso") and "1/2-13 UNC" in ts.sizes_for("un")
    assert ts.sizes_for("acme") == []
    m16 = ts.suggest_size(0.013546)
    assert (m16["size"], m16["matches"]) == ("M16", "screw_minor")
    assert ts.suggest_size(0.010)["size"] == "M10"
    assert ts.suggest_size(1.0) is None  # no size near a metre


def test_a_thread_is_checked_and_resolved():
    thread = ts.make_thread(form="iso", size="M16")
    got = ts.describe(thread)
    assert got["pitch_m"] == pytest.approx(0.002)
    assert got["pitch_diameter_m"] == pytest.approx(0.014701, abs=1e-6)
    assert got["screw_minor_m"] == pytest.approx(0.013546, abs=1e-6)
    with pytest.raises(PuzzleError) as no_size:
        ts.make_thread(form="iso", size="M17")
    assert "M16" in no_size.value.details["sizes"]
    with pytest.raises(PuzzleError):  # acme has no size table: major and pitch
        ts.make_thread(form="acme")
    acme = ts.make_thread(form="acme", major={"value": 20, "unit": "mm"},
                          pitch={"value": 4, "unit": "mm"})  # fmt: skip
    assert ts.describe(acme)["flank_angles_deg"] == [14.5, 14.5]


def test_one_edit_sets_the_thread_and_one_undo_takes_it(tmp_path):
    from puzzle.commands.session import Session

    _, comp = _project(tmp_path / "p")
    session = Session(tmp_path / "p")
    edit = ts.thread_command(comp, "Thread", ts.make_thread(size="M16"), "no_separation")
    session.submit(edit)
    (c,) = session.project.of_type(Component)[0].contacts
    assert (c.thread.size, c.type, c.meshing) == ("M16", "no_separation", None)
    with pytest.raises(PuzzleError):  # frictional threads aren't written yet
        ts.thread_command(comp, "Thread", ts.make_thread(size="M16"), "frictional")
    session.undo()
    (c,) = session.project.of_type(Component)[0].contacts
    assert c.thread is None and c.meshing == "conformal"


# --- results ----------------------------------------------------------------------------


def _store_run(root, comp, study):
    from puzzle.mesh.mesh import Group, Mesh
    from puzzle.project.layout import RESULTS_DIR
    from puzzle.results import Field, Results, ResultStore, StepResult

    coords = np.array([[0.007, 0, z] for z in (0.0, 0.002, 0.004)]
                      + [[0.0072, 0, z] for z in (0.0, 0.002, 0.004)], float)  # fmt: skip
    mesh = Mesh(node_ids=np.arange(1, 7, dtype=np.int64), coords=coords)
    mesh.groups = {"Boss/Shank": Group("Boss/Shank", 2, [], np.array([1, 2, 3])),
                   "Boss/Bore": Group("Boss/Bore", 2, [], np.array([4, 5, 6]))}  # fmt: skip
    loads = Field("thread_load", "global", ["Thread: 1", "Thread: 2", "Thread: 3"],
                  np.array([0]), np.array([[500.0, 300.0, 200.0]]))  # fmt: skip
    wedge = Field("thread_wedge", "global", ["Thread"], np.array([0]), np.array([[577.0]]))
    pressure = Field("thread_pressure", "node", [""], np.array([1, 2, 3]),
                     np.array([[3e8], [2e8], [1e8]]))  # fmt: skip
    disp = Field("displacement", "node", ["x", "y", "z"], mesh.node_ids, np.zeros((6, 3)))
    step = StepResult(1, "stationary", 1.0, None, {"displacement": disp, "thread_load": loads,
                                                   "thread_wedge": wedge,
                                                   "thread_pressure": pressure})  # fmt: skip
    return ResultStore(root / RESULTS_DIR).write_run(
        run_key="k", study_id=str(study.id), study_name=study.name, solver="calculix",
        runtime="native", mesh=mesh, results=Results("calculix", [step]),
        started_at=datetime.now(UTC), duration_s=1.0)  # fmt: skip


def _call(server, tool, **arguments):
    result = asyncio.run(server.call_tool(tool, arguments))
    return result.structured_content.get("result", result.structured_content)


def test_agents_set_a_thread_and_read_its_loads(tmp_path):
    """MCP: `thread_forms`, `contact_thread` (a command to submit), `thread_results`."""
    pytest.importorskip("mcp")
    from puzzle.agent.mcp import build_server
    from puzzle.commands.session import Session

    root = tmp_path / "p"
    _project(root)
    Session(root).submit({"command": "set_autonomy", "level": "act_in_budget"})
    server = build_server(root, "test")
    forms = _call(server, "thread_forms", form="iso")
    assert "M16" in forms["sizes"] and any(f["form"] == "trapezoidal" for f in forms["forms"])
    made = _call(server, "contact_thread", component="Joint", contact="Thread", size="M16",
                 cs="Screw axis", type="frictionless",
                 clearance={"value": 0.01, "unit": "mm"})  # fmt: skip
    assert made["thread"]["pitch_m"] == pytest.approx(0.002)
    assert made["written_by"] == ["CalculiX"]
    assert any("nonlinear" in n for n in made["notes"])
    assert _call(server, "submit", command=made["command"], rationale="M16")["status"] == "applied"
    project = Session(root).project
    (c,) = project.of_type(Component)[0].contacts
    axes = project.of_type(CoordinateSystem)[0]
    assert c.thread.cs == axes.id and c.type == "frictionless"
    assert c.thread.clearance.to_si() == pytest.approx(1e-5)

    comp, study = project.of_type(Component)[0], project.of_type(Study)[0]
    _store_run(root, comp, study)
    got = _call(server, "thread_results", study="Pull")
    (row,) = got["threads"]
    assert row["axial_N"] == pytest.approx(1000.0)
    assert [t["share"] for t in row["threads"]] == pytest.approx([0.5, 0.3, 0.2])
    assert row["first_thread_share"] == pytest.approx(0.5)
    assert row["wedge_N"] == pytest.approx(577.0)
    assert row["peak_pressure_Pa"] == pytest.approx(3e8)
    gone = _call(server, "contact_thread", component="Joint", contact="Thread", remove=True)
    _call(server, "submit", command=gone["command"], rationale="plain again")
    assert Session(root).project.of_type(Component)[0].contacts[0].thread is None


# --- the schematic -----------------------------------------------------------------------


def test_a_coordinate_system_wires_into_a_thread():
    from puzzle.commands.model import parse_command
    from puzzle.commands.session import _execute
    from puzzle.schematic import links

    p, comp = _project()
    _execute(p, ts.thread_command(comp, "Thread", ts.make_thread(size="M16")))
    comp = p.objects[comp.id]
    axes = p.of_type(CoordinateSystem)[0]
    pick = next(c for c in links.choices(p, axes, comp) if c.label == "Thread axis of Thread")
    for cmd in links.commands(pick, axes, comp):
        _execute(p, parse_command(cmd))
    assert p.objects[comp.id].contacts[0].thread.cs == axes.id
    made = links.existing(p, axes.id, p.objects[comp.id])
    for cmd in links.clear_commands(made[0], p.objects[comp.id]):
        _execute(p, parse_command(cmd))
    assert p.objects[comp.id].contacts[0].thread.cs is None


# --- the GUI ------------------------------------------------------------------------------


@pytest.fixture
def window(tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from puzzle.gui.window import MainWindow

    QApplication.instance() or QApplication([])
    _project(tmp_path / "p")
    w = MainWindow(tmp_path / "p")
    yield w
    w.close()


def test_make_it_a_thread_from_the_contacts_menu(window):
    from forms import fill

    from puzzle.gui.menus import build_menu
    from puzzle.gui.thread_dialog import remove_thread
    from puzzle.gui.tree import Node

    comp = window.session.project.of_type(Component)[0]
    node = Node("contact", str(comp.id), "contacts.0", "Thread")
    texts = [a.text() for a in build_menu(window, node).actions()]
    assert "Make it a thread…" in texts and "Remove the thread" not in texts
    fill(window, form="iso", size="M12", hand="left", type="bonded")
    next(a for a in build_menu(window, node).actions() if a.text() == "Make it a thread…"
         ).trigger()  # fmt: skip
    offered = [window.last_form.widgets["form"].itemText(i)
               for i in range(window.last_form.widgets["form"].count())]  # fmt: skip
    assert "ISO metric (ISO 68-1, ISO 724)" in offered  # names, not keys
    comp = window.session.project.of_type(Component)[0]
    assert (comp.contacts[0].thread.size, comp.contacts[0].thread.hand) == ("M12", "left")
    texts = [a.text() for a in build_menu(window, node).actions()]
    assert "Edit the thread…" in texts and "Remove the thread" in texts
    remove_thread(window, comp, 0)
    assert window.session.project.of_type(Component)[0].contacts[0].thread is None


def test_the_contacts_properties_show_the_thread_in_words(window):
    from puzzle.gui.tree import Node

    comp = window.session.project.of_type(Component)[0]
    window.submit(ts.thread_command(comp, "Thread", ts.make_thread(form="buttress", major={
        "value": 40, "unit": "mm"}, pitch={"value": 6, "unit": "mm"})))  # fmt: skip
    comp = window.session.project.of_type(Component)[0]
    window.tree.select(Node("contact", str(comp.id), "contacts.0", "Thread"))
    panel = window.properties
    form = panel.dropdown("contacts.0.thread.form")
    assert form.currentText() == "Buttress (ANSI B1.9)"
    assert panel.dropdown("contacts.0.thread.bearing").currentText().startswith("Automatic")
    assert panel.dropdown("contacts.0.thread.compliance").currentText() == "The teeth's (Yamamoto)"
    assert panel.dropdown("contacts.0.thread.cs").currentText() == "Fitted to the faces"
    assert panel.dropdown("contacts.0.thread.size") is None  # buttress: major and pitch
    shown = []
    stack = [panel.invisibleRootItem()]
    while stack:
        item = stack.pop()
        for i in range(item.childCount()):
            shown.append(item.child(i).text(0))
            stack.append(item.child(i))
    assert {"Thread", "Major", "Pitch", "Axis from a coordinate system"} <= set(shown)
    assert "Pitch diameter" not in shown  # from the form, until set
    form.setCurrentIndex(form.findData("iso"))  # one Edit, as every field
    assert window.session.project.of_type(Component)[0].contacts[0].thread.form == "iso"


# --- Find contacts and the Advisor (geometry) -------------------------------------------


def _pin(tmp_path):
    pytest.importorskip("OCP")
    from test_contacts_in_a_part import _write_pin

    from puzzle.project import save_project

    root = tmp_path / "pin"
    root.mkdir()
    _write_pin(root / "pin.step")  # a 10 mm pin filling its bore: one part, two bodies
    p = Project(name="pin")
    geo = p.add(Geometry(name="Pin", source="pin.step", source_unit="mm"))
    geo.selections += [NamedSelection(name="Block", entity="body", members=["body-1"]),
                       NamedSelection(name="Pin", entity="body", members=["body-2"])]  # fmt: skip
    mesh = p.add(MeshRecipe(name="Tets", global_size=Quantity(value=5, unit="mm")))
    comp = p.add(Component(name="Pin", mesh=mesh.id,
                           parts=[PartInstance(name="Pin", geometry=geo.id)]))  # fmt: skip
    save_project(p, root)
    return root, p, comp


def test_find_contacts_offers_coaxial_cylinders_as_a_thread(tmp_path):
    from puzzle.geometry.contacts import (
        body_labels,
        contact_commands,
        find_contacts,
        proposals,
    )

    root, p, comp = _pin(tmp_path)
    found = proposals(find_contacts(p, comp, root), body_labels(p, comp))
    (bore,) = [x for x in found if x.thread]
    assert bore.thread["size"] == "M10" and bore.thread["radius"] == pytest.approx(0.005)
    assert not any(x.thread for x in found if x is not bore)  # flat faces: no thread
    bore.use_thread = True
    edit = contact_commands(p, comp, [bore])[-1]
    contact = next(o.value for o in edit.ops if o.path == "contacts")
    assert contact["thread"]["size"] == "M10"


def test_the_dialog_has_a_thread_column_and_the_advisor_offers_one(tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from puzzle.advisor import advise
    from puzzle.gui.contact_search import THREAD, ContactSearch
    from puzzle.gui.window import MainWindow

    QApplication.instance() or QApplication([])
    root, p, comp = _pin(tmp_path)
    w = MainWindow(root)
    try:
        comp = w.session.project.of_type(Component)[0]
        dialog = ContactSearch(w.session.project, comp, root, w)
        rows = [r for r in range(dialog.table.rowCount())
                if dialog.table.item(r, THREAD).text() == "M10"]  # fmt: skip
        assert len(rows) == 1 and "thread" in dialog.note.text()
        for r in range(dialog.table.rowCount()):  # only the bore, bonded and plain
            dialog.table.item(r, 0).setCheckState(
                Qt.CheckState.Checked if r == rows[0] else Qt.CheckState.Unchecked
            )
        (prop,) = dialog.accepted_proposals()
        assert not prop.use_thread  # offered, not ticked
        from puzzle.geometry.contacts import contact_commands

        w.submit_batch(contact_commands(w.session.project, comp, [prop]))
        comp = w.session.project.of_type(Component)[0]
        advice = advise(w.session.project, (), comp, root=root)
        offer = [a for a in advice if a.action == "thread:0"]
        assert offer and "coaxial" in offer[0].detail
    finally:
        w.close()
