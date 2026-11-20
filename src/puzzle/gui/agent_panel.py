"""The agent panel: chat with the built-in agent and review its proposals (ADR 0015).

The agent runs on the thread pool and changes the project only through the command
layer, so the window's history watcher picks up whatever it applies. Proposals
(the default "propose" level) are listed here to accept or reject, whole. The
autonomy level is set here too; "act" asks twice, as the double sign-off requires.

ADR 0090 package 12: a proposal is a card in the transcript (each value old → new, a
tick box per command, Show in view); what the user is looking at goes with a message
(the context chip); the tree follows the turn, its touched objects marked; a turn is
undone as one; conversations are kept per project (the Chats menu); the agent's plan
shows above the message box; "/" offers the skills and "@" the objects; an empty chat
offers three requests from the project's state.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QSize, QStringListModel, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QCompleter,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStyle,
    QStyleOptionButton,
    QStyleOptionToolButton,
    QStylePainter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from puzzle.agent.agent import Agent, Event
from puzzle.agent.providers import ProviderError
from puzzle.agent.settings import load_settings, settings_path
from puzzle.commands.model import SetAutonomy
from puzzle.errors import PuzzleError
from puzzle.gui.agent_cards import PlanList, ProposalCard, TurnRow
from puzzle.gui.agent_view import Transcript
from puzzle.gui.theme import role

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
        # "/" at the start offers the skills, "@" the project's objects (UXA-14).
        self.choices = {"/": lambda: [], "@": lambda: []}
        self.completer = QCompleter([], self)
        self.completer.setWidget(self)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.completer.activated.connect(self.complete_with)

    def word(self) -> str:
        """The word being typed, ending at the cursor ("@Ti", "/bo")."""
        cursor = self.textCursor()
        before = self.toPlainText()[: cursor.position()]
        return before.split()[-1] if before and not before[-1].isspace() else ""

    def offers(self) -> list[str]:
        """What "/" or "@" offers for the word being typed: none for other words."""
        word = self.word()
        if word.startswith("@"):
            names = self.choices["@"]()
        elif word.startswith("/") and self.toPlainText().lstrip().startswith(word):
            names = self.choices["/"]()
        else:
            return []
        prefix = word[1:].lower()
        return [n for n in names if prefix in n.lower()]

    def complete_with(self, choice: str) -> None:
        """Put the choice in place of the word being typed."""
        from PySide6.QtGui import QTextCursor

        word = self.word()
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Left,
                            QTextCursor.MoveMode.KeepAnchor, len(word))  # fmt: skip
        cursor.insertText(f"{word[:1]}{choice} " if word[:1] == "/" else f"{choice} ")
        self.setTextCursor(cursor)
        self.completer.popup().hide()

    def _offer(self) -> None:
        found = self.offers()
        if not found:
            self.completer.popup().hide()
            return
        self.completer.setModel(QStringListModel(found, self.completer))
        self.completer.setCompletionPrefix("")
        rect = self.cursorRect()
        rect.setWidth(260)
        self.completer.complete(rect)

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

        popup = self.completer.popup()
        if popup.isVisible() and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter,
                                                 Qt.Key.Key_Escape, Qt.Key.Key_Tab):  # fmt: skip
            event.ignore()  # the completer's list takes them
            return
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
        if event.text() or event.key() == Qt.Key.Key_Backspace:
            self._offer()

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

    # A pasted image, a dropped file or a long pasted text becomes an attachment
    # (ADR 0074); a short text pastes as usual.
    attached = Signal(object)  # an attachments.Attachment
    LONG_PASTE = 30  # lines

    def canInsertFromMimeData(self, source) -> bool:  # noqa: N802 - Qt's name
        return source.hasImage() or source.hasUrls() or super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source) -> None:  # noqa: N802 - Qt's name
        files = source.hasUrls() and any(u.isLocalFile() for u in source.urls())
        if source.hasImage() or files:
            for a in attachments_of(source):
                self.attached.emit(a)
            return
        text = source.text()
        if text.count("\n") >= self.LONG_PASTE:
            from puzzle.agent.attachments import from_text

            self.attached.emit(from_text(text))
            return
        super().insertFromMimeData(source)


SEES = "What you have selected and the result shown go with your message; click to leave them out"


class Chip(QToolButton):
    """A button whose words are cut short with "…" when there is no room for them.

    A plain button is as wide as its words, and the words name what is on show: a
    result's whole name made the panel a thousand pixels wide at least, wider with each
    result clicked, and never draggable narrower."""

    NARROWEST = 90

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

    def minimumSizeHint(self) -> QSize:
        hint = super().sizeHint()
        return QSize(min(hint.width(), self.NARROWEST), hint.height())

    def shown_text(self) -> str:
        """The words as drawn at this width."""
        option = QStyleOptionToolButton()
        self.initStyleOption(option)
        spare = super().sizeHint().width() - self.fontMetrics().horizontalAdvance(option.text)
        return self.fontMetrics().elidedText(
            option.text, Qt.TextElideMode.ElideRight, max(self.width() - spare, 0)
        )

    def paintEvent(self, _event) -> None:
        option = QStyleOptionToolButton()
        self.initStyleOption(option)
        option.text = self.shown_text()
        QStylePainter(self).drawComplexControl(QStyle.ComplexControl.CC_ToolButton, option)


class Suggestion(QPushButton):
    """A suggested question, cut short with "…" in a narrow panel (its whole text is its
    tooltip): a study's long name in one held the panel wide."""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setToolTip(text)
        self.setStyleSheet("text-align: left;")
        # a push button's own policy takes its size hint as its least width
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def minimumSizeHint(self) -> QSize:
        hint = super().sizeHint()
        return QSize(min(hint.width(), Chip.NARROWEST), hint.height())

    def shown_text(self) -> str:
        spare = super().sizeHint().width() - self.fontMetrics().horizontalAdvance(self.text())
        return self.fontMetrics().elidedText(
            self.text(), Qt.TextElideMode.ElideRight, max(self.width() - spare, 0)
        )

    def paintEvent(self, _event) -> None:
        option = QStyleOptionButton()
        self.initStyleOption(option)
        option.text = self.shown_text()
        QStylePainter(self).drawControl(QStyle.ControlElement.CE_PushButton, option)


def attachments_of(source) -> list:
    """The files in pasted or dropped data: an image as PNG, local files as theirs.
    One that can't be read (too large, not text) is skipped."""
    from PySide6.QtCore import QBuffer, QIODevice

    from puzzle.agent import attachments

    out = []
    if source.hasImage():
        image = source.imageData()
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buf, "PNG")
        out.append(attachments.from_image(bytes(buf.data())))
    elif source.hasUrls():
        for url in source.urls():
            if url.isLocalFile():
                try:
                    out.append(attachments.from_path(url.toLocalFile()))
                except (OSError, attachments.AttachmentError):
                    continue
    return out


class AgentPanel(QWidget):
    """The chat (ADR 0015, 0074): a transcript of rich replies, a status line while the
    agent works, a composer with attachments and the model chip, the proposals below."""

    def __init__(self, window, provider_factory=None) -> None:
        super().__init__()
        self.window = window
        self.provider_factory = provider_factory  # tests pass a scripted provider
        self.agent: Agent | None = None
        self.pending: list = []  # attachments for the next message
        self.busy = False
        self._stop: threading.Event | None = None
        self._turn = 0  # the running turn's number: a stale turn's events are dropped
        self._started = 0.0
        self._doing = ""
        self.models: list | None = None  # what the chip offers, read once off the GUI
        self.model_notes: list[str] = []
        self.setAcceptDrops(True)

        self.level = QComboBox()
        for key, text in LEVELS.items():
            self.level.addItem(text, key)
        self.level.activated.connect(lambda _: self._set_level(self.level.currentData()))
        # What "act within the budget" may spend (ADR 0015), shown with what is used.
        self.budget_button = QToolButton(text="Budget…")
        self.budget_button.setToolTip("How many runs, how much solver time and remote "
                                      "core-hours agents may use before they must ask")  # fmt: skip
        self.budget_button.clicked.connect(lambda: self.edit_budget())
        self.budget_used = QLabel("")
        role(self.budget_used, "muted")
        new_chat = QPushButton("New chat")
        new_chat.clicked.connect(lambda: self.new_chat())
        # Kept conversations (UXA-4): open an earlier one, export, copy.
        self.chats_button = QToolButton(text="Chats")
        self.chats_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.chats_button.setToolTip("Earlier conversations about this project, kept on "
                                     "this computer; export or copy this one")  # fmt: skip
        self.chats_menu = QMenu(self.chats_button)
        self.chats_menu.aboutToShow.connect(self._fill_chats)
        self.chats_button.setMenu(self.chats_menu)
        top = QHBoxLayout()
        top.addWidget(QLabel("Agents may"))
        top.addWidget(self.level)
        top.addWidget(self.budget_button)
        top.addWidget(self.budget_used)
        top.addStretch()
        top.addWidget(self.chats_button)
        top.addWidget(new_chat)

        self.transcript = Transcript()
        self.transcript.object_clicked.connect(lambda oid: self.window.show_object(oid))
        self.chat = None  # the conversation shown (chats.Chat), before the agent is built
        self.cards: dict[str, ProposalCard] = {}  # proposal id -> its card
        self.turn_rows: dict[str, TurnRow] = {}
        self._touched: list[dict] = []  # what the running turn changed
        self._progress = None  # a run's progress.State while a tool solves (UXA-8)
        self.context_tokens = 0
        self.status = QLabel("")
        role(self.status, "muted")
        self.stop_button = QToolButton(text="Stop")
        self.stop_button.setToolTip("End the agent's turn after the step it is on")
        self.stop_button.clicked.connect(self.stop)
        self.stop_button.hide()
        status_row = QHBoxLayout()
        status_row.addWidget(self.status, 1)
        status_row.addWidget(self.stop_button)
        self._ticker = QTimer(self, interval=1000)
        self._ticker.timeout.connect(self._show_doing)

        self.chips_box = QWidget()
        self.chips = QHBoxLayout(self.chips_box)
        self.chips.setContentsMargins(0, 0, 0, 0)
        self.chips.addStretch()
        self.chips_box.hide()
        self.plan = PlanList()
        # Requests to start from, on an empty chat (UXA-14).
        self.suggestions_box = QWidget()
        self.suggestions = QVBoxLayout(self.suggestions_box)
        self.suggestions.setContentsMargins(0, 0, 0, 0)
        self.continue_button = QPushButton("Continue")
        self.continue_button.setToolTip("Let the agent carry on where it stopped")
        self.continue_button.clicked.connect(lambda: self.send_continue())
        self.continue_button.hide()
        # What the user is looking at, sent with the message unless switched off (UXA-3).
        self.context_chip = Chip()
        self.context_chip.setCheckable(True)
        self.context_chip.setChecked(True)
        self.context_chip.setToolTip(SEES)
        self.context_chip.toggled.connect(lambda _on: self.show_context())
        self.input = PromptBox()
        self.input.submitted.connect(self.send)
        self.input.attached.connect(self.attach)
        self.input.choices = {"/": self._skill_names, "@": self._object_names}
        attach = QToolButton(text="Attach…")
        attach.setToolTip("Send a file with the next message: an image, a PDF or a text "
                          "file (or paste or drop one here)")  # fmt: skip
        attach.clicked.connect(self._choose_files)
        self.model_chip = QToolButton()
        self.model_chip.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.model_chip.setToolTip("The model the agent uses, and where it runs")
        self.model_menu = QMenu(self.model_chip)
        self.model_menu.aboutToShow.connect(self._fill_models)
        self.model_chip.setMenu(self.model_menu)
        send = QToolButton(text="Send")
        send.clicked.connect(lambda: self.send())
        bar = QHBoxLayout()
        bar.addWidget(attach)
        bar.addWidget(self.model_chip)
        bar.addWidget(self.context_chip)
        bar.addStretch()
        bar.addWidget(self.continue_button)
        bar.addWidget(send)
        self._show_chip()

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
        chat_layout.addWidget(self.suggestions_box)
        chat_layout.addLayout(status_row)
        chat_layout.addWidget(self.plan)
        chat_layout.addWidget(self.chips_box)
        chat_layout.addWidget(self.input)
        chat_layout.addLayout(bar)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(chat)
        split.addWidget(lower)
        split.setSizes([500, 160])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(top)
        layout.addWidget(split, 1)
        # macOS draws push buttons with wide margins of their own; lay them out by their
        # visible edges so the rows' spacing is what the layouts say.
        for b in (new_chat, accept, reject):
            b.setAttribute(Qt.WidgetAttribute.WA_LayoutUsesWidgetRect)

    # --- project changes -------------------------------------------------------------

    def refresh(self) -> None:
        """Show the project's autonomy level and open proposals."""
        s = self.window.session
        if s is None:
            return
        self.level.setCurrentIndex(self.level.findData(s.project.agent.level))
        self._show_budget()
        self.proposals.clear()
        for p in s.proposals():
            names = {str(o.id): o.name for o in s.project.objects.values()}
            item = QListWidgetItem(_proposal_text(p, names))
            item.setData(Qt.ItemDataRole.UserRole, p["id"])
            self.proposals.addItem(item)

    def project_opened(self) -> None:
        """Another project: its last conversation reopened (UXA-4), or its suggestions."""
        self._end_turn()  # the old turn edits the old project: stopped, never shown here
        self.agent = None
        self.transcript.clear()
        self.cards, self.turn_rows = {}, {}
        self.chat = None
        s = self.window.session
        if s is not None:
            from puzzle.agent import chats

            try:
                self.chat = chats.latest(s.root)
            except OSError:
                self.chat = None
        self._replay()
        self.refresh()

    # --- kept conversations (UXA-4) --------------------------------------------------

    def current_chat(self):
        """The conversation shown: the agent's once it is built."""
        return self.agent.chat if self.agent is not None else self.chat

    def _replay(self) -> None:
        """Draw a kept conversation again: the messages, the tools in words, its plan,
        and Undo this turn where a turn's changes are still in effect."""
        from puzzle.agent import evidence

        self.transcript.clear()
        self.cards, self.turn_rows = {}, {}
        chat = self.current_chat()
        self.plan.show_plan(chat.plan if chat is not None else [])
        self.continue_button.hide()
        if chat is None or not chat.messages:
            self._show_suggestions()
            return
        self._show_suggestions(False)
        touched = {r["id"]: r for r in chat.turns}
        last_turn = None
        for m in chat.messages:
            if m["role"] == "user":
                if last_turn is not None:
                    self._turn_row(last_turn, touched.get(last_turn, {}).get("touched", []))
                last_turn = m.get("turn")
                names = [a["name"] for a in m.get("attachments") or []]
                self.transcript.user(m.get("shown", m.get("content", "")), names)
            elif m["role"] == "assistant":
                for c in m.get("tool_calls") or []:
                    self.transcript.tool(f"→ {evidence.call_words(c.name, c.arguments)}")
                if m.get("text"):
                    self.transcript.reply(m["text"])
            elif m["role"] == "tool":
                words = evidence.result_words(m.get("name", ""), m.get("content", ""))
                self.transcript.tool(f"← {words}", error=bool(m.get("is_error")))
        if last_turn is not None:
            self._turn_row(last_turn, touched.get(last_turn, {}).get("touched", []))

    def _fill_chats(self) -> None:
        from puzzle.agent import chats

        menu = self.chats_menu
        menu.clear()
        s = self.window.session
        if s is None:
            menu.addAction("Open a project first").setEnabled(False)
            return
        current = self.current_chat()
        found = chats.listing(s.root)
        if not found:
            menu.addAction("No earlier conversations").setEnabled(False)
        for c in found[:20]:
            when = c["updated"].replace("T", " ")[:16]
            action = menu.addAction(f"{c['title'] or 'Untitled'}   ({when} UTC)")
            action.setCheckable(True)
            action.setChecked(current is not None and current.id == c["id"])
            action.triggered.connect(lambda _=False, cid=c["id"]: self.open_chat(cid))
        menu.addSeparator()
        menu.addAction("Export this conversation…", lambda: self.export_chat())
        menu.addAction("Copy the whole conversation", self.transcript.copy_all)
        delete = menu.addAction("Delete this conversation", lambda: self.delete_chat())
        delete.setEnabled(current is not None and bool(current.messages))

    def open_chat(self, chat_id: str) -> bool:
        """Show and continue a kept conversation; not while a turn runs."""
        from puzzle.agent import chats

        s = self.window.session
        if s is None or self.busy:
            self.transcript.note("Stop the agent's turn first, then open the conversation.")
            return False
        try:
            chat = chats.load(s.root, chat_id)
        except (OSError, ValueError, KeyError) as exc:
            self.window.report(exc)
            return False
        if self.agent is not None:
            self.agent.open(chat)
        else:
            self.chat = chat
        self._replay()
        return True

    def export_chat(self, path: str | None = None) -> str | None:
        """This conversation as a Markdown file the user picks (`path` for tests)."""
        from puzzle.agent import chats
        from puzzle.project import atomic

        chat = self.current_chat()
        if chat is None or not chat.messages:
            self.transcript.note("Nothing to export yet.")
            return None
        if path is None:
            from PySide6.QtWidgets import QFileDialog

            path, _ = QFileDialog.getSaveFileName(self, "Export the conversation",
                                                  f"{chat.title or 'conversation'}.md",
                                                  "Markdown (*.md)")  # fmt: skip
            if not path:
                return None
        try:
            atomic.write_text(Path(path), chats.export_markdown(chat), encoding="utf-8")
        except OSError as exc:
            self.window.report(exc)
            return None
        self.window.statusBar().showMessage(f"Conversation exported to {path}", 8000)
        return path

    def delete_chat(self) -> None:
        from puzzle.agent import chats

        chat, s = self.current_chat(), self.window.session
        if chat is None or s is None:
            return
        chats.delete(s.root, chat.id)
        self.new_chat()

    # --- context, suggestions, completion (UXA-3, UXA-14) ----------------------------

    def context(self) -> dict | None:
        """What the user is looking at now, when the chip is on."""
        if not self.context_chip.isChecked():
            return None
        getter = getattr(self.window, "agent_context", None)
        try:
            return getter() if getter is not None else None
        except Exception:  # noqa: BLE001 - context is a help, never a failure
            logging.getLogger("puzzle.agent").debug("no context", exc_info=True)
            return None

    def show_context(self) -> None:
        """The chip's words follow the selection and the result shown."""
        from puzzle.agent.context import chip

        if not self.context_chip.isChecked():
            self.context_chip.setText("Context off")
            return
        words = chip(self.context())
        self.context_chip.setText(f"Sees: {words}" if words else "Sees: the model")
        self.context_chip.setToolTip(f"{words}. {SEES}" if words else SEES)  # cut short when narrow

    def _show_suggestions(self, on: bool = True) -> None:
        while self.suggestions.count():
            w = self.suggestions.takeAt(0).widget()
            if w is not None:
                w.hide()
                w.deleteLater()
        s = self.window.session
        if not on or s is None:
            self.suggestions_box.hide()
            return
        from puzzle.agent.suggest import suggestions

        try:
            runs = self.window._store().runs()
        except Exception:  # noqa: BLE001 - suggestions without runs
            runs = []
        head = QLabel("Try asking:")
        role(head, "muted")
        self.suggestions.addWidget(head)
        for text in suggestions(s.project, runs):
            b = Suggestion(text)
            b.clicked.connect(lambda _=False, text=text: self.send(text))
            self.suggestions.addWidget(b)
        self.suggestions_box.show()

    def _skill_names(self) -> list[str]:
        from puzzle.agent.suggest import skill_choices

        return [name for name, _when in skill_choices()]

    def _object_names(self) -> list[str]:
        from puzzle.agent.suggest import object_choices

        s = self.window.session
        return object_choices(s.project) if s is not None else []

    def _end_turn(self) -> None:
        """Stop a running turn and stop listening to it (ADR 0090, AGENT-3): a new chat,
        another project or another model starts from a clean conversation."""
        if not self.busy:
            return
        if self._stop is not None:
            self._stop.set()
        if self.agent is not None:
            self.agent.reset()
        self._turn += 1

    # --- chat --------------------------------------------------------------------------

    def send(self, text: str | None = None):
        from puzzle.agent.context import block
        from puzzle.agent.suggest import slash
        from puzzle.agent.toolsets import skill_groups

        text = (text if isinstance(text, str) else self.input.text()).strip()
        if (not text and not self.pending) or self.window.session is None or self.busy:
            return None
        self.input.clear()
        files, self.pending = list(self.pending), []
        self._show_attachments()
        self._show_suggestions(False)
        self.continue_button.hide()
        self.transcript.user(text, [a.label() for a in files])
        asked, skill = slash(text)  # "/bolts ..." reads the skill first (UXA-14)
        context = block(self.context())
        stop = threading.Event()
        self._turn += 1
        turn = self._turn
        self._touched = []
        self._begin(stop, "Starting the agent…" if self.agent is None else "Thinking…")

        def current() -> bool:
            return turn == self._turn

        def work(emit, emit_event):
            if self.agent is None:  # built here, off the GUI thread (ADR 0074)
                self.agent = self._build_agent()
            if skill:
                self.agent.load(*skill_groups().get(skill, ()))
            return self.agent.ask(asked, emit_event, attachments=files, stop=stop,
                                  context=context)  # fmt: skip

        def done(_answer) -> None:
            self._end()
            if current():  # a reply cut off by Stop was never said: taken away
                self.transcript.end_live()
                if self.agent is not None:
                    self._turn_row(self.agent.turn_id, self._touched)

        def failed(exc) -> None:
            self._end()
            if not current():  # a turn ended by a new chat: its failure is not this chat's
                return
            self.transcript.end_live()  # text streamed before the failure was never said
            if isinstance(exc, ProviderError):  # the plain sentence, the provider's words after
                said = exc.plain() if exc.kind else (
                    f"{exc} Choose a model with the model button, or configure the provider "
                    f"in Settings > Agent ({settings_path()}).")  # fmt: skip
                if exc.kind:
                    logging.getLogger("puzzle.agent").warning("%s", exc)
                self.transcript.note(said, error=True)
            else:
                self.transcript.note(str(exc), error=True)

        def on_event(e: Event) -> None:
            if current():
                self._event(e)

        return self.window.background(
            "The agent is working…", work, done, failed=failed, on_event=on_event,
            lock=False,
        )  # fmt: skip

    def send_continue(self):
        """Carry on a task the round limit stopped (UXA-7)."""
        from puzzle.agent.agent import CONTINUE

        return self.send(CONTINUE)

    def _build_agent(self) -> Agent:
        if self.provider_factory is not None:
            provider, images = self.provider_factory(), None
        else:
            settings = load_settings()
            provider = settings.build()
            images = settings.images_allowed(provider)
        if sys.platform == "darwin":
            images = False  # off-screen VTK must render on macOS's main thread
        return Agent(self.window.session.root, provider, images=images, chat=self.chat,
                     keep=True)  # fmt: skip

    def _turn_row(self, turn: str | None, touched: list[dict]) -> None:
        """Undo this turn, under a turn whose changes are still in effect (UXA-5)."""
        s = self.window.session
        if not turn or s is None or not touched or turn in self.turn_rows:
            return
        try:
            if not s.turn_changes(turn):
                return
        except PuzzleError:
            return
        row = TurnRow(self, turn, touched)
        self.turn_rows[turn] = row
        self.transcript.card(row)

    def undo_turn(self, turn: str) -> bool:
        """Take back every change of the agent's turn, as one undoable step."""
        s = self.window.session
        if s is None:
            return False
        try:
            s.undo_turn(turn)
        except PuzzleError as exc:
            self.window.report(exc)
            return False
        if turn in self.turn_rows:
            self.turn_rows[turn].undone()
        self.window.agent_touched([], clear=True)
        self.window.refresh()
        return True

    def _begin(self, stop, doing: str) -> None:
        self.busy, self._stop, self._started = True, stop, time.monotonic()
        self._doing = doing
        self.stop_button.show()
        self._show_doing()
        self._ticker.start()
        self.window.agent_turn(True)

    def _end(self) -> None:
        self.busy = False
        self._ticker.stop()
        self.stop_button.hide()
        self._progress = None
        seconds = time.monotonic() - self._started
        if self.agent is not None:
            t = self.agent.tokens
            held = (f" · about {self.context_tokens / 1000:.1f}k tokens in context"
                    if self.context_tokens else "")  # UXA-10's meter  # fmt: skip
            self.status.setText(f"{self._chip_text()} · {t['input']:,} in / {t['output']:,} "
                                f"out tokens · {seconds:.0f} s{held}")  # fmt: skip
        else:
            self.status.setText("")
        self._show_chip()
        self.window.agent_turn(False)

    def stop(self) -> None:
        if self._stop is not None:
            self._stop.set()
            self._doing = "Stopping…"  # a reply at once, a run it started, else this step
            self._show_doing()

    def _show_doing(self) -> None:
        if self.busy:
            doing = self._doing
            if self._progress is not None and self._progress.line():  # a run (UXA-8)
                doing = f"Solving: {self._progress.line()}"
            self.status.setText(f"{doing}  {time.monotonic() - self._started:.0f} s")

    def _event(self, e: Event) -> None:
        if e.kind == "status":
            self._doing = e.text
            self._show_doing()
        elif e.kind == "delta":
            self.transcript.stream(e.text)
        elif e.kind == "thinking":
            self.transcript.thinking(e.text)
        elif e.kind == "text":
            self.transcript.reply(e.text)
        elif e.kind == "tool_call":
            words = e.data.get("words") or e.text
            self.transcript.tool(f"→ {words}")
        elif e.kind == "tool_result":
            words = e.data.get("words") or _brief(e.text)
            self.transcript.tool(f"← {words}", objects=e.data.get("touched") or None,
                                 rows=e.data.get("rows") or None)  # fmt: skip
            if e.data.get("image"):  # what the agent rendered, shown to the user too
                self.transcript.picture(e.data["image"], e.data.get("sent", False))
        elif e.kind == "error":
            self.transcript.tool(f"✗ {e.text[:300]}", error=True)
            if e.data.get("continue"):
                self.continue_button.show()
        elif e.kind == "note":
            self.transcript.note(e.text)
        elif e.kind == "touched":
            self._touched += e.data["objects"]
            self.window.agent_touched(e.data["objects"])
        elif e.kind == "proposal":
            self.show_proposal_card(e.data["id"])
        elif e.kind == "plan":
            self.plan.show_plan(e.data["plan"])
        elif e.kind == "progress":
            from puzzle import progress

            if self._progress is None:
                self._progress = progress.State()
            self._progress.add(e.data["event"])
            self._show_doing()
        elif e.kind == "usage":
            self.context_tokens = e.data.get("context", 0)
        elif e.kind == "ran":  # a run the agent started has ended (UXA-8)
            study = e.data.get("study") or "a study"
            words = (f"The agent's run of {study} failed" if e.data.get("failed")
                     else f"The agent's run of {study} finished")  # fmt: skip
            self.window.toasts.show(words, state="error" if e.data.get("failed") else "info")

    def new_chat(self) -> None:
        """A new conversation; a turn still running is stopped first and its replies
        no longer shown. The one before stays among the kept chats."""
        self._end_turn()
        if self.agent is not None:
            self.agent.reset()
        self.chat = None
        self.transcript.clear()
        self.cards, self.turn_rows = {}, {}
        self.plan.show_plan([])
        self.continue_button.hide()
        self._show_suggestions()

    # --- attachments (ADR 0074) ------------------------------------------------------

    def attach(self, attachment) -> None:
        self.pending.append(attachment)
        self._show_attachments()

    def _choose_files(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        from puzzle.agent.attachments import AttachmentError, from_path

        paths, _ = QFileDialog.getOpenFileNames(self, "Attach files")
        for p in paths:
            try:
                self.attach(from_path(p))
            except (OSError, AttachmentError) as exc:
                self.transcript.note(str(exc), error=True)

    def _show_attachments(self) -> None:
        while self.chips.count() > 1:
            item = self.chips.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()
        for i, a in enumerate(self.pending):
            chip = QToolButton(text=f"📎 {a.label()}  ✕")
            chip.setToolTip("Remove it from the message")
            chip.clicked.connect(lambda _=False, i=i: self._drop(i))
            self.chips.insertWidget(i, chip)
        self.chips_box.setVisible(bool(self.pending))

    def _drop(self, i: int) -> None:
        del self.pending[i]
        self._show_attachments()

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt's name
        if event.mimeData().hasUrls() or event.mimeData().hasImage():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt's name
        for a in attachments_of(event.mimeData()):
            self.attach(a)
        event.acceptProposedAction()

    # --- the model chip (ADR 0074) ---------------------------------------------------

    def _chip_text(self) -> str:
        if self.agent is not None:
            p = self.agent.provider
            try:
                where = load_settings().label(p.name)
            except ProviderError:
                where = "a server" if p.remote else "this computer"
            return f"{p.model} · {where}"
        s = load_settings()
        try:
            from puzzle.agent.catalog import entry

            model = s.model or entry(s.provider).default_model or "choose a model"
            return f"{model} · {s.label()}"
        except ProviderError:
            return f"{s.model or 'choose a model'} · {s.provider}"

    def _show_chip(self) -> None:
        self.model_chip.setText(f"{self._chip_text()} ▾")

    def _fill_models(self) -> None:
        """Every enabled provider's models, grouped by provider: favourites, those known
        to call tools, then under "may not call tools" the rest (ADR 0084 decision 5);
        a provider that lists none offers "Type a model…"."""
        from puzzle.agent.catalog import entry

        menu = self.model_menu
        menu.clear()
        s = load_settings()
        if self.models is None:
            menu.addAction("Looking for models…").setEnabled(False)
            self._read_models()
        else:
            from puzzle.agent.pick import kept_score

            current = (s.provider, s.model)
            scores = {c.model: kept_score(c.model) for c in self.models}
            groups: dict[str, list] = {}
            for c in self.models:
                groups.setdefault(c.provider, []).append(c)
            for pid in [p for p in s.ids() if s.enabled(p)]:
                choices = groups.get(pid, [])
                typed = entry(pid).models == "typed" or pid.startswith("compatible:")
                if not choices and not typed:
                    continue
                head = menu.addAction(s.label(pid))
                head.setEnabled(False)
                unsure = False
                for c in choices:
                    if c.tools is not True and not c.favourite and not unsure:
                        unsure = True
                        menu.addAction("      may not call tools").setEnabled(False)
                    star = "★ " if c.favourite else ""
                    score = scores.get(c.model, "")  # measured by the suite (ADR 0022)
                    action = menu.addAction(f"   {star}{c.model}" + (f"   ({score})" if score
                                                                      else ""))  # fmt: skip
                    action.setCheckable(True)
                    action.setChecked((c.provider, c.model) == current)
                    action.triggered.connect(lambda _=False, c=c: self.choose(c))
                if typed:
                    menu.addAction("   Type a model…", lambda p=pid: self._type_model(p))
            for note in self.model_notes:
                menu.addAction(note).setEnabled(False)
            menu.addAction("Look again", self._reread_models)
        menu.addSeparator()
        menu.addAction("Pick a local model…", self._pick_model)
        menu.addAction("API key…", self._set_key)
        menu.addAction("Providers and keys…", self._open_settings)

    def _read_models(self) -> None:
        from puzzle.agent.settings import available_models

        def done(found) -> None:
            self.models, self.model_notes = found
            if self.model_menu.isVisible():
                self._fill_models()

        self.window.background("Looking for models…", lambda _emit: available_models(),
                               done, lock=False)  # fmt: skip

    def _reread_models(self) -> None:
        self.models = None
        self._read_models()

    def _type_model(self, pid: str) -> None:
        from puzzle.agent.settings import ModelChoice

        s = load_settings()
        model, ok = QInputDialog.getText(self, "Model", f"{s.label(pid)} model (for Thinking "
                                         "Machines, the checkpoint's tinker:// path):")  # fmt: skip
        if ok and model.strip():
            self.choose(ModelChoice(pid, model.strip(), True, None, False, s.label(pid)))

    def choose(self, choice) -> None:
        """Use `choice` from the next message on: a user setting, a new chat. A model
        not known to call tools is asked once with a trivial tool (off the GUI thread)
        and refused if it doesn't, naming its provider's models that do."""
        from puzzle.agent import capabilities
        from puzzle.agent.settings import choose_model

        def switch(_=None) -> None:
            choose_model(choice)
            self._end_turn()
            self.agent = None
            self._show_chip()
            self.transcript.note(f"Now using {choice.model} ({choice.where()}); a new chat "
                                 "starts.")  # fmt: skip

        if choice.tools is True or self.provider_factory is not None:
            switch()
            return
        others = [c.model for c in self.models or []
                  if c.provider == choice.provider and c.tools is True]  # fmt: skip

        def check(_emit) -> None:
            provider = load_settings().build(choice.provider, choice.model)
            capabilities.check(provider, others)

        def refused(exc) -> None:
            said = exc.plain() if isinstance(exc, ProviderError) and exc.kind != "tools" else exc
            self.transcript.note(f"Not switched to {choice.model}: {said}", error=True)

        self.transcript.note(f"Checking that {choice.model} can call tools…")
        self.window.background(f"Checking {choice.model}…", check, switch, failed=refused,
                               lock=False)  # fmt: skip

    def _set_key(self) -> None:
        from puzzle.agent.settings import paste_problem, store_key

        s = load_settings()
        provider = s.provider
        key, ok = QInputDialog.getText(
            self,
            "API key",
            f"{s.label(provider)} API key (kept in the system keychain):",
            QLineEdit.EchoMode.Password,
        )
        if not ok or not key.strip():
            return
        try:
            store_key(provider, key.strip())
        except ProviderError as exc:
            self.transcript.note(str(exc), error=True)
            return
        problem = paste_problem(provider, key.strip())
        self.agent, self.models = None, None  # rebuilt with the new key, models re-read
        self.transcript.note(f"{s.label(provider)} key stored in the keychain"
                             + (f" ({problem})" if problem else ""))  # fmt: skip

    def _open_settings(self) -> None:
        opener = getattr(self.window, "open_settings", None)
        if opener is not None:
            opener()
        self.agent, self.models = None, None
        self._show_chip()

    def _pick_model(self) -> None:
        from puzzle.gui.pick_dialog import PickDialog

        PickDialog(self.window).exec()
        self.agent, self.models = None, None
        self._show_chip()

    # --- review --------------------------------------------------------------------------

    def show_proposal_card(self, pid: str) -> ProposalCard | None:
        """A proposal as a card in the transcript (UXA-2)."""
        s = self.window.session
        if s is None or pid in self.cards:
            return self.cards.get(pid)
        found = next((p for p in s.proposals() if p["id"] == pid), None)
        if found is None:
            return None
        card = ProposalCard(self, found)
        self.cards[pid] = card
        self.transcript.card(card, f"[proposal {len(self.cards)}]")
        self.window.docks.show("agent")
        return card

    def accept_proposal(self, pid: str, only: list[int] | None = None):
        """Accept a proposal's ticked commands (all: `only` None), off the GUI thread."""
        s = self.window.session
        if s is None:
            return None
        card = self.cards.get(pid)

        def done(_):
            self._end_preview()
            if card is not None:
                n = len(only) if only is not None else len(card.ticks)
                card.decided(f"Accepted ({n} of {len(card.ticks)})" if only is not None
                             else "Accepted")  # fmt: skip
            self.window.refresh()

        def failed(exc):
            self.window.report(exc)
            self.window.refresh()

        return self.window.background(f"Applying proposal {pid}…",
                                      lambda _emit: s.accept(pid, only=only), done,
                                      failed=failed)  # fmt: skip

    def _end_preview(self) -> None:
        """A proposal's preview (Show in view) ends when it is decided."""
        w = self.window
        if getattr(w, "pending", None) is not None and w.workbench.tool is None:
            w.pending = None

    def reject_proposal(self, pid: str) -> None:
        self._end_preview()
        try:
            self.window.session.reject(pid)
        except PuzzleError as exc:
            self.window.report(exc)
        else:
            if pid in self.cards:
                self.cards[pid].decided("Rejected")
        self.window.refresh()

    def accept_selected(self):
        pid = self._selected_proposal()
        if pid is None:
            return None
        s = self.window.session

        def done(_):
            if pid in self.cards:
                self.cards[pid].decided("Accepted")
            self.window.refresh()

        # A proposal can include a run: apply it off the GUI thread.
        return self.window.background(
            f"Applying proposal {pid}…", lambda _emit: s.accept(pid), done
        )

    def reject_selected(self) -> None:
        pid = self._selected_proposal()
        if pid is None:
            return
        self.reject_proposal(pid)

    def _selected_proposal(self) -> str | None:
        """The proposal picked in the list; none picked is none (ADR 0090, AGENT-11:
        Accept used to take the first)."""
        item = self.proposals.currentItem()
        if item is None or not item.isSelected():
            self.transcript.note("Pick a proposal in the list first.")
            return None
        return item.data(Qt.ItemDataRole.UserRole)

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

    def _show_budget(self) -> None:
        """The budget's button and what is used of it, shown only at act within budget."""
        s = self.window.session
        on = s is not None and s.project.agent.level == "act_in_budget"
        self.budget_button.setVisible(on)
        self.budget_used.setVisible(on)
        if not on:
            return
        b, used = s.project.agent.budget, s.budget_used()
        text = (f"{used['runs']} of {b.runs} runs, {_minutes(used['solver_seconds'])} of "
                f"{_minutes(b.solver_seconds)} used")  # fmt: skip
        if used["core_hours"] or b.core_hours != 2.0:
            text += f", {used['core_hours']:.3g} of {b.core_hours:g} core-hours"
        self.budget_used.setText(text)

    def edit_budget(self, values: dict | None = None) -> bool:
        """Set the agents' budget in one form (`values` fills it, for tests). Saving
        starts the count of what they have used again, as any autonomy change does."""
        from puzzle.gui.quick_form import Ask

        s = self.window.session
        if s is None:
            return False
        b = s.project.agent.budget
        asks = [
            Ask("runs", "Fresh solver runs", kind="text", default=str(b.runs),
                help="runs reused from earlier ones don't count"),
            Ask("minutes", "Solver time (minutes)", kind="text",
                default=f"{b.solver_seconds / 60:g}",
                help="the total wall time of their runs; each run gets what is left"),
            Ask("core_hours", "Remote core-hours", kind="text", default=f"{b.core_hours:g}",
                help="cores × hours on remote hosts (ADR 0021), which can cost money"),
        ]  # fmt: skip

        def check(v):
            try:
                _budget(v)
            except ValueError as exc:
                return str(exc)
            return None

        intro = (
            'What agents may use at "act within the budget" before they must ask. '
            "Saving starts the count of what they have used again."
        )
        got = (
            values
            if values is not None
            else self.window.ask_form("Agents' budget", asks, intro, check)
        )
        if not got:
            return False
        try:
            budget = _budget(got)
        except ValueError as exc:
            self.window.statusBar().showMessage(str(exc), 8000)
            return False
        try:
            s.submit(SetAutonomy(budget=budget))
        except PuzzleError as exc:
            self.window.report(exc)
            return False
        self.window.refresh()
        return True

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


def _minutes(seconds: float) -> str:
    return f"{seconds / 60:.3g} min" if seconds >= 60 or not seconds else f"{seconds:.0f} s"


def _budget(values: dict):
    """A Budget from the form's text: whole runs, minutes, core-hours, none negative."""
    from puzzle.model.schema import Budget

    try:
        runs = int(str(values["runs"]).strip())
        minutes = float(str(values["minutes"]).strip())
        hours = float(str(values["core_hours"]).strip())
    except (KeyError, ValueError):
        raise ValueError("type whole runs, and minutes and core-hours as numbers") from None
    if runs < 0 or minutes < 0 or hours < 0:
        raise ValueError("a budget can't be negative")
    return Budget(runs=runs, solver_seconds=minutes * 60, core_hours=hours)


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
