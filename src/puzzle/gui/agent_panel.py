"""The agent panel: chat with the built-in agent and review its proposals (ADR 0015).

The agent runs on the thread pool and changes the project only through the command
layer, so the window's history watcher picks up whatever it applies. Proposals
(the default "propose" level) are listed here to accept or reject, whole. The
autonomy level is set here too; "act" asks twice, as the double sign-off requires.
"""

from __future__ import annotations

import html
import json
import sys

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from puzzle.agent.agent import Agent, Event
from puzzle.agent.providers import ProviderError
from puzzle.agent.settings import load_settings, settings_path
from puzzle.commands.model import SetAutonomy
from puzzle.errors import PuzzleError

# Autonomy levels as people read them (ADR 0015).
LEVELS = {
    "advise": "only advise",
    "propose": "propose changes",
    "act_in_budget": "act within the budget",
    "act": "act freely",
}


class PromptBox(QPlainTextEdit):
    """The message box: wraps words and grows with what is typed (up to `MAX_LINES`,
    then scrolls). Settings > Agent says which key sends: by default Enter starts a new
    line and Ctrl+Enter (⌘+Enter on a Mac: Qt reports Command as Control) sends; or
    Enter sends and Shift+Enter starts a new line. The placeholder names the keys."""

    submitted = Signal()
    MAX_LINES = 8

    def __init__(self) -> None:
        super().__init__()
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setTabChangesFocus(True)
        self.document().contentsChanged.connect(self._fit)
        self.update_hint()
        self._fit()

    def text(self) -> str:
        return self.toPlainText()

    def setText(self, text: str) -> None:  # noqa: N802 - as QLineEdit, for callers
        from PySide6.QtGui import QTextCursor

        self.setPlainText(text)
        self.moveCursor(QTextCursor.MoveOperation.End)  # typing carries on at the end

    def update_hint(self) -> None:
        from puzzle.settings import SEND_KEYS, current

        if current().get("agent.send_key") == "enter":
            keys = "Enter to send, Shift+Enter for a new line"
        else:
            keys = f"{SEND_KEYS} to send"
        self.setPlaceholderText(f"Ask about the model, or ask for a change… ({keys})")

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt's name
        from puzzle.settings import current

        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            mods = event.modifiers()
            command = bool(mods & Qt.KeyboardModifier.ControlModifier)
            shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
            enter_sends = current().get("agent.send_key") == "enter"
            if command or (enter_sends and not shift):
                self.submitted.emit()
                return
            if enter_sends and shift:
                self.insertPlainText("\n")
                return
        super().keyPressEvent(event)

    def _fit(self) -> None:
        """One line high when empty, a line more per wrapped line, up to MAX_LINES."""
        doc = self.document()
        lines = 0
        block = doc.begin()
        while block.isValid():
            lines += max(1, block.layout().lineCount() if block.layout() else 1)
            block = block.next()
        line = self.fontMetrics().lineSpacing()
        margins = self.contentsMargins().top() + self.contentsMargins().bottom()
        frame = int(doc.documentMargin() * 2) + margins + 4
        self.setFixedHeight(min(max(lines, 1), self.MAX_LINES) * line + frame)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt's name: re-wrap, refit
        super().resizeEvent(event)
        self._fit()


class AgentPanel(QWidget):
    def __init__(self, window, provider_factory=None) -> None:
        super().__init__()
        self.window = window
        self.provider_factory = provider_factory  # tests pass a scripted provider
        self.agent: Agent | None = None

        self.level = QComboBox()
        for key, text in LEVELS.items():
            self.level.addItem(text, key)
        self.level.activated.connect(lambda _: self._set_level(self.level.currentData()))
        self.model_label = QLabel("")
        self.model_label.setStyleSheet("color: gray")
        new_chat = QPushButton("New chat")
        new_chat.clicked.connect(self.new_chat)
        key = QPushButton("API key…")
        key.setToolTip("Store the provider's API key in the system keychain")
        key.clicked.connect(self._set_key)
        top = QHBoxLayout()
        top.addWidget(QLabel("Agents may"))
        top.addWidget(self.level)
        top.addStretch()
        top.addWidget(key)
        top.addWidget(new_chat)

        self.transcript = QTextBrowser()
        self.transcript.setOpenExternalLinks(False)
        self.input = PromptBox()
        self.input.submitted.connect(self.send)
        ask = QHBoxLayout()
        ask.addWidget(self.input, 1)

        self.proposals = QListWidget()
        self.proposals.setWordWrap(True)
        accept, reject = QPushButton("Accept"), QPushButton("Reject")
        accept.clicked.connect(self.accept_selected)
        reject.clicked.connect(self.reject_selected)
        review = QHBoxLayout()
        review.addWidget(QLabel("Proposals"))
        review.addStretch()
        review.addWidget(accept)
        review.addWidget(reject)
        lower = QWidget()
        lower_layout = QVBoxLayout(lower)
        lower_layout.setContentsMargins(0, 0, 0, 0)
        lower_layout.addLayout(review)
        lower_layout.addWidget(self.proposals)

        chat = QWidget()
        chat_layout = QVBoxLayout(chat)
        chat_layout.setContentsMargins(0, 0, 0, 0)
        chat_layout.addWidget(self.transcript, 1)
        chat_layout.addLayout(ask)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(chat)
        split.addWidget(lower)
        split.setSizes([500, 160])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(top)
        layout.addWidget(self.model_label)
        layout.addWidget(split, 1)

    # --- project changes -------------------------------------------------------------

    def refresh(self) -> None:
        """Show the project's autonomy level and open proposals."""
        s = self.window.session
        if s is None:
            return
        self.level.setCurrentIndex(self.level.findData(s.project.agent.level))
        self.proposals.clear()
        for p in s.proposals():
            names = {str(o.id): o.name for o in s.project.objects.values()}
            item = QListWidgetItem(_proposal_text(p, names))
            item.setData(Qt.ItemDataRole.UserRole, p["id"])
            self.proposals.addItem(item)

    def project_opened(self) -> None:
        self.agent = None
        self.transcript.clear()
        self.refresh()

    # --- chat --------------------------------------------------------------------------

    def send(self, text: str | None = None):
        text = (text if isinstance(text, str) else self.input.text()).strip()
        if not text or self.window.session is None:
            return None
        if self.agent is None and not self._start_agent():
            return None
        self.input.clear()
        self._say("you", text)
        self.input.setReadOnly(True)  # until the answer is in
        agent = self.agent

        def work(emit):
            return agent.ask(text, lambda e: emit(_event_html(e)))

        def done(_answer) -> None:
            self.input.setReadOnly(False)
            self.window.refresh()
            tokens = agent.tokens
            self.model_label.setText(
                f"{self._model_text()} · {tokens['input']:,} in / {tokens['output']:,} out tokens"
            )

        def failed(exc) -> None:
            self.input.setReadOnly(False)
            self._say("error", str(exc))

        return self.window.background(
            "The agent is working…",
            work,
            done,
            failed=failed,
            output=self.transcript.append,
            lock=False,
        )

    def new_chat(self) -> None:
        if self.agent is not None:
            self.agent.reset()
        self.transcript.clear()

    def _start_agent(self) -> bool:
        try:
            if self.provider_factory is not None:
                provider, images = self.provider_factory(), None
            else:
                settings = load_settings()
                provider = settings.build()
                images = settings.images_allowed(provider)
            if sys.platform == "darwin":
                images = False  # off-screen VTK must render on macOS's main thread
            self.agent = Agent(self.window.session.root, provider, images=images)
        except ProviderError as exc:
            self._say(
                "error",
                f"{exc}. Configure the provider in {settings_path()} "
                "(provider, model, base_url) or with PUZZLE_AGENT_PROVIDER/MODEL.",
            )
            return False
        self.model_label.setText(self._model_text())
        return True

    def _set_key(self) -> None:

        from puzzle.agent.settings import store_key

        provider = load_settings().provider
        key, ok = QInputDialog.getText(
            self,
            "API key",
            f"{provider} API key (kept in the system keychain):",
            QLineEdit.EchoMode.Password,
        )
        if not ok or not key.strip():
            return
        try:
            store_key(provider, key.strip())
        except ProviderError as exc:
            self._say("error", str(exc))
            return
        self.agent = None  # rebuilt with the new key on the next message
        self._say("note", f"{provider} key stored in the keychain")

    def _model_text(self) -> str:
        p = self.agent.provider
        where = "remote" if p.remote else "local"
        return f"{p.name}: {p.model} ({where})"

    def _say(self, who: str, text: str) -> None:
        colour = {"you": "#2b6cb0", "error": "#c53030"}.get(who, "#444")
        self.transcript.append(f'<p><b style="color:{colour}">{who}</b>: {html.escape(text)}</p>')

    # --- review --------------------------------------------------------------------------

    def accept_selected(self):
        pid = self._selected_proposal()
        if pid is None:
            return None
        s = self.window.session

        def done(_):
            self.window.refresh()

        # A proposal can include a run: apply it off the GUI thread.
        return self.window.background(
            f"Applying proposal {pid}…", lambda _emit: s.accept(pid), done
        )

    def reject_selected(self) -> None:
        pid = self._selected_proposal()
        if pid is None:
            return
        try:
            self.window.session.reject(pid)
        except PuzzleError as exc:
            self.window.report(exc)
        self.window.refresh()

    def _selected_proposal(self) -> str | None:
        item = self.proposals.currentItem() or self.proposals.item(0)
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    # --- autonomy ------------------------------------------------------------------------

    def _set_level(self, level: str, confirm=None) -> None:
        s = self.window.session
        if s is None or level == s.project.agent.level:
            return
        try:
            s.submit(SetAutonomy(level=level, confirm=confirm))
        except PuzzleError as exc:
            if exc.code == "confirmation_required" and "token" in exc.details:
                if self._confirm_act():
                    self._set_level(level, exc.details["token"])
                    return
            else:
                self.window.report(exc)
        self.window.refresh()

    def _confirm_act(self) -> bool:
        first = QMessageBox.question(
            self,
            "Let agents act without a budget?",
            "Agents will apply changes and run studies without asking and without a "
            "compute budget. Every change stays in the history and can be undone.",
        )
        if first != QMessageBox.StandardButton.Yes:
            return False
        typed, ok = QInputDialog.getText(self, "Second confirmation", 'Type "act" to confirm:')
        return ok and typed.strip() == "act"


def _event_html(e: Event) -> str:
    if e.kind == "text":
        return f'<p><b style="color:#2f855a">agent</b>: {html.escape(e.text)}</p>'
    if e.kind == "tool_call":
        args = json.dumps(e.data.get("arguments", {}))
        args = args if len(args) <= 160 else args[:160] + "…"
        return f'<p style="color:gray">→ {html.escape(e.text)} {html.escape(args)}</p>'
    if e.kind == "error":
        return f'<p style="color:#c53030">✗ {html.escape(e.text[:300])}</p>'
    if e.kind == "tool_result":
        return f'<p style="color:gray">← {html.escape(_brief(e.text))}</p>'
    return ""


def _brief(text: str, limit: int = 160) -> str:
    """One line for a tool result: a JSON reply's status, else its text squeezed."""
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if isinstance(data, dict) and "status" in data:
        extra = f" {data['proposal']}" if data.get("proposal") else ""
        return f"{data['status']}{extra}"
    squeezed = " ".join(text.split())
    return squeezed if len(squeezed) <= limit else squeezed[:limit] + "…"


def _proposal_text(p: dict, names: dict | None = None) -> str:
    """A proposal in words: what would change, by name, and the agent's reason."""
    from puzzle.gui.window import _describe

    what = "; ".join(_describe(c, names or {}) for c in p["commands"])
    why = f" — {p['rationale']}" if p.get("rationale") else ""
    return f"{p['actor'].get('name', 'Agent')}: {what[:1].upper()}{what[1:]}{why}"
