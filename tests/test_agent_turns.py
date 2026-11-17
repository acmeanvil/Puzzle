"""A turn of the built-in agent (ADR 0090 package 3): Stop closes a streaming reply and
reaches the run a tool started; a reset during a turn leaves a valid conversation; an
empty reply is never kept; a picture the agent made is said truthfully; and the
providers' token totals, dropped streams and fragment keys.
"""

from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path

import pytest

from puzzle.agent.agent import Agent
from puzzle.agent.providers import Reply, ScriptedProvider, ToolCall, to_anthropic

EXAMPLE = Path(__file__).parents[1] / "examples" / "cantilever"


@pytest.fixture
def root(tmp_path):
    dest = tmp_path / "c"
    shutil.copytree(EXAMPLE, dest, ignore=shutil.ignore_patterns("results*", ".puzzle",
                                                                 "history.jsonl"))  # fmt: skip
    return dest


def _valid(messages: list[dict]) -> None:
    """A conversation a provider takes: it starts with the user, no assistant turn is
    empty, and every tool call is answered before the next assistant turn."""
    assert messages[0]["role"] == "user"
    waiting: set[str] = set()
    for m in messages:
        if m["role"] == "assistant":
            assert not waiting, f"unanswered tool calls {waiting}"
            assert m.get("text") or m.get("tool_calls"), "an empty assistant turn"
            waiting = {c.id for c in m.get("tool_calls", [])}
        elif m["role"] == "tool":
            waiting.discard(m["id"])
    for m in to_anthropic(messages):
        assert m["content"], "Anthropic refuses empty content"


class Slow:
    """A provider streaming a long reply a piece every 50 ms."""

    name, model, remote, tool_images = "slow", "slow", False, True

    def __init__(self) -> None:
        self.pieces = 0

    def complete(self, system, messages, tools, on_delta=None):
        for _ in range(200):
            time.sleep(0.05)
            self.pieces += 1
            on_delta("text", "word ")
        return Reply(text="word " * 200)


# --- 5. Stop (AGENT-2) -------------------------------------------------------------------


def test_stop_closes_a_streaming_reply_within_a_piece(root):
    provider, stop = Slow(), threading.Event()
    agent = Agent(root, provider)
    events = []
    threading.Timer(0.3, stop.set).start()
    started = time.monotonic()
    answer = agent.ask("talk", events.append, stop=stop)
    assert time.monotonic() - started < 2.0  # not the 10 s the reply would take
    assert answer == "(stopped)" and provider.pieces < 20
    assert "Stopped." in [e.text for e in events if e.kind == "note"]
    assert [m["role"] for m in agent.messages] == ["user"]  # the half reply never kept
    agent.provider = ScriptedProvider([Reply(text="ok")])
    assert agent.ask("again") == "ok"
    _valid(agent.messages)


def test_stop_reaches_the_run_a_tool_started(root, monkeypatch):
    """The turn's Stop is the cancel of a run the agent's tool started."""
    from puzzle.commands import Session

    Session(root).submit({"command": "set_autonomy", "level": "act_in_budget"})
    seen = {}

    def run_study(project, study_id, root_, workdir, **kwargs):
        cancel = kwargs["cancel"]
        stop.set()  # the user presses Stop while it solves
        seen["cancelled"] = cancel is not None and cancel.is_set()
        from puzzle.errors import PuzzleError

        raise PuzzleError("stopped", code="stopped")

    monkeypatch.setattr("puzzle.solve.run_study", run_study)
    stop = threading.Event()
    call = ToolCall("1", "submit", {"command": {"command": "run_study", "study": "Tip load"}})
    agent = Agent(root, ScriptedProvider([Reply(tool_calls=[call]), Reply(text="done")]))
    agent.ask("run it", stop=stop)
    assert seen["cancelled"] is True
    _valid(agent.messages)


# --- 6. a reset during a turn (AGENT-3) --------------------------------------------------


def test_a_new_chat_during_a_turn_keeps_both_conversations_valid(root):
    """The probe's reproduction: reset() from inside a reply left [assistant, tool,
    assistant] with no user message first."""

    def resetting(messages):
        agent.reset()  # New chat pressed while the model answers
        return Reply(tool_calls=[ToolCall("r1", "problems", {})])

    agent = Agent(root, ScriptedProvider([resetting, Reply(text="ok")]))
    agent.ask("hello")
    assert agent.messages == []  # the new chat starts empty, the old turn kept apart
    agent.provider = ScriptedProvider([Reply(text="fresh")])
    assert agent.ask("hi") == "fresh"
    _valid(agent.messages)
    assert [m["role"] for m in agent.messages] == ["user", "assistant"]


# --- 7. an empty reply (AGENT-4) --------------------------------------------------------


def test_an_empty_reply_is_never_kept(root):
    events = []
    agent = Agent(root, ScriptedProvider([Reply(text=""), Reply(text="second")]))
    assert "empty reply" in agent.ask("hi", events.append)
    assert any("empty reply" in e.text for e in events if e.kind == "error")
    assert all(m["role"] != "assistant" for m in agent.messages)
    assert agent.ask("again") == "second"
    _valid(agent.messages)


# --- 9. pictures (AGENT-6) ---------------------------------------------------------------


def test_a_picture_is_said_truthfully(root, monkeypatch):
    from mcp.server.mcpserver import Image

    png = bytes.fromhex("89504e470d0a1a0a")

    class Chat(ScriptedProvider):
        tool_images = False  # the Chat Completions wire: text only

    agent = Agent(root, Chat([]))

    async def rendered(name, arguments):
        from mcp.types import CallToolResult, ImageContent

        image = Image(data=png, format="png").to_image_content()
        assert isinstance(image, ImageContent)
        return CallToolResult(content=[image])

    monkeypatch.setattr(agent.server, "call_tool", rendered)
    out = agent._call("render", {"study": "Tip load"})
    assert out["image"] is None and out["shown"] == png  # the user's copy only
    assert "not sent pictures" in out["content"]
    agent.provider = ScriptedProvider([])
    out = agent._call("render", {"study": "Tip load"})
    assert out["image"] == png and "attached for you too" in out["content"]


# --- 10. providers (AGENT-8, AGENT-13) --------------------------------------------------


def test_anthropic_totals_count_cached_input(tmp_path):
    pytest.importorskip("anthropic")
    import json
    import threading as th
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from puzzle.agent.providers import MessagesProvider

    body = json.dumps({"id": "m", "type": "message", "role": "assistant", "model": "c",
                       "content": [{"type": "text", "text": "Hi."}], "stop_reason": "end_turn",
                       "usage": {"input_tokens": 12, "output_tokens": 3,
                                 "cache_read_input_tokens": 2000,
                                 "cache_creation_input_tokens": 500}}).encode()  # fmt: skip

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    th.Thread(target=server.serve_forever, daemon=True).start()
    try:
        p = MessagesProvider("c", "sk-ant-api03-test",
                             base_url=f"http://127.0.0.1:{server.server_port}")  # fmt: skip
        reply = p.complete("s", [{"role": "user", "content": "hi"}], [])
    finally:
        server.shutdown()
    assert reply.input_tokens == 12 + 2000 + 500 and reply.output_tokens == 3


def test_a_stream_dropped_part_way_is_a_network_error():
    import socket
    import threading as th

    from puzzle.agent.http import ProviderError
    from puzzle.agent.providers import ChatProvider

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def serve():
        conn, _ = listener.accept()
        conn.recv(65536)
        event = b'data: {"choices": [{"delta": {"content": "Hal"}}]}\n\n'
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                     b"Transfer-Encoding: chunked\r\n\r\n"
                     + f"{len(event):x}\r\n".encode() + event + b"\r\n")  # fmt: skip
        time.sleep(0.2)
        conn.close()  # mid-stream: the chunked body never ends

    th.Thread(target=serve, daemon=True).start()
    p = ChatProvider("m", f"http://127.0.0.1:{listener.getsockname()[1]}/v1")
    pieces = []
    with pytest.raises(ProviderError) as err:
        p.complete("s", [{"role": "user", "content": "hi"}], [],
                   on_delta=lambda k, t: pieces.append(t))  # fmt: skip
    assert err.value.kind == "network" and pieces == ["Hal"]
    listener.close()


def test_tool_call_fragments_without_an_index_are_joined_by_id():
    from puzzle.agent.providers import _fragment_key

    calls: dict = {}
    for part in ({"id": "a", "function": {"name": "outline", "arguments": "{"}},
                 {"function": {"arguments": "}"}},
                 {"id": "b", "function": {"name": "problems", "arguments": "{}"}}):  # fmt: skip
        c = calls.setdefault(_fragment_key(part, calls), {"id": "", "name": "", "arguments": ""})
        c["id"] = part.get("id") or c["id"]
        c["name"] += part["function"].get("name", "")
        c["arguments"] += part["function"].get("arguments", "")
    assert [(c["id"], c["name"], c["arguments"]) for c in calls.values()] == [
        ("a", "outline", "{}"), ("b", "problems", "{}")]  # fmt: skip


def test_a_server_error_is_tried_again(monkeypatch):
    import json
    import threading as th
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from puzzle.agent import http
    from puzzle.agent.providers import ChatProvider

    monkeypatch.setattr(http.time, "sleep", lambda s: None)
    answers = [503, 200]

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            self.rfile.read(int(self.headers["Content-Length"]))
            status = answers.pop(0)
            body = json.dumps({"choices": [{"message": {"content": "Back."}}]} if status == 200
                              else {"error": "overloaded"}).encode()  # fmt: skip
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    th.Thread(target=server.serve_forever, daemon=True).start()
    try:
        p = ChatProvider("m", f"http://127.0.0.1:{server.server_port}/v1")
        assert p.complete("s", [{"role": "user", "content": "hi"}], []).text == "Back."
    finally:
        server.shutdown()
