"""Solver adapters (ADR 0006, ADR 0007).

Adapters register under the `puzzle.adapters` entry-point group, so a separate
package can add a solver without changing Puzzle.
"""

from functools import cache
from importlib.metadata import entry_points

from puzzle.adapters.base import Capabilities, SolverAdapter
from puzzle.model.schema import Project, Study

# Each solver's name as people write it, for every client (the GUI, the schematic, the CLI).
SOLVER_NAMES = {
    "calculix": "CalculiX",
    "code_aster": "Code_Aster",
    "code_saturne": "Code_Saturne",
    "elmer": "Elmer",
    "fenicsx": "FEniCSx",
    "openfoam": "OpenFOAM",
    "pynite": "PyNite",
}
# Adapters shipped in Puzzle itself, by entry point: found even when the installed
# package's metadata predates one (an editable install from an older checkout).
BUILT_IN = {"pynite": "puzzle.adapters.pynite:PyNiteAdapter"}


def available_adapters() -> dict[str, type[SolverAdapter]]:
    """The installed adapters by solver name (a copy: callers may change theirs)."""
    return dict(_adapters())


@cache
def _adapters() -> dict[str, type[SolverAdapter]]:
    # Read once: entry_points() opens every installed package's metadata, which took
    # ~300 ms per call in the installed app on Windows, and the Advisor and Problems ask
    # on every selection. Packages don't change while Puzzle runs.
    found = {ep.name: ep.load() for ep in entry_points(group="puzzle.adapters")}
    from importlib import import_module

    for name, target in BUILT_IN.items():
        if name not in found:
            module, cls = target.split(":")
            found[name] = getattr(import_module(module), cls)
    return found


def check_study(project: Project, study: Study) -> dict[str, list[str]]:
    """Check a study against its primary solver and every shoot-out solver."""
    from puzzle.bolts import resolved

    adapters = available_adapters()
    project = resolved(project)  # bolts with their types' values (ADR 0050)
    return {
        solver: adapters[solver]().check(project, study)
        if solver in adapters
        else [f"no adapter installed for {solver}"]
        for solver in study.all_solvers()
    }


__all__ = ["SOLVER_NAMES", "Capabilities", "SolverAdapter", "available_adapters", "check_study"]
