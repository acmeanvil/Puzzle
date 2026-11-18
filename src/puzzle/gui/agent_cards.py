"""Cards in the agent's transcript (ADR 0090 package 12): a proposal to review, a turn's
changes to undo, the plan above the message box.

A proposal card (UXA-2) shows each command with a tick box, a row per value it changes
(old → new, from `commands.changes`), the problems before and after, and Accept ticked,
Reject and Show in view. A turn's row (UXA-5) names what the turn changed and offers
Undo this turn. The plan (UXA-7) is the agent's list of steps with their states.
"""

from __future__ import annotations

import html
import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from puzzle.gui.theme import role


def field_words(path: str) -> str:
    """A changed value's place in words: "physics[Structural].features[Tip].values.F" →
    "Physics › Structural › Features › Tip › Values › F"."""
    from puzzle.gui.choices import label

    out = []
    for piece in re.findall(r"[^.\[\]]+|\[[^\]]*\]", path):
        name = piece[1:-1] if piece.startswith("[") else piece
        # A key ("solid_mechanics", "values") in words; a name ("Bar/Tip") as it is.
        out.append(label(name) if re.fullmatch(r"[a-z0-9_]+", name) else name)
    return " › ".join(out)


def rows_html(rows: list[dict]) -> str:
    """Change rows as a small table: object, value, before → after."""
    cells = []
    for r in rows:
        where = html.escape(r["object"]) + (
            f" · {html.escape(field_words(r['field']))}" if r["field"] else "")  # fmt: skip
        before, after = html.escape(r["before"]), html.escape(r["after"])
        change = f"{before} → <b>{after}</b>" if before else f"<b>{after}</b>"
        cells.append(f"<tr><td>{where}</td><td>&nbsp;&nbsp;{change}</td></tr>")
    return "<table cellspacing='0' cellpadding='1'>" + "".join(cells) + "</table>"


class ProposalCard(QFrame):
    """One proposal: tick boxes per command, its value changes, problems before and
    after, Accept ticked / Reject / Show in view. The panel acts on its buttons."""

    def __init__(self, panel, proposal: dict) -> None:
        super().__init__()
        from puzzle.commands.changes import changes
        from puzzle.gui.window import _describe

        self.setObjectName("agent_card")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.panel, self.pid = panel, proposal["id"]
        session = panel.window.session
        names = {str(o.id): o.name for o in session.project.objects.values()}
        try:
            found = changes(session.project, proposal["commands"])
        except Exception as exc:  # noqa: BLE001 - the card still offers Accept and Reject
            found = {"commands": [], "problems_before": [], "problems_after": [],
                     "error": str(exc)}  # fmt: skip
        self.found = found
        col = QVBoxLayout(self)
        col.setContentsMargins(8, 6, 8, 6)
        title = QLabel("Proposed for your review" + (
            f": {proposal['rationale']}" if proposal.get("rationale") else ""))  # fmt: skip
        title.setWordWrap(True)
        role(title, "strong")
        col.addWidget(title)
        self.ticks: list[QCheckBox] = []
        for i, cmd in enumerate(proposal["commands"]):
            words = _describe(cmd, names)
            tick = QCheckBox(words[:1].upper() + words[1:])
            tick.setChecked(True)
            tick.toggled.connect(lambda _on: self._count())
            self.ticks.append(tick)
            col.addWidget(tick)
            info = next((c for c in found["commands"] if c["index"] == i), None)
            if info is None:
                continue
            if info.get("error"):
                said = QLabel(f"Would fail: {info['error']}")
                role(said, "error")
                said.setWordWrap(True)
                col.addWidget(said)
            elif info.get("runs"):
                said = QLabel("Runs the study once the changes are made")
                role(said, "muted")
                col.addWidget(said)
            elif info["rows"]:
                table = QLabel(rows_html(info["rows"]))
                table.setTextFormat(Qt.TextFormat.RichText)
                table.setWordWrap(True)
                table.setContentsMargins(22, 0, 0, 0)
                col.addWidget(table)
        before, after = len(found["problems_before"]), len(found["problems_after"])
        problems = QLabel(f"Problems: {before} now → {after} after" if before or after
                          else "No problems before or after")  # fmt: skip
        role(problems, "error" if after > before else "muted")
        col.addWidget(problems)
        buttons = QHBoxLayout()
        self.accept_button = QPushButton("Accept")
        self.accept_button.clicked.connect(
            lambda: self.panel.accept_proposal(self.pid, self.ticked())
        )
        self.reject_button = QPushButton("Reject")
        self.reject_button.clicked.connect(lambda: self.panel.reject_proposal(self.pid))
        self.show_button = QToolButton(text="Show in view")
        self.show_button.setToolTip("Select what it changes and preview it in the view")
        self.show_button.clicked.connect(lambda: self.panel.window.show_proposal(self.pid))
        buttons.addWidget(self.accept_button)
        buttons.addWidget(self.reject_button)
        buttons.addWidget(self.show_button)
        buttons.addStretch()
        col.addLayout(buttons)
        self.state = QLabel("")
        role(self.state, "muted")
        self.state.hide()
        col.addWidget(self.state)
        self._count()

    def ticked(self) -> list[int] | None:
        """The ticked commands' indices; None when all are (the whole proposal)."""
        picks = [i for i, t in enumerate(self.ticks) if t.isChecked()]
        return None if len(picks) == len(self.ticks) else picks

    def _count(self) -> None:
        n = sum(t.isChecked() for t in self.ticks)
        whole = n == len(self.ticks)
        self.accept_button.setText("Accept" if whole else f"Accept {n} of {len(self.ticks)}")
        self.accept_button.setEnabled(n > 0)

    def decided(self, words: str) -> None:
        """Accepted or rejected: the buttons go, the card says which."""
        for w in (self.accept_button, self.reject_button, *self.ticks):
            w.setEnabled(False)
        self.state.setText(words)
        self.state.show()


class TurnRow(QWidget):
    """What a turn changed, by name, with Undo this turn (UXA-5)."""

    def __init__(self, panel, turn: str, objects: list[dict]) -> None:
        super().__init__()
        self.panel, self.turn = panel, turn
        row = QHBoxLayout(self)
        row.setContentsMargins(18, 2, 0, 2)
        names = list(dict.fromkeys(o["name"] for o in objects if o.get("name")))
        shown = ", ".join(names[:6]) + (f" and {len(names) - 6} more" if len(names) > 6 else "")
        self.words = QLabel(f"This turn changed {shown}" if shown else "This turn changed "
                            "the model")  # fmt: skip
        self.words.setWordWrap(True)
        role(self.words, "muted")
        self.undo_button = QToolButton(text="Undo this turn")
        self.undo_button.setToolTip("Take back every change the agent made in this turn, "
                                    "as one step (Redo puts them back)")  # fmt: skip
        self.undo_button.clicked.connect(lambda: self.panel.undo_turn(self.turn))
        row.addWidget(self.words, 1)
        row.addWidget(self.undo_button)

    def undone(self) -> None:
        self.undo_button.setEnabled(False)
        self.undo_button.setText("Undone")


class PlanList(QWidget):
    """The agent's plan above the message box: an item per line with its state."""

    def __init__(self) -> None:
        super().__init__()
        self.lines = QVBoxLayout(self)
        self.lines.setContentsMargins(4, 2, 4, 2)
        self.lines.setSpacing(1)
        self.items: list[dict] = []
        self.hide()

    def show_plan(self, items: list[dict]) -> None:
        from puzzle.agent.plans import MARKS

        while self.lines.count():
            w = self.lines.takeAt(0).widget()
            if w is not None:
                w.hide()
                w.deleteLater()
        self.items = list(items)
        if items:
            head = QLabel("Plan")
            role(head, "strong")
            self.lines.addWidget(head)
        for item in items:
            line = QLabel(f"{MARKS.get(item['state'], '○')}  {item['text']}")
            line.setWordWrap(True)
            role(line, {"done": "done", "doing": "running", "skipped": "muted"}.get(
                item["state"], "muted" if item["state"] == "todo" else ""))  # fmt: skip
            self.lines.addWidget(line)
        self.setVisible(bool(items))

    def text(self) -> str:
        from puzzle.agent.plans import text

        return text(self.items)
