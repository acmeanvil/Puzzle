"""The forms for what only the agent reached (ADR 0072 section 10, items 1-5): each,
filled as a user would (`forms.fill`), submits the batch the MCP tool's function gives
for the same arguments, or, read only, shows what the tool returns.

Add weld… vs `weld_add`; VDI 2230 check… vs `bolt_vdi2230`; Weld fatigue… vs
`weld_fatigue` (a CalculiX solve of a lap joint); Add sweep step… vs
`sweep.with_sweep_step` (agents edit a study's steps; no tool of its own); New frame
from a DXF… vs `frame_geometry`. Each is in the menu bar and has a help page."""

import asyncio
import json
import os
import shutil
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("mcp")

from forms import fill  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from solvers import over  # noqa: E402

from puzzle.agent.mcp import build_server  # noqa: E402
from puzzle.gui.window import MainWindow  # noqa: E402
from puzzle.model.schema import Component, Geometry, Quantity, Study  # noqa: E402

EXAMPLE = Path(__file__).parents[1] / "examples" / "cantilever"
q = lambda v, u: Quantity(value=v, unit=u)  # noqa: E731


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _cantilever(tmp_path) -> Path:
    root = tmp_path / "c"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("results*", ".puzzle",
                                                                 "history.jsonl"))  # fmt: skip
    return root


@pytest.fixture
def window(app, tmp_path):
    w = MainWindow(_cantilever(tmp_path))
    yield w
    w.close()


def _call(root, tool, **arguments):
    server = build_server(root, "test")
    result = asyncio.run(server.call_tool(tool, arguments))
    return result.structured_content.get("result", result.structured_content)


def _json(value):
    return json.loads(json.dumps(value))


def _recording(window) -> list:
    """The window's submit_batch, recording what it is given before submitting it."""
    seen, real = [], window.submit_batch

    def submit_batch(commands, **kwargs):
        seen.append([c.model_dump(mode="json") for c in commands])
        return real(commands, **kwargs)

    window.submit_batch = submit_batch
    return seen


# --- VDI 2230 check… ---------------------------------------------------------------------


def test_the_vdi2230_form_shows_what_the_tool_returns(window, tmp_path):
    from puzzle.gui import vdi2230_form

    fill(window, size="M16", grade="8.8", end="tapped", clamp_length="40 mm",
         shank_length="10 mm", mu_thread="0.1", axial_load="12 kN",
         tightening_factor="1.6")  # fmt: skip
    shown = vdi2230_form.vdi_check(window)
    tool = _call(window.session.root, "bolt_vdi2230", size="M16", clamp_length=0.04,
                 grade="8.8", tapped=True, shank_length=0.01, mu_thread=0.1,
                 axial_load=12e3, tightening_factor=1.6)  # fmt: skip
    assert _json(shown) == tool
    view = window.last_form.view
    name, worst = vdi2230_form.governing(shown)
    marked = [r for r in view.shown if r[2].endswith("(governs)")]
    assert [r[1] for r in marked] == [name] and f"{worst:.3f}" in view.verdict.text()
    assert any(r[1] == "Tightening torque MA" for r in view.shown)
    for suffix in ("csv", "txt"):
        path = str(tmp_path / f"vdi.{suffix}")
        window.ask_save_path = lambda *a, p=path: p
        assert vdi2230_form._save(window, view, "Save", "x", "") == path
        assert "Assembly preload FMzul" in Path(path).read_text(encoding="utf-8")
    assert window.last_form.property("help") == "form:vdi2230"


def test_a_bad_value_blocks_and_says_why(window):
    from puzzle.gui import vdi2230_form

    fill(window, clamp_length="forty")
    assert vdi2230_form.vdi_check(window) is None
    assert "clamp length" in window.last_form.view.verdict.text()


def test_ask_the_agent_hands_the_form_to_it(app, tmp_path):
    """With an agent set up, Ask the agent… closes the form and types the request."""
    from puzzle.gui import vdi2230_form

    w = MainWindow(_cantilever(tmp_path), provider_factory=lambda: None)
    try:

        def run(dialog):
            dialog.set("size", "M20")
            dialog.ask_button.click()
            return False

        w.run_dialog = run
        vdi2230_form.vdi_check(w)
        text = w.agent.input.text()
        assert "M20" in text and "bolt_vdi2230" in text
    finally:
        w.close()


# --- Add sweep step… ---------------------------------------------------------------------


def _with_parameter(window):
    from puzzle.commands.model import Create

    window.submit(Create(kind="parameters", data={
        "name": "Sizes", "values": {"t": {"value": 10, "unit": "mm"}}}))  # fmt: skip
    return window.session.project.of_type(Study)[0]


def test_the_sweep_form_writes_the_sweep_step(window):
    from puzzle import sweep
    from puzzle.commands.model import Edit, EditOp
    from puzzle.gui.sweep_form import sweep_step

    study = _with_parameter(window)
    expected = sweep.with_sweep_step(
        study, [{"name": "t", "values": [{"value": 8.0, "unit": "mm"},
                                          {"value": 12.0, "unit": "mm"}]}], "grid")  # fmt: skip
    sent, real = [], window.submit
    window.submit = lambda command, **k: sent.append(command) or real(command, **k)
    fill(window, **{"how:t": "list", "values:t": "8, 12 mm"})
    sweep_step(window, study)
    assert [c.model_dump(mode="json") for c in sent] == [Edit(
        object=str(study.id), ops=[EditOp(op="set", path="steps", value=expected)],
    ).model_dump(mode="json")]  # fmt: skip
    after = window.session.project.get(study.id, Study)
    assert after.steps[0].type == "parametric_sweep" and sweep.run_count(after) == 2
    assert (
        "2 points"
        in window.last_form.layout()
        .itemAt(window.last_form.layout().indexOf(window.last_form.buttons) - 1)
        .widget()
        .text()
    )

    # its Edit sweep… reopens it: a range of five in place of the list, one undo
    fill(window, **{"how:t": "range", "start:t": "5 mm", "end:t": "15 mm", "count:t": "5"})
    sweep_step(window, after, 0)
    edited = window.session.project.get(study.id, Study)
    assert [v.value for v in edited.steps[0].parameters[0].values] == [5, 7.5, 10, 12.5, 15]
    assert sum(s.type == "parametric_sweep" for s in edited.steps) == 1
    assert window.last_form.widgets["how:t"].currentData() == "range"


def test_the_sweep_form_refuses_nothing_swept(window):
    from puzzle.gui.sweep_form import sweep_step

    study = _with_parameter(window)
    fill(window)
    assert sweep_step(window, study) is None
    assert "at least one" in window.last_form.problem()


# --- New frame from a DXF… ---------------------------------------------------------------


def test_the_dxf_frame_form_makes_the_tools_geometry(window, tmp_path):
    ezdxf = pytest.importorskip("ezdxf")
    from puzzle.gui.frame_editor import new_frame_from_dxf

    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    msp.add_line((0, 0, 0), (0, 3000, 0), dxfattribs={"layer": "Columns"})
    msp.add_line((6000, 0, 0), (6000, 3000, 0), dxfattribs={"layer": "Columns"})
    msp.add_lwpolyline([(0, 3000), (3000, 3000), (6000, 3000)], dxfattribs={"layer": "Beams"})
    outside = tmp_path / "drawings" / "portal.dxf"
    outside.parent.mkdir()
    doc.saveas(outside)

    fill(window, file=str(outside))
    new_frame_from_dxf(window)
    form = window.last_form
    assert [form.widgets["layer"].itemData(i) for i in range(form.widgets["layer"].count())] == [
        "",
        "Beams",
        "Columns",
    ]
    assert form.widgets["scale"].text() == "0.001"
    root = window.session.root
    assert (root / "portal.dxf").exists()  # copied in, as imported geometry is
    made = next(g for g in window.session.project.of_type(Geometry) if g.name == "portal")
    tool = _call(root, "frame_geometry", name="portal", dxf="portal.dxf")
    data = made.model_dump(mode="json")
    expected = tool["command"]["data"]
    data.pop("id"), expected.pop("id")
    assert data == expected
    assert "4 members" in form.layout().itemAt(form.layout().indexOf(form.buttons) - 1
                                               ).widget().text()  # fmt: skip

    fill(window, file="portal.dxf", layer="Columns", scale="0.002", name="Columns only")
    new_frame_from_dxf(window)
    made = next(g for g in window.session.project.of_type(Geometry) if g.name == "Columns only")
    tool = _call(root, "frame_geometry", name="Columns only", dxf="portal.dxf",
                 layer="Columns", scale=0.002)  # fmt: skip
    data, expected = made.model_dump(mode="json"), tool["command"]["data"]
    data.pop("id"), expected.pop("id")
    assert data == expected


# --- welds -------------------------------------------------------------------------------


def _box(p, name, dx, dy, dz, edges=()):
    from puzzle.model.schema import HistoryStep, NamedSelection

    faces = ("-x", "+x", "-y", "+y", "-z", "+z")
    return p.add(Geometry(
        name=name,
        history=[HistoryStep(kind="box", name="B", args={"dx": dx, "dy": dy, "dz": dz})],
        selections=[
            NamedSelection(name="Body", entity="body", members=["Body"]),
            *(NamedSelection(name=f, entity="face", members=[f"B/{f}"]) for f in faces),
            *(NamedSelection(name=n, entity=kind, members=m) for n, kind, m in edges),
        ],
    ))  # fmt: skip


def _lap_joint(root: Path, welded: bool, solver="calculix"):
    """A 10 mm plate lapped on another (as tests/integration/test_welds_solved.py)."""
    from puzzle.materials import add_library_material
    from puzzle.model.schema import (
        Feature,
        MaterialAssignment,
        MeshRecipe,
        PartInstance,
        Physics,
        Project,
        SolveStep,
        Weld,
    )
    from puzzle.project import save_project

    p = Project(name="welded")
    steel = add_library_material(p, "structural_steel")
    lower = _box(p, "Lower", 0.1, 0.05, 0.01)
    upper = _box(p, "Upper", 0.1, 0.05, 0.01, edges=(("Foot", "edge", ["B/-x|B/-z"]),))
    mesh = p.add(MeshRecipe(name="M", global_size=q(3.0, "mm")))
    welds = [Weld(name="End fillet", leg=q(6, "mm"), path="Upper/Foot",
                  faces=["Upper/-x", "Lower/+z"])] if welded else []  # fmt: skip
    comp = p.add(Component(
        name="Joint",
        parts=[PartInstance(name="Lower", geometry=lower.id, translation=(0.0, 0.0, 0.0)),
               PartInstance(name="Upper", geometry=upper.id, translation=(0.06, 0.0, 0.01))],
        mesh=mesh.id,
        materials=[MaterialAssignment(material=steel.id, selection=f"{n}/Body")
                   for n in ("Lower", "Upper")],
        physics=[Physics(type="solid_mechanics", features=[
            Feature(type="fixed", selection="Lower/-x"),
            Feature(type="force", selection="Upper/+x", values={"x": q(10e3, "N")})],
            settings={"temperature_dependent_materials": False})],
        welds=welds,
    ))  # fmt: skip
    study = p.add(Study(name="Load", component=comp.id, solver=solver,
                        steps=[SolveStep(type="stationary")]))  # fmt: skip
    save_project(p, root)
    return p, comp, study


def test_the_add_weld_form_submits_the_tools_batch(app, tmp_path):
    pytest.importorskip("OCP")
    from puzzle.gui.weld_forms import add_weld

    root = tmp_path / "joint"
    _lap_joint(root, welded=False)
    w = MainWindow(root)
    try:
        comp = w.session.project.of_type(Component)[0]
        tool = _call(root, "weld_add", component="Joint", part="Upper", edges=["B/-x|B/-z"],
                     leg={"value": 6, "unit": "mm"}, leg2={"value": 8, "unit": "mm"},
                     name="End fillet")  # fmt: skip
        seen = _recording(w)
        fill(w, source="Upper/Foot", leg="6 mm", leg2="8 mm", name="End fillet")
        add_weld(w, comp)
        assert seen == [tool["batch"]]
        made = w.session.project.get(comp.id, Component).welds[0]
        assert made.faces == ["Upper/-x", "Lower/+z"] and made.leg2.value == 8
        preview = w.last_form.layout().itemAt(w.last_form.layout().indexOf(w.last_form.buttons)
                                              - 1).widget().text()  # fmt: skip
        assert "Joins Upper to Lower" in preview
        assert w.last_form.property("help") == "form:weld_add"

        # a second weld of the same name is refused before OK
        fill(w, source="Upper/Foot", leg="6 mm", name="End fillet")
        assert add_weld(w, w.session.project.get(comp.id, Component)) is None
        assert "already" in w.last_form.problem()
    finally:
        w.close()


@over("calculix")
def test_the_weld_fatigue_form_shows_what_the_tool_returns(app, tmp_path, solver):
    pytest.importorskip("OCP")
    from puzzle.gui.weld_forms import weld_fatigue
    from puzzle.project.layout import RESULTS_DIR
    from puzzle.results.store import ResultStore
    from puzzle.solve import run_study

    root = tmp_path / "joint"
    p, _comp, study = _lap_joint(root, welded=True, solver=solver)
    run_study(p, study.id, root, tmp_path / "w", store=ResultStore(root / RESULTS_DIR))
    w = MainWindow(root)
    try:
        fill(w, study=str(study.id), weld="End fillet", method="aisc", category="F",
             cycles="2e6")  # fmt: skip
        shown = weld_fatigue(w)
        tool = _call(root, "weld_fatigue", study="Load", weld="End fillet", category="F",
                     cycles=2e6)  # fmt: skip
        tool.pop("run_id")
        assert _json(shown) == tool
        assert "ratio" in w.last_form.view.summary.text()

        fill(w, study=str(study.id), weld="End fillet", method="asme", thickness="10 mm",
             cycles="1e5", side="attachment")  # fmt: skip
        shown = weld_fatigue(w)
        tool = _call(root, "weld_fatigue", study="Load", weld="End fillet", method="asme",
                     thickness_m=0.01, cycles=1e5)  # fmt: skip
        tool.pop("run_id")
        shown = _json(shown)
        assert shown["stations"][:50] == tool.pop("stations")
        shown.pop("stations")
        assert shown == {k: v for k, v in tool.items()}
        view = w.last_form.view
        assert view.table.rowCount() == len(_json(view.result)["stations"])
        assert view.plot.series and "Damage" in view.summary.text()
    finally:
        w.close()


# --- reached from the menus, and F1 -------------------------------------------------------


def test_the_forms_are_in_the_menu_bar_and_have_help(window):
    from puzzle import help as manual

    def leaves(menu):
        out = []
        for a in menu.actions():
            if a.menu() is not None:
                a.menu().aboutToShow.emit()
                out += leaves(a.menu())
            else:
                out.append(a.text())
        return out

    have = {t for m in window.menus.values() for t in leaves(m)}
    for text in ("Add weld…", "Weld fatigue…", "VDI 2230 check…", "Add sweep step…",
                 "New frame from a DXF…"):  # fmt: skip
        assert text in have, text
    for key in ("form:weld_add", "form:weld_fatigue", "form:vdi2230", "form:sweep_step",
                "form:frame_dxf"):  # fmt: skip
        found = manual.lookup(key)
        assert found is not None and found[1], key  # a heading of its own
        assert found[0].name.startswith("guides/"), key


def test_a_studys_steps_menu_offers_the_sweep(window):
    from puzzle.gui import menus
    from puzzle.gui.tree import Node

    study = window.session.project.of_type(Study)[0]
    menu = menus.build_menu(window, Node("object", str(study.id)))
    steps = next(a.menu() for a in menu.actions() if a.text() == "Add step")
    assert "Parametric sweep…" in [a.text() for a in steps.actions()]
