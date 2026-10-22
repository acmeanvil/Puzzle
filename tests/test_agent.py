"""The built-in agent: its loop, autonomy handling, and provider wire formats.

Scripted replies stand in for a model, so these run anywhere; a live Claude test runs
only with ANTHROPIC_API_KEY and PUZZLE_LIVE_AGENT_TESTS=1.
"""

import json
import os
import shutil
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from puzzle.agent.agent import Agent
from puzzle.agent.providers import (
    OpenAIProvider,
    Reply,
    ScriptedProvider,
    ToolCall,
    to_anthropic,
    to_openai,
)
from puzzle.agent.settings import AgentSettings, load_settings
from puzzle.commands import Session
from puzzle.commands.model import SetAutonomy

EXAMPLE = Path(__file__).parents[1] / "examples" / "cantilever"
SET_TIP_LOAD = {
    "command": "edit",
    "object": "Cantilever",
    "ops": [
        {
            "op": "set",
            "path": "physics.0.features.1.values.y",
            "value": {"value": -250, "unit": "N"},
        }
    ],
}


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "cantilever"
    shutil.copytree(
        EXAMPLE, root, ignore=shutil.ignore_patterns("results", ".puzzle", "history.jsonl")
    )
    return root


def _tip_load(root):
    comp = next(o for o in Session(root).project.objects.values() if o.name == "Cantilever")
    return comp.physics[0].features[1].values["y"].value


def test_reads_then_proposes_at_the_default_level(project):
    provider = ScriptedProvider(
        [
            Reply(tool_calls=[ToolCall("1", "outline", {})]),
            Reply(
                text="I'll raise the tip load.",
                tool_calls=[
                    ToolCall("2", "submit", {"command": SET_TIP_LOAD, "rationale": "asked"})
                ],
            ),
            Reply(text="Proposed: tip load -250 N, waiting for your review."),
        ]
    )
    events = []
    answer = Agent(project, provider).ask("Make the tip load 250 N downwards", events.append)

    assert answer == "Proposed: tip load -250 N, waiting for your review."
    outline = provider.seen[1][-1]
    assert outline["role"] == "tool" and 'Project "Cantilever"' in outline["content"]
    assert json.loads(provider.seen[2][-1]["content"])["status"] == "proposed"
    assert _tip_load(project) == -100  # nothing changed until the user accepts
    (proposal,) = Session(project).proposals()
    assert proposal["actor"] == {"kind": "agent", "name": "built-in"}
    Session(project).accept(proposal["id"])
    assert _tip_load(project) == -250
    assert [e.kind for e in events].count("tool_call") == 2


def test_acts_when_allowed_and_is_refused_when_advising(project):
    s = Session(project)
    s.submit(SetAutonomy(level="act_in_budget"))
    calls = [
        Reply(tool_calls=[ToolCall("1", "submit", {"command": SET_TIP_LOAD})]),
        Reply(text="done"),
    ]
    Agent(project, ScriptedProvider(calls)).ask("set it")
    assert _tip_load(project) == -250
    entry = Session(project).history(limit=1)[0]
    assert entry["actor"]["name"] == "built-in" and entry["type"] == "command"

    Session(project).submit(SetAutonomy(level="advise"))
    provider = ScriptedProvider(
        [Reply(tool_calls=[ToolCall("1", "submit", {"command": SET_TIP_LOAD})]), Reply(text="ok")]
    )
    Agent(project, provider).ask("set it again")
    refused = provider.seen[1][-1]
    assert refused["is_error"] and json.loads(refused["content"])["code"] == "not_allowed"


def test_bad_calls_come_back_as_errors(project):
    provider = ScriptedProvider(
        [
            Reply(
                tool_calls=[
                    ToolCall("1", "get_object", {"name": "Nope"}),
                    ToolCall("2", "no_such_tool", {}),
                    ToolCall("3", "outline", {"_unparsed": "{oops"}),
                ]
            ),
            Reply(text="sorry"),
        ]
    )
    Agent(project, provider).ask("look")
    results = provider.seen[1][-3:]
    assert all(r["is_error"] for r in results)
    assert json.loads(results[0]["content"])["code"] == "not_found"
    assert "no tool" in results[1]["content"] and "not valid JSON" in results[2]["content"]


def test_rounds_are_bounded(project):
    provider = ScriptedProvider(
        [Reply(tool_calls=[ToolCall(str(i), "problems", {})]) for i in range(10)]
    )
    answer = Agent(project, provider, max_rounds=3).ask("loop")
    assert "stopped after 3 tool rounds" in answer


def test_pictures_stay_local_unless_allowed(project):
    class Remote(ScriptedProvider):
        remote = True

    assert "render" not in {t.name for t in Agent(project, Remote([])).tools}
    assert "render" in {t.name for t in Agent(project, ScriptedProvider([])).tools}
    assert AgentSettings().images_allowed(Remote([])) is False
    assert AgentSettings(send_images=True).images_allowed(Remote([])) is True


def test_message_formats():
    messages = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "text": "",
            "tool_calls": [ToolCall("a", "outline", {}), ToolCall("b", "problems", {})],
        },
        {
            "role": "tool",
            "id": "a",
            "name": "outline",
            "content": "tree",
            "is_error": False,
            "image": b"png",
        },
        {
            "role": "tool",
            "id": "b",
            "name": "problems",
            "content": "[]",
            "is_error": False,
            "image": None,
        },
    ]
    claude = to_anthropic(messages)
    assert [m["role"] for m in claude] == ["user", "assistant", "user"]
    results = claude[2]["content"]
    assert [r["tool_use_id"] for r in results] == ["a", "b"]
    assert results[0]["content"][1]["type"] == "image"
    oai = to_openai("sys", messages)
    assert [m["role"] for m in oai] == ["system", "user", "assistant", "tool", "tool"]
    assert oai[2]["tool_calls"][0]["function"] == {"name": "outline", "arguments": "{}"}
    assert "cannot show it" in oai[3]["content"]


def test_openai_compatible_endpoint(project):
    """A local endpoint (as Ollama or llama.cpp serve) driving one tool round."""
    requests = []
    replies = [
        {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "c1",
                                "type": "function",
                                "function": {"name": "outline", "arguments": "{}"},
                            }
                        ],
                    }
                }
            ],
            "usage": {"prompt_tokens": 50, "completion_tokens": 5},
        },
        {"choices": [{"message": {"content": "Three studies."}}], "usage": {}},
    ]

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            body = json.dumps(replies[len(requests) - 1]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        provider = OpenAIProvider("local-model", f"http://127.0.0.1:{server.server_port}/v1")
        assert provider.remote is False
        agent = Agent(project, provider)
        assert agent.ask("how many studies?") == "Three studies."
    finally:
        server.shutdown()
    assert requests[0]["model"] == "local-model"
    assert {t["function"]["name"] for t in requests[0]["tools"]} >= {"outline", "submit"}
    assert requests[1]["messages"][-1]["role"] == "tool"
    assert 'Project "Cantilever"' in requests[1]["messages"][-1]["content"]
    assert agent.tokens == {"input": 50, "output": 5}


def test_settings_file_and_environment(tmp_path, monkeypatch):
    path = tmp_path / "agent.toml"
    path.write_text('provider = "openai"\nmodel = "qwen3:8b"\nsend_images = true\n')
    s = load_settings(path)
    assert (s.provider, s.model, s.send_images) == ("openai", "qwen3:8b", True)
    monkeypatch.setenv("PUZZLE_AGENT_MODEL", "llama3.2")
    assert load_settings(path).model == "llama3.2"
    assert isinstance(AgentSettings(provider="openai", model="m").build(), OpenAIProvider)


@pytest.mark.skipif(
    not (os.environ.get("ANTHROPIC_API_KEY") and os.environ.get("PUZZLE_LIVE_AGENT_TESTS")),
    reason="live Claude test: set ANTHROPIC_API_KEY and PUZZLE_LIVE_AGENT_TESTS=1",
)
def test_claude_answers_from_the_outline(project):
    agent = Agent(project, AgentSettings(provider="anthropic").build())
    answer = agent.ask("Which studies does this project have? Reply with their names only.")
    assert all(name in answer for name in ("Conduction", "Tip load", "Modes"))


def test_api_keys_come_from_the_environment_then_the_keychain(monkeypatch):
    import keyring
    from keyring.backend import KeyringBackend

    from puzzle.agent import settings

    class Memory(KeyringBackend):
        priority = 1
        store: dict = {}

        def get_password(self, service, user):
            return self.store.get((service, user))

        def set_password(self, service, user, password):
            self.store[service, user] = password

        def delete_password(self, service, user):
            del self.store[service, user]

    previous = keyring.get_keyring()
    keyring.set_keyring(Memory())
    try:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        s = AgentSettings(provider="anthropic")
        assert s.api_key() is None and s.key_source() == "none"
        settings.store_key("anthropic", "sk-from-keychain")
        assert s.api_key() == "sk-from-keychain" and s.key_source() == "keychain"
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-env")
        assert s.api_key() == "sk-from-env" and s.key_source() == "environment (ANTHROPIC_API_KEY)"
        assert settings.delete_key("anthropic") and not settings.delete_key("anthropic")
    finally:
        keyring.set_keyring(previous)


def test_wrong_pastes_are_caught_before_storing():
    from puzzle.agent.settings import anthropic_key_problem

    good = "sk-ant-api03-" + "x" * 90
    assert anthropic_key_problem(good) is None
    assert anthropic_key_problem("sk-ant-usr-" + "y" * 90) is None  # Anthropic decides
    assert "more than one key" in anthropic_key_problem(good + good)
    assert "start with 'sk-ant-'" in anthropic_key_problem("abc123")


def test_key_header_choice():
    from puzzle.agent.providers import key_auth

    assert key_auth("sk-ant-api03-x") == "api_key"
    assert key_auth("sk-ant-usr-x") == "auth_token"
