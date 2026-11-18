"""Model providers for the built-in agent (ADR 0015, ADR 0084): four wire formats.

A provider turns the conversation plus the tool list into one model reply: text,
tool calls, or both. The conversation is kept provider-neutral:

    {"role": "user", "content": "...", "attachments": [Attachment.block(), ...]}
    {"role": "assistant", "text": "...", "tool_calls": [ToolCall, ...],
     "carry": {"provider": id, "data": ...}}
    {"role": "tool", "id": ..., "name": ..., "content": "...", "image": b"..." | None,
     "is_error": bool}

An assistant turn's `carry` is what its provider must be sent back unchanged with the
tool results that follow (ADR 0084 decision 2): Anthropic's thinking blocks with their
signatures, OpenAI's output items with their encrypted reasoning, Gemini's parts with
their thought signatures. It is sent only to the provider that made it (switching models
starts a new chat anyway).

- MessagesProvider: Anthropic's Messages API (`anthropic` package).
- ResponsesProvider: OpenAI's Responses API, `store: false` with the reasoning kept
  encrypted in the carry; standard library.
- GeminiProvider: Gemini's generateContent; standard library.
- ChatProvider: Chat Completions, for Hugging Face, Meta, NVIDIA, Thinking Machines,
  local servers (Ollama, llama.cpp, LM Studio) and any compatible endpoint.
- ScriptedProvider: replays fixed replies, for tests and the evaluation suite.

`complete(..., on_delta=)` streams where the wire can: `on_delta(kind, text)` with kind
"text" (reply text as it comes), "thinking" (reasoning text, or "" while it runs
unseen) or "tool" (a tool call's name as its arguments arrive). A proxy that won't
stream gets one response (`http.post_stream`).
"""

from __future__ import annotations

import base64
import json
import os
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from puzzle.agent.http import ProviderError, get_json, headers_for, post_json, post_stream

__all__ = ["ProviderError"]
Delta = Callable[[str, str], None] | None
REASONING_BUDGET = {"low": 2048, "medium": 8192, "high": 24576}  # Anthropic thinking tokens


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Reply:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    carry: Any = None  # sent back to this provider with the next request


@dataclass
class ToolSpec:
    name: str
    description: str
    schema: dict[str, Any]


class Provider(Protocol):
    name: str
    model: str
    remote: bool  # whether data leaves the machine

    def complete(self, system: str, messages: list[dict], tools: list[ToolSpec],
                 on_delta: Delta = None) -> Reply: ...  # fmt: skip


def _carried(m: dict, provider: str):
    """An assistant turn's carry, when it came from this provider."""
    c = m.get("carry")
    return c["data"] if isinstance(c, dict) and c.get("provider") == provider else None


def _arguments(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        out = json.loads(raw or "{}")
    except ValueError:
        return {"_unparsed": raw}  # reported back to the model as an error
    return out if isinstance(out, dict) else {"_unparsed": raw}


def _remote(url: str) -> bool:
    host = urllib.request.urlparse(url).hostname or ""
    return host not in ("localhost", "127.0.0.1", "::1")


# --- Anthropic's Messages ------------------------------------------------------------


class MessagesProvider:
    """Claude through the Messages API. With `reasoning` set, extended thinking: its
    blocks (signed) go back with the tool results that follow, as Anthropic requires."""

    remote = True
    tool_images = True  # a tool's picture goes with its result

    def __init__(self, model: str = "claude-sonnet-5-5", api_key: str | None = None,
                 auth: str | None = None, *, name: str = "anthropic", base_url: str = "",
                 max_output_tokens: int = 0, reasoning: str = "") -> None:  # fmt: skip
        try:
            import anthropic  # noqa: F401 - only checking that it is installed
        except ImportError as exc:
            raise ProviderError(
                "the Claude provider needs the anthropic package: pip install anthropic"
            ) from exc
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise ProviderError("no Claude API key: run `puzzle agent-key set` or set "
                                "ANTHROPIC_API_KEY", "auth", name)  # fmt: skip
        self.name, self.model = name, model
        self.max_output_tokens, self.reasoning = max_output_tokens or 8192, reasoning
        self.client = anthropic_client(key, auth or key_auth(key), base_url)

    def complete(self, system: str, messages: list[dict], tools: list[ToolSpec],
                 on_delta: Delta = None) -> Reply:  # fmt: skip
        import anthropic

        specs = [
            {"name": t.name, "description": t.description, "input_schema": t.schema} for t in tools
        ]
        if specs:  # the system prompt and tools repeat every turn: cache them
            specs[-1]["cache_control"] = {"type": "ephemeral"}
        args: dict[str, Any] = dict(
            model=self.model, max_tokens=self.max_output_tokens, tools=specs,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=to_anthropic(messages, self.name),
        )  # fmt: skip
        budget = REASONING_BUDGET.get(self.reasoning)
        if budget:
            args["thinking"] = {"type": "enabled", "budget_tokens": budget}
            args["max_tokens"] = budget + self.max_output_tokens
        try:
            if on_delta is None:
                response = self.client.messages.create(**args)
            else:
                with self.client.messages.stream(**args) as stream:
                    for event in stream:
                        _anthropic_delta(event, on_delta)
                    response = stream.get_final_message()
        except anthropic.APIStatusError as exc:
            from puzzle.agent.http import classify

            raise ProviderError(f"Claude: {exc}", classify(exc.status_code, str(exc)),
                                self.name, exc.status_code) from exc  # fmt: skip
        except anthropic.APIConnectionError as exc:
            raise ProviderError(f"Claude: {exc}", "network", self.name) from exc
        except anthropic.APIError as exc:
            raise ProviderError(f"Claude: {exc}", "", self.name) from exc
        usage = response.usage
        # Anthropic's input_tokens leaves out what was read from or written to the cache
        # (ADR 0090, AGENT-8): the prompt's whole size is the three together.
        cached = sum(
            getattr(usage, k, None) or 0
            for k in ("cache_read_input_tokens", "cache_creation_input_tokens")
        )
        reply = Reply(input_tokens=usage.input_tokens + cached,
                      output_tokens=usage.output_tokens)  # fmt: skip
        thinking = []
        for block in response.content:
            if block.type == "text":
                reply.text += block.text
            elif block.type == "tool_use":
                reply.tool_calls.append(ToolCall(block.id, block.name, dict(block.input)))
            elif block.type in ("thinking", "redacted_thinking"):
                thinking.append(block.model_dump(exclude_none=True))
        reply.carry = thinking or None
        return reply


AnthropicProvider = MessagesProvider  # its name before ADR 0084


def _anthropic_delta(event, on_delta) -> None:
    kind = getattr(event, "type", "")
    if kind == "content_block_start":
        block = event.content_block
        if block.type == "tool_use":
            on_delta("tool", block.name)
        elif block.type in ("thinking", "redacted_thinking"):
            on_delta("thinking", "")
    elif kind == "content_block_delta":
        d = event.delta
        if d.type == "text_delta":
            on_delta("text", d.text)
        elif d.type == "thinking_delta":
            on_delta("thinking", d.thinking)


def key_auth(key: str) -> str:
    """How a key is sent: "api_key" (x-api-key header) for classic sk-ant-api keys,
    "auth_token" (Authorization: Bearer) otherwise; `check_anthropic_key` decides
    for real when a key is stored."""
    return "api_key" if key.startswith("sk-ant-api") else "auth_token"


def anthropic_client(key: str, auth: str, base_url: str = ""):
    import anthropic

    from puzzle.agent.http import USER_AGENT

    extra = {"base_url": base_url} if base_url else {}
    return anthropic.Anthropic(**{auth: key}, **extra,
                               default_headers={"User-Agent": USER_AGENT})  # fmt: skip


def check_anthropic_key(key: str) -> str:
    """Ask Anthropic which way the key works (listing models costs nothing); returns
    "api_key" or "auth_token", or raises ProviderError with the reason."""
    import anthropic

    errors = []
    for auth in dict.fromkeys([key_auth(key), "api_key", "auth_token"]):
        try:
            anthropic_client(key, auth).models.list(limit=1)
            return auth
        except anthropic.AuthenticationError as exc:
            errors.append(f"{auth}: {exc.message if hasattr(exc, 'message') else exc}")
        except anthropic.APIError as exc:
            raise ProviderError(f"could not check the key: {exc}", "network",
                                "anthropic") from exc  # fmt: skip
    raise ProviderError("Anthropic rejected this key (" + "; ".join(errors) + ")", "auth",
                        "anthropic")  # fmt: skip


def to_anthropic(messages: list[dict], provider: str = "anthropic") -> list[dict]:
    """Neutral messages as Anthropic ones: tool results become user tool_result blocks;
    an assistant turn's thinking blocks go first, as they came."""
    out: list[dict] = []
    for m in messages:
        if m["role"] == "user":
            out.append({"role": "user", "content": _anthropic_user(m)})
        elif m["role"] == "assistant":
            blocks: list[dict] = list(_carried(m, provider) or [])
            if m.get("text"):
                blocks.append({"type": "text", "text": m["text"]})
            blocks += [
                {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments}
                for c in m.get("tool_calls", [])
            ]
            out.append({"role": "assistant", "content": blocks})
        else:
            content: list[dict] = [{"type": "text", "text": m["content"] or "(no output)"}]
            if m.get("image"):
                data = base64.b64encode(m["image"]).decode()
                content.append({"type": "image", "source": {
                    "type": "base64", "media_type": "image/png", "data": data}})  # fmt: skip
            block = {
                "type": "tool_result",
                "tool_use_id": m["id"],
                "content": content,
                "is_error": bool(m.get("is_error")),
            }
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(block)  # results of one turn's calls go together
            else:
                out.append({"role": "user", "content": [block]})
    return out


def _anthropic_user(m: dict):
    """A user message: its text, after its attachments (ADR 0074) as image, document
    and text blocks."""
    from puzzle.agent.attachments import text_of

    files = m.get("attachments") or []
    if not files:
        return m["content"]
    blocks: list[dict] = []
    for a in files:
        if a["kind"] == "image":
            blocks.append({"type": "image", "source": {
                "type": "base64", "media_type": a["media_type"], "data": a["data"]}})  # fmt: skip
        elif a["kind"] == "pdf":
            blocks.append({"type": "document", "title": a["name"], "source": {
                "type": "base64", "media_type": "application/pdf", "data": a["data"]}})  # fmt: skip
        else:
            blocks.append({"type": "text", "text": text_of(a)})
    return [*blocks, {"type": "text", "text": m["content"] or "(see the attachments)"}]


# --- Chat Completions ----------------------------------------------------------------


def _openai_user(m: dict):
    """A user message for a Chat Completions endpoint: images as image_url parts,
    text files inline; a PDF is named, since these endpoints don't read documents."""
    from puzzle.agent.attachments import text_of

    files = m.get("attachments") or []
    if not files:
        return m["content"]
    parts: list[dict] = []
    for a in files:
        if a["kind"] == "image":
            url = f"data:{a['media_type']};base64,{a['data']}"
            parts.append({"type": "image_url", "image_url": {"url": url}})
        elif a["kind"] == "pdf":
            parts.append({"type": "text", "text": f"(the PDF {a['name']} was attached; this "
                          "model can't read documents)"})  # fmt: skip
        else:
            parts.append({"type": "text", "text": text_of(a)})
    return [*parts, {"type": "text", "text": m["content"] or "(see the attachments)"}]


class ChatProvider:
    """A Chat Completions endpoint: e.g. Ollama at http://localhost:11434/v1, Hugging
    Face's router, NVIDIA's API catalog, Meta's compatibility endpoint."""

    tool_images = False  # a tool result is text only on this wire (`to_openai`)

    def __init__(self, model: str, base_url: str = "http://localhost:11434/v1",
                 api_key: str | None = None, timeout_s: float = 600.0, *,
                 name: str = "local", key_header: str = "bearer",
                 headers: dict | None = None, max_output_tokens: int = 0,
                 reasoning: str = "", remote: bool | None = None) -> None:  # fmt: skip
        self.model, self.name = model, name
        self.base_url = base_url.rstrip("/")
        self.api_key, self.key_header, self.extra = api_key, key_header, dict(headers or {})
        self.timeout_s = timeout_s
        self.max_output_tokens, self.reasoning = max_output_tokens, reasoning
        self.remote = _remote(self.base_url) if remote is None else remote

    def _body(self, system: str, messages: list[dict], tools: list[ToolSpec]) -> dict:
        body: dict[str, Any] = {"model": self.model, "messages": to_openai(system, messages)}
        if tools:
            body["tools"] = [{"type": "function", "function": {
                "name": t.name, "description": t.description, "parameters": t.schema}}
                for t in tools]  # fmt: skip
        if self.max_output_tokens:
            body["max_tokens"] = self.max_output_tokens
        if self.reasoning in ("none", "low", "medium", "high"):
            body["reasoning_effort"] = self.reasoning
        return body

    def complete(self, system: str, messages: list[dict], tools: list[ToolSpec],
                 on_delta: Delta = None) -> Reply:  # fmt: skip
        body = self._body(system, messages, tools)
        url = f"{self.base_url}/chat/completions"
        headers = headers_for(self.api_key, self.key_header, self.extra)
        if on_delta is None:
            return _chat_reply(post_json(url, body, headers, self.timeout_s, self.name))
        body |= {"stream": True, "stream_options": {"include_usage": True}}
        text, calls, usage = "", {}, {}
        for _event, data in post_stream(url, body, headers, self.timeout_s, self.name):
            if "choices" in data and data["choices"] and "message" in data["choices"][0]:
                return _chat_reply(data)  # not a stream after all
            usage = data.get("usage") or usage
            for choice in data.get("choices") or []:
                d = choice.get("delta") or {}
                thought = d.get("reasoning_content") or d.get("reasoning")
                if thought:
                    on_delta("thinking", thought)
                if d.get("content"):
                    text += d["content"]
                    on_delta("text", d["content"])
                for part in d.get("tool_calls") or []:
                    c = calls.setdefault(_fragment_key(part, calls),
                                         {"id": "", "name": "", "arguments": ""})  # fmt: skip
                    c["id"] = part.get("id") or c["id"]
                    fn = part.get("function") or {}
                    if fn.get("name"):
                        c["name"] += fn["name"]
                        on_delta("tool", c["name"])
                    c["arguments"] += fn.get("arguments") or ""
        reply = Reply(text=text, input_tokens=usage.get("prompt_tokens", 0),
                      output_tokens=usage.get("completion_tokens", 0))  # fmt: skip
        for i, c in sorted(calls.items()):
            reply.tool_calls.append(ToolCall(c["id"] or f"call-{i}", c["name"],
                                             _arguments(c["arguments"])))  # fmt: skip
        return reply


OpenAIProvider = ChatProvider  # its name before ADR 0084


def _fragment_key(part: dict, calls: dict):
    """Which call a streamed tool-call fragment belongs to: its `index`; a server that
    leaves the index out (ADR 0090, AGENT-13) by its id, a fragment with neither going
    on the call before it."""
    if part.get("index") is not None:
        return part["index"]
    if part.get("id"):
        for key, c in calls.items():
            if c["id"] == part["id"]:
                return key
        return len(calls)
    return next(reversed(calls)) if calls else 0


def _chat_reply(data: dict) -> Reply:
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        raise ProviderError(f"an unexpected reply: {str(data)[:300]}", "server") from None
    reply = Reply(text=message.get("content") or "")
    for i, call in enumerate(message.get("tool_calls") or []):
        reply.tool_calls.append(
            ToolCall(
                call.get("id") or f"call-{i}",
                call["function"]["name"],
                _arguments(call["function"].get("arguments")),
            )
        )
    usage = data.get("usage") or {}
    reply.input_tokens = usage.get("prompt_tokens", 0)
    reply.output_tokens = usage.get("completion_tokens", 0)
    return reply


def to_openai(system: str, messages: list[dict]) -> list[dict]:
    out: list[dict] = [{"role": "system", "content": system}]
    for m in messages:
        if m["role"] == "user":
            out.append({"role": "user", "content": _openai_user(m)})
        elif m["role"] == "assistant":
            entry: dict[str, Any] = {"role": "assistant", "content": m.get("text") or ""}
            if m.get("tool_calls"):
                entry["tool_calls"] = [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                    }
                    for c in m["tool_calls"]
                ]
            out.append(entry)
        else:
            text = m["content"] or "(no output)"
            if m.get("image"):
                text += "\n(an image was produced; this provider cannot show it)"
            out.append({"role": "tool", "tool_call_id": m["id"], "content": text})
    return out


# --- OpenAI's Responses --------------------------------------------------------------


class ResponsesProvider:
    """OpenAI's Responses API. Nothing is stored at OpenAI (`store: false`); a turn's
    output items, its reasoning encrypted among them, are the carry sent back as input,
    so a reasoning model keeps its reasoning across tool calls."""

    remote = True
    tool_images = True  # a tool's picture goes with its result

    def __init__(self, model: str, base_url: str = "https://api.openai.com/v1",
                 api_key: str | None = None, timeout_s: float = 600.0, *,
                 name: str = "openai", headers: dict | None = None,
                 max_output_tokens: int = 0, reasoning: str = "") -> None:  # fmt: skip
        if not api_key:
            raise ProviderError(f"no key for {name}", "auth", name)
        self.model, self.name, self.base_url = model, name, base_url.rstrip("/")
        self.api_key, self.extra, self.timeout_s = api_key, dict(headers or {}), timeout_s
        self.max_output_tokens, self.reasoning = max_output_tokens, reasoning

    def _body(self, system: str, messages: list[dict], tools: list[ToolSpec]) -> dict:
        body: dict[str, Any] = {
            "model": self.model, "instructions": system, "store": False,
            "include": ["reasoning.encrypted_content"],
            "input": to_responses(messages, self.name),
        }  # fmt: skip
        if tools:
            body["tools"] = [{"type": "function", "name": t.name, "description": t.description,
                              "parameters": t.schema} for t in tools]  # fmt: skip
        if self.reasoning in ("none", "low", "medium", "high"):
            body["reasoning"] = {"effort": self.reasoning}
        if self.max_output_tokens:
            body["max_output_tokens"] = self.max_output_tokens
        return body

    def complete(self, system: str, messages: list[dict], tools: list[ToolSpec],
                 on_delta: Delta = None) -> Reply:  # fmt: skip
        body = self._body(system, messages, tools)
        url = f"{self.base_url}/responses"
        headers = headers_for(self.api_key, "bearer", self.extra)
        if on_delta is None:
            return _responses_reply(post_json(url, body, headers, self.timeout_s, self.name))
        final = None
        for event, data in post_stream(url, body | {"stream": True}, headers,
                                       self.timeout_s, self.name):  # fmt: skip
            kind = event or data.get("type", "")
            if kind == "" and "output" in data:
                return _responses_reply(data)  # not a stream after all
            if kind == "response.output_text.delta":
                on_delta("text", data.get("delta", ""))
            elif kind in ("response.reasoning_summary_text.delta", "response.reasoning_text.delta"):
                on_delta("thinking", data.get("delta", ""))
            elif kind == "response.output_item.added":
                item = data.get("item") or {}
                if item.get("type") == "function_call":
                    on_delta("tool", item.get("name", ""))
                elif item.get("type") == "reasoning":
                    on_delta("thinking", "")
            elif kind in ("response.completed", "response.incomplete"):
                final = data.get("response") or data
            elif kind in ("response.failed", "error"):
                err = (data.get("response") or {}).get("error") or data.get("error") or data
                raise ProviderError(f"{self.name}: {err}", "server", self.name)
        if final is None:
            raise ProviderError(f"{self.name}: the stream ended without a response", "network",
                                self.name)  # fmt: skip
        return _responses_reply(final)


def _responses_reply(data: dict) -> Reply:
    if data.get("error"):
        raise ProviderError(f"openai: {data['error']}", "server", "openai")
    output = data.get("output") or []
    reply = Reply(carry=output or None)
    for item in output:
        if item.get("type") == "message":
            for part in item.get("content") or []:
                if part.get("type") == "output_text":
                    reply.text += part.get("text", "")
        elif item.get("type") == "function_call":
            reply.tool_calls.append(
                ToolCall(
                    item.get("call_id") or item.get("id", ""),
                    item["name"],
                    _arguments(item.get("arguments")),
                )
            )
    usage = data.get("usage") or {}
    reply.input_tokens, reply.output_tokens = usage.get("input_tokens", 0), usage.get(
        "output_tokens", 0)  # fmt: skip
    return reply


def to_responses(messages: list[dict], provider: str = "openai") -> list[dict]:
    """Neutral messages as Responses input items; an assistant turn is its own output
    items when it came from this provider (reasoning included), else rebuilt."""
    out: list[dict] = []
    for m in messages:
        if m["role"] == "user":
            parts: list[dict] = []
            for a in m.get("attachments") or []:
                if a["kind"] == "image":
                    parts.append(
                        {
                            "type": "input_image",
                            "image_url": f"data:{a['media_type']};base64,{a['data']}",
                        }
                    )
                elif a["kind"] == "pdf":
                    parts.append(
                        {
                            "type": "input_file",
                            "filename": a["name"],
                            "file_data": f"data:application/pdf;base64,{a['data']}",
                        }
                    )
                else:
                    from puzzle.agent.attachments import text_of

                    parts.append({"type": "input_text", "text": text_of(a)})
            parts.append({"type": "input_text", "text": m["content"] or "(see the attachments)"})
            out.append({"role": "user", "content": parts})
        elif m["role"] == "assistant":
            carried = _carried(m, provider)
            if carried:
                out += [{k: v for k, v in item.items() if k != "status"} for item in carried]
                continue
            if m.get("text"):
                out.append({"role": "assistant", "content": m["text"]})
            out += [
                {
                    "type": "function_call",
                    "call_id": c.id,
                    "name": c.name,
                    "arguments": json.dumps(c.arguments),
                }
                for c in m.get("tool_calls", [])
            ]
        else:
            text = m["content"] or "(no output)"
            if m.get("image"):
                data = base64.b64encode(m["image"]).decode()
                output: Any = [
                    {"type": "input_text", "text": text},
                    {"type": "input_image", "image_url": f"data:image/png;base64,{data}"},
                ]
            else:
                output = text
            out.append({"type": "function_call_output", "call_id": m["id"], "output": output})
    return out


# --- Gemini's generateContent --------------------------------------------------------


class GeminiProvider:
    """Google's Gemini API, natively: a model turn's parts are the carry, sent back as
    they came, since Gemini 3 refuses a function call's turn without its thought
    signature."""

    remote = True
    tool_images = True  # a tool's picture goes with its result

    def __init__(self, model: str,
                 base_url: str = "https://generativelanguage.googleapis.com/v1beta",
                 api_key: str | None = None, timeout_s: float = 600.0, *,
                 name: str = "google", headers: dict | None = None,
                 max_output_tokens: int = 0, reasoning: str = "") -> None:  # fmt: skip
        if not api_key:
            raise ProviderError(f"no key for {name}", "auth", name)
        self.model = model.removeprefix("models/")
        self.name, self.base_url = name, base_url.rstrip("/")
        self.api_key, self.extra, self.timeout_s = api_key, dict(headers or {}), timeout_s
        self.max_output_tokens, self.reasoning = max_output_tokens, reasoning

    def _body(self, system: str, messages: list[dict], tools: list[ToolSpec]) -> dict:
        body: dict[str, Any] = {"systemInstruction": {"parts": [{"text": system}]},
                                "contents": to_gemini(messages, self.name)}  # fmt: skip
        if tools:
            body["tools"] = [{"functionDeclarations": [
                {"name": t.name, "description": t.description, "parametersJsonSchema": t.schema}
                for t in tools]}]  # fmt: skip
        config: dict[str, Any] = {}
        if self.max_output_tokens:
            config["maxOutputTokens"] = self.max_output_tokens
        if self.reasoning in ("low", "medium", "high"):
            config["thinkingConfig"] = {"thinkingLevel": "low" if self.reasoning == "low"
                                        else "high"}  # fmt: skip
        if config:
            body["generationConfig"] = config
        return body

    def complete(self, system: str, messages: list[dict], tools: list[ToolSpec],
                 on_delta: Delta = None) -> Reply:  # fmt: skip
        body = self._body(system, messages, tools)
        headers = headers_for(self.api_key, "x-goog-api-key", self.extra)
        if on_delta is None:
            url = f"{self.base_url}/models/{self.model}:generateContent"
            return _gemini_reply([post_json(url, body, headers, self.timeout_s, self.name)])
        url = f"{self.base_url}/models/{self.model}:streamGenerateContent?alt=sse"
        chunks = []
        for _event, data in post_stream(url, body, headers, self.timeout_s, self.name):
            chunks.append(data)
            for cand in data.get("candidates") or []:
                for part in (cand.get("content") or {}).get("parts") or []:
                    if part.get("thought"):
                        on_delta("thinking", part.get("text", ""))
                    elif "text" in part:
                        on_delta("text", part["text"])
                    elif "functionCall" in part:
                        on_delta("tool", part["functionCall"].get("name", ""))
        return _gemini_reply(chunks)


def _gemini_reply(chunks: list[dict]) -> Reply:
    parts: list[dict] = []
    usage: dict = {}
    for data in chunks:
        if data.get("error"):
            raise ProviderError(f"google: {data['error']}", "server", "google")
        usage = data.get("usageMetadata") or usage
        for cand in data.get("candidates") or []:
            parts += (cand.get("content") or {}).get("parts") or []
            if cand.get("finishReason") in ("MALFORMED_FUNCTION_CALL", "UNEXPECTED_TOOL_CALL"):
                raise ProviderError(f"google: {cand['finishReason']}", "tools", "google")
        if not data.get("candidates") and (data.get("promptFeedback") or {}).get("blockReason"):
            raise ProviderError(f"google: blocked ({data['promptFeedback']['blockReason']})",
                                "", "google")  # fmt: skip
    reply = Reply(carry=parts or None)
    for i, part in enumerate(parts):
        if "functionCall" in part:
            fc = part["functionCall"]
            reply.tool_calls.append(ToolCall(fc.get("id") or f"call-{i}", fc["name"],
                                             dict(fc.get("args") or {})))  # fmt: skip
        elif "text" in part and not part.get("thought"):
            reply.text += part["text"]
    reply.input_tokens = usage.get("promptTokenCount", 0)
    reply.output_tokens = usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0)
    return reply


def to_gemini(messages: list[dict], provider: str = "google") -> list[dict]:
    """Neutral messages as Gemini contents: a model turn as its own parts when it came
    from this provider (thought signatures included); tool results as one user turn of
    functionResponse parts."""
    out: list[dict] = []
    names: dict[str, str] = {}
    for m in messages:
        if m["role"] == "user":
            parts: list[dict] = []
            for a in m.get("attachments") or []:
                if a["kind"] in ("image", "pdf"):
                    mime = a.get("media_type") or "application/pdf"
                    parts.append({"inlineData": {"mimeType": mime, "data": a["data"]}})
                else:
                    from puzzle.agent.attachments import text_of

                    parts.append({"text": text_of(a)})
            parts.append({"text": m["content"] or "(see the attachments)"})
            out.append({"role": "user", "parts": parts})
        elif m["role"] == "assistant":
            for c in m.get("tool_calls", []):
                names[c.id] = c.name
            carried = _carried(m, provider)
            if carried:
                out.append({"role": "model", "parts": list(carried)})
                continue
            parts = [{"text": m["text"]}] if m.get("text") else []
            parts += [{"functionCall": {"name": c.name, "args": c.arguments}}
                      for c in m.get("tool_calls", [])]  # fmt: skip
            out.append({"role": "model", "parts": parts or [{"text": ""}]})
        else:
            key = "error" if m.get("is_error") else "result"
            response: dict[str, Any] = {
                "name": m.get("name") or names.get(m["id"], ""),
                "response": {key: m["content"] or "(no output)"},
            }
            if not m["id"].startswith("call-"):
                response["id"] = m["id"]
            part: dict[str, Any] = {"functionResponse": response}
            extra = []
            if m.get("image"):
                data = base64.b64encode(m["image"]).decode()
                extra = [{"inlineData": {"mimeType": "image/png", "data": data}}]
            if out and out[-1]["role"] == "user" and any(
                    "functionResponse" in p for p in out[-1]["parts"]):  # fmt: skip
                out[-1]["parts"] += [part, *extra]
            else:
                out.append({"role": "user", "parts": [part, *extra]})
    return out


# --- listing models ------------------------------------------------------------------


def list_models(wire: str, base_url: str, key: str | None, key_header: str,
                headers: dict | None = None, timeout: float = 5.0,
                who: str = "") -> list[tuple[str, bool | None]]:  # fmt: skip
    """(model id, calls tools: True/False/None unknown) as the provider lists them."""
    h = headers_for(key, key_header, headers)
    if wire == "gemini":
        data = get_json(f"{base_url.rstrip('/')}/models?pageSize=1000", h, timeout, who)
        out = []
        for m in data.get("models") or []:
            methods = m.get("supportedGenerationMethods") or []
            if "generateContent" in methods:
                out.append((m["name"].removeprefix("models/"), None))
        return out
    data = get_json(f"{base_url.rstrip('/')}/models", h, timeout, who)
    out = []
    for m in data.get("data") or data.get("models") or []:
        mid = m.get("id") or m.get("name") or ""
        hosts = m.get("providers")  # Hugging Face's router says per host
        tools = None
        if isinstance(hosts, list) and hosts:
            flags = [p.get("supports_tools") for p in hosts if isinstance(p, dict)]
            tools = True if any(flags) else (False if flags and all(f is False for f in flags)
                                             else None)  # fmt: skip
        if mid:
            out.append((mid, tools))
    return out


class ScriptedProvider:
    """Replays replies in order; each may be a Reply or a function of the messages."""

    name = "scripted"
    model = "script"
    remote = False
    tool_images = True

    def __init__(self, replies: list) -> None:
        self.replies = list(replies)
        self.seen: list[list[dict]] = []
        self.tools_seen: list[list[str]] = []  # the tools offered with each request
        self.systems: list[str] = []

    def complete(self, system: str, messages: list[dict], tools: list[ToolSpec],
                 on_delta: Delta = None) -> Reply:  # fmt: skip
        self.seen.append([dict(m) for m in messages])
        self.tools_seen.append([t.name for t in tools])
        self.systems.append(system)
        if not self.replies:
            return Reply(text="(script finished)")
        reply = self.replies.pop(0)
        reply = reply(messages) if callable(reply) else reply
        if on_delta is not None and reply.text:
            on_delta("text", reply.text)
        return reply
