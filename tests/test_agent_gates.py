"""Agent gates (ADR 0090 package 3): every solve an agent starts goes through the
session, so the autonomy level and the budget bound it whichever tool started it; the
"agents propose code" gate decides by what a command puts into the project; tools with
an effect outside the model are gated by level.

Solves are stand-ins: the real `run_study` runs to its budget check, then the mesher
(monkeypatched) fails after a moment, as a solve that failed would, so each solve is
counted exactly where a real one is.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402

from puzzle import budget  # noqa: E402
from puzzle.agent.mcp import build_server  # noqa: E402
from puzzle.commands import Actor, Session  # noqa: E402
from puzzle.errors import PuzzleError  # noqa: E402

EXAMPLE = Path(__file__).parent.parent / "examples" / "cantilever"
AGENT = Actor("agent", "test")
PDE = {
    "type": "custom_pde",
    "equation": {"unknowns": [{"name": "u"}], "weak_form": "u * v_u * dx"},
}


@pytest.fixture
def root(tmp_path) -> Path:
    dest = tmp_path / "cantilever"
    shutil.copytree(EXAMPLE, dest, ignore=shutil.ignore_patterns("results*", ".puzzle",
                                                                 "history.jsonl"))  # fmt: skip
    return dest


@pytest.fixture
def solves(monkeypatch):
    """Each solve's mesher, failing after it was counted: the calls made."""
    from puzzle import solve

    calls = []

    def mesher(project, component, root, workdir, *args, **kwargs):
        calls.append(workdir)
        raise PuzzleError("the stand-in mesher refuses", code="mesh_failed")

    monkeypatch.setattr(solve, "generate_mesh", mesher)
    return calls


def _level(root, level, **budget_):
    s = Session(root)
    extra = {"budget": {"runs": 5, "solver_seconds": 600, **budget_}} if budget_ else {}
    s.submit({"command": "set_autonomy", "level": level, **extra})


def _call(server, tool, **arguments):
    result = asyncio.run(server.call_tool(tool, arguments))
    if result.structured_content is not None:
        return result.structured_content.get("result", result.structured_content)
    return result.content[0].text


def _error(server, tool, **arguments) -> dict:
    with pytest.raises(ToolError) as err:
        asyncio.run(server.call_tool(tool, arguments))
    return json.loads(str(err.value).split(": ", 1)[1])


def _optimisation(root, points: int = 5) -> str:
    """An optimisation of the cantilever's tip-load study over a parameter, `points`
    points (each a solve)."""
    from puzzle.model.schema import (
        DesignOutput,
        DesignVariable,
        Operator,
        Optimisation,
        ParameterSet,
        Quantity,
        Study,
    )
    from puzzle.project import load_project, save_project

    p = load_project(root)
    study = next(s for s in p.of_type(Study) if s.name == "Tip load")
    p.add(ParameterSet(name="Load", values={"f": Quantity(value=100, unit="N")}))
    goal = p.add(Operator(name="Half", type="expression", expression="f / N / 2"))
    p.add(Optimisation(name="Sweep F", study=study.id, method="full_factorial", levels=points,
                       variables=[DesignVariable(name="f", low=Quantity(value=50, unit="N"),
                                                 high=Quantity(value=150, unit="N"))],
                       objective=DesignOutput(source=goal.id)))  # fmt: skip
    save_project(p, root)
    return "Sweep F"


# --- 1. every solving tool through the session (AGENT-1, ADR-10, ADR-44) -------------


def test_an_optimisation_stops_when_the_budget_is_spent(root, solves):
    """AGENT-1's reproduction: a budget of 2 runs, an optimisation of 5 points. It used
    to solve all five, uncounted; now the third is refused and two are counted."""
    name = _optimisation(root)
    _level(root, "act_in_budget", runs=2)
    server = build_server(root, "test")
    err = _error(server, "optimisation_run", name=name)
    assert err["code"] == "budget_exceeded"
    assert len(solves) == 2  # two solves began; none after the budget was spent
    s = Session(root)
    assert s.budget_used()["runs"] == 2
    (entry,) = [e for e in s.history_entries() if e["type"] == "command"
                and e["command"]["command"] == "run_optimisation"]  # fmt: skip
    assert entry["actor"]["kind"] == "agent" and entry["result"]["spent"]["solves"] == 2
    from puzzle.project import versions

    assert any(v.trigger == "before_run" for v in versions.versions(root))
    # Spent: the next run of any kind is refused before it starts.
    again = _error(server, "submit", command={"command": "run_study", "study": "Tip load"})
    assert again["code"] == "budget_exceeded" and len(solves) == 2


def test_below_act_a_run_tool_proposes_and_at_advise_refuses(root, solves):
    name = _optimisation(root)
    server = build_server(root, "test")  # the default level: propose
    out = _call(server, "optimisation_run", name=name)
    assert out["status"] == "proposed" and not solves
    out = _call(server, "mesh_convergence", study="Tip load", solve=True)
    assert out["status"] == "proposed"
    out = _call(server, "mesh_adapt", study="Tip load", solve=True)
    assert out["status"] == "proposed"
    out = _call(server, "mesh_shootout", study="Tip load")
    assert out["status"] == "proposed" and not solves
    kinds = [p["commands"][0]["command"] for p in Session(root).proposals()]
    assert kinds == ["run_optimisation", "run_convergence", "run_adapt", "run_mesh_shootout"]
    _level(root, "advise")
    for tool, args in (("optimisation_run", {"name": name}),
                       ("mesh_convergence", {"study": "Tip load", "solve": True}),
                       ("mesh_adapt", {"study": "Tip load", "solve": True}),
                       ("mesh_shootout", {"study": "Tip load"})):  # fmt: skip
        assert _error(server, tool, **args)["code"] == "not_allowed"
    assert not solves


def test_convergence_and_adapt_levels_are_counted_solve_by_solve(root, solves):
    _level(root, "act_in_budget", runs=1)
    server = build_server(root, "test")
    err = _error(server, "mesh_convergence", study="Tip load", levels=3, solve=True)
    assert err["code"] == "budget_exceeded" and len(solves) == 1
    assert Session(root).budget_used()["runs"] == 1
    err = _error(server, "mesh_adapt", study="Tip load", solve=True)  # nothing left
    assert err["code"] == "budget_exceeded" and len(solves) == 1


def test_a_user_accepting_a_proposed_run_runs_it(root, solves):
    name = _optimisation(root, points=2)
    server = build_server(root, "test")
    proposal = _call(server, "optimisation_run", name=name)["proposal"]
    Session(root).accept(proposal)  # a user's run: counted, never budgeted
    assert len(solves) == 2
    assert Session(root).budget_used()["runs"] == 0  # the user's, not the agent's


def test_an_agent_solve_round_the_session_is_refused():
    """The guard behind the gate: inside an agent's tool call, a solve with no gate
    around it (a tool calling run_study itself) is refused."""
    with budget.agent_call(), pytest.raises(PuzzleError) as err:
        budget.begin(None)
    assert err.value.code == "not_allowed"
    assert budget.begin(None) == (None, None)  # anyone else's: no gate needed
    gate = budget.Gate()
    with budget.gated(gate), budget.agent_call():
        budget.begin(None)
    assert gate.solved == 1


def test_a_failed_run_still_spends_its_solve(root, solves):
    """A run that failed after solving began is in the history with what it spent (it
    used to leave no entry, so failing runs were free)."""
    _level(root, "act_in_budget", runs=3)
    s = Session(root)
    with pytest.raises(PuzzleError) as err:
        s.submit({"command": "run_study", "study": "Tip load"}, AGENT)
    assert err.value.code == "mesh_failed"
    assert s.budget_used()["runs"] == 1
    entry = s.history(limit=1)[0]
    assert entry["result"]["failed"]["code"] == "mesh_failed"


# --- 2. the code gate decides by value (BE-9, AGENT-5, ADR-1) -------------------------


@pytest.mark.parametrize(
    "command",
    [
        {"command": "edit", "object": "Cantilever",
         "ops": [{"op": "append", "path": "physics", "value": PDE}]},
        {"command": "edit", "object": "Cantilever",
         "ops": [{"op": "set", "path": "physics.0", "value": PDE}]},
    ],
    ids=["append to physics", "set physics.0"],
)  # fmt: skip
def test_a_pde_put_in_by_any_path_is_proposed(root, command):
    """BE-9's reproduction: these were applied at act_in_budget; the probe's
    `_writes_code` returned False for them."""
    _level(root, "act_in_budget")
    s = Session(root)
    out = s.submit(command, AGENT)
    assert out.status == "proposed"
    from puzzle import trust

    assert not trust.code_texts(Session(root).project)


def test_every_route_for_code_in_is_proposed_below_act(root):
    from puzzle import trust

    s = Session(root)
    users = {"command": "edit", "object": "Cantilever",
             "ops": [{"op": "append", "path": "physics", "value": PDE}]}  # fmt: skip
    s.submit(users)  # the user's own equation
    _level(root, "act_in_budget")
    weak = "physics.-1.equation.weak_form"  # the PDE appended last
    edit = {"command": "edit", "object": "Cantilever",
            "ops": [{"op": "set", "path": weak, "value": "2 * u * v_u * dx"}]}  # fmt: skip
    assert s.submit(edit, AGENT).status == "proposed"
    copy = {"command": "copy_component", "component": "Cantilever", "name": "Twin"}
    assert s.submit(copy, AGENT).status == "proposed"  # a second copy of the code
    made = {
        "command": "create",
        "kind": "operator",
        "data": {"name": "Num", "type": "number", "inputs": {"value": {"value": 1.0}}},
    }
    to_script = {"command": "edit", "object": "Num",
                 "ops": [{"op": "remove", "path": "inputs.value"},
                         {"op": "set", "path": "type", "value": "script"},
                         {"op": "set", "path": "code", "value": "out = 1"}]}  # fmt: skip
    assert s.submit_batch([made, to_script], AGENT).status == "proposed"
    # What doesn't add code applies: renaming the component that holds it.
    rename = {"command": "edit", "object": "Cantilever",
              "ops": [{"op": "set", "path": "name", "value": "Beam"}]}  # fmt: skip
    assert s.submit(rename, AGENT).status == "applied"
    assert sum(trust.code_texts(Session(root).project).values()) == 1
    Session(root).submit({"command": "set_autonomy", "level": "act", "confirm": _token(root)})
    assert s.submit(copy | {"component": "Beam"}, AGENT).status == "applied"


def _token(root) -> str:
    try:
        Session(root).submit({"command": "set_autonomy", "level": "act"})
    except PuzzleError as exc:
        return exc.details["token"]
    raise AssertionError("act needs a second sign-off")


def test_an_undo_or_restore_bringing_code_back_is_the_users(root):
    from puzzle.project import versions

    s = Session(root)
    _level(root, "act_in_budget")
    script = {"command": "create", "kind": "operator",
              "data": {"name": "S", "type": "script", "code": "out = 2"}}  # fmt: skip
    proposed = s.submit(script, AGENT)
    s.accept(proposed.proposal)  # the user reads it and accepts: the agent's change
    held = versions.save(root, how="user", trigger="user", project=Session(root).project,
                         history_seq=0)  # fmt: skip
    deleted = s.submit({"command": "delete", "object": "S"}, AGENT)
    assert deleted.status == "applied"
    with pytest.raises(PuzzleError) as err:
        s.undo(AGENT)  # the delete undone would bring the script back
    assert err.value.code == "not_allowed"
    with pytest.raises(PuzzleError) as err:
        s.restore_version(held.number, AGENT)
    assert err.value.code == "not_allowed"
    s.restore_version(held.number)  # the user may


# --- 3. tools with effects outside the model (AGENT-12) --------------------------------


def test_side_effect_tools_are_refused_at_advise(root):
    _level(root, "advise")
    server = build_server(root, "test")
    for tool, args in (
        ("export", {"study": "Tip load", "format": "vtu", "path": "exports/x"}),
        ("project_archive", {"path": "exports/p.puzzle"}),
        ("materials_export", {"path": "exports/m.json"}),
        ("operator_export", {"name": "Nope"}),
        ("pardiso_build", {}),
        ("host_check", {"name": "lab"}),
        ("jobs_list", {"live": True}),
        ("version_save", {"label": "x"}),
        ("job_cancel", {"job": "j"}),
    ):
        assert _error(server, tool, **args)["code"] == "not_allowed", tool
    assert not (root / "exports").exists()
    _level(root, "propose")
    assert _error(server, "pardiso_build")["code"] == "not_allowed"  # it builds a program
    assert _error(server, "job_cancel", job="j")["code"] == "not_allowed"


def test_render_leaves_no_folder_behind(root, monkeypatch, tmp_path):
    import tempfile

    import puzzle.agent.mcp as mcp

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir()
    server = build_server(root, "test")
    assert _error(server, "render", study="Tip load")["code"] == "not_found"  # no runs
    assert mcp.tempfile is tempfile and not list((tmp_path / "tmp").iterdir())


# --- 8. one session per server (AGENT-7) ---------------------------------------------


def test_four_agent_edits_make_at_most_one_version(root):
    """The probe's reproduction: four edits made three versions, two "opened"."""
    from puzzle.project import versions

    _level(root, "act_in_budget")
    before = len(versions.versions(root))
    server = build_server(root, "test")
    for value in (-110, -120, -130, -140):
        edit = {"command": "edit", "object": "Cantilever",
                "ops": [{"op": "set", "path": "physics.0.features.1.values.y.value",
                         "value": value}]}  # fmt: skip
        assert _call(server, "submit", command=edit)["status"] == "applied"
    made = versions.versions(root)[before:]
    assert len(made) <= 1 and not [v for v in made if v.trigger == "opened"]
