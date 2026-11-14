"""Whether a model can call tools, when neither its provider nor the catalog says
(ADR 0084 decision 5): one request with a trivial tool on first use, the answer kept in
`<user data dir>/agent-models.json`.

The agent is useless without tools (every action is a tool call, ADR 0015), so a model
known not to call them is refused when chosen, saying why and naming the provider's
models that do, never run tool-less.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from puzzle.agent.providers import Provider, ProviderError, ToolSpec

PROBE_TOOL = ToolSpec("get_time", "Returns the current time. Call it to answer.",
                      {"type": "object", "properties": {}, "required": []})  # fmt: skip
PROBE = "What time is it? Use the get_time tool; don't answer from memory."


def path() -> Path:
    from puzzle.materials import user_data_dir

    return user_data_dir() / "agent-models.json"


def _read() -> dict:
    try:
        return json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def known(provider: str, model: str) -> bool | None:
    """The kept answer for a model, or None when it hasn't been probed."""
    entry = _read().get(f"{provider}/{model}")
    return entry.get("tools") if isinstance(entry, dict) else None


def remember(provider: str, model: str, tools: bool, note: str = "") -> None:
    data = _read()
    data[f"{provider}/{model}"] = {"tools": tools, "note": note,
                                   "when": time.strftime("%Y-%m-%d")}  # fmt: skip
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")


def probe(provider: Provider) -> bool:
    """Ask the model once to call a trivial tool; kept for next time. A refusal of the
    tools themselves (kind `tools`) is a no; other failures are raised."""
    name, model = provider.name, provider.model
    try:
        reply = provider.complete("You answer by calling tools.",
                                  [{"role": "user", "content": PROBE}], [PROBE_TOOL])  # fmt: skip
    except ProviderError as exc:
        if exc.kind != "tools":
            raise
        remember(name, model, False, str(exc)[:200])
        return False
    tools = any(c.name == PROBE_TOOL.name for c in reply.tool_calls)
    remember(name, model, tools, "" if tools else (reply.text or "")[:200])
    return tools


def check(provider: Provider, alternatives: list[str] = ()) -> None:
    """Refuse a model that can't call tools (probing it if not known), with the reason
    and the provider's models that can."""
    from puzzle.agent.catalog import calls_tools

    name, model = provider.name, provider.model
    tools = calls_tools(name, model)
    if tools is None:
        tools = known(name, model)
    if tools is None:
        tools = probe(provider)
    if not tools:
        others = ", ".join(alternatives[:6])
        raise ProviderError(
            f"{model} can't call tools, and the agent acts only through tools"
            + (f"; this provider's models that can: {others}" if others else ""),
            "tools", name)  # fmt: skip
