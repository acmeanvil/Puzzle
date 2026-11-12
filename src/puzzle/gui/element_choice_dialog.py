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
    kept = [c.model_dump(mode="json") for c in comp.elements if c.selection != got["selection"]]
    if got["formulation"] != "none":
        kept.append({"selection": got["selection"], "formulation": got["formulation"]})
    return window.submit(Edit(object=str(comp.id),
                              ops=[EditOp(op="set", path="elements", value=kept)]))  # fmt: skip
