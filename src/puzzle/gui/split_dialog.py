"""One part per body… (ADR 0039): a part holding several bodies, an imported STEP
assembly, split into a part per body, asked in one form and made as one batch."""

from __future__ import annotations


def split_part(window, comp, part: str | None = None):
    """Split `part` of `comp` (asked when not given), after showing what it makes."""
    from puzzle.gui.quick_form import Ask
    from puzzle.split_parts import plan

    if part is None:
        names = [(p.name, p.name) for p in comp.parts]
        if not names:
            window.statusBar().showMessage("The component has no parts", 8000)
            return None
        part = names[0][1] if len(names) == 1 else window.choose(
            "One part per body", "Which part?", names)  # fmt: skip
        if part is None:
            return None
    project, root = window.session.project, window.session.root
    try:
        bonded = plan(project, comp, part, root, bond=True)
    except Exception as exc:  # one body, or a part not found: said plainly
        window.statusBar().showMessage(str(getattr(exc, "message", exc)), 10000)
        return None
    lines = "\n".join(f"• {name}" for name, _ in bonded.parts)
    notes = ("\n\nTo check:\n" + "\n".join(f"• {n}" for n in bonded.notes)) if bonded.notes else ""
    touching = len(bonded.bonds)
    got = window.ask_form("One part per body", [
        Ask("bond", "Where the bodies touch", choices=[
            (f"Bond them ({touching} bonded contacts), as Share faces joined them", True),
            ("Leave them apart (add contacts yourself)", False)]),
    ], intro=f"'{part}' becomes {len(bonded.parts)} parts, each body's own geometry (the "
             f"file imported, then that body kept):\n{lines}\n\nMaterials, loads, "
             f"supports, contacts and bolts follow their bodies; one undo takes it all "
             f"back.{notes}")  # fmt: skip
    if got is None:
        return None
    made = bonded if got["bond"] else plan(project, comp, part, root, bond=False)
    return window.submit_batch(made.commands)
