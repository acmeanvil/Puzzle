"""Agent skills (ADR 0057): each finished feature's how-to for agents, listed in the
built-in agent's prompt and read through MCP. The files must parse, name a real ADR,
and name only tools the MCP server has."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from puzzle.agent import skills

ADRS = Path(__file__).resolve().parents[1] / "docs" / "adr"
# ADRs whose finished work an agent uses: each must have a skill (ADR 0057's rule).
WITH_SKILLS = {"0050", "0055"}


def _tools(tmp_path) -> set[str]:
    pytest.importorskip("mcp")
    from puzzle.agent.mcp import build_server
    from puzzle.model.schema import Project
    from puzzle.project import save_project

    save_project(Project(name="p"), tmp_path / "p")
    server = build_server(tmp_path / "p", "test")
    return {t.name for t in asyncio.run(server.list_tools())}


def test_every_skill_parses_and_names_its_adr():
    found = skills.skills()
    assert found, "no skills"
    for s in found.values():
        assert re.fullmatch(r"[a-z][a-z0-9-]*", s.name), s.name
        assert len(s.description) < 200 and s.body.strip()
        assert list(ADRS.glob(f"{s.adr}-*.md")), f"{s.name}: no ADR {s.adr}"
    assert {s.adr for s in found.values()} >= WITH_SKILLS


def test_skills_name_only_real_tools(tmp_path):
    tools = _tools(tmp_path)
    for s in skills.skills().values():
        names = "|".join(sorted(tools))
        called = set(re.findall(r"`([a-z_]+)\(", s.body)) | set(re.findall(rf"`({names})`", s.body))
        unknown = {t for t in re.findall(r"`([a-z]+_[a-z_]+)\(", s.body)} - tools
        assert not unknown, f"{s.name} names tools that don't exist: {unknown}"
        assert called <= tools | {"outline"}, f"{s.name}: {called - tools}"


def test_the_agent_lists_them_and_mcp_reads_them(tmp_path):
    pytest.importorskip("mcp")
    from puzzle.agent.agent import SYSTEM
    from puzzle.agent.mcp import build_server
    from puzzle.model.schema import Project
    from puzzle.project import save_project

    for s in skills.skills().values():
        assert f"- {s.name}: {s.description}" in SYSTEM
    save_project(Project(name="p"), tmp_path / "p")
    server = build_server(tmp_path / "p", "test")

    def call(tool, **arguments):
        result = asyncio.run(server.call_tool(tool, arguments))
        return result.structured_content.get("result", result.structured_content)

    listed = call("skills_list")["skills"]
    assert {"bolts", "beam-sections"} <= {s["name"] for s in listed}
    assert call("skill_read", name="bolts")["skill"].startswith("# bolts (ADR 0050)")
    found = call("sections_find", standard="aisc", family="W", contains="W12X2")
    assert {"W12X26", "W12X22"} <= {s["designation"] for s in found["sections"]}
