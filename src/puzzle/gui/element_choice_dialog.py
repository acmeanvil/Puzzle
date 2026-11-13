"""Model → Components → Element formulation… (ADR 0082 step 3): one form choosing the
element a body is written with, in neutral words, as one Edit. Each solver maps it from
the element library or refuses it at the solve, naming what it has."""

from __future__ import annotations

from puzzle.commands.model import Edit, EditOp
from puzzle.gui.quick_form import Ask

CHOICES = [
    ("Each solver's default", "none"),
    ("Incompatible modes: linear bricks that bend without locking (CalculiX C3D8I)",
     "incompatible_modes"),
    ("Mixed u-p: nearly incompressible material (Code_Aster 3D_INCO_UPG)", "mixed_up"),
    ("Euler-Bernoulli beam: slender members (Code_Aster POU_D_E)", "euler_bernoulli"),
    ("Lumped heat capacity: no undershoot after a thermal shock (Code_Aster 3D_DIAG)",
     "lumped"),
    ("Reduced integration: 20-node bricks (CalculiX C3D20R, Code_Aster 3D_SI)", "reduced"),
    ("Thick shell: plates thicker than about a twentieth of their span (Code_Aster DST)",
     "thick_shell"),
    ("Higher order on the same mesh: linear tetrahedra that bend (Elmer p:2)",
     "higher_order"),
    ("Full integration", "full"),
    ("Timoshenko beam", "timoshenko"),
    ("Thin shell", "thin_shell"),
]  # fmt: skip


def choose_element(window, comp):
    from puzzle.gui.menus import selections_of

    bodies = selections_of(window.session.project, comp, "body") + [p.name for p in comp.parts]
    if not bodies:
        window.statusBar().showMessage(f"{comp.name} has no bodies to choose for", 8000)
        return None
    got = window.ask_form(
        f"Element formulation in {comp.name}",
        [Ask("selection", "Bodies", choices=[(b, b) for b in bodies]),
         Ask("formulation", "Element", choices=CHOICES)],
        intro="Help → Element library lists every solver's elements; a solver that has "
        "no element of this kind for the body's cells refuses it at the solve.",
    )  # fmt: skip
    if got is None:
        return None
    return _set(window, comp, got["selection"], got["formulation"])


def _set(window, comp, selection: str, formulation: str):
    kept = [c.model_dump(mode="json") for c in comp.elements if c.selection != selection]
    if formulation != "none":
        kept.append({"selection": selection, "formulation": formulation})
    return window.submit(Edit(object=str(comp.id),
                              ops=[EditOp(op="set", path="elements", value=kept)]))  # fmt: skip


def add_to_properties(window, comp, part_name: str):
    """A part's Properties gain Element formulation (ADR 0082 decision 7): a dropdown of
    the neutral choices, its current one shown; choosing one is the form's one Edit.
    Below it, the element each solver of the component's studies writes for it."""
    from PySide6.QtWidgets import QComboBox, QTreeWidgetItem

    panel = window.properties
    current = next((c.formulation for c in comp.elements if c.selection == part_name), "none")
    item = QTreeWidgetItem(panel.invisibleRootItem(), ["Element formulation", ""])
    box = QComboBox()
    for label, value in CHOICES:
        box.addItem(label.split(":")[0], value)
        box.setItemData(box.count() - 1, label, 3)  # the whole line as its tooltip
    box.setCurrentIndex(max(box.findData(current), 0))
    box.currentIndexChanged.connect(
        lambda _i: _set(window, comp, part_name, box.currentData()))  # fmt: skip
    panel.setItemWidget(item, 1, box)
    panel.element_box = box
    for solver, written in written_by(window.session.project, comp, part_name, current):
        QTreeWidgetItem(item, [solver, written])
    item.setExpanded(True)
    return box


def written_by(project, comp, part_name: str, formulation: str) -> list[tuple[str, str]]:
    """(solver, what it writes) for each solver of the component's studies: the library's
    written element for the choice, its default, or why it refuses."""
    from puzzle import elements
    from puzzle.model.schema import Study

    solvers = sorted({s.solver for s in project.of_type(Study) if s.component == comp.id})
    out = []
    for solver in solvers:
        if solver not in elements.SOLVERS:
            continue
        name = elements.SOLVER_NAMES.get(solver, solver)
        if formulation == "none":
            out.append((name, "its default"))
            continue
        rows = [e for e in elements.table(solver) if e.formulation == formulation and e.written]
        out.append((name, ", ".join(e.native for e in rows) if rows else
                    "refuses it (nothing written)"))  # fmt: skip
    return out
