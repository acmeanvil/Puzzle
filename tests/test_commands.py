import shutil
from pathlib import Path

import pytest

from puzzle.commands import Actor, Session, command_schema, outline
from puzzle.errors import PuzzleError
from puzzle.model.schema import Component, Material
from puzzle.project.store import load_project

EXAMPLE = Path(__file__).parent.parent / "examples" / "cantilever"
AGENT = Actor("agent", "test-agent")
FORCE = "physics.0.features.1.values.y.value"  # the tip load's y value, in N


@pytest.fixture
def root(tmp_path) -> Path:
    dest = tmp_path / "cantilever"
    shutil.copytree(EXAMPLE, dest, ignore=shutil.ignore_patterns("results", ".puzzle"))
    return dest


def _load(session: Session) -> float:
    comp = next(iter(session.project.of_type(Component)))
    return comp.physics[0].features[1].values["y"].value


def _set_level(session: Session, level: str, **extra) -> None:
    session.submit({"command": "set_autonomy", "level": level, **extra})


def _edit(value: float) -> dict:
    return {
        "command": "edit",
        "object": "Cantilever",
        "ops": [{"op": "set", "path": FORCE, "value": value}],
    }


def test_user_edit_is_saved_logged_and_undoable(root):
    s = Session(root)
    out = s.submit(_edit(-250))
    assert out.status == "applied" and out.changed[0]["name"] == "Cantilever"
    assert _load(Session(root)) == -250  # saved to disk
    (entry,) = s.history(limit=1)
    assert entry["actor"] == {"kind": "user", "name": ""} and entry["undone"] is False

    s.undo()
    assert _load(Session(root)) == -100
    s.undo()  # undoing the undo redoes the edit
    assert _load(Session(root)) == -250


def test_create_and_delete(root):
    s = Session(root)
    out = s.submit({"command": "add_library_material", "key": "copper"})
    assert out.created[0]["kind"] == "material"
    s.submit({"command": "delete", "object": "Copper"})
    assert {m.name for m in load_project(root).of_type(Material)} == {"Structural steel"}
    with pytest.raises(PuzzleError) as err:
        s.submit({"command": "delete", "object": "Structural steel"})
    assert err.value.code == "inconsistent" and err.value.details["used_by"] == ["Cantilever"]


def test_errors_are_structured(root):
    s = Session(root)
    with pytest.raises(PuzzleError) as err:
        s.submit(_edit("heavy"))
    assert err.value.code == "invalid" and err.value.details["errors"]
    with pytest.raises(PuzzleError) as err:
        s.submit(
            {
                "command": "edit",
                "object": "Cantilever",
                "ops": [{"op": "set", "path": "physics.9.type", "value": "x"}],
            }
        )
    assert err.value.code == "not_found"
    with pytest.raises(PuzzleError) as err:
        s.submit({"command": "delete", "object": "Nothing"})
    assert err.value.code == "not_found" and "Cantilever" in err.value.details["names"]
    with pytest.raises(PuzzleError) as err:
        s.submit({"command": "launch"})
    assert err.value.code == "invalid"
    assert _load(Session(root)) == -100  # nothing changed


def test_problems_have_codes(root):
    s = Session(root)
    out = s.submit(
        {
            "command": "edit",
            "object": "Cantilever",
            "ops": [{"op": "set", "path": "materials.0.selection", "value": "Bar/Nope"}],
        }
    )
    assert [p.code for p in out.problems] == ["unknown_selection"]
    assert out.problems[0].details == {"selection": "Bar/Nope"}


def test_agent_proposes_by_default_and_a_user_decides(root):
    s = Session(root)
    out = s.submit(_edit(-300), AGENT, rationale="design load is 300 N")
    assert out.status == "proposed"
    assert _load(Session(root)) == -100  # not applied
    assert out.data["preview"]["changed"][0]["name"] == "Cantilever"
    (proposal,) = s.proposals()
    assert proposal["rationale"] == "design load is 300 N"

    with pytest.raises(PuzzleError) as err:
        s.accept(proposal["id"], AGENT)
    assert err.value.code == "not_allowed"

    s.accept(proposal["id"])
    assert _load(Session(root)) == -300 and not s.proposals()
    applied = next(e for e in s.history() if e["type"] == "command")
    assert applied["actor"] == AGENT.to_dict() and applied["approved_by"]["kind"] == "user"

    second = s.submit(_edit(-1), AGENT)
    s.reject(second.proposal, reason="no")
    assert _load(Session(root)) == -300 and not s.proposals()


def test_advise_refuses_changes_and_agents_cannot_change_autonomy(root):
    s = Session(root)
    _set_level(s, "advise")
    for command in (_edit(-1), {"command": "set_autonomy", "level": "act"}):
        with pytest.raises(PuzzleError) as err:
            s.submit(command, AGENT)
        assert err.value.code == "not_allowed"


def test_act_in_budget(root):
    s = Session(root)
    _set_level(s, "act_in_budget", budget={"runs": 0, "solver_seconds": 60})
    assert s.submit(_edit(-50), AGENT).status == "applied"
    with pytest.raises(PuzzleError) as err:
        s.submit({"command": "run_study", "study": "Tip load"}, AGENT)
    assert err.value.code == "budget_exceeded"


def test_act_needs_double_sign_off(root):
    s = Session(root)
    with pytest.raises(PuzzleError) as err:
        _set_level(s, "act")
    assert err.value.code == "confirmation_required"
    token = err.value.details["token"]
    assert Session(root).project.agent.level == "propose"
    _set_level(s, "act", confirm=token)
    assert Session(root).project.agent.level == "act"
    kinds = [e["type"] for e in s.history_entries()]
    assert kinds == ["confirmation_request", "command"]  # both sign-offs are recorded

    _set_level(s, "propose")
    with pytest.raises(PuzzleError) as err:
        _set_level(s, "act", confirm=token)  # a token works once
    assert err.value.code == "confirmation_required"


def test_agents_undo_only_their_own_and_never_over_later_edits(root):
    s = Session(root)
    _set_level(s, "act_in_budget")
    agent_edit = s.submit(_edit(-10), AGENT)
    s.submit(_edit(-20))  # the user edits the same object afterwards
    with pytest.raises(PuzzleError) as err:
        s.undo(AGENT, seq=agent_edit.seq)
    assert err.value.code == "not_undoable" and err.value.details["objects"] == ["Cantilever"]
    with pytest.raises(PuzzleError) as err:
        s.undo(AGENT, seq=agent_edit.seq + 1)
    assert err.value.code == "not_allowed"


def test_sessions_see_each_others_changes(root):
    gui, agent = Session(root), Session(root)
    _set_level(gui, "act_in_budget")
    agent.submit(_edit(-70), AGENT)
    gui.submit({"command": "add_library_material", "key": "air"})
    assert _load(gui) == -70  # the GUI session reloaded before applying its own change


def test_preview_changes_nothing(root):
    s = Session(root)
    view = s.preview([_edit(-5), {"command": "run_study", "study": "Tip load"}])
    assert view["changed"][0]["name"] == "Cantilever" and view["steps"][1]["would_run"]
    assert _load(Session(root)) == -100 and not s.history_entries()


def test_outline_and_schema(root):
    text = outline(Session(root).project)
    assert "Component Cantilever" in text and "force on Bar/Tip y=-100 N" in text
    schema = command_schema()
    tags = {k for k in schema["discriminator"]["mapping"]}
    assert {"create", "edit", "delete", "run_study", "set_autonomy"} <= tags
