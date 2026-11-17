"""The agent panel: chat with the built-in agent and review its proposals (ADR 0015).

The agent runs on the thread pool and changes the project only through the command
layer, so the window's history watcher picks up whatever it applies. Proposals
(the default "propose" level) are listed here to accept or reject, whole. The
autonomy level is set here too; "act" asks twice, as the double sign-off requires.
"""

from __future__ import annotations

import json
import logging
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
        new_chat.clicked.connect(self.new_chat)
        top = QHBoxLayout()
        top.addWidget(QLabel("Agents may"))
        top.addWidget(self.level)
        top.addWidget(self.budget_button)
        top.addWidget(self.budget_used)
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
        self._show_budget()
        self.proposals.clear()
        for p in s.proposals():
            names = {str(o.id): o.name for o in s.project.objects.values()}
            item = QListWidgetItem(_proposal_text(p, names))
            item.setData(Qt.ItemDataRole.UserRole, p["id"])
            self.proposals.addItem(item)

    def project_opened(self) -> None:
        self._end_turn()  # the old turn edits the old project: stopped, never shown here
        self.agent = None
        self.transcript.clear()
        self.refresh()

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
        text = (text if isinstance(text, str) else self.input.text()).strip()
        if (not text and not self.pending) or self.window.session is None or self.busy:
            return None
        self.input.clear()
        files, self.pending = list(self.pending), []
        self._show_attachments()
        self.transcript.user(text, [a.label() for a in files])
        stop = threading.Event()
        self._turn += 1
        turn = self._turn
        self._begin(stop, "Starting the agent…" if self.agent is None else "Thinking…")

        def current() -> bool:
            return turn == self._turn

        def work(emit, emit_event):
            if self.agent is None:  # built here, off the GUI thread (ADR 0074)
                self.agent = self._build_agent()
            return self.agent.ask(text, emit_event, attachments=files, stop=stop)

        def done(_answer) -> None:
            self._end()
            if current():  # a reply cut off by Stop was never said: taken away
                self.transcript.end_live()

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
            self._doing = "Stopping…"  # a reply at once, a run it started, else this step
            self._show_doing()

    def _show_doing(self) -> None:
        if self.busy:
            self.status.setText(f"{self._doing}  {time.monotonic() - self._started:.0f} s")

    def _event(self, e: Event) -> None:
        if e.kind == "status":
            self._doing = e.text
            self._show_doing()
        elif e.kind == "delta":
            self.transcript.stream(e.text)
        elif e.kind == "text":
            self.transcript.reply(e.text)
        elif e.kind == "tool_call":
            args = json.dumps(e.data.get("arguments", {}))
            self.transcript.tool(f"→ {e.text} {args if len(args) <= 160 else args[:160] + '…'}")
        elif e.kind == "tool_result":
            self.transcript.tool(f"← {_brief(e.text)}")
            if e.data.get("image"):  # what the agent rendered, shown to the user too
                self.transcript.picture(e.data["image"], e.data.get("sent", False))
        elif e.kind == "error":
            self.transcript.tool(f"✗ {e.text[:300]}", error=True)
        elif e.kind == "note":
            self.transcript.note(e.text)

    def new_chat(self) -> None:
        """A new conversation; a turn still running is stopped first and its replies
        no longer shown."""
        self._end_turn()
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
