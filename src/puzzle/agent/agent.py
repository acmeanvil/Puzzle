"""The built-in agent: a model, the project's tools, and the user watching (ADR 0015).

It uses the same tools as outside agents (puzzle.agent.mcp, called in-process) and
acts as Actor("agent", "built-in"), so the project's autonomy level decides whether
its changes are refused, proposed for the user to accept, or applied, and the
history records them like anyone's.

Built for small models: narrow tools, compact reads, tool output trimmed, a bounded
number of tool rounds per question.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from puzzle.agent.mcp import INSTRUCTIONS, build_server
from puzzle.agent.providers import Provider, ProviderError, ToolSpec

NAME = "built-in"
MAX_ROUNDS = 16
MAX_TOOL_CHARS = 8000  # longer tool output is cut, with a note, to keep context small
CUT = "(cut to fit the model's context; call the tool again if it's needed)"


def _takes_delta(provider) -> bool:
    """Whether a provider's `complete` streams (an older or test provider may not)."""
    import inspect

    try:
        return "on_delta" in inspect.signature(provider.complete).parameters
    except (TypeError, ValueError):
        return False


def _system() -> str:
    from puzzle.agent.skills import catalog

    return (
        INSTRUCTIONS
        + """
You are Puzzle's built-in assistant. The user sees the model tree, the 3D view and
your replies side by side, and reviews your proposals in the agent panel. Answer
briefly and concretely: name the objects, give values with units. When you propose
changes, say what they do and why in the rationale. Do not invent results.
Your replies are drawn as Markdown: use lists and tables where they help, write
equations in TeX between $...$ (or $$...$$ on their own line), and put code in fenced
blocks with its language (```python): a code block is shown folded, so explain what it
does before it.

Skills (read one with `skill_read` before a task in its area):
"""
        + catalog()
        + "\n"
    )


SYSTEM = _system()


def _doing(tool: str, arguments: dict) -> str:
    """What a tool call is, in words, for the status line."""
    if tool == "skill_read":
        return f"Reading the skill {arguments.get('name', '')}".strip()
    if tool == "knowledge":
        return "Looking it up"
    return f"Calling {tool}"


@dataclass
class Event:
    """Something the agent did, for the GUI or terminal to show as it happens."""

    kind: str  # "delta" (reply text streaming), "text", "tool_call", "tool_result",
    # "error", "usage", "status", "note"
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)


class Agent:
    def __init__(
        self,
        root: Path,
        provider: Provider,
        *,
        images: bool | None = None,
        max_rounds: int = MAX_ROUNDS,
    ) -> None:
        self.root = Path(root)
        self.provider = provider
        from puzzle.agent.settings import load_settings

        picked = load_settings().picked  # "date suite version", when picked (ADR 0022)
        self.server = build_server(self.root, NAME, outside=False,
                                   model=getattr(provider, "model", "") or "",
                                   suite=picked.split("suite ")[-1] if "suite " in picked
                                   else "")  # fmt: skip
        self.images = (not provider.remote) if images is None else images
        self.max_rounds = max_rounds
        self.messages: list[dict] = []
        self.tokens = {"input": 0, "output": 0}
        self.timings = {"model": 0.0, "tools": 0.0, "total": 0.0}  # seconds, this chat
        self.tools = self._tool_specs()

    def ask(self, text: str, on_event: Callable[[Event], None] | None = None,
            attachments=(), stop=None) -> str:  # fmt: skip
        """Answer one user message, calling tools as the model asks; return the reply.

        `attachments` (ADR 0074) go with the message: images and PDFs only where files
        may go (a local model, or a remote one with `send_images`), text always. Status
        events say what it is doing as it works; `stop` (a threading.Event) set ends
        the turn after the current step."""
        import time

        emit = on_event or (lambda e: None)
        sent = [a for a in attachments if self.images or not a.is_file]
        held = [a.name for a in attachments if a not in sent]
        if held:
            emit(Event("note", f"Not sent to {self.provider.name} (files stay on this computer "
                               f"unless send_images is on): {', '.join(held)}"))  # fmt: skip
        message: dict[str, Any] = {"role": "user", "content": text}
        if sent:
            message["attachments"] = [a.block() for a in sent]
        self.messages.append(message)
        answer = ""
        started = time.perf_counter()
        for _ in range(self.max_rounds):
            if stop is not None and stop.is_set():
                emit(Event("note", "Stopped."))
                return answer or "(stopped)"
            emit(Event("status", "Thinking…"))
            asked = time.perf_counter()
            reply = self._complete(emit if on_event is not None else None)
            self.timings["model"] += time.perf_counter() - asked
            self.tokens["input"] += reply.input_tokens
            self.tokens["output"] += reply.output_tokens
            turn: dict[str, Any] = {"role": "assistant", "text": reply.text,
                                    "tool_calls": reply.tool_calls}  # fmt: skip
            if reply.carry is not None:  # sent back to this provider only (ADR 0084)
                turn["carry"] = {"provider": self.provider.name, "data": reply.carry}
            self.messages.append(turn)
            if reply.text:
                answer = reply.text
                emit(Event("text", reply.text))
            if not reply.tool_calls:
                self.timings["total"] = time.perf_counter() - started
                emit(Event("usage", data=dict(self.tokens) | {
                    "timings": dict(self.timings), "provider": self.provider.name,
                    "model": getattr(self.provider, "model", "")}))  # fmt: skip
                return answer
            for call in reply.tool_calls:
                if stop is not None and stop.is_set():
                    # Every call still gets a result, or the next message is refused.
                    self.messages.append({"role": "tool", "id": call.id, "name": call.name,
                                          "content": "(not run: the user stopped the turn)",
                                          "is_error": True, "image": None})  # fmt: skip
                    continue
                emit(Event("status", _doing(call.name, call.arguments)))
                emit(Event("tool_call", call.name, {"arguments": call.arguments}))
                called = time.perf_counter()
                result = self._call(call.name, call.arguments)
                self.timings["tools"] += time.perf_counter() - called
                self.messages.append({"role": "tool", "id": call.id, "name": call.name, **result})
                emit(
                    Event(
                        "error" if result["is_error"] else "tool_result",
                        result["content"][:400],
                        {"name": call.name, "image": result.get("image")},
                    )
                )
        note = f"(stopped after {self.max_rounds} tool rounds; ask again to continue)"
        emit(Event("error", note))
        return answer or note

    def reset(self) -> None:
        self.messages.clear()

    def _complete(self, emit):
        """One model reply, streamed when someone is watching and the provider can
        (ADR 0084 decision 6). A conversation too long for the model has its oldest tool
        results cut, saying so, and is asked again."""
        shown = {"status": ""}

        def on_delta(kind: str, text: str) -> None:
            if kind == "text" and text:
                emit(Event("delta", text))
                status = "Writing…"
            elif kind == "thinking":
                status = "Thinking…"
            elif kind == "tool":
                status = f"Preparing {text}" if text else "Preparing a tool call"
            else:
                return
            if status != shown["status"]:
                shown["status"] = status
                emit(Event("status", status))

        streams = emit is not None and _takes_delta(self.provider)
        while True:
            try:
                if streams:
                    return self.provider.complete(SYSTEM, self.messages, self.tools,
                                                  on_delta=on_delta)  # fmt: skip
                return self.provider.complete(SYSTEM, self.messages, self.tools)
            except ProviderError as exc:
                if exc.kind != "context" or not self._cut_oldest():
                    raise
                if emit is not None:
                    emit(Event("note", "The conversation was too long for the model: the "
                                       "oldest tool results were cut."))  # fmt: skip

    def _cut_oldest(self, share: float = 0.5) -> bool:
        """Cut the oldest half of the tool results still whole; False when none are."""
        whole = [m for m in self.messages if m["role"] == "tool" and m.get("content") != CUT]
        if not whole:
            return False
        for m in whole[: max(1, int(len(whole) * share))]:
            m["content"], m["image"] = CUT, None
        return True

    # --- tools -------------------------------------------------------------------

    def _tool_specs(self) -> list[ToolSpec]:
        listed = asyncio.run(self.server.list_tools())
        return [
            ToolSpec(t.name, t.description or "", t.input_schema)
            for t in listed
            if self.images or t.name != "render"  # pictures stay local unless allowed
        ]

    def _call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name not in {t.name for t in self.tools}:
            return {"content": f"no tool '{name}'", "is_error": True, "image": None}
        if "_unparsed" in arguments:
            return {
                "content": f"the arguments were not valid JSON: {arguments['_unparsed'][:200]}",
                "is_error": True,
                "image": None,
            }
        try:
            result = asyncio.run(self.server.call_tool(name, arguments))
        except ToolError as exc:
            message = str(exc).removeprefix(f"Error executing tool {name}: ")
            return {"content": message, "is_error": True, "image": None}
        texts, image = [], None
        for block in result.content:
            if block.type == "text":
                texts.append(block.text)
            elif block.type == "image":
                import base64

                image = base64.b64decode(block.data)
                texts.append("(rendered image attached)")
        if not texts and result.structured_content is not None:
            texts.append(
                json.dumps(result.structured_content.get("result", result.structured_content))
            )
        text = "\n".join(texts)
        if len(text) > MAX_TOOL_CHARS:
            cut = len(text) - MAX_TOOL_CHARS
            text = text[:MAX_TOOL_CHARS] + f"\n... ({cut} more characters cut; ask for less)"
        return {"content": text, "is_error": bool(result.is_error), "image": image}
