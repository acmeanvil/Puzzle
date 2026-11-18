"""The Model ribbon (ADR 0090 UX-9): the common modelling tools in sight while the Model
tab is shown, as the Geometry, Mesh and Results tabs have theirs. Each button is the
menu bar's own command (the same function and words), so the two never differ; the
component they act on is the one selected in the tree (or open), else the only one,
else asked (`main_menu.current_component`)."""

from __future__ import annotations

from PySide6.QtWidgets import QMenu, QToolButton

from puzzle.gui.ribbon import Ribbon


class ModelRibbon(Ribbon):
    def __init__(self, window) -> None:
        super().__init__()
        self.window = window
        self.buttons: dict[str, QToolButton] = {}
        from puzzle.gui import main_menu, menus
        from puzzle.gui.contact_search import find_contacts_dialog

        with_comp = main_menu._with_component

        def group(title: str, entries) -> None:
            buttons = []
            for key, text, tip, slot in entries:
                b = self.button(text, tip, tool=key)
                if slot is not None:
                    b.clicked.connect(lambda _=False, s=slot: s())
                self.buttons[key] = b
                buttons.append(b)
            self.group(title, buttons)

        group("Analysis", [
            ("new_analysis", "New analysis…", "Mesh recipe, component, material, physics and "
             "study for a geometry, in one form", lambda: menus.new_analysis(window)),
            ("material", "Add material…", "A material from the library",
             window._add_material_dialog),
            ("assign", "Assign material…", "Give a component's bodies a material",
             with_comp(window, lambda c: menus.assign_material(window, c))),
        ])  # fmt: skip
        group("Loads and supports", [
            ("loads", "Add load or support", "A support or load on a selection or on the "
             "faces picked, its values in the same form", None),
        ])  # fmt: skip
        loads = self.buttons["loads"]
        loads.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(loads)
        menu.setToolTipsVisible(True)
        menu.aboutToShow.connect(lambda: main_menu._fill_features(window, menu))
        loads.setMenu(menu)
        group("Connections", [
            ("contacts", "Find contacts…", "Where the parts, or the bodies of a part, touch",
             with_comp(window, lambda c: find_contacts_dialog(window, c))),
            ("contact_tool", "Contact tool", "Each contact's setup, initial state and results",
             lambda: window.open_contacts()),
        ])  # fmt: skip
        group("Study", [
            ("new_study", "New study…", "Which analysis to run, on which solver",
             lambda: menus.new_study(window)),
            ("solver_settings", "Solver settings…", "The study's solver settings",
             lambda: main_menu._solver_settings(window)),
            ("run", "Run study", "Solve the selected study (or the only one)",
             lambda: main_menu._run(window)),
        ])  # fmt: skip
