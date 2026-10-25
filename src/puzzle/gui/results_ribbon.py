"""The Results ribbon (ADR 0042): every result tool in sight while a result is shown,
as Ansys shows its Solution toolbar. Probe, Reaction, On a selection and Path make
result tools; Section cut, Animate and Plots reach what the view toolbar, the results
bar and the Messages dock already have. Each button's tooltip says what it does and
what to pick."""

from __future__ import annotations

from PySide6.QtWidgets import QToolButton

from puzzle.gui.ribbon import Ribbon


class ResultsRibbon(Ribbon):
    def __init__(self, window) -> None:
        super().__init__()
        self.window = window
        self.buttons: dict[str, QToolButton] = {}

        def group(title: str, entries) -> None:
            buttons = []
            for key, text, tip, slot in entries:
                b = self.button(text, tip)
                b.clicked.connect(lambda _=False, s=slot: s())
                self.buttons[key] = b
                buttons.append(b)
            self.group(title, buttons)

        from puzzle.gui import result_tools as tools

        group(
            "Probe",
            [
                ("probe", "Probe", "Read a value: double-click the part (Shift adds more). "
                 "Keep, under the view, adds it to the tree", self.probe),
                ("reaction", "Reaction…", "The force and moment a support takes: the "
                 "reaction forces summed over a selection", lambda: tools.add_reaction(window)),
                ("selection", "Result on a selection…", "A result's maximum, minimum, average or "
                 "integral over a face, edge, body or point selection",
                 lambda: tools.add_selection_result(window)),
                ("path", "Path…", "A result along a straight line, plotted against the "
                 "distance", lambda: tools.add_path(window)),
            ],
        )  # fmt: skip
        group(
            "Show",
            [
                ("cut", "Section cut", "Cut the model by a plane to see inside", self.cut),
                ("animate", "Animate", "Play the deformation, or the steps", self.animate),
                (
                    "legend",
                    "Legend…",
                    "The colour bar: its range, colours, size and place",
                    window.edit_legend,
                ),
                ("plots", "Plots", "Result tools against time, steps or distance", self.plots),
            ],
        )

    def probe(self) -> None:
        w = self.window
        w.statusBar().showMessage(
            "Double-click the part to read a value there; Shift adds more, Esc clears. "
            "Keep (under the view) adds a probe to the tree.",
            15000,
        )

    def cut(self) -> None:
        action = self.window.viewport.view_bar.actions_by_name["section"]
        action.toggle()

    def animate(self) -> None:
        self.window.results_bar.play.toggle()

    def plots(self) -> None:
        w = self.window
        w.plots.refresh()
        w.output_tabs.setCurrentWidget(w.plots)
        w.fold_messages(False)
