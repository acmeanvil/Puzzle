import asyncio
import json
import shutil
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402

from puzzle.agent.mcp import build_server  # noqa: E402
from puzzle.commands import Session  # noqa: E402

EXAMPLE = Path(__file__).parent.parent / "examples" / "cantilever"
TIP = {
    "command": "edit",
    "object": "Cantilever",
    "ops": [{"op": "set", "path": "physics.0.features.1.values.y.value", "value": -400}],
}


@pytest.fixture
def root(tmp_path) -> Path:
    dest = tmp_path / "cantilever"
    shutil.copytree(EXAMPLE, dest, ignore=shutil.ignore_patterns("results", ".puzzle"))
    return dest


def _call(server, tool, **arguments):
    result = asyncio.run(server.call_tool(tool, arguments))
    if result.structured_content is not None:
        return result.structured_content.get("result", result.structured_content)
    return result.content[0].text


def _error(server, tool, **arguments) -> dict:
    with pytest.raises(ToolError) as err:
        asyncio.run(server.call_tool(tool, arguments))
    return json.loads(str(err.value).split(": ", 1)[1])


def test_agent_reads_the_model(root):
    server = build_server(root, "test")
    text = _call(server, "outline")
    assert "Component Cantilever" in text and "(agents: propose, mcp: local)" in text
    obj = _call(server, "get_object", name="Cantilever")
    assert obj["kind"] == "component"
    assert "edit" in json.dumps(_call(server, "schema", which="commands"))
    assert _call(server, "problems") == []
    assert {"key": "copper", "name": "Copper"} in _call(server, "materials_library", text="cop")
    assert "solid_mechanics" in _call(server, "capabilities", solver="calculix")["physics"]


def test_agent_changes_are_proposed_and_attributed(root):
    server = build_server(root, "test")
    out = _call(server, "submit", command=TIP, rationale="400 N design load")
    assert out["status"] == "proposed"
    (proposal,) = _call(server, "proposals")
    assert proposal["actor"] == {"kind": "agent", "name": "test"}
    Session(root).accept(proposal["id"])  # a user accepts, outside MCP
    comp = Session(root).project.objects
    loads = [
        c.physics[0].features[1].values["y"].value for c in comp.values() if c.kind == "component"
    ]
    assert loads == [-400]


def test_errors_reach_the_agent_with_codes(root):
    server = build_server(root, "test")
    assert _error(server, "get_object", name="Nope")["code"] == "not_found"
    Session(root).submit({"command": "set_autonomy", "level": "advise"})
    assert _error(server, "submit", command=TIP)["code"] == "not_allowed"
    Session(root).submit({"command": "set_autonomy", "mcp": "off"})
    assert _error(server, "outline")["code"] == "not_allowed"


def test_stdio_round_trip(root):
    """A real MCP client talks to `puzzle mcp` over stdio."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "puzzle.cli", "mcp", str(root), "--name", "stdio-test"],
    )

    async def talk():
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            tools = {t.name for t in (await session.list_tools()).tools}
            outline = await session.call_tool("outline", {})
            proposed = await session.call_tool("submit", {"command": TIP})
            return tools, outline.content[0].text, proposed

    tools, outline, proposed = asyncio.run(talk())
    assert {"outline", "submit", "result_stats", "render"} <= tools
    assert "Study Tip load" in outline
    assert not proposed.is_error
    (entry,) = Session(root).history(limit=1)
    assert entry["type"] == "proposal" and entry["actor"]["name"] == "stdio-test"


def test_submit_takes_a_list_of_edits(root):
    Session(root).submit({"command": "set_autonomy", "level": "act_in_budget"})
    server = build_server(root, "test")
    batch = [
        {"command": "copy_component", "component": "Cantilever", "name": "Heavy"},
        {
            "command": "edit",
            "object": "Heavy",
            "ops": [{"op": "set", "path": "physics.0.features.1.values.y.value", "value": -1000}],
        },
    ]
    out = _call(server, "submit", command=batch, rationale="heavy load case")
    assert out["status"] == "applied"
    (entry,) = Session(root).history(limit=1)
    assert entry["command"]["command"] == "batch"
    assert entry["actor"] == {"kind": "agent", "name": "test"}
