"""A form's **Ask the agent…** (ADR 0072 section 10): the agent window opened with the
request typed in from the form's values, so the conversation starts there. Nothing is
sent: the user reads it, changes it and sends it."""

from __future__ import annotations

from collections.abc import Callable


def agent_ready(window) -> bool:
    """Whether the built-in agent can answer: a provider needing no key, or one whose key
    is set (tests give the panel a scripted provider)."""
    panel = getattr(window, "agent", None)
    if panel is None:
        return False
    if getattr(panel, "provider_factory", None) is not None:
        return True
    try:
        from puzzle.agent.settings import load_settings

        s = load_settings()
        return not s.needs_key() or s.api_key() is not None
    except Exception:  # noqa: BLE001 - unreadable settings: no agent to offer
        return False


def ask_agent(window, text: str) -> None:
    """The agent window shown, `text` in its prompt box, waiting to be sent."""
    window.agent_dock.show()
    window.agent_dock.raise_()
    window.agent.input.setText(text)
    window.agent.input.setFocus()


def add_ask_button(dialog, window, prompt: Callable[[dict], str]) -> None:
    """An **Ask the agent…** button on a one-dialog form (`gui.quick_form.QuickForm`),
    where the agent is set up: it closes the form and opens the agent window with
    `prompt(values)` typed in."""
    if not agent_ready(window):
        return
    from PySide6.QtWidgets import QDialogButtonBox

    button = dialog.buttons.addButton("Ask the agent…", QDialogButtonBox.ButtonRole.ActionRole)
    button.setToolTip("Close this form and ask the agent instead, its values in the request")

    def ask() -> None:
        text = prompt(dialog.values())
        dialog.reject()
        ask_agent(window, text)

    button.clicked.connect(ask)
    dialog.ask_button = button


def run_form(window, title: str, asks: list, *, help: str, prompt: Callable[[dict], str],  # noqa: A002
             intro: str = "", check=None, dialog_class=None) -> dict | None:  # fmt: skip
    """`window.ask_form` with a help key (F1) and the Ask the agent… button: the values,
    or None when cancelled, blocked or handed to the agent. Tests fill it as any form."""
    from puzzle.gui.quick_form import QuickForm

    dialog = (dialog_class or QuickForm)(title, asks, intro, check, window)
    dialog.setProperty("help", help)
    add_ask_button(dialog, window, prompt)
    if not window.run_dialog(dialog):
        return None
    if check is not None and check(dialog.values()):
        return None
    return dialog.values()
