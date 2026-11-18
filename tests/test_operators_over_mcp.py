"""ADR 0071 decision 6: "tests drive each new operator through MCP as well as the GUI"
(ADR 0090 package 16, ADR-60). Step 5's kinds over MCP on a stored run of the
cantilever (`schematic_project.store_run`: two tets, the tip moving 1 mm down): a field
operator, a ParaView filter on VTK, a Report written by `operator_export`, and a
recipe put in by `recipe_insert`, each read back by `operator_value`."""

from __future__ import annotations

import asyncio

import pytest
import schematic_project as sp

pytest.importorskip("mcp")

from puzzle.agent.mcp import build_server  # noqa: E402
from puzzle.commands import Session  # noqa: E402
from puzzle.project.graph import run_hashes  # noqa: E402


@pytest.fixture
def server(tmp_path):
    root = sp.copy(tmp_path)
    s = Session(root)
    s.submit({"command": "set_autonomy", "level": "act_in_budget"})
    study = sp.study(s.project, "Tip load")
    sp.store_run(root, study, run_hashes(s.project, root)[study.id][study.solver][0])
    srv = build_server(root, "test")
    srv.root, srv.study = root, study
    return srv


def call(server, tool, **arguments):
    result = asyncio.run(server.call_tool(tool, arguments))
    if result.structured_content is not None:
        return result.structured_content.get("result", result.structured_content)
    return result.content[0].text


def outputs(server, name: str) -> dict:
    (row,) = call(server, "operator_value", name=name)["operators"]
    return row["outputs"]


def test_a_field_operator_over_mcp(server):
    """Normalise of the total deformation: a field, its largest value 1."""
    sid = str(server.study.id)
    call(server, "operator_add", type="filter", name="Moved",
         inputs={"result": {"source": sid, "port": "result"}},
         settings={"quantity": "displacement:total"})  # fmt: skip
    out = call(
        server,
        "operator_add",
        type="normalise",
        name="Shape",
        inputs={"series": {"source": "Moved"}},
    )
    assert out["status"] == "applied", out
    got = outputs(server, "Shape")["out"]
    assert got["text"] == "Total deformation (normalised): 5 of 5 nodes", got


def test_a_paraview_filter_over_mcp(server):
    """Glyph (ADR 0070 level 2) on the displacement: an arrow per node, the field's own."""
    sid = str(server.study.id)
    out = call(server, "operator_add", type="glyph", name="Arrows",
               inputs={"result": {"source": sid, "port": "result"}},
               settings={"quantity": "displacement"})  # fmt: skip
    assert out["status"] == "applied", out
    got = outputs(server, "Arrows")["out"]
    assert len(got["rows"]) == 5 and "displacement_y [m]" in got["columns"], got
    tip = got["rows"][1]  # the node at x = 1
    assert tip[got["columns"].index("displacement_y [m]")] == pytest.approx(-1e-3)


def test_a_report_written_by_operator_export(server):
    sid = str(server.study.id)
    call(server, "operator_add", type="reduce", name="Peak",
         inputs={"result": {"source": sid, "port": "result"}},
         settings={"quantity": "displacement:total"})  # fmt: skip
    call(
        server,
        "operator_add",
        type="check",
        name="Stiff enough",
        inputs={"value": {"source": "Peak"}, "limit": {"value": {"value": 2, "unit": "mm"}}},
    )
    out = call(
        server,
        "operator_add",
        type="report",
        name="Tip report",
        inputs={"a": {"source": "Stiff enough"}, "b": {"source": "Peak"}},
    )
    assert out["status"] == "applied", out
    written = call(server, "operator_export", name="Tip report")["written"]
    assert written == ["exports/Tip_report.html"] or written == ["exports\\Tip_report.html"]
    page = (server.root / written[0]).read_text(encoding="utf-8")
    assert "1 of 1 checks pass" in page and "<h2>Peak</h2>" in page


def test_a_recipe_inserted_over_mcp(server):
    sid = str(server.study.id)
    before = {o["name"] for o in call(server, "operator_value")["operators"]}
    out = call(
        server,
        "recipe_insert",
        key="stress_margin",
        bindings={"result": {"source": sid, "port": "result"}},
    )
    assert out["status"] == "applied", out
    after = call(server, "operator_value")["operators"]
    added = [o for o in after if o["name"] not in before]
    assert added and any(o["type"] == "check" for o in added), added
