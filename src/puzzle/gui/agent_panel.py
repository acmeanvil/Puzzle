"""The agent panel: chat with the built-in agent and review its proposals (ADR 0015).

The agent runs on the thread pool and changes the project only through the command
layer, so the window's history watcher picks up whatever it applies. Proposals
(the default "propose" level) are listed here to accept or reject, whole. The
autonomy level is set here too; "act" asks twice, as the double sign-off requires.
"""

from __future__ import annotations

import json
import sys
import threading
import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
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
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from puzzle.agent.agent import Agent, Event
from puzzle.agent.providers import ProviderError
from puzzle.agent.settings import load_settings, settings_path
from puzzle.commands.model import SetAutonomy
from puzzle.errors import PuzzleError
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
        self._started = 0.0
        self._doing = ""
        self.models: list | None = None  # what the chip offers, read once off the GUI
        self.model_notes: list[str] = []
        self.setAcceptDrops(True)

        self.level = QComboBox()
        for key, text in LEVELS.items():
            self.level.addItem(text, key)
        self.level.activated.connect(lambda _: self._set_level(self.level.currentData()))
        new_chat = QPushButton("New chat")
        new_chat.clicked.connect(self.new_chat)
        top = QHBoxLayout()
        top.addWidget(QLabel("Agents may"))
        top.addWidget(self.level)
        top.addStretch()
        top.addWidget(new_chat)

        self.transcript = Transcript()
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
        self.input = PromptBox()
        self.input.submitted.connect(self.send)
        self.input.attached.connect(self.attach)
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
        bar.addStretch()
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
        chat_layout.addLayout(status_row)
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
        if (not text and not self.pending) or self.window.session is None or self.busy:
            return None
        self.input.clear()
        files, self.pending = list(self.pending), []
        self._show_attachments()
        self.transcript.user(text, [a.label() for a in files])
        stop = threading.Event()
        self._begin(stop, "Starting the agent…" if self.agent is None else "Thinking…")

        def work(emit, emit_event):
            if self.agent is None:  # built here, off the GUI thread (ADR 0074)
                self.agent = self._build_agent()
            return self.agent.ask(text, emit_event, attachments=files, stop=stop)

        def done(_answer) -> None:
            self._end()

        def failed(exc) -> None:
            self._end()
            hint = (f" Choose a model with the model button, or configure the provider in "
                    f"{settings_path()}." if isinstance(exc, ProviderError) else "")  # fmt: skip
            self.transcript.note(f"{exc}{hint}", error=True)

        return self.window.background(
            "The agent is working…", work, done, failed=failed, on_event=self._event,
            lock=False,
        )  # fmt: skip

    def _build_agent(self) -> Agent:
        if self.provider_factory is not None:
            provider, images = self.provider_factory(), None
        else:
            settings = load_settings()
            provider = settings.build()
            images = settings.images_allowed(provider)
        if sys.platform == "darwin":
            images = False  # off-screen VTK must render on macOS's main thread
        return Agent(self.window.session.root, provider, images=images)

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
        seconds = time.monotonic() - self._started
        if self.agent is not None:
            t = self.agent.tokens
            self.status.setText(f"{self._chip_text()} · {t['input']:,} in / {t['output']:,} "
                                f"out tokens · {seconds:.0f} s")  # fmt: skip
        else:
            self.status.setText("")
        self._show_chip()
        self.window.agent_turn(False)

    def stop(self) -> None:
        if self._stop is not None:
            self._stop.set()
            self._doing = "Stopping after this step…"
            self._show_doing()

    def _show_doing(self) -> None:
        if self.busy:
            self.status.setText(f"{self._doing}  {time.monotonic() - self._started:.0f} s")

    def _event(self, e: Event) -> None:
        if e.kind == "status":
            self._doing = e.text
            self._show_doing()
        elif e.kind == "text":
            self.transcript.reply(e.text)
        elif e.kind == "tool_call":
            args = json.dumps(e.data.get("arguments", {}))
            self.transcript.tool(f"→ {e.text} {args if len(args) <= 160 else args[:160] + '…'}")
        elif e.kind == "tool_result":
            self.transcript.tool(f"← {_brief(e.text)}")
        elif e.kind == "error":
            self.transcript.tool(f"✗ {e.text[:300]}", error=True)
        elif e.kind == "note":
            self.transcript.note(e.text)

    def new_chat(self) -> None:
        if self.agent is not None:
            self.agent.reset()
        self.transcript.clear()

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
            where = "Anthropic" if p.name == "anthropic" else (
                "a server" if p.remote else "this computer")  # fmt: skip
            return f"{p.model} · {where}"
        s = load_settings()
        from puzzle.agent.settings import DEFAULT_MODELS

        model = s.model or DEFAULT_MODELS.get(s.provider, s.provider)
        return f"{model} · {'Anthropic' if s.provider == 'anthropic' else 'local'}"

    def _show_chip(self) -> None:
        self.model_chip.setText(f"{self._chip_text()} ▾")

    def _fill_models(self) -> None:
        menu = self.model_menu
        menu.clear()
        if self.models is None:
            menu.addAction("Looking for models…").setEnabled(False)
            self._read_models()
        else:
            current = (load_settings().provider, self._chip_text().split(" · ")[0])
            heads = {}
            for c in self.models:
                where = c.where()
                if where not in heads:
                    heads[where] = menu.addAction(where)
                    heads[where].setEnabled(False)
                action = menu.addAction(f"   {c.model}")
                action.setCheckable(True)
                action.setChecked((c.provider, c.model) == current)
                action.triggered.connect(lambda _=False, c=c: self.choose(c))
            for note in self.model_notes:
                menu.addAction(note).setEnabled(False)
            menu.addAction("Look again", self._reread_models)
        menu.addSeparator()
        menu.addAction("Pick a local model…", self._pick_model)
        menu.addAction("API key…", self._set_key)

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

    def choose(self, choice) -> None:
        """Use `choice` from the next message on: a user setting, a new chat."""
        from puzzle.agent.settings import choose_model

        choose_model(choice)
        self.agent = None
        self._show_chip()
        self.transcript.note(f"Now using {choice.model} ({choice.where()}); a new chat starts.")

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
            self.transcript.note(str(exc), error=True)
            return
        self.agent, self.models = None, None  # rebuilt with the new key, models re-read
        self.transcript.note(f"{provider} key stored in the keychain")

    def _pick_model(self) -> None:
        from puzzle.gui.pick_dialog import PickDialog

        PickDialog(self.window).exec()
        self.agent, self.models = None, None
        self._show_chip()

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
