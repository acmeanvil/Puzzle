"""Model providers for the built-in agent (ADR 0015): Claude first, local models too.

A provider turns the conversation plus the tool list into one model reply: text,
tool calls, or both. The conversation is kept provider-neutral:

    {"role": "user", "content": "..."}
    {"role": "assistant", "text": "...", "tool_calls": [ToolCall, ...]}
    {"role": "tool", "id": ..., "name": ..., "content": "...", "image": b"..." | None,
     "is_error": bool}

- AnthropicProvider: Claude through the Messages API (`pip install anthropic`).
- OpenAIProvider: any OpenAI-compatible chat endpoint (Ollama, llama.cpp's server,
  LM Studio, vLLM), through the standard library, so local models need nothing extra.
- ScriptedProvider: replays fixed replies, for tests and the evaluation suite.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol


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


@dataclass
class ToolSpec:
    name: str
    description: str
    schema: dict[str, Any]


class ProviderError(RuntimeError):
    pass


class Provider(Protocol):
    name: str
    model: str
    remote: bool  # whether data leaves the machine

    def complete(self, system: str, messages: list[dict], tools: list[ToolSpec]) -> Reply: ...


class AnthropicProvider:
    name = "anthropic"
    remote = True

    def __init__(
        self, model: str = "claude-sonnet-5-5", api_key: str | None = None, auth: str | None = None
    ) -> None:
        try:
            import anthropic  # noqa: F401 - only checking that it is installed
        except ImportError as exc:
            raise ProviderError(
                "the Claude provider needs the anthropic package: pip install anthropic"
            ) from exc
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise ProviderError(
                "no Claude API key: run `puzzle agent-key set` or set ANTHROPIC_API_KEY"
            )
        self.model = model
        self.client = anthropic_client(key, auth or key_auth(key))


def key_auth(key: str) -> str:
    """How a key is sent: "api_key" (x-api-key header) for classic sk-ant-api keys,
    "auth_token" (Authorization: Bearer) otherwise; `check_anthropic_key` decides
    for real when a key is stored."""
    return "api_key" if key.startswith("sk-ant-api") else "auth_token"


def anthropic_client(key: str, auth: str):
    import anthropic

    return anthropic.Anthropic(**{auth: key})


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
            raise ProviderError(f"could not check the key: {exc}") from exc
    raise ProviderError("Anthropic rejected this key (" + "; ".join(errors) + ")")

    def complete(self, system: str, messages: list[dict], tools: list[ToolSpec]) -> Reply:
        import anthropic

        specs = [
            {"name": t.name, "description": t.description, "input_schema": t.schema} for t in tools
        ]
        if specs:  # the system prompt and tools repeat every turn: cache them
            specs[-1]["cache_control"] = {"type": "ephemeral"}
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                tools=specs,
                messages=to_anthropic(messages),
            )
        except anthropic.APIError as exc:
            raise ProviderError(f"Claude: {exc}") from exc
        reply = Reply(
            input_tokens=response.usage.input_tokens, output_tokens=response.usage.output_tokens
        )
        for block in response.content:
            if block.type == "text":
                reply.text += block.text
            elif block.type == "tool_use":
                reply.tool_calls.append(ToolCall(block.id, block.name, dict(block.input)))
        return reply


def to_anthropic(messages: list[dict]) -> list[dict]:
    """Neutral messages as Anthropic ones: tool results become user tool_result blocks."""
    out: list[dict] = []
    for m in messages:
        if m["role"] == "user":
            out.append({"role": "user", "content": m["content"]})
        elif m["role"] == "assistant":
            blocks: list[dict] = [{"type": "text", "text": m["text"]}] if m.get("text") else []
            blocks += [
                {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments}
                for c in m.get("tool_calls", [])
            ]
            out.append({"role": "assistant", "content": blocks})
        else:
            content: list[dict] = [{"type": "text", "text": m["content"] or "(no output)"}]
            if m.get("image"):
                data = base64.b64encode(m["image"]).decode()
                content.append(
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/png", "data": data},
                    }
                )
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


class OpenAIProvider:
    """An OpenAI-compatible /chat/completions endpoint, e.g. Ollama at
    http://localhost:11434/v1 or llama.cpp's server at http://localhost:8080/v1."""

    name = "openai"

    def __init__(
        self,
        model: str,
        base_url: str = "http://localhost:11434/v1",
        api_key: str | None = None,
        timeout_s: float = 600.0,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_s = timeout_s
        host = urllib.request.urlparse(self.base_url).hostname or ""
        self.remote = host not in ("localhost", "127.0.0.1", "::1")

    def complete(self, system: str, messages: list[dict], tools: list[ToolSpec]) -> Reply:
        body = {
            "model": self.model,
            "messages": to_openai(system, messages),
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.schema,
                    },
                }
                for t in tools
            ],
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"}
            | ({"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}),
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                data = json.loads(response.read())
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ProviderError(f"{self.base_url}: {exc}") from exc
        message = data["choices"][0]["message"]
        reply = Reply(text=message.get("content") or "")
        for i, call in enumerate(message.get("tool_calls") or []):
            raw = call["function"].get("arguments") or "{}"
            try:
                arguments = json.loads(raw) if isinstance(raw, str) else dict(raw)
            except ValueError:
                arguments = {"_unparsed": raw}  # reported back to the model as an error
            reply.tool_calls.append(
                ToolCall(call.get("id") or f"call-{i}", call["function"]["name"], arguments)
            )
        usage = data.get("usage") or {}
        reply.input_tokens = usage.get("prompt_tokens", 0)
        reply.output_tokens = usage.get("completion_tokens", 0)
        return reply


def to_openai(system: str, messages: list[dict]) -> list[dict]:
    out: list[dict] = [{"role": "system", "content": system}]
    for m in messages:
        if m["role"] == "user":
            out.append({"role": "user", "content": m["content"]})
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


class ScriptedProvider:
    """Replays replies in order; each may be a Reply or a function of the messages."""

    name = "scripted"
    model = "script"
    remote = False

    def __init__(self, replies: list) -> None:
        self.replies = list(replies)
        self.seen: list[list[dict]] = []

    def complete(self, system: str, messages: list[dict], tools: list[ToolSpec]) -> Reply:
        self.seen.append([dict(m) for m in messages])
        if not self.replies:
            return Reply(text="(script finished)")
        reply = self.replies.pop(0)
        return reply(messages) if callable(reply) else reply
