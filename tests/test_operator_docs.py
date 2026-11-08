"""Operators explain themselves, by names a user looks for first (ADR 0071 decisions 1,
6 and 7), to people in the schematic and to agents over MCP alike."""

from __future__ import annotations

import asyncio

import pytest

from puzzle.operators import LABELS, TYPES, doc_text, operator_doc


def test_every_operator_says_what_it_does():
    for key, t in TYPES.items():
        doc = operator_doc(key)
        assert len(doc["purpose"]) > 20, key
        assert doc["name"] == t.label and doc["group"]
        for port in doc["inputs"] + doc["outputs"]:
            assert port["name"] and port["about"].startswith(port["name"]), (key, port)
        assert t.label in doc_text(key)


def test_names_are_unique_and_none_is_another_operators_word():
    labels = {t.label.lower(): k for k, t in TYPES.items()}
    assert len(labels) == len(TYPES), "two operators share a name"
    for key, t in TYPES.items():
        for alias in t.aliases:  # a search word may not be another operator's name
            owner = labels.get(alias.lower())
            assert owner in (None, key), f"'{alias}' of {key} is {owner}'s name"


JARGON = {"reduce", "pivot", "unpivot", "stack", "distinct", "expression", "top n", "divide"}


def test_names_are_in_an_engineers_words_and_old_ones_still_find_them():
    for t in TYPES.values():
        assert t.label.lower() not in JARGON, t.label
    for key, label in LABELS.items():
        assert TYPES[key].label == label
    assert "reduce" in TYPES["reduce"].aliases and "divide" in TYPES["divide"].aliases
    assert TYPES["divide"].label == "Points along"  # not arithmetic
    assert TYPES["series"].label == "Stepped list"  # a series is a plot's data


def test_the_schematic_carries_the_help(tmp_path):
    from puzzle import schematic as sch
    from puzzle.model.schema import Operator, Project

    project = Project(name="p")
    project.add(Operator(name="Most", type="reduce"))
    node = next(n for n in sch.build(project, []).nodes.values() if n.kind == "operator")
    assert node.subtitle == "Result summary" and "Result summary:" in node.help
    port = node.port("result")
    assert port.help.startswith("Result: takes a result or field")


def test_agents_get_the_same_help_over_mcp(tmp_path):
    pytest.importorskip("mcp")
    from puzzle.agent.mcp import build_server
    from puzzle.model.schema import Project
    from puzzle.project import save_project

    save_project(Project(name="p"), tmp_path / "p")
    server = build_server(tmp_path / "p", "test")

    def call(tool, **arguments):
        result = asyncio.run(server.call_tool(tool, arguments))
        return result.structured_content.get("result", result.structured_content)

    every = call("operator_help")["operators"]
    assert len(every) == len(TYPES) and {"type", "name", "group", "purpose"} <= set(every[0])
    found = {o["type"] for o in call("operator_help", search="maximum")["operators"]}
    assert "reduce" in found
    assert "pivot" in {o["type"] for o in call("operator_help", search="pivot")["operators"]}
    card = call("operator_help", type="Check against limit")
    assert card["key"] == "check" and card["example"] and card["inputs"][1]["typed"]
    assert call("operator_help", type="reduce")["name"] == "Result summary"
