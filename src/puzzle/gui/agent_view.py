"""The agent window's transcript (ADR 0074): replies drawn as Markdown, TeX as equations,
code blocks folded, each reply's tool calls folded into one line, a status line.

Qt's own Markdown (QTextDocument) draws the text; matplotlib's mathtext draws the TeX
between $...$ or $$...$$ as images in it (what it can't draw stays as text). No web
engine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QFont, QImage, QTextDocument
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from puzzle.gui.theme import current as tokens
from puzzle.gui.theme import role

FENCE = re.compile(r"^```([\w+.-]*)[^\n]*\n(.*?)^```[ \t]*$", re.MULTILINE | re.DOTALL)
DISPLAY_TEX = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
INLINE_TEX = re.compile(r"(?<![\\$\w])\$(?!\s)([^$\n]+?)(?<!\s)\$(?![\w$])")


@dataclass
class Segment:
    kind: str  # "text" or "code"
    text: str
    language: str = ""


def segments(markdown: str) -> list[Segment]:
    """A reply cut into its prose and its fenced code blocks, in order."""
    out, at = [], 0
    for m in FENCE.finditer(markdown):
        if markdown[at : m.start()].strip():
            out.append(Segment("text", markdown[at : m.start()]))
        out.append(Segment("code", m.group(2).rstrip("\n"), m.group(1)))
        at = m.end()
    if markdown[at:].strip():
        out.append(Segment("text", markdown[at:]))
    return out


def tex_image(tex: str, colour: str, display: bool = False) -> QImage | None:
    """TeX drawn by matplotlib's mathtext, or None when it can't be (then it stays as
    the TeX text)."""
    try:
        from io import BytesIO

        from matplotlib import mathtext
        from matplotlib.font_manager import FontProperties

        buf = BytesIO()
        size = 13 if display else 11
        import matplotlib

        with matplotlib.rc_context({"savefig.transparent": True}):  # on the panel's colour
            mathtext.math_to_image(f"${tex}$", buf, prop=FontProperties(size=size), dpi=120,
                                   format="png", color=colour)  # fmt: skip
        image = QImage.fromData(buf.getvalue(), "PNG")
        return None if image.isNull() else image
    except Exception:  # noqa: BLE001 - unknown commands, no matplotlib: the TeX text
        return None


class MarkdownView(QTextBrowser):
    """One piece of prose: Markdown, its TeX as images, as tall as its text."""

    def __init__(self, markdown: str) -> None:
        super().__init__()
        self.setOpenExternalLinks(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.viewport().setAutoFillBackground(False)
        self.markdown = markdown
        self._images: dict[str, QImage] = {}
        self.document().setMarkdown(self._with_tex(markdown))
        # After setMarkdown, which clears the document's resources.
        for url, image in self._images.items():
            self.document().addResource(QTextDocument.ResourceType.ImageResource,
                                        QUrl(url), image)  # fmt: skip
        self.document().markContentsDirty(0, self.document().characterCount())
        self.document().contentsChanged.connect(self.updateGeometry)

    def _with_tex(self, markdown: str) -> str:
        """Each $...$ replaced by an image of it (kept in `_images` by its URL)."""
        colour = tokens().text
        n = 0

        def put(m, display: bool) -> str:
            nonlocal n
            image = tex_image(m.group(1).strip(), colour, display)
            if image is None:
                return m.group(0)
            n += 1
            url = f"tex://{n}"
            self._images[url] = image
            tag = f"![equation]({url})"
            return f"\n\n{tag}\n\n" if display else tag

        out = DISPLAY_TEX.sub(lambda m: put(m, True), markdown)
        return INLINE_TEX.sub(lambda m: put(m, False), out)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt's name
        self.document().setTextWidth(max(self.viewport().width(), 200))
        return QSize(super().sizeHint().width(), int(self.document().size().height()) + 6)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt's name
        super().resizeEvent(event)
        self.updateGeometry()


class Fold(QWidget):
    """A line that opens to show what it holds: a code block, a reply's tool calls."""

    def __init__(self, title: str, body: QWidget, opened: bool = False) -> None:
        super().__init__()
        self.title, self.body = title, body
        self.toggle = QToolButton(text=title, checkable=True, checked=opened)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if opened else Qt.ArrowType.RightArrow)
        self.toggle.setAutoRaise(True)
        self.toggle.toggled.connect(self._toggled)
        self.row = QHBoxLayout()
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.addWidget(self.toggle)
        self.row.addStretch()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addLayout(self.row)
        layout.addWidget(body)
        body.setVisible(opened)

    def _toggled(self, on: bool) -> None:
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if on else Qt.ArrowType.RightArrow)
        self.body.setVisible(on)


def code_fold(code: str, language: str) -> Fold:
    """A fenced block, folded: its language and length on the line, Copy beside it."""
    view = QPlainTextEdit(code)
    view.setReadOnly(True)
    view.setFont(QFont("Consolas" if QFont("Consolas").exactMatch() else "Monospace"))
    view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
    lines = code.count("\n") + 1
    view.setFixedHeight(min(18 * lines + 14, 360))
    name = (language or "code").capitalize()
    fold = Fold(f"{name} · {lines} line{'s' * (lines != 1)}", view)
    copy = QToolButton(text="Copy")
    copy.setAutoRaise(True)
    copy.clicked.connect(lambda: QApplication.clipboard().setText(code))
    fold.row.insertWidget(1, copy)
    fold.code = code
    return fold


class Transcript(QScrollArea):
    """The chat, a widget per message, newest at the bottom."""

    def __init__(self) -> None:
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        self.column = QVBoxLayout(inner)
        self.column.setContentsMargins(4, 4, 4, 4)
        self.column.addStretch()
        self.setWidget(inner)
        self.texts: list[str] = []  # what was shown, as plain text (tests, copying)
        self._tools: QVBoxLayout | None = None  # the reply in progress's tool calls
        self._tool_fold: Fold | None = None

    def _add(self, widget: QWidget) -> None:
        self.column.insertWidget(self.column.count() - 1, widget)
        bar = self.verticalScrollBar()
        bar.rangeChanged.connect(
            lambda _a, high: bar.setValue(high), Qt.ConnectionType.SingleShotConnection
        )

    def _who(self, name: str, colour: str) -> QLabel:
        label = QLabel(f"<b style='color:{colour}'>{name}</b>")
        return label

    def user(self, text: str, attachments: list[str] = ()) -> None:
        self._end_tools()
        box = QWidget()
        col = QVBoxLayout(box)
        col.setContentsMargins(0, 6, 0, 0)
        col.addWidget(self._who("you", tokens().accent))
        body = QLabel(text)
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        col.addWidget(body)
        for name in attachments:
            chip = QLabel(f"📎 {name}")
            role(chip, "muted")
            col.addWidget(chip)
        self._add(box)
        self.texts.append(f"you: {text}" + "".join(f"\n📎 {a}" for a in attachments))

    def reply(self, markdown: str) -> None:
        """A reply: its prose as Markdown, its code blocks folded."""
        box = QWidget()
        col = QVBoxLayout(box)
        col.setContentsMargins(0, 6, 0, 0)
        col.addWidget(self._who("agent", tokens().done))
        for seg in segments(markdown):
            col.addWidget(MarkdownView(seg.text) if seg.kind == "text"
                          else code_fold(seg.text, seg.language))  # fmt: skip
        self._add(box)
        self._end_tools()
        self.texts.append(f"agent: {markdown}")

    def tool(self, text: str, error: bool = False) -> None:
        """A tool call or its result: into the reply's one folded "tools used" line."""
        if self._tools is None:
            body = QWidget()
            self._tools = QVBoxLayout(body)
            self._tools.setContentsMargins(18, 0, 0, 0)
            self._tool_fold = Fold("1 tool used", body)
            self._add(self._tool_fold)
        line = QLabel(text)
        line.setWordWrap(True)
        role(line, "error" if error else "muted")
        self._tools.addWidget(line)
        calls = sum(1 for i in range(self._tools.count())
                    if self._tools.itemAt(i).widget().text().startswith("→"))  # fmt: skip
        self._tool_fold.toggle.setText(f"{calls} tool{'s' * (calls != 1)} used")
        self.texts.append(text)

    def _end_tools(self) -> None:
        self._tools, self._tool_fold = None, None

    def note(self, text: str, error: bool = False) -> None:
        label = QLabel(text)
        label.setWordWrap(True)
        role(label, "error" if error else "muted")
        self._add(label)
        self.texts.append(("error: " if error else "") + text)

    def clear(self) -> None:
        while self.column.count() > 1:
            item = self.column.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()
        self.texts.clear()
        self._end_tools()

    def toPlainText(self) -> str:  # noqa: N802 - as QTextEdit's, for callers and tests
        return "\n".join(self.texts)
