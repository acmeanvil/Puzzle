"""New analysis (ADR 0090 UX-3): a geometry to a runnable study in one batch, from
`puzzle.new_analysis` (the GUI's form, MCP `analysis_new`, CLI `new-analysis`)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from puzzle.commands import Session
from puzzle.errors import PuzzleError
from puzzle.model.schema import Component, Material, MeshRecipe, Project, Study
from puzzle.new_analysis import FINENESS, commands, mesh_size, unanalysed
from puzzle.project.store import create_project

EXAMPLES = Path(__file__).parents[1] / "examples"


@pytest.fixture
def bar(tmp_path) -> Session:
    """A project holding only the cantilever's imported bar (100 x 10 x 10 mm)."""
    root = tmp_path / "bar"
    create_project(Project(name="Bar"), root)
    shutil.copy(EXAMPLES / "cantilever" / "bar.step", root / "bar.step")
    s = Session(root)
    s.submit({"command": "create", "kind": "geometry", "data": {
        "name": "Bar", "source": "bar.step",
        "selections": [{"name": "Body", "entity": "body", "members": ["body-1"]}]}})  # fmt: skip
    return s


def test_one_batch_makes_a_runnable_static_analysis(bar):
    out = bar.submit_batch(commands(bar.project, ["Bar"], root=bar.root))
    names = {o["kind"]: o["name"] for o in out.created}
    assert names == {"material": "Structural steel", "mesh": "Bar mesh", "component": "Bar",
                     "study": "Bar static structural"}  # fmt: skip
    p = bar.project
    comp = p.of_type(Component)[0]
    study = p.of_type(Study)[0]
    assert [ph.type for ph in comp.physics] == ["solid_mechanics"]
    assert [(a.selection, a.material) for a in comp.materials] == [
        ("Bar/Body", p.of_type(Material)[0].id)
    ]
    assert study.solver == "calculix" and study.steps[0].type == "stationary"
    assert [pr.code for pr in p.problems()] == ["unconstrained"]  # next: a support
    assert unanalysed(p) == []
    bar.undo()  # one undo takes it all back
    assert not bar.project.of_type(Component) and not bar.project.of_type(MeshRecipe)


def test_the_mesh_size_comes_from_the_part(bar):
    """The bar (200 x 10 x 10 mm) has a 200.5 mm diagonal: 20 elements across it is
    10.02 mm, rounded down to 10; 10 is 20.05, 40 is 5.01."""
    from puzzle.model.schema import Geometry

    geo = bar.project.of_type(Geometry)[0]
    sizes = {k: mesh_size(bar.project, [geo], bar.root, k) for k in FINENESS}
    assert sizes == pytest.approx({"coarse": 0.02, "medium": 0.01, "fine": 0.005})


def test_other_analyses_materials_and_refusals(bar):
    out = bar.submit_batch(commands(bar.project, ["Bar"], "modal", "copper", "coarse",
                                    name="Rod", root=bar.root))  # fmt: skip
    study = bar.project.of_type(Study)[0]
    assert study.name == "Rod modal" and study.steps[0].settings["modes"] == 6
    assert bar.project.of_type(Material)[0].name == "Copper"
    again = commands(bar.project, ["Bar"], "steady_heat", "Copper", root=bar.root)
    assert [c["kind"] for c in again] == ["mesh", "component", "study"]  # Copper reused
    assert again[1]["data"]["name"] == "Bar"  # the geometry's name, free again
    for bad, words in ((dict(analysis="cfd"), "analysis is one of"),
                       (dict(fineness="tiny"), "mesh is one of"),
                       (dict(material="unobtainium"), "no material")):  # fmt: skip
        with pytest.raises(PuzzleError, match=words):
            commands(bar.project, ["Bar"], **bad)
    assert out.status == "applied"


def test_the_agent_and_the_cli_reach_it(bar, capsys):
    """Nothing is GUI-only (CLAUDE.md): MCP `analysis_new`, `puzzle new-analysis`, and
    `puzzle redo` beside `undo` (UX-4)."""
    import asyncio

    pytest.importorskip("mcp")
    from puzzle.agent.mcp import build_server
    from puzzle.cli import main

    bar.submit({"command": "set_autonomy", "level": "act_in_budget"})
    server = build_server(bar.root, "test")
    result = asyncio.run(server.call_tool("analysis_new", {"geometry": "Bar",
                                                          "analysis": "buckling"}))  # fmt: skip
    assert not result.is_error, result
    assert {s.name for s in Session(bar.root).project.of_type(Study)} == {"Bar buckling"}
    assert main(["undo", str(bar.root)]) == 0
    assert not Session(bar.root).project.of_type(Study)
    assert main(["redo", str(bar.root)]) == 0
    assert main(["new-analysis", str(bar.root), "Bar", "--analysis", "modal", "--name",
                 "Rod"]) == 0  # fmt: skip
    assert {s.name for s in Session(bar.root).project.of_type(Study)} == {"Bar buckling",
                                                                         "Rod modal"}  # fmt: skip
    assert "Next: a support and a load" in capsys.readouterr().out
