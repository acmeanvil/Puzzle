"""Solver adapters (ADR 0006, ADR 0007).

Adapters register under the `puzzle.adapters` entry-point group, so a separate
package can add a solver without changing Puzzle.
"""

from importlib.metadata import entry_points

from puzzle.adapters.base import Capabilities, SolverAdapter
from puzzle.model.schema import Project, Study


def available_adapters() -> dict[str, type[SolverAdapter]]:
    return {ep.name: ep.load() for ep in entry_points(group="puzzle.adapters")}


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


__all__ = ["Capabilities", "SolverAdapter", "available_adapters", "check_study"]
