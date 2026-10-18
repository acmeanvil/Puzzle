"""An agent over MCP runs a study within its budget and reads the results (ADR 0015)."""

import asyncio
import json
import shutil
from pathlib import Path

import pytest
from solvers import available

from puzzle.agent.mcp import build_server
from puzzle.commands import Session
from puzzle.materials import library_material

EXAMPLE = Path(__file__).parent.parent.parent / "examples" / "cantilever"
STEEL = library_material("structural_steel").properties
E, NU = STEEL["youngs_modulus"].to_si(), STEEL["poissons_ratio"].to_si()
FORCE, L, A, INERTIA = 100.0, 0.2, 1e-4, 0.01**4 / 12
# Euler-Bernoulli plus shear (Timoshenko, k = 5/6), as test_benchmarks.py
TIP_DEFLECTION = FORCE * L**3 / (3 * E * INERTIA) + FORCE * L / ((5 / 6) * E / (2 * (1 + NU)) * A)

pytestmark = pytest.mark.skipif(not available("calculix"), reason="calculix not available")


def _call(server, tool, **arguments):
    from mcp.server.mcpserver.exceptions import ToolError

    try:
        result = asyncio.run(server.call_tool(tool, arguments))
    except ToolError as exc:
        return {"error": json.loads(str(exc).split(": ", 1)[1])}
    content = result.structured_content
    return content.get("result", content) if content is not None else result.content


def test_agent_runs_within_budget_and_reads_results(tmp_path):
    root = tmp_path / "cantilever"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("results", ".puzzle"))
    Session(root).submit(
        {
            "command": "set_autonomy",
            "level": "act_in_budget",
            "budget": {"runs": 1, "solver_seconds": 600},
        }
    )
    server = build_server(root, "test-agent")

    run = _call(server, "submit", command={"command": "run_study", "study": "Tip load"})
    assert run["status"] == "applied" and not run["data"]["cached"]
    assert run["data"]["summary"][0]["fields"]["displacement"]["max"] > 0

    deflection = _call(
        server, "result_stats", study="Tip load", field="displacement", component="y"
    )
    assert -deflection["min"] == pytest.approx(TIP_DEFLECTION, rel=0.01)
    assert deflection["min_at"]["xyz"][0] == pytest.approx(0.2)  # at the tip

    tip = _call(
        server, "result_value_at", study="Tip load", field="displacement", x=0.2, y=None, z=None
    )
    assert tip["values"][1] == pytest.approx(deflection["min"], rel=0.01)

    root_face = _call(
        server, "result_integral", study="Tip load", field="displacement", selection="Bar/Root"
    )
    assert root_face["mean"] == pytest.approx(0.0, abs=1e-12)  # fixed

    used = _call(server, "autonomy")["used"]
    assert used["runs"] == 1 and used["solver_seconds"] > 0
    again = _call(server, "submit", command={"command": "run_study", "study": "Modes"})
    assert again["error"]["code"] == "budget_exceeded"
