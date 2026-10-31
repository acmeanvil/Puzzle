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

AGENT = "Agent"
PROVIDERS = [
    ("Claude (Anthropic)", "anthropic"),
    ("A local or other OpenAI-compatible model (Ollama, LM Studio, llama.cpp)", "openai"),
]
IMAGES = [
    ("Default: local models yes, remote models no", None),
    ("Always send rendered images", True),
    ("Never send rendered images", False),
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
        self.boxes: dict[str, QComboBox] = {}
        groups = list(dict.fromkeys(p.group for p in PREFERENCES)) + [AGENT]
        self.groups = QListWidget()
        self.groups.addItems(groups)
        self.groups.setFixedWidth(150)
        self.pages = QStackedWidget()
        for g in groups:
            self.pages.addWidget(self._agent_page() if g == AGENT else self._page(g))
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
                heading.setStyleSheet("font-weight: bold; margin-top: 8px")
                form.addRow(heading)
            if pref.kind == "colour":
                box = ColourButton(self.settings.get(pref.key), pref.label)
            else:
                box = QComboBox()
                for text, value in pref.choices:
                    box.addItem(text, value)
                box.setCurrentIndex(max(box.findData(self.settings.get(pref.key)), 0))
            box.setEnabled(pref.ready)
            if pref.help:
                box.setToolTip(pref.help)
            self.boxes[pref.key] = box
            form.addRow(pref.label, box)
            if pref.help and not pref.key.startswith("units.custom."):
                hint = QLabel(pref.help)
                hint.setWordWrap(True)
                role(hint, "muted")
                form.addRow("", hint)
        column.addLayout(form)
        column.addStretch()
        if group == "Units":
            self.boxes["units.system"].currentIndexChanged.connect(lambda _: self._custom_units())
        return page

    def _custom_units(self) -> None:
        own = self.boxes["units.system"].currentData() == "custom"
        for key, box in self.boxes.items():
            if key.startswith("units.custom."):
                box.setEnabled(own)

    def _agent_page(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.provider = QComboBox()
        for text, value in PROVIDERS:
            self.provider.addItem(text, value)
        self.provider.setCurrentIndex(max(self.provider.findData(self.agent.provider), 0))
        self.model = QLineEdit(self.agent.model)
        self.base_url = QLineEdit(self.agent.base_url)
        for line in (self.model, self.base_url):
            line.setMinimumWidth(380)
        self.images = QComboBox()
        for text, value in IMAGES:
            self.images.addItem(text, value)
        self.images.setCurrentIndex(max(self.images.findData(self.agent.send_images), 0))
        self.key_status = QLabel("")
        key = QPushButton("Set API key…")
        key.setToolTip("Kept in the system keychain, never in a file")
        key.clicked.connect(self._set_key)
        row = QHBoxLayout()
        row.addWidget(self.key_status, 1)
        row.addWidget(key)
        form.addRow("Model provider", self.provider)
        form.addRow("Model", self.model)
        form.addRow("Endpoint (local models)", self.base_url)
        form.addRow("Rendered images", self.images)
        form.addRow("API key", row)
        self.provider.currentIndexChanged.connect(lambda _: self._agent_fields())
        self._agent_fields()
        return page

    def _agent_fields(self) -> None:
        from puzzle.agent.settings import DEFAULT_MODELS, AgentSettings

        provider = self.provider.currentData()
        self.model.setPlaceholderText(f"default: {DEFAULT_MODELS.get(provider, '')}")
        self.base_url.setEnabled(provider == "openai")
        source = AgentSettings(provider=provider).key_source()
        self.key_status.setText({"none": "No key stored"}.get(source, f"From the {source}"))

    def _set_key(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        from puzzle.agent.providers import ProviderError
        from puzzle.agent.settings import store_key

        provider = self.provider.currentData()
        key, ok = QInputDialog.getText(
            self, "API key", "API key (kept in the system keychain):", QLineEdit.EchoMode.Password
        )
        if ok and key.strip():
            try:
                store_key(provider, key.strip())
            except ProviderError as exc:
                self.key_status.setText(str(exc))
                return
        self._agent_fields()

    # --- actions ----------------------------------------------------------------------

    def restore_page(self) -> None:
        group = self.groups.currentItem().text()
        if group == AGENT:
            from puzzle.agent.settings import AgentSettings

            d = AgentSettings()
            self.provider.setCurrentIndex(self.provider.findData(d.provider))
            self.model.setText(d.model)
            self.base_url.setText(d.base_url)
            self.images.setCurrentIndex(self.images.findData(d.send_images))
            return
        for pref in [p for p in PREFERENCES if p.group == group]:
            box = self.boxes[pref.key]
            box.setCurrentIndex(max(box.findData(pref.default), 0))

    def save(self) -> None:
        from puzzle.agent.settings import save_settings

        for key, box in self.boxes.items():
            self.settings.set(key, box.currentData())
        self.settings.save()
        self.agent.provider = self.provider.currentData()
        self.agent.model = self.model.text().strip()
        self.agent.base_url = self.base_url.text().strip() or self.agent.base_url
        self.agent.send_images = self.images.currentData()
        save_settings(self.agent, self.agent_path)
        self.accept()


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
