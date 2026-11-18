"""The Settings dialog (ADR 0024): per-user preferences, grouped, in readable words.

Pages come from `puzzle.settings.PREFERENCES`; the Agent page edits `agent.toml`.
Preferences waiting for a feature still to come are shown greyed, saying which.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from puzzle.gui.theme import role
from puzzle.settings import PREFERENCES, WAITING, Settings

ADVANCED = "Advanced"  # resets made by hand (the PARDISO build's wait)
AGENT = "Agent"
LAST = ("Schematic", "Connections", "3D mouse")  # groups after the rest, in this order


def plain_help(text: str) -> str:
    """A preference's description as a person reads it: the design records it cites
    ("(ADR 0023)", "ADR 0068, 0051:") are the maintainers', not the user's (UX-23)."""
    import re

    text = re.sub(r"\s*\((?:ADRs? )\d{4}[^)]*\)", "", text or "")
    cited = r",?\s*\bADRs? \d{4}(?:[ ,]+(?:and )?\d{4})*(?: (?:decision|step|section) \d+)?:?"
    text = re.sub(cited, "", text)
    return re.sub(r"\s{2,}", " ", text).strip()


IMAGES = [
    ("Default: local models yes, remote models no", None),
    ("Always send images and files", True),
    ("Never send images and files", False),
]
REASONING = [
    ("The provider's default", ""),
    ("None", "none"),
    ("Low", "low"),
    ("Medium", "medium"),
    ("High", "high"),
]
KEY_STATES = {"none": "No key", "keychain": "Key in the keychain"}
CONNECT = [  # who may drive the project over MCP (ADR 0015), as `puzzle autonomy --mcp`
    ("Nobody: MCP is off for this project", "off"),
    ("Programs on this computer (the default)", "local"),
    ("Programs on the network too", "network"),
]


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings | None = None, agent_path=None, parent=None):
        super().__init__(parent)
        from puzzle.agent.settings import load_settings

        self.setWindowTitle("Settings")
        self.resize(760, 480)
        self.settings = settings or Settings()
        self.agent_path = agent_path
        self.agent = load_settings(agent_path)
        # The open project's autonomy and MCP connections (ADR 0072 section 10): the
        # project's, not the user's, so saved through its Session, not settings.toml.
        self.project_window = parent if getattr(parent, "session", None) is not None else None
        self.boxes: dict[str, QComboBox] = {}
        groups = sorted(
            dict.fromkeys(p.group for p in PREFERENCES),
            key=lambda g: (g in LAST, list(LAST).index(g) if g in LAST else 0),
        )
        groups += [AGENT, ADVANCED]  # UX-23: everyday groups first, the device last
        self.groups = QListWidget()
        self.groups.addItems(groups)
        self.groups.setFixedWidth(150)
        self.pages = QStackedWidget()
        for g in groups:
            self.pages.addWidget(
                self._agent_page()
                if g == AGENT
                else self._advanced_page()
                if g == ADVANCED
                else self._page(g)
            )
        self.groups.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.groups.setCurrentRow(0)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        restore = buttons.addButton("Restore defaults", QDialogButtonBox.ButtonRole.ResetRole)
        restore.setToolTip("This page's settings back to their defaults")
        restore.clicked.connect(self.restore_page)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        body = QHBoxLayout()
        body.addWidget(self.groups)
        body.addWidget(self.pages, 1)
        layout = QVBoxLayout(self)
        layout.addLayout(body, 1)
        layout.addWidget(buttons)
        self._custom_units()

    # --- pages ------------------------------------------------------------------------

    def _page(self, group: str) -> QWidget:
        page = QWidget()
        column = QVBoxLayout(page)
        if group in WAITING:
            note = QLabel(WAITING[group])
            role(note, "muted")
            column.addWidget(note)
        form = QFormLayout()
        for pref in [p for p in PREFERENCES if p.group == group]:
            if pref.key == "units.custom.length":
                heading = QLabel("My own units, per quantity")
                heading.setContentsMargins(0, 8, 0, 0)
                role(heading, "strong")
                form.addRow(heading)
            if pref.kind == "colour":
                box = ColourButton(self.settings.get(pref.key), pref.label)
            else:
                box = QComboBox()
                for text, value in pref.choices:
                    box.addItem(text, value)
                box.setCurrentIndex(max(box.findData(self.settings.get(pref.key)), 0))
            box.setEnabled(pref.ready)
            words = plain_help(pref.help)
            if words:
                box.setToolTip(words)
            self.boxes[pref.key] = box
            form.addRow(pref.label, box)
            if words and not pref.key.startswith("units.custom."):
                hint = QLabel(words)
                hint.setWordWrap(True)
                role(hint, "muted")
                form.addRow("", hint)
        column.addLayout(form)
        if group == "3D mouse":  # ADR 0069: the device read, or why none
            from puzzle.gui.spacemouse.controller import describe

            self.mouse3d = QLabel(describe())
            self.mouse3d.setWordWrap(True)
            self.mouse3d.setContentsMargins(0, 8, 0, 0)
            column.addWidget(self.mouse3d)
        if group == "Solvers":  # ADR 0068 decision 6: what is here and how it runs
            from puzzle.gui.solvers_panel import SolversPanel

            self.solvers = SolversPanel()
            column.addWidget(self.solvers)
        column.addStretch()
        if group == "Units":
            self.boxes["units.system"].currentIndexChanged.connect(lambda _: self._custom_units())
        if group == "Solvers":  # the longest page: it scrolls
            from PySide6.QtWidgets import QScrollArea

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(page)
            return scroll
        return page

    def _advanced_page(self) -> QWidget:
        """Resets the user makes by hand: the PARDISO build's day-long wait after a
        failure (`solver_builds.clear_wait`)."""
        from PySide6.QtWidgets import QPushButton

        page = QWidget()
        column = QVBoxLayout(page)
        heading = QLabel("CalculiX with PARDISO")
        role(heading, "strong")
        column.addWidget(heading)
        self.retry_note = QLabel()
        self.retry_note.setWordWrap(True)
        role(self.retry_note, "muted")
        column.addWidget(self.retry_note)
        self.clear_retry = QPushButton("Clear the 24-hour wait")
        self.clear_retry.setToolTip("Try the build again the next time Puzzle opens, rather "
                                    "than a day after it failed")  # fmt: skip
        self.clear_retry.clicked.connect(self._clear_retry)
        row = QHBoxLayout()
        row.addWidget(self.clear_retry)
        row.addStretch()
        column.addLayout(row)
        column.addStretch()
        self._show_retry()
        return page

    def _show_retry(self, said: str = "") -> None:
        import time as _time

        from puzzle import solver_builds

        when = solver_builds.retry_at()
        if said:
            text = said
        elif when is None:
            text = "No wait: the build isn't waiting after a failure."
        else:
            status = solver_builds.status()
            text = (
                f"The last build failed ({status.detail}). Puzzle tries again by itself "
                f"after {_time.strftime('%Y-%m-%d %H:%M', _time.localtime(when))}."
            )
        self.retry_note.setText(text)
        self.clear_retry.setEnabled(when is not None)

    def _clear_retry(self) -> None:
        from puzzle import solver_builds

        if solver_builds.clear_wait():
            self._show_retry("Cleared: the build starts the next time Puzzle opens (or now, "
                             "from the Solvers page's Build button).")  # fmt: skip
        else:
            self._show_retry()

    def _custom_units(self) -> None:
        own = self.boxes["units.system"].currentData() == "custom"
        for key, box in self.boxes.items():
            if key.startswith("units.custom."):
                box.setEnabled(own)

    def _agent_page(self) -> QWidget:
        """ADR 0084 decision 4: the model in use, then a row per provider (its key's
        state, Set key…, Test, Remove, Options, its docs), then the options of the one
        chosen. Keys go to the keychain from a hidden field, never a file."""
        from PySide6.QtWidgets import QCheckBox, QGridLayout, QGroupBox, QScrollArea

        page = QWidget()
        column = QVBoxLayout(page)
        top = QFormLayout()
        self.provider = QComboBox()
        self.model = QLineEdit(self.agent.model)
        self.model.setMinimumWidth(320)
        self.images = QComboBox()
        for text, value in IMAGES:
            self.images.addItem(text, value)
        self.images.setCurrentIndex(max(self.images.findData(self.agent.send_images), 0))
        top.addRow("Model provider", self.provider)
        top.addRow("Model", self.model)
        top.addRow("Images and files (default)", self.images)
        column.addLayout(top)
        self.rows_box = QWidget()
        self.rows = QGridLayout(self.rows_box)
        self.rows.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.rows_box)
        scroll.setMinimumHeight(190)
        column.addWidget(scroll, 1)
        add = QPushButton("Add endpoint…")
        add.setToolTip("Any other Chat Completions endpoint: OpenRouter, Groq, a NIM of "
                       "your own, Azure OpenAI")  # fmt: skip
        add.clicked.connect(self._add_endpoint)
        row = QHBoxLayout()
        self.key_status = QLabel("")
        role(self.key_status, "muted")
        self.key_status.setWordWrap(True)
        row.addWidget(self.key_status, 1)
        row.addWidget(add)
        column.addLayout(row)
        self.details = QGroupBox("")
        form = QFormLayout(self.details)
        self.base_url = QLineEdit()
        self.key_env = QLineEdit()
        self.enabled = QCheckBox("Offer its models in the model button")
        self.send_files = QComboBox()
        for text, value in IMAGES:
            self.send_files.addItem(text.replace("Default: ", "Default (the page's): ")
                                    if value is None else text, value)  # fmt: skip
        self.reasoning = QComboBox()
        for text, value in REASONING:
            self.reasoning.addItem(text, value)
        self.max_tokens = QLineEdit()
        self.max_tokens.setPlaceholderText("the provider's default")
        self.favourites = QLineEdit()
        self.favourites.setPlaceholderText("models pinned to the top, separated by commas")
        self.route = QLineEdit()
        self.route.setPlaceholderText(":fastest, :cheapest or a host such as :groq")
        form.addRow("Endpoint", self.base_url)
        form.addRow("Key variable", self.key_env)
        form.addRow("", self.enabled)
        form.addRow("Images and files", self.send_files)
        form.addRow("Reasoning", self.reasoning)
        form.addRow("Most output tokens", self.max_tokens)
        form.addRow("Favourites", self.favourites)
        form.addRow("Host or policy", self.route)
        self.route_label = form.labelForField(self.route)
        column.addWidget(self.details)
        self._project_box(column)
        self.editing = ""
        self._fill_providers()
        self.provider.currentIndexChanged.connect(lambda _: self._agent_fields())
        self._options(self.agent.provider if self.agent.provider in self.agent.ids()
                      else "anthropic")  # fmt: skip
        self._agent_fields()
        return page

    def _project_box(self, column) -> None:
        """This project: what agents may do, and who may connect over MCP."""
        from PySide6.QtWidgets import QGroupBox

        from puzzle.gui.agent_panel import LEVELS

        self.who = self.may = None
        if self.project_window is None:
            return
        agent = self.project_window.session.project.agent
        box = QGroupBox("This project")
        box.setProperty("help", "form:who_may_connect")
        form = QFormLayout(box)
        self.may = QComboBox()
        for key, text in LEVELS.items():
            self.may.addItem(text, key)
        self.may.setCurrentIndex(max(self.may.findData(agent.level), 0))
        self.may.setToolTip('"act freely" asks you twice, the second time to type "act"')
        self.who = QComboBox()
        for text, value in CONNECT:
            self.who.addItem(text, value)
        self.who.setCurrentIndex(max(self.who.findData(agent.mcp), 0))
        self.who.setToolTip("Outside agents (puzzle mcp PROJECT): Claude Code, Claude Desktop, "
                            "an editor. The same autonomy and budget apply to them.")  # fmt: skip
        form.addRow("Agents may", self.may)
        form.addRow("Who may connect over MCP", self.who)
        column.addWidget(box)

    def _fill_providers(self) -> None:
        """The provider dropdown and the rows, from the catalog and the user's entries."""
        from PySide6.QtWidgets import QLabel as Label

        current = self.provider.currentData() or self.agent.provider
        self.provider.blockSignals(True)
        self.provider.clear()
        for pid in self.agent.ids():
            self.provider.addItem(self.agent.label(pid), pid)
        self.provider.setCurrentIndex(max(self.provider.findData(current), 0))
        self.provider.blockSignals(False)
        while self.rows.count():
            item = self.rows.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()
        self.states: dict[str, QLabel] = {}
        for r, pid in enumerate(self.agent.ids()):
            from puzzle.agent.catalog import entry

            e = entry(pid)
            self.rows.addWidget(Label(self.agent.label(pid)), r, 0)
            state = Label("")
            role(state, "muted")
            self.states[pid] = state
            self.rows.addWidget(state, r, 1)
            buttons = [("Set key…", lambda _=False, p=pid: self._set_key(p)),
                       ("Test", lambda _=False, p=pid: self._test(p)),
                       ("Remove", lambda _=False, p=pid: self._remove_key(p)),
                       ("Options", lambda _=False, p=pid: self._options(p))]  # fmt: skip
            for c, (text, slot) in enumerate(buttons, start=2):
                b = QPushButton(text)
                b.clicked.connect(slot)
                if text != "Options" and not self.agent.needs_key(pid):
                    b.setEnabled(text == "Test")  # local: Test lists its models
                self.rows.addWidget(b, r, c)
            links = [f'<a href="{e.docs}">docs</a>'] if e.docs else []
            if e.data_terms:
                links.append(f'<a href="{e.data_terms}">data terms</a>')
            link = Label(" · ".join(links))
            link.setOpenExternalLinks(True)
            self.rows.addWidget(link, r, 6)
            self._show_state(pid)

    def _show_state(self, pid: str, said: str = "") -> None:
        if pid not in self.states:
            return
        if said:
            self.states[pid].setText(said)
            return
        if not self.agent.needs_key(pid):
            self.states[pid].setText("No key needed")
            return
        source = self.agent.key_source(pid)
        self.states[pid].setText(KEY_STATES.get(source, f"Key from the {source}"))

    def _agent_fields(self) -> None:
        pid = self.provider.currentData() or "anthropic"
        from puzzle.agent.catalog import entry

        default = entry(pid).default_model
        self.model.setPlaceholderText(f"default: {default}" if default
                                      else "choose one in the model button")  # fmt: skip

    def _options(self, pid: str) -> None:
        """Show one provider's options below the rows (what's typed is kept per
        provider until Save)."""
        from puzzle.agent.catalog import entry

        self._keep_options()
        self.editing = pid
        e, cfg = entry(pid), self.agent.config(pid)
        self.details.setTitle(f"{self.agent.label(pid)}: options")
        self.base_url.setText(cfg.base_url)
        self.base_url.setPlaceholderText(e.base_url or "https://…/v1")
        self.key_env.setText(cfg.key_env)
        self.key_env.setPlaceholderText(e.key_env or "none")
        self.enabled.setChecked(self.agent.enabled(pid))
        self.send_files.setCurrentIndex(max(self.send_files.findData(cfg.send_files), 0))
        self.reasoning.setCurrentIndex(max(self.reasoning.findData(cfg.reasoning), 0))
        self.reasoning.setEnabled(bool(e.reasoning) or e.wire == "chat")
        self.max_tokens.setText(str(cfg.max_output_tokens) if cfg.max_output_tokens else "")
        self.favourites.setText(", ".join(cfg.favourites))
        self.route.setText(cfg.route)
        hf = pid == "huggingface"
        self.route.setVisible(hf)
        if self.route_label is not None:
            self.route_label.setVisible(hf)

    def _keep_options(self) -> None:
        if not self.editing:
            return
        cfg = self.agent.config(self.editing)
        cfg.base_url = self.base_url.text().strip()
        cfg.key_env = self.key_env.text().strip()
        default_on = cfg.enabled is None and self.enabled.isChecked() == self.agent.enabled(
            self.editing)  # fmt: skip
        cfg.enabled = None if default_on else self.enabled.isChecked()
        cfg.send_files = self.send_files.currentData()
        cfg.reasoning = self.reasoning.currentData() or ""
        text = self.max_tokens.text().strip()
        cfg.max_output_tokens = int(text) if text.isdigit() else 0
        cfg.favourites = [m.strip() for m in self.favourites.text().split(",") if m.strip()]
        cfg.route = self.route.text().strip()

    def _set_key(self, pid: str | None = None) -> None:
        from PySide6.QtWidgets import QInputDialog

        from puzzle.agent.providers import ProviderError
        from puzzle.agent.settings import paste_problem, store_key

        pid = pid or self.provider.currentData()
        key, ok = QInputDialog.getText(
            self, "API key", f"{self.agent.label(pid)} API key (kept in the system keychain):",
            QLineEdit.EchoMode.Password,
        )  # fmt: skip
        if not ok or not key.strip():
            return
        problem = paste_problem(pid, key.strip())
        try:
            store_key(pid, key.strip())
        except ProviderError as exc:
            self.key_status.setText(str(exc))
            return
        stored = "Stored in the keychain. Test checks it with the provider."
        self.key_status.setText(f"Stored; {problem}." if problem else stored)
        self._show_state(pid)

    def _remove_key(self, pid: str) -> None:
        from puzzle.agent.settings import delete_key

        removed = delete_key(pid)
        delete_key(f"{pid}-auth")
        self.key_status.setText("Removed from the keychain." if removed
                                else "No key in the keychain to remove.")  # fmt: skip
        self._show_state(pid)

    def _test(self, pid: str) -> None:
        """One cheap authenticated call, off the GUI thread; its answer in the row."""
        import threading

        from PySide6.QtCore import QTimer

        from puzzle.agent.providers import ProviderError
        from puzzle.agent.settings import available_models, check_key

        self._keep_options()
        key = self.agent.api_key(pid)
        if self.agent.needs_key(pid) and not key:
            self._show_state(pid, "No key to test")
            return
        self._show_state(pid, "Testing…")
        result: list[str] = []

        def work() -> None:
            try:
                if self.agent.needs_key(pid):
                    result.append(check_key(pid, key, self.agent))
                else:
                    found, notes = available_models(_only(self.agent, pid))
                    result.append(f"{len(found)} models answer" if found
                                  else (notes[0] if notes else "no models"))  # fmt: skip
            except ProviderError as exc:
                result.append(f"Refused: {exc.plain(self.agent.label(pid))}")
            except Exception as exc:  # noqa: BLE001 - said in the row
                result.append(f"Failed: {exc}")

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        timer = QTimer(self)

        def poll() -> None:
            if result:
                timer.stop()
                self._show_state(pid, result[0])

        timer.timeout.connect(poll)
        timer.start(100)
        self._test_thread = thread  # tests join it

    def _add_endpoint(self) -> None:
        """A compatible Chat Completions endpoint: its name, address and key variable in
        one form."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Add endpoint")
        form = QFormLayout(dialog)
        name, url, env = QLineEdit(), QLineEdit(), QLineEdit()
        name.setPlaceholderText("openrouter")
        url.setPlaceholderText("https://openrouter.ai/api/v1")
        env.setPlaceholderText("OPENROUTER_API_KEY (or leave empty and Set key…)")
        form.addRow("Name", name)
        form.addRow("Address", url)
        form.addRow("Key variable", env)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)  # fmt: skip
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if not self.add_endpoint(dialog, name, url, env):
            return

    def add_endpoint(self, dialog, name, url, env) -> bool:
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        label = "".join(c for c in name.text().strip() if c.isalnum() or c in "-_.")
        if not label or not url.text().strip().startswith(("http://", "https://")):
            self.key_status.setText("An endpoint needs a name and an http(s) address.")
            return False
        self._keep_options()
        cfg = self.agent.config(f"compatible:{label}")
        cfg.name, cfg.base_url, cfg.key_env = label, url.text().strip(), env.text().strip()
        self._fill_providers()
        self._options(f"compatible:{label}")
        return True

    # --- actions ----------------------------------------------------------------------

    def restore_page(self) -> None:
        group = self.groups.currentItem().text()
        if group == AGENT:
            from puzzle.agent.settings import AgentSettings

            d = AgentSettings()
            self.provider.setCurrentIndex(self.provider.findData(d.provider))
            self.model.setText(d.model)
            self.images.setCurrentIndex(self.images.findData(d.send_images))
            self.agent.providers = {p: c for p, c in self.agent.providers.items()
                                    if p.startswith("compatible:")}  # fmt: skip
            self.editing = ""
            self._options(self.provider.currentData())
            return
        for pref in [p for p in PREFERENCES if p.group == group]:
            box = self.boxes[pref.key]
            box.setCurrentIndex(max(box.findData(pref.default), 0))

    def save(self) -> None:
        """The project's autonomy first: its "act" sign-off declined keeps the dialog
        open with nothing saved, so Cancel then discards everything (PANEL-32)."""
        from puzzle.agent.settings import save_settings

        if self.who is not None and not set_autonomy(
            self.project_window, self.may.currentData(), self.who.currentData()
        ):
            return  # the second sign-off for "act" was declined: stay open, unsaved
        for key, box in self.boxes.items():
            self.settings.set(key, box.currentData())
        self.settings.save()
        self._keep_options()
        self.agent.provider = self.provider.currentData()
        self.agent.model = self.model.text().strip()
        self.agent.send_images = self.images.currentData()
        save_settings(self.agent, self.agent_path)
        self.accept()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt's name
        """Tab follows the rows as they sit (ADR 0045)."""
        super().showEvent(event)
        from puzzle.gui.theme import tab_in_order

        if self.layout() is not None:
            self.layout().activate()
        tab_in_order(self)


def set_autonomy(window, level: str | None, mcp: str | None) -> bool:
    """The project's autonomy level and MCP connections changed through its Session,
    as `puzzle autonomy --level --mcp` sends them (one set_autonomy command); "act"
    needs the agent panel's double sign-off. False when it was declined or refused."""
    from puzzle.commands.model import SetAutonomy
    from puzzle.errors import PuzzleError

    s = window.session
    now = s.project.agent
    fields = {k: v for k, v in (("level", level), ("mcp", mcp))
              if v is not None and getattr(now, k) != v}  # fmt: skip
    if not fields:
        return True
    try:
        try:
            s.submit(SetAutonomy(**fields))
        except PuzzleError as exc:
            if exc.code != "confirmation_required" or "token" not in exc.details:
                raise
            if not window.agent._confirm_act():
                return False
            s.submit(SetAutonomy(**fields, confirm=exc.details["token"]))
    except PuzzleError as exc:
        window.report(exc)
        return False
    window.refresh()
    return True


def _only(s, pid: str):
    """A copy of the agent settings offering one provider (Test on a keyless one)."""
    import copy

    one = copy.deepcopy(s)
    for p in one.ids():
        one.config(p).enabled = p == pid
    return one


class ColourButton(QPushButton):
    """A colour preference: shows the colour, and a click opens the colour picker.
    `currentData()` gives "#rrggbb", as a choice box gives its value."""

    def __init__(self, colour: str, title: str = "Colour") -> None:
        super().__init__()
        self.title = title
        self._set(colour)
        self.clicked.connect(self.pick)

    def _set(self, colour: str) -> None:
        self.colour = colour
        self.setText(colour)
        self.setStyleSheet(f"QPushButton {{ background: {colour}; }}")  # the user's colour

    def pick(self) -> None:
        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import QColorDialog

        chosen = QColorDialog.getColor(QColor(self.colour), self, self.title)
        if chosen.isValid():
            self._set(chosen.name())

    def currentData(self) -> str:  # noqa: N802 - matches QComboBox
        return self.colour


class ProjectUnitsDialog(QDialog):
    """A project's unit switch (ADR 0030): on, the project's system for everyone who
    opens it; off, each user's own preference (Settings → Units)."""

    def __init__(self, units=None, parent=None) -> None:
        from PySide6.QtWidgets import QCheckBox, QComboBox, QDialogButtonBox, QLabel, QVBoxLayout

        from puzzle.model.units import SYSTEMS
        from puzzle.settings import current

        super().__init__(parent)
        self.setWindowTitle("Project units")
        layout = QVBoxLayout(self)
        self.use = QCheckBox("Use the project's units (everyone who opens it sees them)")
        self.use.setChecked(units.use if units is not None else True)
        self.system = QComboBox()
        for key, (label, _) in SYSTEMS.items():
            self.system.addItem(label, key)
        chosen = units.system if units is not None else current().get("units.system")
        self.system.setCurrentIndex(max(0, self.system.findData(chosen)))
        note = QLabel(
            "Off: each person sees their own units (Settings → Units). Units only change "
            "what is shown; values are kept as typed and solvers get SI."
        )
        note.setWordWrap(True)
        role(note, "muted")
        self.use.toggled.connect(self.system.setEnabled)
        self.system.setEnabled(self.use.isChecked())
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        for w in (self.use, self.system, note, buttons):
            layout.addWidget(w)

    def choice(self) -> tuple[bool, str]:
        return self.use.isChecked(), self.system.currentData()
