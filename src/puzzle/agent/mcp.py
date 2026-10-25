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

Make the changes the user asks for with `submit`: one command, or a list of model
edits that belong together (applied all or none). The project's autonomy level then
decides what happens: "advise" refuses changes, "propose" records them for the user to
accept, "act_in_budget" and "act" apply them. Use `propose` only when you want the
user to review something they did not ask for. `schema("commands")` describes the
commands, `schema(<kind>)` an object, and `preview` shows what commands would change
without changing anything. Check `problems` in every answer.

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
        from puzzle.project.layout import RESULTS_DIR
        from puzzle.results.store import ResultStore

        try:
            runs = ResultStore(root / RESULTS_DIR).runs()
        except PuzzleError:  # an old store: the model still outlines
            runs = None
        return outline(session().project, ids=ids, runs=runs)

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

    @tool(
        description="Check a geometry for what would trouble meshing or solving: gaps, "
        "surfaces that are not solids, short edges, slivers, overlapping or unshared bodies."
    )
    @guarded
    def geometry_check(geometry: str) -> list[dict[str, Any]]:
        from puzzle.commands import resolve
        from puzzle.geometry.prep import check_geometry
        from puzzle.model.schema import Geometry

        s = session()
        return check_geometry(s.project, resolve(s.project, geometry, Geometry), root)

    @tool(
        description="How a geometry's selections resolve after edits or a replaced file: "
        "each member kept, re-found (by where it was), by rule, or detached with ranked "
        "candidates. `accept_all` is an edit command taking every proposal (and a rule "
        "where one fits); review it, then pass it to submit."
    )
    @guarded
    def selections_check(geometry: str) -> dict[str, Any]:
        from puzzle.commands import resolve
        from puzzle.geometry.naming import accept_all, check_selections, reattach_ops, rules_for
        from puzzle.model.schema import Geometry

        s = session()
        geo = resolve(s.project, geometry, Geometry)
        done = check_selections(s.project, geo, root)
        choices = accept_all(done.reports)
        for sel, rule in rules_for(geo, choices, done.named).items():
            choices.setdefault(sel, {})["rule"] = rule
        ops = reattach_ops(geo, choices, done.named, done.reports)
        command = (
            {"command": "edit", "object": geo.name, "ops": [o.model_dump() for o in ops]}
            if ops
            else None
        )
        return {"selections": [r.to_dict() for r in done.reports], "accept_all": command}

    @tool(
        description="Find where the parts of a component touch (ADR 0039): one proposal per "
        "pair of parts with the faces on each side, the gap (m) and the overlap (m^2). "
        "`tolerance` is the largest gap in m (default 0.5 % of the model's size), `angle` how "
        "far from face to face, in degrees. `make_bonded` is the batch that makes every "
        "proposal as a bonded contact (face selections first); change a contact's type "
        "or drop proposals, then pass the list to submit. Nothing is made here."
    )
    @guarded
    def contacts_find(
        component: str, tolerance: float | None = None, angle: float = 10.0
    ) -> dict[str, Any]:
        from puzzle.commands import resolve
        from puzzle.geometry.contacts import contact_commands, find_contacts, proposals
        from puzzle.model.schema import Component

        s = session()
        comp = resolve(s.project, component, Component)
        found = proposals(find_contacts(s.project, comp, root, tolerance=tolerance, angle=angle))
        batch = [c.model_dump(mode="json") for c in contact_commands(s.project, comp, found)]
        return {
            "proposals": [
                {
                    "name": p.name,
                    "source_part": p.source_part,
                    "source_faces": p.source_faces,
                    "target_part": p.target_part,
                    "target_faces": p.target_faces,
                    "gap_m": p.distance,
                    "overlap_m2": p.area,
                }
                for p in found
            ],
            "make_bonded": batch,
        }

    @tool(
        description="Mesh a component with its recipe and report the mesh (ADR 0019): node "
        "and element counts, each quality measure's min, mean and histogram (shape and "
        "scaled Jacobian 0..1, 1 ideal; edge aspect ratio from 1), the worst-shaped elements "
        "with their centres (m), and the mesher's warnings. A mesh that fails says why "
        "(conflicting sizes, an unknown option, a quality floor missed)."
    )
    @guarded
    def mesh_quality(component: str) -> dict[str, Any]:
        from puzzle.commands import resolve
        from puzzle.mesh import generate_mesh
        from puzzle.model.schema import Component

        s = session()
        comp = resolve(s.project, component, Component)
        from puzzle.project.layout import WORK_DIR

        out = root / WORK_DIR / "mcp-mesh" / str(comp.id)
        meshed = generate_mesh(s.project, comp.id, root, out)
        q = meshed.quality
        return {
            "nodes": int(len(meshed.mesh.node_ids)),
            "elements": q.get("elements", 0),
            "element_types": meshed.mesh.element_counts(),
            "measures": q.get("measures", {}),
            "worst": q.get("worst", [])[:5],
            "warnings": meshed.warnings,
        }

    @tool(
        description='Solve a sketch (an extrude or revolve profile {"sketch": {...}}; '
        "format in puzzle.geometry.sketch) with the project's parameters: whether it solves, "
        "the degrees of freedom left, the conflicting constraints (1-based) and the solved "
        "point positions in m. Check a sketch here before submitting it."
    )
    @guarded
    def sketch_check(sketch: dict[str, Any]) -> dict[str, Any]:
        from puzzle.geometry.sketch import SketchError, describe, loops, solve
        from puzzle.model.schema import ParameterSet

        s = session()
        params = {
            k: q.to_si() for ps in s.project.of_type(ParameterSet) for k, q in ps.values.items()
        }
        try:
            solved = solve(sketch, params)
            if solved.status == "ok":
                loops(sketch, solved)  # an open outline is an error too
        except SketchError as exc:
            raise PuzzleError(str(exc), code="invalid") from None
        return {
            "status": solved.status,
            "dof": solved.dof,
            "message": describe(solved, sketch),
            "conflicting": [i + 1 for i in solved.failed],
            "points": {n: list(p) for n, p in solved.points.items()},
        }

    @tool(
        description="What a material can be used for (ADR 0020): for each solver and analysis, "
        "the properties it lacks and the material models that solver would refuse. "
        "`material` is a project material's name, or a library key."
    )
    @guarded
    def material_check(material: str) -> dict[str, Any]:
        from puzzle.materials import library_keys, library_material
        from puzzle.materials.registry import completeness
        from puzzle.model.schema import Material

        s = session()
        found = next((m for m in s.project.of_type(Material) if m.name == material), None)
        if found is None and material in library_keys():
            found = library_material(material)
        if found is None:
            raise PuzzleError(
                f"no material '{material}' in the project or library", code="not_found"
            )
        return {
            "material": found.name,
            "models": found.models.used(),
            "analyses": completeness(found),
        }

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
        description="Make a change: one command, or a list of model edits applied together "
        "(all or none, undone as one). See schema('commands'). The autonomy level decides "
        "whether it is refused, proposed or applied."
    )
    @guarded
    def submit(
        command: dict[str, Any] | list[dict[str, Any]], rationale: str = ""
    ) -> dict[str, Any]:
        s = session()
        if isinstance(command, list):
            return s.submit_batch(command, actor, rationale=rationale).to_dict()
        return s.submit(command, actor, rationale=rationale).to_dict()

    @tool(
        description="Ask the user to review commands before they apply, whatever the level. "
        "Use only when you want their review; changes they asked for go through submit."
    )
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
        """A study's latest run, or a derived result (ADR 0025) by name, evaluated."""
        from puzzle.commands import resolve
        from puzzle.model.schema import ResultDefinition, Study
        from puzzle.project.layout import RESULTS_DIR
        from puzzle.results.store import ResultStore

        s = session()
        store = ResultStore(root / RESULTS_DIR)
        defined = [d for d in s.project.of_type(ResultDefinition) if study in (d.name, str(d.id))]
        if defined:
            from types import SimpleNamespace

            from puzzle.post.derived import evaluate

            d = evaluate(s.project, store, defined[0])
            record = SimpleNamespace(run_id=f"derived:{defined[0].id}", solver="derived")
            return record, d.mesh, d.results
        found = resolve(s.project, study, Study)
        runs = [r for r in store.runs() if r.study_id == str(found.id)]
        if not runs:
            raise PuzzleError(f"study '{found.name}' has no runs yet", code="not_found")
        record = runs[-1]
        return record, store.read_mesh(record.mesh_id), store.read_results(record.run_id)

    @tool(
        description="A derived result (combination or equation, ADR 0025): what it reads, "
        "the results it gives (field names for result_stats etc., which take its name in "
        "place of a study), warnings, and for equations the names an expression may use."
    )
    @guarded
    def derived_result(name: str) -> dict[str, Any]:
        from puzzle.commands import resolve
        from puzzle.model.schema import ResultDefinition
        from puzzle.post import derived
        from puzzle.project.layout import RESULTS_DIR
        from puzzle.results.store import ResultStore

        s = session()
        d = resolve(s.project, name, ResultDefinition)
        store = ResultStore(root / RESULTS_DIR)
        out = derived.evaluate(s.project, store, d)
        info: dict[str, Any] = {
            "definition": d.model_dump(mode="json"),
            "runs": out.run_ids,
            "results": [{"field": i.field, "key": i.key, "label": i.label} for i in out.items],
            "warnings": out.warnings,
        }
        if d.method != "equation":
            info["equation_names"] = sorted(derived.names(out.items))
        return info

    @tool(
        description="The names an equation over a study's latest run may use "
        "(von_mises, deformation_y, stress_xx, ...), and the project parameters."
    )
    @guarded
    def equation_names(study: str) -> dict[str, Any]:
        from puzzle.post import derived

        _, _, results = latest(study)
        items = derived._items_of(results)
        return {
            "results": {derived.name_of(i): i.label for i in items},
            "parameters": derived.parameters(session().project),
            "functions": list(derived.FUNCTIONS),
        }

    @tool(
        description="Every kept probe (ADR 0032) read for its source's current run: "
        "value (SI), the quantity, the point (m), and how far outside the mesh it was."
    )
    @guarded
    def probes() -> list[dict[str, Any]]:
        from puzzle.model.schema import ResultProbe
        from puzzle.post.probes import evaluate
        from puzzle.project.layout import RESULTS_DIR
        from puzzle.results.store import ResultStore

        s = session()
        store = ResultStore(root / RESULTS_DIR)
        out = []
        for p in s.project.of_type(ResultProbe):
            try:
                found = evaluate(s.project, store, p)
                out.append({"probe": p.name, **found})
            except PuzzleError as exc:
                out.append({"probe": p.name, "error": exc.message, "code": exc.code})
        return out

    @tool(
        description="A kept probe at every step of its source's current run (ADR 0038): "
        "[{step: time, increment or frequency, value (SI)}], for load-deflection curves "
        "and transients."
    )
    @guarded
    def probe_history(name: str) -> list[dict[str, Any]]:
        from puzzle.commands import resolve
        from puzzle.model.schema import ResultProbe
        from puzzle.post.probes import history
        from puzzle.project.layout import RESULTS_DIR
        from puzzle.results.store import ResultStore

        s = session()
        probe = resolve(s.project, name, ResultProbe)
        return history(s.project, ResultStore(root / RESULTS_DIR), probe)

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
