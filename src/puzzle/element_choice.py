"""Element choices (ADR 0082 step 3): the native element a solver writes for a body's
cells, from the body's `ElementChoice` and the element library (`puzzle.elements`).

An adapter asks `native(solver, comp, mesh, block, family)` for each block it writes:
None leaves its default; otherwise the library's row for that solver, family and
formulation whose cells include the block's (only rows Puzzle writes: `written`), or an
expert's `solver_types` name. A formulation the solver has no written element for is
refused with what it does have.
"""

from __future__ import annotations

from puzzle.adapters.common import InputError


def choice_for(comp, mesh, block):
    """The ElementChoice whose selection holds the block's entity, the last given winning."""
    found = None
    for c in getattr(comp, "elements", []):
        group = mesh.groups.get(c.selection)
        if group is not None and block.entity in set(group.entities):
            found = c
    return found


def native(solver: str, comp, mesh, block, family: str) -> str | None:
    """The solver's element name for `block`, or None for the solver's own default."""
    c = choice_for(comp, mesh, block)
    if c is None:
        return None
    if solver in c.solver_types:
        return c.solver_types[solver]
    return lookup(solver, family, c.formulation, block.element, c.selection)


def lookup(solver: str, family: str, formulation: str, cell: str, where: str = "") -> str:
    """The written element of `solver` for that family, formulation and cell."""
    from puzzle import elements

    rows = [e for e in elements.table(solver) if e.family == family and e.written]
    for e in rows:
        if e.formulation == formulation and cell in e.shapes:
            names = [n.strip() for n in e.native.split("/")]
            if len(names) == len(e.shapes):  # one name per cell (CPS3/CPS6/...)
                return names[e.shapes.index(cell)]
            return names[0].split(" ")[0]
    offered = sorted({f"{e.native} ({elements.FORMULATIONS[e.formulation]})" for e in rows
                      if cell in e.shapes})  # fmt: skip
    label = elements.FORMULATIONS.get(formulation, formulation)
    raise InputError(
        f"{elements.SOLVER_NAMES.get(solver, solver)} has no {label} element for the {cell} "
        f"cells of '{where}' that Puzzle writes" + (f"; it has {', '.join(offered)}" if offered
                                                    else ""),
        code="unsupported",
        details={"offered": offered},
    )  # fmt: skip


def check(solver: str, comp) -> list[str]:
    """Element choices a solver has nothing written for, whatever the cells."""
    from puzzle import elements

    rows = elements.table(solver) if solver in elements.SOLVERS else []
    have = {e.formulation for e in rows if e.written}
    return [
        f"{elements.SOLVER_NAMES.get(solver, solver)} writes no "
        f"{elements.FORMULATIONS[c.formulation]} element yet (on '{c.selection}')"
        for c in getattr(comp, "elements", [])
        if solver not in c.solver_types and c.formulation not in have
    ]
