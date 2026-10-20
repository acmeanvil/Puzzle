"""Puzzle's MCP server: the project's command layer, offered to AI agents (ADR 0015).

    puzzle mcp PROJECT [--name claude-code]

Every change an agent asks for goes through puzzle.commands.Session as
Actor("agent", <name>), so the project's autonomy level decides whether it is
refused, proposed or applied, and the history records it. Reads are summaries:
an outline of the model, result statistics, a rendered picture.

Exposure follows the project's agent.mcp setting: "off" refuses to serve; "local"
and "network" serve over stdio for now (network transport comes with the GUI).
"""

from __future__ import annotations

import functools
import json
import tempfile
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from puzzle.commands import Actor, Session, command_schema, outline
from puzzle.errors import PuzzleError

INSTRUCTIONS = """\
You are working in a Puzzle FEM/CFD project with its user. Start with `outline`:
it lists every object by name, with its selections, loads and studies. Refer to
objects by name. Values are quantities: {"value": 5, "unit": "kN"}.

Change the model with `submit` (one command) or `propose` (several, for review);
`schema("commands")` describes them, `schema(<kind>)` an object, and `preview` shows
what commands would change without changing anything. The project's autonomy level
decides what happens: "advise" refuses changes, "propose" records them for the user
to accept, "act_in_budget" and "act" apply them. Check `problems` in every answer.

Read results with `result_summary` first, then `result_stats`, `result_value_at`,
`result_integral` or `result_total` (reaction forces), and `render` to look.
Numbers come only from solver runs: label your own estimates as estimates.
Errors carry a `code` and `details`.
"""


def build_server(root: Path, client: str = "mcp-client", *, outside: bool = True) -> MCPServer:
    """The tools, for an outside agent (`outside`, refused when the project's MCP access
    is off) or for the built-in agent in-process (agent.agent), which the setting does
    not cover."""
    actor = Actor("agent", client)

    def session() -> Session:
        s = Session(root)
        if outside and s.project.agent.mcp == "off":
            raise ToolError(
                json.dumps({"code": "not_allowed", "message": "MCP access is off for this project"})
            )
        return s

    def guarded(fn):
        """Report PuzzleErrors to the agent as their structured form."""

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except PuzzleError as exc:
                raise ToolError(json.dumps(exc.to_dict())) from None

        return wrapper

    server = MCPServer(name="puzzle", instructions=INSTRUCTIONS)
    tool = server.tool

    # --- the model ------------------------------------------------------------------

    @tool(name="outline", description="The model tree as compact text, with any problems.")
    @guarded
    def outline_tool(ids: bool = False) -> str:
        return outline(session().project, ids=ids)

    @tool(description="One object's full data (JSON), by name or id.")
    @guarded
    def get_object(name: str) -> dict[str, Any]:
        from puzzle.commands import resolve

        obj = resolve(session().project, name)
        return {"kind": obj.kind, **obj.model_dump(mode="json")}

    @tool(description='JSON schema: "commands", or an object kind such as "component".')
    @guarded
    def schema(which: str) -> dict[str, Any]:
        from pydantic import TypeAdapter

        from puzzle.model.schema import OBJECT_TYPES

        if which == "commands":
            return command_schema()
        kinds = {cls.kind: cls for cls in OBJECT_TYPES}
        if which not in kinds:
            raise PuzzleError(
                f"no kind '{which}'",
                code="not_found",
                details={"kinds": ["commands", *sorted(kinds)]},
            )
        return TypeAdapter(kinds[which]).json_schema()

    @tool(description="Consistency problems of the project, each with a code.")
    @guarded
    def problems() -> list[dict[str, Any]]:
        return [p.to_dict() for p in session().project.problems()]

    @tool(description="Materials in the library whose name or group contains `text`.")
    @guarded
    def materials_library(text: str = "") -> list[dict[str, str]]:
        from puzzle.materials import library_material, search_library

        return [{"key": k, "name": library_material(k).name} for k in search_library(text)]

    @tool(
        description="What a solver supports (physics -> step types, features, contacts). "
        "Lists planned capabilities too; use check_study for a real answer."
    )
    @guarded
    def capabilities(solver: str) -> dict[str, Any]:
        from puzzle.adapters import available_adapters

        adapters = available_adapters()
        if solver not in adapters:
            raise PuzzleError(
                f"no solver '{solver}'", code="not_found", details={"solvers": sorted(adapters)}
            )
        c = adapters[solver].capabilities
        return {
            "physics": {k: sorted(v) for k, v in c.physics.items()},
            "features": sorted(c.features),
            "contacts": sorted(c.contacts),
            "couplings": sorted(c.couplings),
            "dimensions": sorted(c.dimensions),
            "experimental": sorted(c.experimental),
        }

    @tool(description="Whether a study can be written for its solver(s); problems if not.")
    @guarded
    def check_study(study: str) -> dict[str, list[str]]:
        from puzzle.adapters import check_study as check
        from puzzle.commands import resolve
        from puzzle.model.schema import Study

        s = session()
        return check(s.project, resolve(s.project, study, Study))

    # --- changes ----------------------------------------------------------------------

    @tool(
        description="Submit one command (see schema('commands')). Depending on the "
        "autonomy level it is refused, proposed or applied."
    )
    @guarded
    def submit(command: dict[str, Any], rationale: str = "") -> dict[str, Any]:
        return session().submit(command, actor, rationale=rationale).to_dict()

    @tool(description="Propose several commands as one change set for the user to review.")
    @guarded
    def propose(commands: list[dict[str, Any]], rationale: str) -> dict[str, Any]:
        return session().propose(commands, actor, rationale=rationale).to_dict()

    @tool(description="What commands would change, and the problems left, without applying.")
    @guarded
    def preview(commands: list[dict[str, Any]]) -> dict[str, Any]:
        return session().preview(commands)

    @tool(description="Undo your own latest change, or history entry `seq`.")
    @guarded
    def undo(seq: int | None = None) -> dict[str, Any]:
        return session().undo(actor, seq=seq).to_dict()

    @tool(description="Recent history entries, newest first.")
    @guarded
    def history(limit: int = 20) -> list[dict[str, Any]]:
        return session().history(limit=limit)

    @tool(description="Open proposals and their previews.")
    @guarded
    def proposals() -> list[dict[str, Any]]:
        return session().proposals()

    @tool(description="The autonomy level, budget and what agents have used of it.")
    @guarded
    def autonomy() -> dict[str, Any]:
        s = session()
        return s.project.agent.model_dump(mode="json") | {"used": s.budget_used()}

    # --- results ---------------------------------------------------------------------

    def latest(study: str):
        from puzzle.commands import resolve
        from puzzle.model.schema import Study
        from puzzle.project.layout import RESULTS_DIR
        from puzzle.results.store import ResultStore

        s = session()
        found = resolve(s.project, study, Study)
        store = ResultStore(root / RESULTS_DIR)
        runs = [r for r in store.runs() if r.study_id == str(found.id)]
        if not runs:
            raise PuzzleError(f"study '{found.name}' has no runs yet", code="not_found")
        record = runs[-1]
        return record, store.read_mesh(record.mesh_id), store.read_results(record.run_id)

    @tool(description="Stored runs: study, solver, when, how long.")
    @guarded
    def runs() -> list[dict[str, Any]]:
        from puzzle.project.layout import RESULTS_DIR
        from puzzle.results.store import ResultStore

        return [
            {
                "run_id": r.run_id,
                "study": r.study_name,
                "solver": r.solver,
                "started_at": r.started_at.isoformat(),
                "duration_s": r.duration_s,
            }
            for r in ResultStore(root / RESULTS_DIR).runs()
        ]

    @tool(description="Every step and field of a study's latest run, in a few numbers.")
    @guarded
    def result_summary(study: str) -> dict[str, Any]:
        from puzzle.results.query import summary

        record, _, results = latest(study)
        return {"run_id": record.run_id, "solver": record.solver, "steps": summary(results)}

    @tool(
        description="Min, max, mean of a field and where the extremes are; optionally on a "
        "selection or for one component (default: magnitude, von Mises)."
    )
    @guarded
    def result_stats(
        study: str,
        field: str,
        step: int | None = None,
        selection: str | None = None,
        component: str | None = None,
    ) -> dict[str, Any]:
        from puzzle.results.query import stats

        _, mesh, results = latest(study)
        return stats(results, mesh, field, step=step, selection=selection, component=component)

    @tool(description="A field at a point (metres); a null coordinate averages along it.")
    @guarded
    def result_value_at(
        study: str,
        field: str,
        x: float | None,
        y: float | None,
        z: float | None,
        step: int | None = None,
    ) -> dict[str, Any]:
        from puzzle.results.query import value_at

        _, mesh, results = latest(study)
        return value_at(results, mesh, field, [x, y, z], step=step)

    @tool(description="Integral and mean of a nodal field over a body or face selection.")
    @guarded
    def result_integral(
        study: str,
        field: str,
        selection: str,
        step: int | None = None,
        component: str | None = None,
    ) -> dict[str, Any]:
        from puzzle.results.query import integrate

        _, mesh, results = latest(study)
        return integrate(results, mesh, field, selection, step=step, component=component)

    @tool(
        description="Sum of a nodal field over a selection, e.g. the reaction force on a "
        "support or contact face (field reaction_force)."
    )
    @guarded
    def result_total(
        study: str, field: str, selection: str, step: int | None = None
    ) -> dict[str, Any]:
        from puzzle.results.query import total

        _, mesh, results = latest(study)
        return total(results, mesh, field, selection, step=step)

    @tool(
        description="A field summed (reduce='total') or averaged ('mean') over a selection "
        "at every increment or time: load-deflection curves, transients."
    )
    @guarded
    def result_history(
        study: str, field: str, selection: str, reduce: str = "total"
    ) -> list[dict[str, Any]]:
        from puzzle.results.query import history

        _, mesh, results = latest(study)
        return history(results, mesh, field, selection, reduce)

    @tool(description="A picture of a study's latest result (PNG).")
    @guarded
    def render(
        study: str,
        field: str = "displacement",
        scalar: str | None = None,
        step: int | None = None,
        mode: int | None = None,
    ) -> Image:
        from puzzle.post.view import View
        from puzzle.post.view import render as draw

        _, mesh, results = latest(study)
        png = Path(tempfile.mkdtemp(prefix="puzzle-render-")) / "view.png"
        try:
            draw(
                mesh,
                results,
                View(field=field, scalar=scalar, step=step, mode=mode),
                screenshot=png,
                window_size=(900, 600),
            )
        except KeyError as exc:
            raise PuzzleError(exc.args[0], code="not_found") from None
        return Image(path=png)

    return server


def serve(root: Path, client: str = "mcp-client") -> None:
    if Session(root).project.agent.mcp == "off":
        raise PuzzleError(
            "MCP access is off for this project",
            code="not_allowed",
            details={"enable": "puzzle autonomy PROJECT --mcp local"},
        )
    build_server(root, client).run("stdio")
