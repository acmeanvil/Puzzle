"""What the user is looking at, sent with a message (ADR 0090 package 12, UXA-3).

The window gathers it (`MainWindow.agent_context`): the tab shown, what the tree has
selected (by name, with the place it is in) and the result shown (study, quantity,
step). `block` turns it into the <context> block put in front of the user's message for
the model; `chip` into the composer's short line ("Bracket › Hole 3 · von Mises, step
2"). Names only, never ids; nothing is sent when the user switches the chip off.
"""

from __future__ import annotations

from typing import Any

MAX_SELECTED = 8


def chip(ctx: dict[str, Any] | None) -> str:
    """The composer's line: what is selected, then the result shown."""
    if not ctx:
        return ""
    parts = []
    selected = ctx.get("selected") or []
    if selected:
        parts.append(selected[0] + (f" and {len(selected) - 1} more" if len(selected) > 1
                                    else ""))  # fmt: skip
    result = ctx.get("result") or {}
    if result.get("quantity"):
        step = f", step {result['step']}" if result.get("step") else ""
        parts.append(f"{result['quantity']}{step}")
    return " · ".join(parts)


def block(ctx: dict[str, Any] | None) -> str:
    """The <context> block for the model; empty when there is nothing to say."""
    if not ctx:
        return ""
    lines = []
    if ctx.get("tab"):
        lines.append(f"The user is in the {ctx['tab']} tab.")
    selected = (ctx.get("selected") or [])[:MAX_SELECTED]
    if selected:
        lines.append("Selected: " + "; ".join(selected) + ".")
    result = ctx.get("result") or {}
    if result.get("study"):
        words = f"Shown: {result.get('quantity') or 'a result'} of {result['study']}"
        if result.get("solver"):
            words += f" ({result['solver']})"
        if result.get("step"):
            words += f", step {result['step']}"
            if result.get("steps"):
                words += f" of {result['steps']}"
        lines.append(words + ".")
    if ctx.get("picked"):
        lines.append("Picked in the view: " + ", ".join(ctx["picked"][:MAX_SELECTED]) + ".")
    if not lines:
        return ""
    return "<context>\n" + "\n".join(lines) + "\n</context>"
