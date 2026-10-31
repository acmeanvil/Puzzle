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
from puzzle.agent.providers import Provider, ToolSpec

NAME = "built-in"
MAX_ROUNDS = 16
MAX_TOOL_CHARS = 8000  # longer tool output is cut, with a note, to keep context small


def _system() -> str:
    from puzzle.agent.skills import catalog

    return (
        INSTRUCTIONS
        + """
You are Puzzle's built-in assistant. The user sees the model tree, the 3D view and
your replies side by side, and reviews your proposals in the agent panel. Answer
briefly and concretely: name the objects, give values with units. When you propose
changes, say what they do and why in the rationale. Do not invent results.

Skills (read one with `skill_read` before a task in its area):
"""
        + catalog()
        + "\n"
    )


SYSTEM = _system()


@dataclass
class Event:
    """Something the agent did, for the GUI or terminal to show as it happens."""

    kind: str  # "text", "tool_call", "tool_result", "error", "usage"
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
        self.server = build_server(self.root, NAME, outside=False)
        self.images = (not provider.remote) if images is None else images
        self.max_rounds = max_rounds
        self.messages: list[dict] = []
        self.tokens = {"input": 0, "output": 0}
        self.tools = self._tool_specs()

    def ask(self, text: str, on_event: Callable[[Event], None] | None = None) -> str:
        """Answer one user message, calling tools as the model asks; return the reply."""
        emit = on_event or (lambda e: None)
        self.messages.append({"role": "user", "content": text})
        answer = ""
        for _ in range(self.max_rounds):
            reply = self.provider.complete(SYSTEM, self.messages, self.tools)
            self.tokens["input"] += reply.input_tokens
            self.tokens["output"] += reply.output_tokens
            self.messages.append(
                {"role": "assistant", "text": reply.text, "tool_calls": reply.tool_calls}
            )
            if reply.text:
                answer = reply.text
                emit(Event("text", reply.text))
            if not reply.tool_calls:
                emit(Event("usage", data=dict(self.tokens)))
                return answer
            for call in reply.tool_calls:
                emit(Event("tool_call", call.name, {"arguments": call.arguments}))
                result = self._call(call.name, call.arguments)
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
