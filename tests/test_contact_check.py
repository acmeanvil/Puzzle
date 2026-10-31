"""Contact initial state and checks (ADR 0056) against hand geometry: the split bar's
two 100 x 10 x 10 mm halves end to end, the right one moved by a known gap, into a
known penetration, or sideways so that its end face runs past the left one's."""

from __future__ import annotations

import numpy as np
import pytest
from conftest_geometry import BAR, HALF, _half

from puzzle import contact_check
from puzzle.contact_check import closest_points
from puzzle.materials import add_library_material
from puzzle.mesh import generate_mesh
from puzzle.model import (
    Component,
    Contact,
    Feature,
    Geometry,
    MaterialAssignment,
    MeshRecipe,
    PartInstance,
    Physics,
    Project,
    Quantity,
)
from puzzle.model.schema import BeamFastener  # noqa: F401 - the schema's bolts

GAP = 0.2e-3


def _bar(tmp_path, offset=(0.0, 0.0, 0.0), contact_type="frictionless", features=None,
         **contact):  # fmt: skip
    p = Project(name="split bar")
    geo = p.add(Geometry(name="Half", source="half.step", selections=_half(tmp_path / "half.step")))
    steel = add_library_material(p, "structural_steel")
    mesh = p.add(
        MeshRecipe(
            name="Mesh",
            global_size=Quantity(value=2.5, unit="mm"),
            local_sizes={"Right/Body": Quantity(value=1.6, unit="mm")},
        )
    )
    friction = {"friction": 0.2} if contact_type == "frictional" else {}
    comp = p.add(Component(
        name="Split bar",
        parts=[PartInstance(name="Left", geometry=geo.id),
               PartInstance(name="Right", geometry=geo.id,
                            translation=(HALF + offset[0], offset[1], offset[2]))],
        mesh=mesh.id,
        materials=[MaterialAssignment(material=steel.id, selection=f"{s}/Body")
                   for s in ("Left", "Right")],
        contacts=[Contact(name="Joint", type=contact_type, source="Right/Start",
                          target="Left/End", **friction, **contact)],
        physics=[Physics(type="solid_mechanics",
                         features=features or [Feature(type="fixed", selection="Left/Start")])],
    ))  # fmt: skip
    m = generate_mesh(p, comp.id, tmp_path, tmp_path / "m").mesh
    return p, comp, m


def test_closest_points_on_a_triangle_by_region():
    a, b, c = np.zeros(3), np.array([1.0, 0, 0]), np.array([0, 1.0, 0])
    points = np.array([[0.2, 0.2, 1.0], [-1, -1, 0], [2, -0.5, 0], [0.5, -1, 0], [1, 1, 3]])
    tri = [np.repeat(v[None], len(points), axis=0) for v in (a, b, c)]
    q = closest_points(points, *tri)
    assert q == pytest.approx(np.array([[0.2, 0.2, 0], [0, 0, 0], [1, 0, 0], [0.5, 0, 0],
                                        [0.5, 0.5, 0]]))  # fmt: skip


def test_a_known_gap(tmp_path):
    _, comp, mesh = _bar(tmp_path, offset=(GAP, 0, 0))
    [s] = contact_check.states(comp, mesh)
    assert s.search_distance == pytest.approx(0.01 * np.linalg.norm([2 * HALF + GAP, *BAR[1:]]))
    assert s.gaps == pytest.approx(np.full(len(s.nodes), GAP), abs=1e-9)
    assert s.status == "near" and s.shares["near"] == pytest.approx(1.0)
    assert s.largest_gap == pytest.approx(GAP, rel=1e-6) and s.largest_penetration == 0
    assert s.nearest == pytest.approx(GAP, rel=1e-6)


def test_a_known_penetration_is_closed(tmp_path):
    _, comp, mesh = _bar(tmp_path, offset=(-GAP, 0, 0))
    [s] = contact_check.states(comp, mesh)
    assert s.status == "closed" and s.shares["closed"] == pytest.approx(1.0)
    assert s.largest_penetration == pytest.approx(GAP, rel=1e-6)


def test_far_apart_and_the_search_distance(tmp_path):
    p, comp, mesh = _bar(tmp_path, offset=(5e-3, 0, 0), contact_type="bonded",
                         meshing="nonconformal")  # fmt: skip
    [s] = contact_check.states(comp, mesh)
    assert s.status == "far" and s.nearest == pytest.approx(5e-3, rel=1e-6)
    codes = [w.code for w in contact_check.checks(p, comp, mesh)]
    assert "contact_far" in codes
    wider = comp.contacts[0].model_copy(update={"search_distance": Quantity(value=6, unit="mm")})
    assert contact_check.state(wider, mesh).status == "open"


def test_an_overhanging_source_face(tmp_path):
    """Moved 3 mm sideways: the strip y in (10, 13) mm of the right end face has no left
    face under it, 30 % of its area."""
    p, comp, mesh = _bar(tmp_path, offset=(0, 3e-3, 0))
    [s] = contact_check.states(comp, mesh)
    assert s.status == "overhanging"
    assert s.shares["overhanging"] == pytest.approx(0.3, abs=0.06)
    assert s.shares["closed"] == pytest.approx(0.7, abs=0.06)
    warnings = contact_check.checks(p, comp, mesh)
    assert [w.code for w in warnings] == ["contact_only_hold", "contact_overhang"]
    assert "swap the sides" in warnings[1].message


def test_a_shared_outline_and_held_source_nodes(tmp_path):
    """End to end the two end faces share their outline; holding the right half's
    bottom puts a support on source nodes too."""
    p, comp, mesh = _bar(tmp_path, contact_type="frictional", features=[
        Feature(type="fixed", selection="Left/Start"),
        Feature(type="fixed", selection="Right/Bottom"),
    ])  # fmt: skip
    assert contact_check.shared_outline(mesh, "Right/Start", "Left/End") == pytest.approx(1.0)
    codes = [w.code for w in contact_check.checks(p, comp, mesh, solver="code_aster")]
    assert codes == ["contact_slave_held", "contact_shared_outline"]
    assert "contact_shared_outline" not in [
        w.code for w in contact_check.checks(p, comp, mesh, solver="calculix")
    ]


def test_a_body_held_only_by_sliding_contact(tmp_path):
    p, comp, _ = _bar(tmp_path)
    [w] = contact_check.held_by_contact_only(p, comp)
    assert (w.code, w.object) == ("contact_only_hold", "Right")
    bonded = comp.model_copy(update={"contacts": [comp.contacts[0].model_copy(
        update={"type": "bonded", "meshing": "nonconformal"})]})  # fmt: skip
    assert contact_check.held_by_contact_only(p, bonded) == []
    loose = comp.model_copy(update={"contacts": []})
    assert [w.code for w in contact_check.held_by_contact_only(p, loose)] == ["free_body"]


def test_what_each_solver_is_given(tmp_path):
    """The Setup tab's words come from the writers: CalculiX's penalty K = 10 E / h with
    the mesh's numbers, Code_Aster's continuous contact with Coulomb friction."""
    from puzzle.adapters.calculix import CalculixAdapter
    from puzzle.adapters.code_aster import CodeAsterAdapter

    p, comp, mesh = _bar(tmp_path, contact_type="frictional")
    ccx = CalculixAdapter().contact_summary(p, comp, mesh)["Joint"]
    assert ccx.startswith("*CONTACT PAIR, surface to surface, slave Right/Start")
    assert "K = 10 E/h = " in ccx and "*FRICTION μ = 0.2" in ccx
    assert "(h from the mesh)" in CalculixAdapter().contact_summary(p, comp)["Joint"]
    aster = CodeAsterAdapter().contact_summary(p, comp, mesh)["Joint"]
    assert "DEFI_CONTACT continuous" in aster and "Coulomb μ = 0.2" in aster
    assert "refused on this quadratic mesh" in aster


def test_the_report_for_the_agent(tmp_path):
    from puzzle.model import Study
    from puzzle.model.schema import SolveStep

    p, comp, mesh = _bar(tmp_path, offset=(0, 3e-3, 0))
    p.add(Study(name="Load", component=comp.id, solver="calculix",
                steps=[SolveStep(type="stationary")]))  # fmt: skip
    out = contact_check.report(p, comp, mesh)
    [row] = out["contacts"]
    assert row["initial"]["status"] == "overhanging"
    assert row["solver_input"]["calculix"].startswith("*CONTACT PAIR")
    assert [w["code"] for w in out["warnings"]] == ["contact_only_hold", "contact_overhang"]


def test_mcp_contact_check(tmp_path):
    pytest.importorskip("mcp")
    import asyncio

    from puzzle.agent.mcp import build_server
    from puzzle.project import save_project

    p, comp, _ = _bar(tmp_path, offset=(GAP, 0, 0))
    save_project(p, tmp_path)  # half.step is already there
    server = build_server(tmp_path, "test")
    result = asyncio.run(server.call_tool("contact_check", {"component": "Split bar",
                                                            "solver": "code_aster"}))  # fmt: skip
    out = result.structured_content.get("result", result.structured_content)
    [row] = out["contacts"]
    assert row["initial"]["status"] == "near"
    assert row["initial"]["largest_gap_m"] == pytest.approx(GAP, rel=1e-6)
    assert "DEFI_CONTACT" in row["solver_input"]["code_aster"]


def test_the_advisor_and_the_cli(tmp_path, capsys):
    from puzzle.advisor import advise
    from puzzle.cli import main
    from puzzle.project import save_project

    p, comp, _ = _bar(tmp_path, offset=(GAP, 0, 0))
    step = next(a for a in advise(p, component=comp) if a.step == "contacts")
    assert (step.state, step.action) == ("needed", "contact_tool")
    assert "held only through" in step.detail
    save_project(p, tmp_path)
    assert main(["contacts", str(tmp_path), "--solver", "calculix"]) == 0
    out = capsys.readouterr().out
    assert "Joint: frictionless, Right/Start -> Left/End" in out
    assert "calculix: *CONTACT PAIR" in out and "starts near: 0% in touch" in out
    assert "warning: body 'Right' is held only" in out


def test_a_solve_logs_the_warnings_first(tmp_path):
    """run_study records the checks as progress warnings before writing the input."""
    from puzzle import progress
    from puzzle.model import Study
    from puzzle.model.schema import SolveStep
    from puzzle.solve import _contact_warnings

    p, comp, mesh = _bar(tmp_path, offset=(0, 3e-3, 0))
    study = p.add(Study(name="Load", component=comp.id, solver="calculix",
                        steps=[SolveStep(type="stationary")]))  # fmt: skip
    seen = []
    _contact_warnings(p, study, mesh, seen.append)
    assert [(e.level, e.code) for e in seen] == [
        ("warning", "contact_only_hold"), ("warning", "contact_overhang")]  # fmt: skip
    assert all(isinstance(e, progress.Message) for e in seen)


def test_adjust_and_offset_move_the_source_nodes(tmp_path):
    """0.2 mm apart: adjust closes the gap (every source node moved 0.2 mm onto the
    target); an offset of -0.05 mm puts them 0.05 mm into it (an interference)."""
    _, comp, mesh = _bar(tmp_path, offset=(GAP, 0, 0))
    joint = comp.contacts[0]
    for interface, offset, gap in (("adjust", None, 0.0), ("offset", -0.05, -0.05e-3)):
        extra = {"offset": Quantity(value=offset, unit="mm")} if offset is not None else {}
        c = joint.model_copy(update={"interface": interface, **extra})
        moved_mesh, moved = contact_check.adjusted(comp.model_copy(update={"contacts": [c]}),
                                                   mesh)  # fmt: skip
        count, largest = moved["Joint"]
        assert count == len(mesh.groups["Right/Start"].node_ids)
        assert largest == pytest.approx(GAP - gap, rel=1e-6)
        s = contact_check.state(joint, moved_mesh)
        assert s.gaps == pytest.approx(np.full(len(s.nodes), gap), abs=1e-9)
    assert mesh.coords is not moved_mesh.coords  # the original mesh is left alone


def test_an_offset_is_only_for_sliding_contacts():
    with pytest.raises(ValueError, match="frictionless and frictional"):
        Contact(name="J", type="bonded", source="A/x", target="B/y", interface="adjust")
    with pytest.raises(ValueError, match="offset is given"):
        Contact(name="J", type="frictionless", source="A/x", target="B/y", interface="offset")


def test_a_pairing_failure_is_explained():
    from puzzle import progress

    text = progress.translate("Échec de l'appariement de contact lors de l'actualisation "
                              "géométrique en Newton généralisé.")  # fmt: skip
    codes = [d.code for d in progress.diagnose([progress.Message(level="error", text=text)])]
    assert codes == ["pairing_failed"]
