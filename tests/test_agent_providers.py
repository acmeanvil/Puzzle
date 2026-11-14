"""The agent on any remote model (ADR 0084): the catalog, the four wire formats against
recorded responses served by a fake HTTP server on this machine (no key, no network),
the reasoning carries sent back, streaming, the error kinds, agent.toml's migration,
keys per provider and the model lists across providers.

Live checks, one tool round-trip per provider with a key in the environment, run only
with PUZZLE_PROVIDER_TESTS=1 (the maintainer's, never CI's).
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from puzzle.agent import capabilities, catalog
from puzzle.agent.http import ProviderError, classify
from puzzle.agent.providers import (
    ChatProvider,
    GeminiProvider,
    ResponsesProvider,
    ScriptedProvider,
    ToolCall,
    ToolSpec,
    to_anthropic,
)
from puzzle.agent.settings import AgentSettings, load_settings, save_settings

DATA = Path(__file__).parent / "data" / "providers"
TOOLS = [ToolSpec("outline", "The project outline.", {"type": "object", "properties": {}})]
ASK = [{"role": "user", "content": "how many studies?"}]


def recorded(wire: str, name: str) -> bytes:
    return (DATA / wire / name).read_bytes()


class Fake:
    """An HTTP server answering each request with the next (status, body, headers)."""

    def __init__(self, answers: list):
        self.answers, self.requests = list(answers), []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def _answer(self):
                size = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(size) if size else b""
                fake.requests.append({"path": self.path,
                                      "headers": {k.lower(): v for k, v in self.headers.items()},
                                      "body": json.loads(body) if body else None})  # fmt: skip
                status, payload, headers = (fake.answers.pop(0) if fake.answers
                                            else (500, b"{}", {}))  # fmt: skip
                self.send_response(status)
                kind = "text/event-stream" if payload.startswith((b"data:", b"event:")) else (
                    "application/json")  # fmt: skip
                self.send_header("Content-Type", headers.pop("Content-Type", kind))
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_POST = do_GET = _answer  # noqa: N815

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.server.shutdown()


@pytest.fixture
def fake():
    servers = []

    def make(*answers):
        server = Fake([(a[0], a[1], dict(a[2]) if len(a) > 2 else {}) for a in answers])
        servers.append(server)
        return server

    yield make
    for s in servers:
        s.close()


# --- the catalog -----------------------------------------------------------------------


def test_every_catalog_entry_is_complete():
    assert set(catalog.PROVIDERS) >= {"anthropic", "openai", "google", "huggingface",
                                      "thinking_machines", "meta", "nvidia", "local",
                                      "compatible"}  # fmt: skip
    for pid, e in catalog.PROVIDERS.items():
        assert e.id == pid and e.name
        assert e.wire in catalog.WIRES and e.key_header in catalog.KEY_HEADERS
        assert e.models in ("list", "typed")
        if e.remote and pid != "compatible":
            assert e.base_url.startswith("https://") and e.key_env and e.docs, pid
    assert not catalog.PROVIDERS["local"].remote
    assert catalog.PROVIDERS["thinking_machines"].models == "typed"
    # only Anthropic names a stable default: a renamed model can't break one elsewhere
    assert [p for p, e in catalog.PROVIDERS.items() if e.default_model] == ["anthropic"]


def test_which_models_call_tools():
    assert catalog.calls_tools("openai", "gpt-5.1") is True
    assert catalog.calls_tools("openai", "text-embedding-3-small") is False
    assert catalog.calls_tools("openai", "gpt-4o-audio-preview") is False
    assert catalog.calls_tools("nvidia", "meta/llama-3.1-70b-instruct") is True
    assert catalog.calls_tools("local", "nomic-embed-text") is False
    assert catalog.calls_tools("meta", "some-new-model") is None  # probed when chosen


def test_pasted_keys_are_checked_per_provider():
    assert catalog.key_problem("huggingface", "hf_abc") is None
    assert "usually start with 'hf_'" in catalog.key_problem("huggingface", "abc")
    assert "spaces" in catalog.key_problem("nvidia", "nvapi- x")
    assert catalog.key_problem("thinking_machines", "anything") is None  # no known shape


def test_errors_are_classified():
    assert classify(401, "") == "auth" and classify(403, "") == "auth"
    assert classify(429, "rate limit") == "rate_limit"
    assert classify(429, "insufficient_quota") == "quota"
    assert classify(400, "maximum context length is 8192 tokens") == "context"
    assert classify(400, "does not support tools") == "tools"
    assert classify(400, "API key not valid.") == "auth"  # Gemini's way
    assert classify(404, "") == "model" and classify(503, "") == "server"
    e = ProviderError("x", "auth", "nvidia")
    assert "puzzle agent-key set --provider nvidia" in e.plain("NVIDIA")


# --- Chat Completions ------------------------------------------------------------------


def test_chat_text_tool_call_and_key_header(fake):
    f = fake((200, recorded("chat", "tool_call.json")), (200, recorded("chat", "text.json")))
    p = ChatProvider("Qwen/Qwen3-32B", f"{f.url}/v1", "hf_secret", name="huggingface",
                     remote=True, max_output_tokens=500, reasoning="low")  # fmt: skip
    reply = p.complete("system", ASK, TOOLS)
    assert reply.tool_calls == [ToolCall("call_1", "outline", {})]
    assert (reply.input_tokens, reply.output_tokens) == (50, 5)
    sent = f.requests[0]
    assert sent["path"] == "/v1/chat/completions"
    assert sent["headers"]["authorization"] == "Bearer hf_secret"
    assert sent["headers"]["user-agent"] == "puzzle-agent"  # nothing about the user
    assert sent["body"]["max_tokens"] == 500 and sent["body"]["reasoning_effort"] == "low"
    assert p.complete("system", ASK, TOOLS).text == "Three studies."


def test_chat_streams_text_reasoning_and_a_tool_call_in_pieces(fake):
    f = fake((200, recorded("chat", "stream.sse")))
    seen = []
    reply = ChatProvider("m", f"{f.url}/v1").complete(
        "s", ASK, TOOLS, on_delta=lambda k, t: seen.append((k, t))
    )
    assert reply.text == "Looking it up."
    assert reply.tool_calls == [ToolCall("call_7", "outline", {"section": "studies"})]
    assert (reply.input_tokens, reply.output_tokens) == (61, 9)
    assert ("thinking", "Hm.") in seen and ("text", "Looking ") in seen
    assert ("tool", "outline") in seen
    assert f.requests[0]["body"]["stream"] is True


def test_a_server_that_will_not_stream_answers_once(fake):
    f = fake((200, recorded("chat", "text.json")))
    reply = ChatProvider("m", f"{f.url}/v1").complete("s", ASK, TOOLS, on_delta=lambda k, t: None)
    assert reply.text == "Three studies."


@pytest.mark.parametrize(
    ("status", "name", "kind"),
    [(401, "error_auth.json", "auth"), (400, "error_context.json", "context"),
     (429, "error_quota.json", "quota"), (404, "error_model.json", "model"),
     (400, "error_tools.json", "tools"), (502, "error_auth.json", "server")],
)  # fmt: skip
def test_chat_errors_have_a_kind(fake, status, name, kind):
    f = fake((status, recorded("chat", name)))
    with pytest.raises(ProviderError) as e:
        ChatProvider("m", f"{f.url}/v1", "k", name="nvidia").complete("s", ASK, TOOLS)
    assert e.value.kind == kind and e.value.provider == "nvidia"


def test_rate_limits_are_retried_after_the_wait_asked(fake, monkeypatch):
    from puzzle.agent import http

    waits = []
    monkeypatch.setattr(http.time, "sleep", waits.append)
    f = fake((429, recorded("chat", "error_rate.json"), {"Retry-After": "7"}),
             (200, recorded("chat", "text.json")))  # fmt: skip
    assert ChatProvider("m", f"{f.url}/v1").complete("s", ASK, TOOLS).text == "Three studies."
    assert waits == [7.0]
    f = fake(*[(429, recorded("chat", "error_rate.json"))] * 4)
    with pytest.raises(ProviderError) as e:
        ChatProvider("m", f"{f.url}/v1").complete("s", ASK, TOOLS)
    assert e.value.kind == "rate_limit" and len(f.requests) == 4


def test_an_unreachable_provider_is_a_network_error():
    with pytest.raises(ProviderError) as e:
        ChatProvider("m", "http://127.0.0.1:9/v1", timeout_s=2).complete("s", ASK, TOOLS)
    assert e.value.kind == "network"


# --- OpenAI's Responses ----------------------------------------------------------------


def test_responses_carry_the_encrypted_reasoning_back(fake):
    f = fake((200, recorded("responses", "tool_call.json")),
             (200, recorded("responses", "text.json")))  # fmt: skip
    p = ResponsesProvider("gpt-5.1", f"{f.url}/v1", "sk-test", reasoning="medium")
    reply = p.complete("system", ASK, TOOLS)
    assert reply.tool_calls == [ToolCall("call_1", "outline", {})]
    first = f.requests[0]["body"]
    assert first["store"] is False and first["include"] == ["reasoning.encrypted_content"]
    assert first["reasoning"] == {"effort": "medium"} and first["instructions"] == "system"
    assert first["tools"][0] == {"type": "function", "name": "outline",
                                 "description": "The project outline.",
                                 "parameters": {"type": "object", "properties": {}}}  # fmt: skip
    messages = [*ASK, {"role": "assistant", "text": "", "tool_calls": reply.tool_calls,
                       "carry": {"provider": "openai", "data": reply.carry}},
                {"role": "tool", "id": "call_1", "name": "outline", "content": "3 studies",
                 "is_error": False, "image": None}]  # fmt: skip
    assert p.complete("system", messages, TOOLS).text == "Three studies."
    items = f.requests[1]["body"]["input"]
    assert items[1] == {"type": "reasoning", "id": "rs_1", "summary": [],
                        "encrypted_content": "gAAAAABpZ-encrypted-reasoning"}  # fmt: skip
    assert items[2]["type"] == "function_call" and "status" not in items[2]
    assert items[3] == {"type": "function_call_output", "call_id": "call_1",
                        "output": "3 studies"}  # fmt: skip


def test_responses_stream(fake):
    f = fake((200, recorded("responses", "stream.sse")))
    seen = []
    reply = ResponsesProvider("gpt-5.1", f"{f.url}/v1", "sk").complete(
        "s", ASK, TOOLS, on_delta=lambda k, t: seen.append((k, t)))  # fmt: skip
    assert reply.text == "Three studies." and reply.carry[0]["type"] == "reasoning"
    assert ("thinking", "Counting.") in seen and ("text", "studies.") in seen
    assert (reply.input_tokens, reply.output_tokens) == (91, 5)


# --- Gemini ----------------------------------------------------------------------------


def test_gemini_sends_its_thought_signature_back(fake):
    f = fake((200, recorded("gemini", "tool_call.json")), (200, recorded("gemini", "text.json")))
    p = GeminiProvider("gemini-3-pro", f"{f.url}/v1beta", "AIzaTest", reasoning="low")
    reply = p.complete("system", ASK, TOOLS)
    assert [c.name for c in reply.tool_calls] == ["outline"] and reply.text == ""
    assert reply.output_tokens == 26  # thinking counted
    first = f.requests[0]
    assert first["path"] == "/v1beta/models/gemini-3-pro:generateContent"
    assert first["headers"]["x-goog-api-key"] == "AIzaTest"
    assert first["body"]["tools"][0]["functionDeclarations"][0]["parametersJsonSchema"] == {
        "type": "object", "properties": {}}  # fmt: skip
    assert first["body"]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "low"}
    call = reply.tool_calls[0]
    messages = [*ASK, {"role": "assistant", "text": "", "tool_calls": reply.tool_calls,
                       "carry": {"provider": "google", "data": reply.carry}},
                {"role": "tool", "id": call.id, "name": "outline", "content": "3 studies",
                 "is_error": False, "image": None}]  # fmt: skip
    assert p.complete("system", messages, TOOLS).text == "Three studies."
    contents = f.requests[1]["body"]["contents"]
    assert contents[1]["role"] == "model"
    assert contents[1]["parts"][1]["thoughtSignature"] == "CiQBVKhc7signature"
    assert contents[2]["parts"][0]["functionResponse"] == {
        "name": "outline", "response": {"result": "3 studies"}}  # fmt: skip


def test_gemini_streams_and_lists_its_chat_models(fake):
    from puzzle.agent.providers import list_models

    f = fake((200, recorded("gemini", "stream.sse")), (200, recorded("gemini", "models.json")))
    seen = []
    reply = GeminiProvider("gemini-3-pro", f"{f.url}/v1beta", "k").complete(
        "s", ASK, TOOLS, on_delta=lambda k, t: seen.append((k, t)))  # fmt: skip
    assert reply.text == "Three studies." and ("thinking", "Hm.") in seen
    assert f.requests[0]["path"].endswith(":streamGenerateContent?alt=sse")
    assert list_models("gemini", f"{f.url}/v1beta", "k", "x-goog-api-key") == [
        ("gemini-3-pro", None)]  # fmt: skip


def test_a_bad_gemini_key_is_an_auth_error(fake):
    f = fake((400, recorded("gemini", "error_auth.json")))
    with pytest.raises(ProviderError) as e:
        GeminiProvider("gemini-3-pro", f"{f.url}/v1beta", "bad").complete("s", ASK, TOOLS)
    assert e.value.kind == "auth"


# --- Anthropic's Messages --------------------------------------------------------------


def test_anthropic_thinking_blocks_go_back_first_and_signed(fake):
    pytest.importorskip("anthropic")
    from puzzle.agent.providers import MessagesProvider

    f = fake((200, recorded("messages", "tool_call.json")),
             (200, recorded("messages", "text.json")))  # fmt: skip
    p = MessagesProvider("claude-test", "sk-ant-api03-test", base_url=f.url, reasoning="low")
    reply = p.complete("system", ASK, TOOLS)
    assert reply.tool_calls == [ToolCall("toolu_1", "outline", {})]
    assert reply.carry == [{"type": "thinking", "thinking": "Read the outline first.",
                            "signature": "sig-abc"}]  # fmt: skip
    body = f.requests[0]["body"]
    assert body["thinking"] == {"type": "enabled", "budget_tokens": 2048}
    assert body["max_tokens"] > 2048
    messages = [*ASK, {"role": "assistant", "text": "", "tool_calls": reply.tool_calls,
                       "carry": {"provider": "anthropic", "data": reply.carry}},
                {"role": "tool", "id": "toolu_1", "name": "outline", "content": "3",
                 "is_error": False, "image": None}]  # fmt: skip
    assert p.complete("system", messages, TOOLS).text == "Three studies."
    sent = f.requests[1]["body"]["messages"][1]["content"]
    assert sent[0] == {"type": "thinking", "thinking": "Read the outline first.",
                       "signature": "sig-abc"}  # fmt: skip
    assert sent[1]["type"] == "tool_use"


def test_a_carry_goes_only_to_the_provider_that_made_it():
    turn = {
        "role": "assistant",
        "text": "x",
        "tool_calls": [],
        "carry": {"provider": "openai", "data": [{"type": "thinking", "signature": "s"}]},
    }
    assert to_anthropic([turn])[0]["content"] == [{"type": "text", "text": "x"}]


# --- the agent -------------------------------------------------------------------------


def test_the_agent_keeps_each_turns_carry_and_streams(tmp_path):
    from puzzle.agent.agent import Agent
    from puzzle.agent.providers import Reply

    first = Reply(tool_calls=[ToolCall("c1", "outline", {})], carry=["opaque"])
    provider = ScriptedProvider([first, Reply(text="Done.")])
    provider.name = "openai"
    project = _project(tmp_path)
    events = []
    answer = Agent(project, provider, images=False).ask("hi", events.append)
    assert answer == "Done."
    second_call = provider.seen[1]
    assert second_call[1]["carry"] == {"provider": "openai", "data": ["opaque"]}
    assert [e.text for e in events if e.kind == "delta"] == ["Done."]
    usage = next(e for e in events if e.kind == "usage")
    assert usage.data["provider"] == "openai"


def test_a_conversation_too_long_has_its_oldest_tool_results_cut(tmp_path):
    from puzzle.agent.agent import CUT, Agent
    from puzzle.agent.providers import Reply

    calls = {"n": 0}

    def answer(messages):
        calls["n"] += 1
        if calls["n"] == 1 and any(m.get("content") != CUT for m in messages
                                   if m["role"] == "tool"):  # fmt: skip
            raise ProviderError("too long", "context")
        return Reply(text="Done.")

    replies = [Reply(tool_calls=[ToolCall("c1", "outline", {})]),
               Reply(tool_calls=[ToolCall("c2", "outline", {})]), answer, answer]  # fmt: skip
    provider = ScriptedProvider(replies)
    events = []
    agent = Agent(_project(tmp_path), provider, images=False)
    assert agent.ask("hi", events.append) == "Done."
    assert any("oldest tool results were cut" in e.text for e in events if e.kind == "note")
    assert [m["content"] for m in agent.messages if m["role"] == "tool"][0] == CUT


def _project(tmp_path):
    from puzzle.model import Project
    from puzzle.project import save_project

    root = tmp_path / "p"
    save_project(Project(name="P"), root)
    return root


# --- tool calling, probed ---------------------------------------------------------------


def test_a_model_that_wont_call_tools_is_refused_and_remembered():
    from puzzle.agent.providers import Reply

    quiet = ScriptedProvider([Reply(text="It's noon.")])
    quiet.name, quiet.model = "meta", "plain-model"
    with pytest.raises(ProviderError) as e:
        capabilities.check(quiet, ["llama-4-maverick"])
    assert e.value.kind == "tools" and "llama-4-maverick" in str(e.value)
    assert capabilities.known("meta", "plain-model") is False
    keen = ScriptedProvider([Reply(tool_calls=[ToolCall("t", "get_time", {})])])
    keen.name, keen.model = "meta", "keen-model"
    capabilities.check(keen)
    assert capabilities.known("meta", "keen-model") is True


# --- agent.toml ------------------------------------------------------------------------


def test_old_files_migrate_to_local_or_a_compatible_entry(tmp_path):
    old = tmp_path / "agent.toml"
    old.write_text('provider = "openai"\nmodel = "qwen3:8b"\nbase_url = '
                   '"http://localhost:1234/v1"\n', encoding="utf-8")  # fmt: skip
    s = load_settings(old)
    assert (s.provider, s.url()) == ("local", "http://localhost:1234/v1")
    old.write_text('provider = "openai"\nmodel = "gpt-4o"\nbase_url = '
                   '"https://api.groq.com/openai/v1"\napi_key_env = "GROQ_API_KEY"\n',
                   encoding="utf-8")  # fmt: skip
    s = load_settings(old)
    assert s.provider == "compatible:api.groq.com"
    assert (s.url(), s.key_env()) == ("https://api.groq.com/openai/v1", "GROQ_API_KEY")
    assert s.config().key_account == "openai"  # the old keychain entry still found
    save_settings(s, old)
    text = old.read_text(encoding="utf-8")
    assert "format = 2" in text and "[[compatible]]" in text and "base_url = " in text
    again = load_settings(old)
    assert (again.provider, again.url(), again.label()) == (
        "compatible:api.groq.com", "https://api.groq.com/openai/v1", "api.groq.com")  # fmt: skip
    new = tmp_path / "new.toml"  # in the new form, "openai" is OpenAI
    new.write_text('format = 2\nprovider = "openai"\nmodel = "gpt-5.1"\n', encoding="utf-8")
    assert load_settings(new).provider == "openai"


def test_provider_tables_round_trip(tmp_path):
    path = tmp_path / "agent.toml"
    s = AgentSettings(provider="huggingface", model="Qwen/Qwen3-32B", send_images=False)
    cfg = s.config("huggingface")
    cfg.route, cfg.favourites, cfg.reasoning, cfg.send_files = ":groq", ["a/b"], "high", True
    s.config("openai").headers = {"OpenAI-Organization": "org-1"}
    save_settings(s, path)
    back = load_settings(path)
    assert back.config("huggingface").route == ":groq"
    assert back.config("huggingface").favourites == ["a/b"]
    assert back.config("openai").headers == {"OpenAI-Organization": "org-1"}
    assert back.files_allowed("huggingface") is True  # its own
    assert back.files_allowed("nvidia") is False  # the page's default
    assert back.files_allowed("local") is False  # the old send_images, for every provider
    assert AgentSettings().files_allowed("local") is True  # unset: local yes, remote no


def test_build_picks_the_wire_and_says_what_is_missing(monkeypatch):
    for env in ("OPENAI_API_KEY", "GEMINI_API_KEY", "NVIDIA_API_KEY", "HF_TOKEN"):
        monkeypatch.delenv(env, raising=False)
    s = AgentSettings(provider="nvidia", model="meta/llama-3.1-70b-instruct")
    with pytest.raises(ProviderError) as e:
        s.build()
    assert e.value.kind == "auth" and "--provider nvidia" in str(e.value)
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-x")
    p = s.build()
    assert isinstance(p, ChatProvider) and p.remote and p.base_url.endswith("nvidia.com/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    assert isinstance(AgentSettings(provider="openai", model="gpt-5.1").build(), ResponsesProvider)
    monkeypatch.setenv("GEMINI_API_KEY", "AIza")
    assert isinstance(AgentSettings(provider="google", model="g").build(), GeminiProvider)
    monkeypatch.setenv("HF_TOKEN", "hf_x")
    s = AgentSettings(provider="huggingface", model="Qwen/Qwen3-32B")
    s.config("huggingface").route = ":fastest"
    assert s.build().model == "Qwen/Qwen3-32B:fastest"
    with pytest.raises(ProviderError) as e:
        AgentSettings(provider="google").build()  # no default: the chip asks
    assert e.value.kind == "model"
    assert AgentSettings(provider="local", model="m").build().remote is False


def test_keys_per_provider(monkeypatch):
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
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        s = AgentSettings()
        assert s.key_source("nvidia") == "none" and not s.enabled("nvidia")
        settings.store_key("nvidia", "nvapi-keychain")
        assert s.api_key("nvidia") == "nvapi-keychain" and s.enabled("nvidia")
        assert s.api_key("google") is None  # one provider's key isn't another's
        monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-env")
        assert s.key_source("nvidia") == "environment (NVIDIA_API_KEY)"
    finally:
        keyring.set_keyring(previous)


def test_the_chip_lists_every_enabled_providers_models(fake, monkeypatch):
    from puzzle.agent import settings

    hf = fake((200, recorded("chat", "models_hf.json")))
    local = fake((200, recorded("chat", "models_local.json")))
    monkeypatch.setenv("HF_TOKEN", "hf_x")
    monkeypatch.setenv("TINKER_API_KEY", "tml-x")
    for env in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "LLAMA_API_KEY",
                "NVIDIA_API_KEY"):  # fmt: skip
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setattr(settings, "stored_key", lambda _p: None)
    s = AgentSettings(provider="local")
    s.config("huggingface").base_url = f"{hf.url}/v1"
    s.config("huggingface").favourites = ["meta-llama/Llama-3.3-70B-Instruct"]
    s.config("local").base_url = f"{local.url}/v1"
    choices, notes = settings.available_models(s)
    hf_models = [c.model for c in choices if c.provider == "huggingface"]
    assert hf_models == ["meta-llama/Llama-3.3-70B-Instruct", "Qwen/Qwen3-32B",
                         "some/base-model"]  # favourite, tools, may not  # fmt: skip
    tools = {c.model: c.tools for c in choices}
    assert tools["Qwen/Qwen3-32B"] is True and tools["some/base-model"] is False
    assert tools["qwen3:8b"] is True and tools["nomic-embed-text"] is False
    assert {c.provider for c in choices} == {"huggingface", "local"}
    assert any("Thinking Machines" in n and "type a model" in n for n in notes)
    assert hf.requests[0]["headers"]["authorization"] == "Bearer hf_x"


# --- the Settings page -----------------------------------------------------------------


def test_the_settings_page_has_a_row_per_provider_and_adds_endpoints(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QDialog, QLineEdit

    from puzzle.gui.settings_dialog import SettingsDialog

    _app = QApplication.instance() or QApplication([])
    path = tmp_path / "agent.toml"
    dialog = SettingsDialog(agent_path=path)
    assert set(dialog.states) == set(dialog.agent.ids())
    assert dialog.states["local"].text() == "No key needed"
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-x")
    dialog._show_state("nvidia")
    assert dialog.states["nvidia"].text() == "Key from the environment (NVIDIA_API_KEY)"

    class Accepted(QDialog):
        def exec(self):
            return QDialog.DialogCode.Accepted

    name, url, env = QLineEdit("openrouter"), QLineEdit("https://openrouter.ai/api/v1"), QLineEdit(
        "OPENROUTER_API_KEY")  # fmt: skip
    assert dialog.add_endpoint(Accepted(), name, url, env)
    assert "compatible:openrouter" in dialog.states
    dialog._options("huggingface")
    dialog.route.setText(":cheapest")
    dialog.reasoning.setCurrentIndex(dialog.reasoning.findData("high"))
    dialog.provider.setCurrentIndex(dialog.provider.findData("compatible:openrouter"))
    dialog.model.setText("openai/gpt-5.1")
    dialog.save()
    s = load_settings(path)
    assert (s.provider, s.model) == ("compatible:openrouter", "openai/gpt-5.1")
    assert s.config("huggingface").route == ":cheapest"
    assert s.config("huggingface").reasoning == "high"
    assert s.url("compatible:openrouter") == "https://openrouter.ai/api/v1"


# --- live checks -----------------------------------------------------------------------


LIVE = os.environ.get("PUZZLE_PROVIDER_TESTS") == "1"


@pytest.mark.skipif(not LIVE, reason="live provider checks: PUZZLE_PROVIDER_TESTS=1")
@pytest.mark.parametrize("pid", ["anthropic", "openai", "google", "huggingface", "meta",
                                 "nvidia"])  # fmt: skip
def test_live_tool_round_trip(pid):
    """One tool round-trip per provider whose key is in the environment, with the model
    named by PUZZLE_<ID>_MODEL (the providers' own names move)."""
    from puzzle.agent.providers import Reply  # noqa: F401

    s = AgentSettings(provider=pid)
    if not s.api_key(pid):
        pytest.skip(f"no {pid} key in the environment")
    model = os.environ.get(f"PUZZLE_{pid.upper()}_MODEL") or catalog.entry(pid).default_model
    if not model:
        pytest.skip(f"set PUZZLE_{pid.upper()}_MODEL")
    p = s.build(pid, model)
    ask = [{"role": "user", "content": capabilities.PROBE}]
    reply = p.complete("Answer by calling tools.", ask, [capabilities.PROBE_TOOL])
    assert reply.tool_calls, reply.text
    call = reply.tool_calls[0]
    messages = [{"role": "user", "content": capabilities.PROBE},
                {"role": "assistant", "text": reply.text, "tool_calls": reply.tool_calls,
                 "carry": {"provider": pid, "data": reply.carry}},
                {"role": "tool", "id": call.id, "name": call.name, "content": "12:00",
                 "is_error": False, "image": None}]  # fmt: skip
    streamed = []
    final = p.complete("Answer by calling tools.", messages, [capabilities.PROBE_TOOL],
                       on_delta=lambda k, t: streamed.append(k))  # fmt: skip
    assert "12" in final.text, final.text
    print(f"{pid} {model}: {reply.input_tokens}+{final.input_tokens} in, streamed "
          f"{len(streamed)} pieces")  # fmt: skip


# --- measuring remote models (ADR 0084 decision 9) -------------------------------------


def test_remote_models_are_measured_within_the_request_budget(tmp_path):
    from puzzle.agent import pick
    from puzzle.agent.providers import Reply
    from puzzle.agent.settings import ModelChoice

    class Settings(AgentSettings):
        def build(self, provider=None, model=None):
            p = ScriptedProvider([Reply(text="No idea.")] * 100)
            p.name, p.model, p.remote = provider, model, True
            return p

    choices = [ModelChoice("nvidia", "a/one", True, True), ModelChoice("meta", "b/two", True, True)]
    tasks = len(pick.planned())
    assert pick.planned_requests(2) == 2 * tasks * 16
    found = pick.pick_remote(tasks + 1, tmp_path, settings=Settings(), choices=choices)
    assert found.used == tasks + 1  # the first in full, one request of the second
    assert [m.model for m in found.ranked] == ["a/one"]
    assert [m.model for m in found.incomplete] == ["b/two"]  # the budget ran out: not kept
    best = found.ranked[0]
    assert pick.kept_score("a/one") == f"{best.passed}/{best.total}"
    assert pick.kept_score("b/two") == ""
