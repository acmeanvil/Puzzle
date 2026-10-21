"""The evaluation suite itself: its checks pass for a correct agent and fail otherwise.

A scripted "perfect" agent solves each task with the calls a good model would make;
a do-nothing agent must fail every task that asks for a change.
"""

import pytest
from solvers import available

from puzzle.agent.evals import TASKS, run_suite, run_task, table
from puzzle.agent.providers import Reply, ScriptedProvider, ToolCall


def _edit(path, value):
    return {
        "command": "edit",
        "object": "Cantilever",
        "ops": [{"op": "set", "path": path, "value": value}],
    }


PERFECT = {
    "describe": [
        Reply(tool_calls=[ToolCall("1", "outline", {})]),
        Reply(text="Conduction, Tip load and Modes, all on calculix."),
    ],
    "edit load": [
        Reply(
            tool_calls=[
                ToolCall(
                    "1",
                    "submit",
                    {
                        "command": _edit(
                            "physics.0.features.1.values.y", {"value": -250, "unit": "N"}
                        )
                    },
                )
            ]
        ),
        Reply(text="Done."),
    ],
    "mesh": [
        Reply(
            tool_calls=[
                ToolCall(
                    "1",
                    "submit",
                    {
                        "command": {
                            "command": "edit",
                            "object": "Tets 2.5 mm",
                            "ops": [
                                {
                                    "op": "set",
                                    "path": "global_size",
                                    "value": {"value": 1.25, "unit": "mm"},
                                }
                            ],
                        }
                    },
                )
            ]
        ),
        Reply(text="Global size now 1.25 mm."),
    ],
    "material": [
        Reply(
            tool_calls=[
                ToolCall(
                    "1",
                    "submit",
                    {"command": {"command": "add_library_material", "key": "aluminium_6061_t6"}},
                )
            ]
        ),
        Reply(text="Added."),
    ],
    "fix mistake": [
        Reply(tool_calls=[ToolCall("1", "problems", {})]),
        Reply(
            tool_calls=[
                ToolCall(
                    "2", "submit", {"command": _edit("physics.0.features.1.selection", "Bar/Tip")}
                )
            ]
        ),
        Reply(text="The force was on 'Bar/Tipp'; it is on Bar/Tip again."),
    ],
    "respect level": [
        Reply(
            tool_calls=[
                ToolCall(
                    "1",
                    "submit",
                    {
                        "command": _edit(
                            "physics.0.features.1.values.y", {"value": -300, "unit": "N"}
                        )
                    },
                )
            ]
        ),
        Reply(text="I may only advise here; set the force to -300 N yourself or raise my level."),
    ],
}


@pytest.mark.parametrize("task", [t for t in TASKS if t.name in PERFECT], ids=lambda t: t.name)
def test_a_correct_agent_passes(task, tmp_path):
    outcome = run_task(task, ScriptedProvider(list(PERFECT[task.name])), tmp_path)
    assert outcome.passed, outcome.detail


def test_a_lazy_agent_fails_the_changes(tmp_path):
    lazy = [t for t in TASKS if t.name in ("edit load", "mesh", "material", "fix mistake")]
    outcomes = run_suite(ScriptedProvider([Reply(text="Sure!")] * 10), tmp_path, lazy)
    assert [o.passed for o in outcomes] == [False] * 4
    assert "| lazy |" in table({"lazy": outcomes})


@pytest.mark.skipif(not available("calculix"), reason="calculix not available")
def test_the_run_task_checks_the_number_against_the_solver(tmp_path):
    task = next(t for t in TASKS if t.name == "run and report")

    def report(messages):
        import json

        # The agent read result_summary; quote the peak it saw, in mm.
        summary = json.loads(messages[-1]["content"])
        peak = summary["steps"][0]["fields"]["displacement"]["max"]  # of the magnitude
        return Reply(text=f"The largest displacement is {peak * 1e3:.4f} mm.")

    script = [
        Reply(
            tool_calls=[
                ToolCall("1", "submit", {"command": {"command": "run_study", "study": "Tip load"}})
            ]
        ),
        Reply(tool_calls=[ToolCall("2", "result_summary", {"study": "Tip load"})]),
        report,
    ]
    outcome = run_task(task, ScriptedProvider(script), tmp_path)
    assert outcome.passed, outcome.detail
    wrong = [*script[:2], Reply(text="About 3 mm.")]
    assert not run_task(task, ScriptedProvider(wrong), tmp_path / "w").passed


def test_hard_checks_tell_right_from_wrong(tmp_path):
    from puzzle.agent.evals import HARD_TASKS

    tasks = {t.name: t for t in HARD_TASKS}
    pressure = {
        "command": "edit",
        "object": "Cantilever",
        "ops": [
            {
                "op": "set",
                "path": "physics.0.features.1",
                "value": {
                    "type": "pressure",
                    "selection": "Bar/Tip",
                    "values": {"p": {"value": 2, "unit": "MPa"}},
                },
            }
        ],
    }
    swap = {
        "command": "edit",
        "object": "Cantilever",
        "ops": [
            {
                "op": "set",
                "path": "physics.1.features.0.values.T",
                "value": {"value": 20, "unit": "degC"},
            },
            {
                "op": "set",
                "path": "physics.1.features.1.values.T",
                "value": {"value": 100, "unit": "degC"},
            },
        ],
    }
    for name, command in (("pressure", pressure), ("swap temperatures", swap)):
        right = [
            Reply(tool_calls=[ToolCall("1", "submit", {"command": command})]),
            Reply(text="Done."),
        ]
        outcome = run_task(tasks[name], ScriptedProvider(right), tmp_path / name)
        assert outcome.passed, outcome.detail
        idle = run_task(
            tasks[name], ScriptedProvider([Reply(text="Done.")]), tmp_path / f"{name}-0"
        )
        assert not idle.passed, idle.detail
