"""Conversations with the built-in agent, kept per project (ADR 0090 package 12, UXA-4).

Where: `<user data dir>/agent-chats/<project key>/`, never in the project folder. A chat
is the user's own working conversation (what they pasted, the model's words, the
provider's carried reasoning), not part of the project's record: the history already
records every change and who made it. So a chat stays on this computer for this user:
Save as, archives and versions don't take it, a colleague opening the project doesn't
read it, and it is outside every content hash (it is not in the project at all). The
project key is its folder's resolved path hashed, as visibility.json and schematic.json
key theirs; a project moved elsewhere starts without its chats (the folder's
`project.txt` says which project a key was, to find them by hand).

Each chat is one JSON file (`<id>.json`, written by `puzzle.project.atomic`): id,
title, created and updated (UTC ISO), provider and model, the plan, the turns (id and
what each changed) and the messages. What is not kept: pictures (a tool's or an
attachment's bytes; the attachment's name and a note stay) and nothing with a key in
it, since no key ever reaches the agent's messages. `export_markdown` writes one as
Markdown for the user to keep or share.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

FORMAT = 1
MAX_CHATS = 50  # per project; the oldest go first


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def folder(root: Path) -> Path:
    from puzzle.materials import user_data_dir

    key = hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest()[:16]
    return user_data_dir() / "agent-chats" / key


@dataclass
class Chat:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    title: str = ""
    created: str = field(default_factory=_now)
    updated: str = field(default_factory=_now)
    provider: str = ""
    model: str = ""
    plan: list[dict[str, str]] = field(default_factory=list)
    turns: list[dict[str, Any]] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"format": FORMAT, "id": self.id, "title": self.title,
                "created": self.created, "updated": self.updated,
                "provider": self.provider, "model": self.model, "plan": self.plan,
                "turns": self.turns, "messages": [_kept(m) for m in self.messages]}  # fmt: skip

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Chat:
        return cls(id=data["id"], title=data.get("title", ""),
                   created=data.get("created", ""), updated=data.get("updated", ""),
                   provider=data.get("provider", ""), model=data.get("model", ""),
                   plan=list(data.get("plan") or []), turns=list(data.get("turns") or []),
                   messages=[_restored(m) for m in data.get("messages") or []])  # fmt: skip


def _kept(m: dict[str, Any]) -> dict[str, Any]:
    """A message as stored: tool calls as plain data, pictures left out."""
    out = {k: v for k, v in m.items() if k not in ("image", "tool_calls", "attachments")}
    if m.get("tool_calls"):
        out["tool_calls"] = [c if isinstance(c, dict) else
                             {"id": c.id, "name": c.name, "arguments": c.arguments}
                             for c in m["tool_calls"]]  # fmt: skip
    if m.get("attachments"):
        out["attachments"] = [a if a.get("kind") == "text" else
                              {"name": a["name"], "kind": "text", "media_type": "text/plain",
                               "text": f"(the {a['kind']} {a['name']} was sent then; it is "
                                       "not kept with the chat)"}
                              for a in m["attachments"]]  # fmt: skip
    if m.get("role") == "tool":
        out["image"] = None
    return out


def _restored(m: dict[str, Any]) -> dict[str, Any]:
    from puzzle.agent.providers import ToolCall

    out = dict(m)
    if m.get("tool_calls"):
        out["tool_calls"] = [ToolCall(c["id"], c["name"], c.get("arguments") or {})
                             for c in m["tool_calls"]]  # fmt: skip
    return out


def title_of(text: str) -> str:
    words = " ".join(text.split())
    return words if len(words) <= 60 else words[:57].rstrip() + "…"


def save(root: Path, chat: Chat) -> Path:
    """Write a chat (atomically) and its project's note; an empty chat is not kept."""
    from puzzle.project import atomic

    if not chat.messages:
        return folder(root) / f"{chat.id}.json"
    where = folder(root)
    where.mkdir(parents=True, exist_ok=True)
    note = where / "project.txt"
    if not note.exists():
        atomic.write_text(note, str(Path(root).resolve()) + "\n", encoding="utf-8", durable=False)
    chat.updated = _now()
    path = where / f"{chat.id}.json"
    atomic.write_text(
        path, json.dumps(chat.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8"
    )
    _prune(where)
    return path


def _prune(where: Path) -> None:
    found = sorted(where.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in found[MAX_CHATS:]:
        old.unlink(missing_ok=True)


def listing(root: Path) -> list[dict[str, str]]:
    """The project's chats, newest first: id, title, updated, model."""
    out = []
    for path in folder(root).glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # a damaged file is skipped, never fatal
        out.append({"id": data.get("id", path.stem), "title": data.get("title", ""),
                    "updated": data.get("updated", ""),
                    "model": data.get("model", "")})  # fmt: skip
    return sorted(out, key=lambda c: c["updated"], reverse=True)


def load(root: Path, chat_id: str) -> Chat:
    path = folder(root) / f"{chat_id}.json"
    return Chat.from_dict(json.loads(path.read_text(encoding="utf-8")))


def latest(root: Path) -> Chat | None:
    for entry in listing(root):
        try:
            return load(root, entry["id"])
        except (OSError, ValueError, KeyError):
            continue
    return None


def delete(root: Path, chat_id: str) -> None:
    (folder(root) / f"{chat_id}.json").unlink(missing_ok=True)


def export_markdown(chat: Chat) -> str:
    """The chat as Markdown: the user's messages, the replies, the tools used in words."""
    from puzzle.agent.evidence import call_words, result_words
    from puzzle.agent.plans import text as plan_text

    lines = [f"# {chat.title or 'Conversation'}", "",
             f"{chat.model} · {chat.updated}", ""]  # fmt: skip
    if chat.plan:
        lines += ["## Plan", "", plan_text(chat.plan), ""]
    for m in chat.messages:
        if m["role"] == "user":
            lines += [f"**You:** {m.get('shown', m.get('content', ''))}", ""]
        elif m["role"] == "assistant":
            for c in m.get("tool_calls") or []:
                name, args = (c.name, c.arguments) if not isinstance(c, dict) else (
                    c["name"], c.get("arguments") or {})  # fmt: skip
                lines.append(f"- *{call_words(name, args)}*")
            if m.get("text"):
                lines += ["", f"**Agent:** {m['text']}", ""]
        elif m["role"] == "tool":
            lines.append(f"  - {result_words(m.get('name', ''), m.get('content', ''))}")
    return "\n".join(lines).rstrip() + "\n"
