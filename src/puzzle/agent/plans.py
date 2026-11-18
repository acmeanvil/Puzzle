"""The agent's plan for a longer task (ADR 0090 package 12, UXA-7): a short list of items,
each todo, doing, done or skipped, shown to the user above the message box and kept
with the conversation. The agent sets the whole list with the `plan` tool; the Advisor's
done and needed steps can seed it (`from_advice`)."""

from __future__ import annotations

STATES = ("todo", "doing", "done", "skipped")
MAX_ITEMS = 20
MARKS = {"todo": "○", "doing": "◐", "done": "●", "skipped": "–"}


def normalise(items: list) -> list[dict[str, str]]:
    """Items as {"text", "state"}: an unknown state is todo, blank items dropped, at
    most MAX_ITEMS, each text one line of at most 200 characters."""
    out = []
    for item in items[:MAX_ITEMS]:
        if isinstance(item, str):
            item = {"text": item}
        text = " ".join(str((item or {}).get("text", "")).split())[:200]
        if not text:
            continue
        state = str(item.get("state", "todo"))
        out.append({"text": text, "state": state if state in STATES else "todo"})
    return out


def from_advice(advice) -> list[dict[str, str]]:
    """A plan from the Advisor's steps (`advisor.advise`): done ones done, needed ones
    todo (optional ones left out), each the step's title and why."""
    out = []
    for a in advice:
        d = a.as_dict()
        state = d.get("state")
        if state not in ("done", "needed"):
            continue
        words = d.get("title") or d.get("step") or ""
        reason = d.get("detail") or ""
        out.append({"text": f"{words}: {reason}" if reason else words,
                    "state": "done" if state == "done" else "todo"})  # fmt: skip
    return normalise(out)


def text(plan: list[dict[str, str]]) -> str:
    """The plan as lines, for an exported conversation."""
    return "\n".join(f"{MARKS.get(i['state'], '○')} {i['text']}" for i in plan)


def unfinished(plan: list[dict[str, str]]) -> int:
    return sum(1 for i in plan if i["state"] in ("todo", "doing"))
