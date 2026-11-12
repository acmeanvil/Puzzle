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
from uuid import UUID

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from puzzle.commands import Actor, Session, command_schema, outline
from puzzle.errors import PuzzleError
from puzzle.post.exports import EXPORTS as _EXPORTS

EXPORT_NAMES = sorted(_EXPORTS)

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

Before a task in an area that has a skill (bolts, beam sections, ...), read it with
`skill_read`: it gives the steps, tools and pitfalls. `skills_list` lists them.
For a question about a solver, an error message or what Puzzle supports, search
`knowledge` and cite the passages you use.
"""


def _bolt_index(comp, name: str) -> int:
    """The position of the bolt named `name` among the component's connectors."""
    for i, c in enumerate(comp.connectors):
        if c.name == name:
            return i
    raise PuzzleError(f"no bolt '{name}' in '{comp.name}'", code="not_found",
                      details={"bolts": [c.name for c in comp.connectors]})  # fmt: skip


def build_server(root: Path, client: str = "mcp-client", *, outside: bool = True,
                 model: str = "", suite: str = "") -> MCPServer:  # fmt: skip
    """The tools, for an outside agent (`outside`, refused when the project's MCP access
    is off) or for the built-in agent in-process (agent.agent), which the setting does
    not cover."""
    actor = Actor("agent", client, model=model, suite=suite)

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

    # --- skills (ADR 0057) ---------------------------------------------------------

    @tool(description="The skills: one line each, saying when to read it (`skill_read`).")
    def skills_list() -> dict[str, Any]:
        from puzzle.agent.skills import skills

        return {"skills": [{"name": s.name, "when": s.description, "adr": s.adr}
                           for s in skills().values()]}  # fmt: skip

    @tool(description="One skill's how-to: the steps, the tools and their arguments, units "
          "and the usual mistakes, for a task in its area.")  # fmt: skip
    def skill_read(name: str) -> dict[str, Any]:
        from puzzle.agent.skills import read

        try:
            return {"skill": read(name)}
        except KeyError as exc:
            raise ToolError(json.dumps({"code": "not_found", "message": exc.args[0]})) from None

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
        description="Find where the parts of a component touch (ADR 0039), and the bodies of "
        "a part with several (an imported STEP assembly is often one part): one proposal per "
        "pair of parts or bodies with the faces on each side, the gap (m) and the overlap "
        "(m^2); `source_body`/`target_body` name the bodies (null between whole parts). "
        "Bodies of one part share their nodes where they touch (Share faces): a bonded "
        "contact between them records that, a frictionless or frictional one parts them. "
        "`tolerance` is the largest gap in m (default 0.5 % of the model's size), `angle` how "
        "far from face to face, in degrees. `make_bonded` is the batch that makes every "
        "proposal as a bonded contact (face selections first); change a contact's type "
        "or drop proposals, then pass the list to submit. Nothing is made here. `thread`, "
        "when set, says the two sides are coaxial cylinders of near-equal radius (a tapped "
        "hole or a screw in a nut?) with a likely ISO size: make the contact, then "
        "`contact_thread` (ADR 0076)."
    )
    @guarded
    def contacts_find(
        component: str, tolerance: float | None = None, angle: float = 10.0
    ) -> dict[str, Any]:
        from puzzle.commands import resolve
        from puzzle.geometry.contacts import (
            body_labels,
            contact_commands,
            find_contacts,
            proposals,
        )
        from puzzle.model.schema import Component

        s = session()
        comp = resolve(s.project, component, Component)
        pairs = find_contacts(s.project, comp, root, tolerance=tolerance, angle=angle)
        found = proposals(pairs, body_labels(s.project, comp))
        batch = [c.model_dump(mode="json") for c in contact_commands(s.project, comp, found)]
        return {
            "proposals": [
                {
                    "name": p.name,
                    "source_part": p.source_part,
                    "source_faces": p.source_faces,
                    "target_part": p.target_part,
                    "target_faces": p.target_faces,
                    "source_body": p.labels[0] if p.labels and p.source_body else None,
                    "target_body": p.labels[1] if p.labels and p.target_body else None,
                    "gap_m": p.distance,
                    "overlap_m2": p.area,
                    "thread": p.thread,  # ADR 0076: coaxial cylinders, a likely size
                }
                for p in found
            ],
            "make_bonded": batch,
        }

    @tool(
        description="Split a part holding several bodies (an imported STEP assembly) into one "
        "part per body (ADR 0039): each body a geometry of its own (the file imported, then "
        "that body kept), named by the body's selection; every reference follows its body "
        "(materials, loads, supports, contacts, bolts, mesh sizes, result tools), and one "
        "on a selection spanning bodies is repeated per part where it can be (materials, "
        "features, sections) or reported in `notes`. `bond` (default true) adds bonded "
        "contacts where the bodies touch, as the part's Share faces joined them. Returns "
        "the plan; `batch` is the list to pass to submit (one undo). Nothing is made here."
    )
    @guarded
    def parts_split(component: str, part: str, bond: bool = True) -> dict[str, Any]:
        from puzzle.commands import resolve
        from puzzle.model.schema import Component
        from puzzle.split_parts import plan

        s = session()
        comp = resolve(s.project, component, Component)
        made = plan(s.project, comp, part, root, bond=bond)
        return {
            "summary": made.summary(),
            "parts": [{"name": n, "body": b} for n, b in made.parts],
            "bonded_contacts": made.bonds,
            "notes": made.notes,
            "batch": [c.model_dump(mode="json") for c in made.commands],
        }

    @tool(
        description="Check a component's contacts before solving (ADR 0056): per contact "
        "its setup, what each solver is given (`solver_input`, from the code that writes the "
        "input), and its initial state on the mesh: `status` (closed, near, open, far, "
        "overhanging), `shares` of the source face's area by status, the largest gap and "
        "penetration and the nearest distance (m), and the search distance (m). `warnings` "
        "name each contact problem and its fix (overhanging source faces, held or bolted "
        "source nodes, faces far apart, a shared outline under Code_Aster friction, bodies "
        "held only by sliding contact). `solver`: one solver's view; default the "
        "component's studies'. Meshes the component."
    )
    @guarded
    def contact_check(component: str, solver: str | None = None) -> dict[str, Any]:
        from puzzle import contact_check as check
        from puzzle.commands import resolve
        from puzzle.mesh import generate_mesh
        from puzzle.model.schema import Component
        from puzzle.project.layout import WORK_DIR

        s = session()
        comp = resolve(s.project, component, Component)
        out = root / WORK_DIR / "mcp-mesh" / str(comp.id)
        mesh = generate_mesh(s.project, comp.id, root, out).mesh
        return check.report(s.project, comp, mesh, solver)

    @tool(
        description="Read a field file (ADR 0044: x, y, z and values: CSV/TXT/DAT, VTU, a "
        "Fluent profile .prof, Exodus II .exo/.e, EnSight Gold .case, or an OpenFOAM case "
        "folder) that is inside the project folder (`file`, relative to it) and say how it "
        "would be read: format, columns with their roles (x, y, z, value, time, frequency, "
        "ignore) and "
        "units, the points' length unit, and for multi-step sources their `steps` (time, "
        "index) and `regions` (blocks, parts, patches, profiles). `region` reads one of "
        "those, `index` one step (default the last). Varying in time: `files` (more files, "
        "one per time, same layout) with `times` (s, one per file, `file`'s first), or "
        "`every_step` (every step of the source, at its own times or at `times`); a CSV time "
        "column is a column with role time. Varying with frequency (for harmonic steps "
        "only): `frequencies` (Hz) in place of `times`, or a CSV column with role frequency. "
        "Returns `create`, the command that adds it as an "
        "imported field: change its `columns`, `length_unit`, or `placement` (translation m, "
        "rotation degrees about x, y, z, about m, axes a coordinate system's id) if the "
        "guess is wrong, then pass it to `submit`. Check it with `field_check` first."
    )
    @guarded
    def field_import(
        file: str,
        name: str | None = None,
        region: str | None = None,
        index: str | None = None,
        files: list[str] | None = None,
        times: list[float] | None = None,
        every_step: bool = False,
        frequencies: list[float] | None = None,
    ) -> dict[str, Any]:
        from puzzle.errors import PuzzleError
        from puzzle.fields import file_hash, sniff

        if frequencies is not None:
            if times is not None:
                raise PuzzleError("give times or frequencies, not both", code="invalid_input")
            times = frequencies

        def inside(rel: str) -> Path:
            path = (root / rel).resolve()
            if not path.is_relative_to(root.resolve()) or not path.exists():
                raise PuzzleError(f"no file '{rel}' inside the project", code="not_found")
            return path

        path = inside(file)
        found = sniff(path)
        data = {
            "name": name or path.stem,
            "file": path.relative_to(root.resolve()).as_posix(),
            "file_hash": file_hash(path),
            **{
                k: found[k]
                for k in ("format", "delimiter", "skip_rows", "header", "length_unit", "columns")
            },  # fmt: skip
        }
        data.update({k: v for k, v in (("region", region), ("index", index)) if v})
        if files:
            if times is None or len(times) != len(files) + 1:
                raise PuzzleError(f"{len(files) + 1} files need as many times (s)",
                                  code="invalid_input")  # fmt: skip
            data["times"] = [{"time": times[0], "file": data["file"], "file_hash":
                              data["file_hash"]}]  # fmt: skip
            for t, rel in zip(times[1:], files, strict=True):
                more = inside(rel)
                data["times"].append({"time": t, "file": more.relative_to(root.resolve())
                                      .as_posix(), "file_hash": file_hash(more)})  # fmt: skip
        elif every_step:
            steps = found.get("steps") or []
            if len(steps) < 2 or (times is not None and len(times) != len(steps)):
                raise PuzzleError(
                    f"{file} has {len(steps)} steps; every_step needs two or "
                    "more (and as many times, if given)",
                    code="invalid_input",
                )
            data["times"] = [{"time": times[k] if times else s["time"], "index": s["index"]}
                             for k, s in enumerate(steps)]  # fmt: skip
        if frequencies is not None:
            if not data.get("times"):
                raise PuzzleError("frequencies go with files or every_step; a CSV frequency "
                                  "column is a column with role frequency",
                                  code="invalid_input")  # fmt: skip
            data["axis"] = "frequency"
        return {"read": found, "create": {"command": "create", "kind": "field", "data": data}}

    @tool(
        description="An imported field's verification report (ADR 0044): points, each "
        "column's range and mean (SI), and with `on` (a component, or Component/Part/"
        "Selection) the share of it within `tolerance_mm` of a data point, the farthest "
        "distance, points off the model, and warnings (sparse data, uncovered areas, a "
        "length unit that looks wrong). Meshes the component when `on` is given."
    )
    @guarded
    def field_check(field: str, on: str | None = None, tolerance_mm: float | None = None
                    ) -> dict[str, Any]:  # fmt: skip
        import tempfile

        from puzzle.commands import resolve
        from puzzle.fields import check, target_points
        from puzzle.model.schema import ImportedField

        s = session()
        f = resolve(s.project, field, ImportedField)
        targets = None
        if on:
            with tempfile.TemporaryDirectory(prefix="puzzle-field-") as work:
                targets = target_points(s.project, root, on, Path(work))
        tol = tolerance_mm / 1000 if tolerance_mm is not None else None
        return check(s.project, root, f, targets, tol)

    @tool(
        description="Propose bolts for a component's holes (ADR 0050). Holes are the "
        "parts' cylindrical faces; a bolt joins a stack of holes on one axis in different "
        "bodies (of different parts, or several bodies of one part), any number of layers: "
        "from the first hole to the last, the ones between in `through`. "
        "With a rule (`rule` names it): hole diameters `min_diameter`/`max_diameter` (m), "
        "`parts`, an `axis` [x, y, z] (within 5 degrees), lengths between the holes' "
        "centres `min_length`/`max_length` (m); or `first` and `second`, two face "
        "selections (Part/Selection) whose holes pair up. `bolt_type` is a bolt type's "
        "name, else each hole gets the largest type that fits; `names` a pattern with {n}; "
        "`group` where they are listed. `ground_unpaired`: a hole with no partner gets a "
        "bolt to ground (an anchor, `ground_length` m below it, default 2 d); `same_part`: "
        "two holes of one part may be joined; `bearing_faces`: imprint the rings under heads "
        "and nuts in the parts' geometry. Returns the proposals, the bolts the rule made "
        "before that have lost their holes (`stale`), and `make_bolts`, the batch to pass "
        "to submit (face selections, bolts, the rule kept on the component). Nothing is "
        "made here."
    )
    @guarded
    def bolts_generate(
        component: str,
        rule: str = "Bolt rule",
        min_diameter: float | None = None,
        max_diameter: float | None = None,
        parts: list[str] | None = None,
        axis: list[float] | None = None,
        min_length: float | None = None,
        max_length: float | None = None,
        first: str | None = None,
        second: str | None = None,
        bolt_type: str | None = None,
        names: str = "Bolt {n}",
        group: str | None = None,
        material: str | None = None,
        ground_unpaired: bool = False,
        ground_length: float | None = None,
        same_part: bool = False,
        bearing_faces: bool = False,
    ) -> dict[str, Any]:
        from puzzle import bolt_generator as gen
        from puzzle.commands import resolve
        from puzzle.model.schema import BoltRule, BoltType, Component, Material, Quantity

        s = session()
        comp = resolve(s.project, component, Component)
        kind = resolve(s.project, bolt_type, BoltType) if bolt_type else None
        grade = resolve(s.project, material, Material) if material else None
        metres = lambda v: Quantity(value=v, unit="m") if v is not None else None  # noqa: E731
        if first and second:
            found = gen.by_sets(s.project, comp, root, first, second)
            the_rule = None
        else:
            the_rule = BoltRule(
                name=rule, min_diameter=metres(min_diameter), max_diameter=metres(max_diameter),
                parts=parts or [], axis=tuple(axis) if axis else None,
                min_length=metres(min_length), max_length=metres(max_length),
                type=kind.id if kind else None, names=names, group=group,
                ground_unpaired=ground_unpaired, ground_length=metres(ground_length),
                same_part=same_part, bearing_faces=bearing_faces,
            )  # fmt: skip
            found = gen.by_rule(s.project, comp, root, the_rule)
        planned = gen.plan(s.project, comp, found, the_rule, kind)
        try:
            given = str(grade.id) if grade else None
            batch = gen.commands(s.project, comp, planned, material=given, rule=the_rule)
        except ValueError as exc:  # no type fits a hole and no material was given
            raise PuzzleError(str(exc), code="invalid") from None
        return {
            "proposals": [
                {
                    "name": p.name,
                    "holes": [f"{p.pair.source_part}/{p.pair.source_face}",
                              f"{p.pair.target_part}/{p.pair.target_face}"],
                    "through": [f"{part}/{face}" for part, face in p.pair.through],
                    "hole_diameter_m": p.pair.diameter,
                    "length_m": p.length,
                    "bolt_type": p.type_name,
                    "note": p.note,
                }
                for p in planned.add
            ],
            "already_there": planned.kept,
            "stale": planned.stale,
            "make_bolts": [c.model_dump(mode="json") for c in batch],
        }  # fmt: skip

    @tool(
        description="Model bolts as solids drawn from the fastener tables (ADR 0077): each "
        "named bolt (`bolt`: a name or a list) becomes a part of its own, the standard bolt "
        "by its type's head (hex or socket) and size, placed on its holes' axis, its grade "
        "as its material, its head (and nut) on the plates by a frictional contact (the "
        "type's head_friction; `bonded_heads` bonds them), its thread into the tapped hole "
        "or nut by a threaded contact, its preload on a section across the shank, and finer "
        "elements at the fillet and thread; plain washers too when the bolt's `washers` "
        "(or its type's) says head, nut or both. Needs a tapped or bolt-and-nut joint. Its "
        "loads then come from that section (bolt_results), and the fatigue tool takes the "
        "bolt's thread_kf as Kf at its thread. Returns `batch` to pass to submit (one "
        "undo); nothing is made here."
    )
    @guarded
    def bolt_make_modelled(component: str, bolt: str | list[str],
                           bonded_heads: bool = False) -> dict[str, Any]:  # fmt: skip
        from puzzle.bolts import model_commands
        from puzzle.commands import resolve
        from puzzle.model.schema import Component

        s = session()
        comp = resolve(s.project, component, Component)
        names = [bolt] if isinstance(bolt, str) else list(bolt)
        indices = [_bolt_index(comp, n) for n in names]
        try:
            batch = model_commands(s.project, comp, indices, root, bonded_heads=bonded_heads)
        except ValueError as exc:
            raise PuzzleError(str(exc), code="invalid") from None
        return {"bolts": names, "batch": [c.model_dump(mode="json") for c in batch]}

    @tool(
        description="Turn a modelled bolt (ADR 0077) back into a beam bolt: its parts, "
        "geometries, materials, contacts, the plates' selections made for them, its "
        "pretension feature and mesh sizes removed; its name, type and preload kept. "
        "Returns `batch` to pass to submit (one undo); nothing is made here."
    )
    @guarded
    def bolt_make_beam(component: str, bolt: str) -> dict[str, Any]:
        from puzzle.commands import resolve
        from puzzle.model.schema import Component
        from puzzle.solid_bolts import beam_commands

        s = session()
        comp = resolve(s.project, component, Component)
        index = _bolt_index(comp, bolt)
        if comp.connectors[index].formulation != "modelled":
            raise PuzzleError(f"bolt '{bolt}' is not a modelled bolt", code="invalid")
        batch = beam_commands(s.project, comp, index)
        return {"bolts": [bolt], "batch": [c.model_dump(mode="json") for c in batch]}

    @tool(
        description="A frame's geometry (ADR 0079): line members between named nodes, or a "
        "DXF's LINE/LWPOLYLINE/POLYLINE entities (layers become member groups). `nodes` "
        "{name: [x, y, z]} in metres, `members` [[name, i, j], or [name, i, j, group]]; or "
        "`dxf` (a file in the project), `layer`, `scale` (m per drawing unit; default from "
        "$INSUNITS, mm when unset). Gives edge selections per member and group and point "
        "selections per node. Returns `command` (a create) to pass to submit; nothing is "
        "made here. Then a component with the geometry as a part, a beam section on it "
        "(or per group), a material, supports and loads at nodes (skill `frames`)."
    )
    @guarded
    def frame_geometry(name: str, nodes: dict | None = None, members: list | None = None,
                       dxf: str | None = None, layer: str | None = None,
                       scale: float | None = None) -> dict[str, Any]:  # fmt: skip
        from puzzle import frames
        from puzzle.commands.model import Create

        try:
            geo = frames.geometry(name, nodes, members, dxf=dxf, layer=layer, scale=scale,
                                  root=root)  # fmt: skip
        except frames.FrameError as exc:
            raise PuzzleError(str(exc), code="invalid") from None
        data = geo.model_dump(mode="json")
        groups = sorted({m[3] for m in (members or []) if not isinstance(m, dict) and len(m) > 3})
        return {
            "command": Create(kind="geometry", data=data).model_dump(mode="json"),
            "members": [sel["name"] for sel in data["selections"] if sel["entity"] == "edge"],
            "nodes": [sel["name"] for sel in data["selections"] if sel["entity"] == "vertex"],
            "groups": groups,
        }

    @tool(
        description="A frame component's members (ADR 0079): each member's ends, length "
        "(m), group and section (or none), the nodes, member end releases and rigid "
        "offsets, and the supports and loads. Frames solve on PyNite (releases, offsets, "
        "line loads, node springs, self weight) and on CalculiX and Code_Aster beams "
        "(no releases yet)."
    )
    @guarded
    def frame_members(component: str) -> dict[str, Any]:
        from puzzle import frames
        from puzzle.commands import resolve
        from puzzle.model.schema import Component

        s = session()
        comp = resolve(s.project, component, Component)
        return frames.summary(s.project, comp)

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
        description="Run a study on its mesh recipe meshed by Gmsh and by Netgen and compare "
        "(ADR 0019): each quantity's peak (or a global value), or 'eigenfrequencies', "
        "as a relative difference against `tolerance`. Slow: it solves the study twice."
    )
    @guarded
    def mesh_shootout(
        study: str, quantities: list[str] | None = None, tolerance: float = 0.02
    ) -> dict[str, Any]:
        import tempfile

        from puzzle.commands import resolve
        from puzzle.model.schema import ComparisonMetric, Study
        from puzzle.shootout import run_mesh_shootout

        s = session()
        target = resolve(s.project, study, Study)
        metrics = [
            ComparisonMetric(quantity=q, norm="relative_value", tolerance=tolerance)
            for q in (quantities or ["displacement"])
        ]
        with tempfile.TemporaryDirectory() as work:
            out = run_mesh_shootout(s.project, target.id, root, Path(work), metrics)
        return {
            "passed": out.passed,
            "comparisons": [
                {"quantity": c.metric.quantity, "relative_difference": c.value, "passed": c.passed}
                for c in out.comparisons
            ],
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

        s = session()
        from puzzle.operators import parameter_values

        params = parameter_values(s.project)
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

    @tool(description="The user's material library as one file to share, written inside the "
          "project folder (e.g. exports/materials.json). scope: yours (made in the builder), "
          "user (imported and yours) or all (with Puzzle's generics). Agents never import "
          "one: the user does, Materials -> Import material library... (ADR 0020).")  # fmt: skip
    @guarded
    def materials_export(path: str, scope: str = "user") -> dict[str, Any]:
        from puzzle.materials import exchange

        out = (Path(path) if Path(path).is_absolute() else root / path).resolve()
        if not out.is_relative_to(Path(root).resolve()):  # agents write inside the project
            raise PuzzleError(
                f"exports go inside the project folder, not {path}", code="not_allowed"
            )
        return {"file": str(out), "materials": exchange.export(out, scope)}

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
        description="The recipes (ready-wired operator chains: worst nodes, peak per part, "
        "solver difference, stress margin, reactions in kN, mass from geometry) and the "
        "user's saved clusters (ADR 0064): key, name, help and input ports."
    )
    @guarded
    def recipes() -> list[dict[str, Any]]:
        from puzzle.operators import clusters

        return clusters.summary()

    @tool(
        description="Put a recipe or cluster into the project (ADR 0064): `key` from "
        'recipes, `bindings` each port name to a source {"source": id, "port": '
        "\"result\"} (a study's result, a table operator's out). One batch, one undo; at "
        "'propose' a proposal."
    )
    @guarded
    def recipe_insert(key: str, bindings: dict[str, dict[str, str]] | None = None,
                      rationale: str = "") -> dict[str, Any]:  # fmt: skip
        from puzzle.operators import clusters

        s = session()
        wired = {k: (v["source"], v.get("port", "out")) for k, v in (bindings or {}).items()}
        batch = clusters.commands(s.project, key, wired)
        return s.submit_batch(batch, actor, rationale=rationale).to_dict()

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

    # --- the domain pack (ADR 0022) --------------------------------------------------

    @tool(description="Search Puzzle's domain pack: the skills, curated solver notes "
          "(what Puzzle learned about each solver), each solver's capabilities, Code_Aster's "
          "French messages in English, and the ADRs. Returns passages with a citation "
          "(source § heading) and, for manual summaries, a link. Cite what you use.")  # fmt: skip
    @guarded
    def knowledge(query: str, limit: int = 5) -> dict[str, Any]:
        from puzzle.agent import knowledge as pack
        from puzzle.agent.settings import load_settings

        s = load_settings()
        embed = pack.ollama_embedder(s.base_url, s.embed_model) if s.embed_model else None
        return {"hits": pack.search(query, limit, embed=embed)}

    # --- the solvers here (ADR 0068 step 4) -------------------------------------------

    @tool(description="The solvers on this computer and how a solve runs each now: "
          "native (its program), in a container (its image) or missing (why); CalculiX's "
          "note says PARDISO or SPOOLES, and `pardiso` the state of CalculiX with PARDISO "
          "built here (built, building, failed with the reason, ready, unavailable).")  # fmt: skip
    @guarded
    def solvers_list() -> dict[str, Any]:
        from dataclasses import asdict

        from puzzle import solver_builds

        return {"solvers": [asdict(r) for r in solver_builds.solvers()],
                "pardiso": asdict(solver_builds.status())}  # fmt: skip

    @tool(description="Start building CalculiX with Intel MKL's PARDISO on this computer, "
          "in the background (minutes; contact and large solves then factor 2-4x faster "
          "with the same answers). Returns at once; follow it with solvers_list. Only when "
          "the user asks: the GUI already builds it once by itself.")  # fmt: skip
    @guarded
    def pardiso_build() -> dict[str, Any]:
        from dataclasses import asdict

        from puzzle import solver_builds

        state = solver_builds.status()
        if state.state in ("unavailable", "building"):
            return {"started": False, "pardiso": asdict(state)}
        import threading

        threading.Thread(target=lambda: solver_builds.build(say=lambda _: None),
                         name="pardiso-build", daemon=True).start()  # fmt: skip
        return {"started": True, "pardiso": asdict(state)}

    # --- remote hosts (ADR 0021) -----------------------------------------------------

    @tool(description="The user's remote hosts (their hosts.toml): name, address, how "
          "solvers run there, scheduler, cores. A study runs on one when its `remote` names "
          "it, or run_study has `host`. Hosts are the user's: agents list and check them, "
          "never add them.")  # fmt: skip
    @guarded
    def hosts_list() -> dict[str, Any]:
        from puzzle import hosts

        return {"hosts": [{"name": h.name, "address": h.target, "runtime": h.runtime,
                           "scheduler": h.scheduler, "partition": h.partition,
                           "cores": h.cores, "walltime": h.walltime}
                          for h in hosts.load().values()]}  # fmt: skip

    @tool(description="Reach a remote host over SSH and say what it has: reachable or why "
          "not, its cores, which solvers run there, whether its scheduler answers.")  # fmt: skip
    @guarded
    def host_check(name: str) -> dict[str, Any]:
        from dataclasses import asdict

        from puzzle import hosts

        found = hosts.check(hosts.get(name))
        return asdict(found) | {"words": found.words()}

    # --- jobs with a runner (ADR 0067) ---------------------------------------------

    @tool(description="The project's solves that are with a job runner and not home yet "
          "(ADR 0067): each job's id, study, solver, where (this computer or a host), "
          "state in words (Queued, Running, Finished, Failed, Cancelled, ...), elapsed "
          "seconds and the log's last line. A finished one waits for job_resume to bring "
          "its results home. `live` asks remote hosts too (SSH: slow).")  # fmt: skip
    @guarded
    def jobs_list(live: bool = False) -> dict[str, Any]:
        from puzzle.jobs import overview

        session()  # refused when MCP access is off
        return {"jobs": [r.to_dict() for r in overview.jobs(root, live=live)]}

    @tool(description="Pick up a pending job (its id, or its study's name): the same run "
          "attached to the job, its results stored as from any run. A run, so the "
          "autonomy level decides: refused at advise, a proposal at propose.")  # fmt: skip
    @guarded
    def job_resume(job: str) -> dict[str, Any]:
        from puzzle.jobs import overview

        s = session()
        (first, *_) = overview.find(root, job)
        return s.resume(first["job"], actor).to_dict()

    @tool(description="Stop a pending job (its id, or its study's name: all its jobs). It "
          "stays listed until resumed or dismissed by the user. Agents cancel only where "
          "the autonomy level lets them act.")  # fmt: skip
    @guarded
    def job_cancel(job: str) -> dict[str, Any]:
        from puzzle.jobs import overview

        s = session()
        if actor.is_agent and s.project.agent.level not in ("act", "act_in_budget"):
            raise PuzzleError("cancelling a solve needs autonomy 'act'; ask the user",
                              code="not_allowed")  # fmt: skip
        return {"cancelled": {e["job"]: overview.cancel(root, e["job"])
                              for e in overview.find(root, job)}}  # fmt: skip

    # --- versions (ADR 0059) --------------------------------------------------------

    @tool(description="The project's versions, newest first: number, time, by, what, known "
          "good, and each one's summary (studies and their state, what changed). Filter by "
          "who (by: you, puzzle, agent), types (user, first, opened, before_run, edits, "
          "before_restore, copied), since/until (ISO dates), good, and text.")  # fmt: skip
    @guarded
    def versions_list(
        limit: int = 20, by: str | None = None, types: list[str] | None = None,
        since: str | None = None, until: str | None = None, good: bool | None = None,
        text: str = "",
    ) -> dict[str, Any]:  # fmt: skip
        """Filters: by (you, puzzle, agent), types (user, first, opened, before_run, edits,
        before_restore, copied), since/until (ISO dates or times), good, text."""
        from datetime import datetime

        from puzzle.project import versions

        every = versions.versions(root)
        found = versions.select(
            every, by=by, types=types, good=good, text=text,
            since=datetime.fromisoformat(since) if since else None,
            until=datetime.fromisoformat(until) if until else None,
        )  # fmt: skip
        return {"versions": [v.to_dict() for v in reversed(found[-limit:])],
                "matching": len(found), "count": len(every)}  # fmt: skip

    @tool(description="Save a version of the project as it is now (before a risky batch "
          "of changes, say), with a label; `good` marks it known good.")  # fmt: skip
    @guarded
    def version_save(label: str, note: str = "", good: bool = False) -> dict[str, Any]:
        if actor.is_agent and session().project.agent.level == "advise":
            raise PuzzleError("agents only advise in this project", code="not_allowed")
        return session().save_version(actor, label=label, note=note, good=good).to_dict()

    @tool(description="Make the project a version's again: its number, or 'good' for the "
          "latest known good. The state replaced is kept as a version and undo takes the "
          "restore back. Agents restore only where the autonomy level lets them act.")  # fmt: skip
    @guarded
    def version_restore(version: str) -> dict[str, Any]:
        which = version if version in ("good", "latest") else int(version)
        return session().restore_version(which, actor).to_dict()

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
        description="Exact measurements of named geometry (ADR 0042), from the CAD shape: "
        "picks are [{entity: body|face|edge|vertex, name, part?}]. With `component`, picks "
        "name a part each and are measured where the parts are placed. One pick: length, "
        "area, volume, radius, centroid, normal or axis; two: minimum distance (and where), "
        "centre distance with dx, dy, dz, angle (degrees); more: totals. SI units."
    )
    @guarded
    def geometry_measure(
        picks: list[dict[str, str]], geometry: str | None = None, component: str | None = None
    ) -> dict[str, Any]:
        from puzzle.commands import resolve
        from puzzle.geometry.measure import MeasureError, measure, measure_placed, rows
        from puzzle.geometry.naming import named_geometry
        from puzzle.model.schema import Component, Geometry

        s = session()
        try:
            if component is not None:
                comp = resolve(s.project, component, Component)
                found = measure_placed(
                    s.project, comp, root, [(p["part"], p["entity"], p["name"]) for p in picks]
                )
            elif geometry is not None:
                geo = resolve(s.project, geometry, Geometry)
                named = named_geometry(s.project, geo, root)
                found = measure(named, [(p["entity"], p["name"]) for p in picks])
            else:
                raise PuzzleError("name a geometry or a component", code="invalid")
        except (MeasureError, KeyError) as exc:
            raise PuzzleError(f"can't measure: {exc}", code="invalid") from exc
        return found.as_dict() | {
            "rows": [{"label": a, "value": v, "unit": u} for a, v, u in rows(found)]
        }

    @tool(
        description="Every result tool (ADR 0032, 0042) read for its source's current run: "
        "point probes (value, point), results over a selection (max, min, mean or "
        "integral), reactions (force and moment components, about which point) and paths "
        "(largest value and where; the full series with probe_path). SI units."
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
                if p.type in ("path", "linearized"):  # a summary; probe_path: the series
                    drop = ("points", "values", "distance", "series")
                    found = {k: v for k, v in found.items() if k not in drop}
                out.append({"probe": p.name, "type": p.type, **found})
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

    @tool(
        description="A path result tool's series (ADR 0042): distance along the path (m) "
        "and the value there (SI; null outside the model)."
    )
    @guarded
    def probe_path(name: str) -> dict[str, Any]:
        import math

        from puzzle.commands import resolve
        from puzzle.model.schema import ResultProbe
        from puzzle.post.probes import evaluate
        from puzzle.project.layout import RESULTS_DIR
        from puzzle.results.store import ResultStore

        s = session()
        probe = resolve(s.project, name, ResultProbe)
        if probe.type not in ("path", "linearized"):
            raise PuzzleError(f"'{probe.name}' is not a path", code="invalid")
        found = evaluate(s.project, ResultStore(root / RESULTS_DIR), probe)
        return {
            "distance": found["distance"],
            "value": [None if math.isnan(v) else v for v in found["values"]],
            "label": found["label"],
        }

    @tool(
        description="Write a study's latest results for other software (ADR 0043). format: "
        + ", ".join(EXPORT_NAMES)
        + ". Which to choose, and each one's options: skill_read('exports'). path is inside "
        "the project folder (e.g. exports/shape). axes (a coordinate system's name, ADR "
        "0049): vectors and tensors in its axes. Returns the files written."
    )
    @guarded
    def export(
        study: str,
        format: str,
        path: str,
        step: int = -1,
        quantity: str | None = None,
        selection: str | None = None,
        scale: float = 1.0,
        compensate: bool = False,
        axes: str | None = None,
    ) -> dict[str, Any]:
        from puzzle.commands import resolve
        from puzzle.model.schema import Study
        from puzzle.post import derived
        from puzzle.post.exports import export as write
        from puzzle.project.layout import RESULTS_DIR
        from puzzle.results.store import ResultStore

        s = session()
        target = resolve(s.project, study, Study)
        store = ResultStore(root / RESULTS_DIR)
        record = derived.run_for(s.project, store, target.id)
        out = (Path(path) if Path(path).is_absolute() else root / path).resolve()
        if not out.is_relative_to(Path(root).resolve()):  # agents write inside the project
            raise PuzzleError(
                f"exports go inside the project folder, not {path}", code="not_allowed"
            )
        files = write(s.project, store, record.run_id, format, out, step=step,
                      quantity=quantity, selection=selection, scale=scale,
                      compensate=compensate, axes=axes, root=root)  # fmt: skip
        return {"files": [str(f) for f in files]}

    @tool(description="The project as one .puzzle archive to share (ADR 0061), written inside "
          "the project folder (e.g. exports/model.puzzle). results: none, latest (per study) "
          "or all; versions and history true or false. Returns its size by part.")  # fmt: skip
    @guarded
    def project_archive(path: str, results: str = "latest", versions: bool = True,
                        history: bool = True) -> dict[str, Any]:  # fmt: skip
        from puzzle.project.archive import Include, write

        out = (Path(path) if Path(path).is_absolute() else root / path).resolve()
        if not out.is_relative_to(Path(root).resolve()):  # agents write inside the project
            raise PuzzleError(f"archives go inside the project folder, not {path}",
                              code="not_allowed")  # fmt: skip
        done = write(root, out, Include(results=results, versions=versions, history=history),
                     by=actor.name if actor.is_agent else "you")  # fmt: skip
        return {"path": str(done.path), "bytes": done.bytes, "sizes": done.sizes,
                "summary": done.summary()}  # fmt: skip

    @tool(
        description="A study's solver settings (ADR 0023): the shared ones (on each solve "
        "step's settings: linear_solver, tolerance, max_iterations, output_every) and each "
        "of its solvers' own (Study.solver_settings[solver]), with labels, the solver's own "
        "name, choices, defaults and current values, and how that solver's expert "
        "pass-through (Study.expert_settings[solver]) is written. Change them with "
        "solver_settings_set."
    )
    @guarded
    def solver_settings(study: str) -> dict[str, Any]:
        from dataclasses import asdict

        from puzzle.adapters import available_adapters
        from puzzle.adapters.settings import SHARED
        from puzzle.commands import resolve
        from puzzle.model.schema import Study

        s = session()
        target = resolve(s.project, study, Study)
        adapters = available_adapters()

        def row(setting, current):
            out = asdict(setting) | {"current": current}
            out["steps"] = sorted(out["steps"])
            return out

        first = target.solve_steps()[0].settings if target.solve_steps() else {}
        return {
            "shared": [row(x, first.get(x.key)) for x in SHARED],
            "solvers": {
                name: {
                    "settings": [
                        row(x, target.solver_settings.get(name, {}).get(x.key))
                        for x in adapters[name]().settings_schema()
                    ],
                    "expert": target.expert_settings.get(name, {}),
                    "expert_help": adapters[name]().expert_help(),
                }
                for name in target.all_solvers()
                if name in adapters
            },
        }

    @tool(
        description="Change a study's solver settings (ADR 0023) in one edit, one undo: "
        "`shared` = {key: value} on every solve step it applies to (linear_solver, "
        "tolerance, max_iterations, output_every; load stepping: increments, "
        "initial_increment, min_increment, fixed_increments); `solvers` = {solver: {key: "
        "value}} for each solver's own (convergence: CalculiX cutbacks, cutback_factor, "
        "increase_factor; Code_Aster line_search, prediction, matrix, cutback_parts, "
        "cutback_levels, absolute_tolerance; Elmer relaxation, newton_after_iterations); "
        "`expert` = {solver: {path: value}} replacing that solver's pass-through. A value "
        "of null removes a setting. Keys, choices and units: solver_settings. At "
        "'propose' a proposal."
    )
    @guarded
    def solver_settings_set(study: str, shared: dict[str, Any] | None = None,
                            solvers: dict[str, dict[str, Any]] | None = None,
                            expert: dict[str, dict[str, str]] | None = None,
                            rationale: str = "") -> dict[str, Any]:  # fmt: skip
        from puzzle.adapters.settings import edit_ops
        from puzzle.commands import resolve
        from puzzle.model.schema import Study

        s = session()
        target = resolve(s.project, study, Study)
        ops = edit_ops(target, shared, solvers, expert)
        if not ops:
            return {"changed": False, "note": "nothing to change"}
        command = {"command": "edit", "object": str(target.id),
                   "ops": [op.model_dump() for op in ops]}  # fmt: skip
        return s.submit(command, actor, rationale=rationale).to_dict()

    @tool(
        description="Put a symmetry or antisymmetry plane on a component (ADR 0081): "
        "`selection` a flat face (a straight edge in 2D), 'Part/Name'; `kind` "
        "'symmetry' (nothing moves through it; on every physics that has one: solid, heat "
        "and electrostatic symmetry, fluid symmetry, magnetic symmetry) or 'antisymmetry' "
        "(solid mechanics: nothing moves along it). One edit, one undo; at 'propose' a "
        "proposal. A 2D or axisymmetric model is the component's `dimension` (an edit)."
    )
    @guarded
    def symmetry_plane(component: str, selection: str, kind: str = "symmetry",
                       rationale: str = "") -> dict[str, Any]:  # fmt: skip
        from puzzle.commands import resolve
        from puzzle.model.schema import Component
        from puzzle.symmetry import plane_ops

        if kind not in ("symmetry", "antisymmetry"):
            raise PuzzleError(f"kind is symmetry or antisymmetry, not {kind!r}", code="invalid")
        s = session()
        comp = resolve(s.project, component, Component)
        ops = plane_ops(comp, kind, selection)
        if not ops:
            raise PuzzleError(f"{comp.name} has no physics that takes a {kind} plane",
                              code="invalid")  # fmt: skip
        command = {"command": "edit", "object": str(comp.id),
                   "ops": [op.model_dump() for op in ops]}  # fmt: skip
        return s.submit(command, actor, rationale=rationale).to_dict()

    @tool(
        description="Make a component one sector or slice of a repeating whole (ADR 0081): "
        "`kind` 'cyclic' (the `target` face is the `source` face turned 360/`sectors` "
        "degrees about `axis` through `origin`, right-handed; static, transient and heat "
        "studies on CalculiX, solid mechanics on Code_Aster) or 'periodic' (the target is "
        "the source moved by `translation`, metres). Selections 'Part/Name'; each node on "
        "the target moves as its twin on the source. Loads are the sector's own. "
        "`kind` 'none' removes both. One edit; at 'propose' a proposal."
    )
    @guarded
    def symmetry_repeat(component: str, kind: str, source: str = "", target: str = "",
                        sectors: int = 0, axis: list[float] | None = None,
                        origin: list[float] | None = None,
                        translation: list[float] | None = None,
                        rationale: str = "") -> dict[str, Any]:  # fmt: skip
        from puzzle.commands import resolve
        from puzzle.model.schema import Component, CyclicSymmetry, LinearPeriodicity

        s = session()
        comp = resolve(s.project, component, Component)
        if kind == "none":
            ops = [{"op": "set", "path": k, "value": None} for k in ("cyclic", "periodic")]
        elif kind == "cyclic":
            value = CyclicSymmetry(source=source, target=target, sectors=sectors,
                                   axis=tuple(axis or (0, 0, 1)),
                                   origin=tuple(origin or (0, 0, 0)))  # fmt: skip
            ops = [{"op": "set", "path": "cyclic", "value": value.model_dump(mode="json")}]
        elif kind == "periodic":
            value = LinearPeriodicity(source=source, target=target,
                                      translation=tuple(translation or ()))  # fmt: skip
            ops = [{"op": "set", "path": "periodic", "value": value.model_dump(mode="json")}]
        else:
            raise PuzzleError(f"kind is cyclic, periodic or none, not {kind!r}", code="invalid")
        command = {"command": "edit", "object": str(comp.id), "ops": ops}
        return s.submit(command, actor, rationale=rationale).to_dict()

    @tool(
        description="A study's weak springs (ADR 0080): which bodies get them (held only by "
        "frictionless or frictional contact; with weak_springs 'always' also bodies held by "
        "nothing), the study's mode, factor and limit, and, for its latest stored run, the "
        "force they carried per step and any weak_springs_carry warning (the body never "
        "seated). Change the mode with an edit of the study's weak_springs (auto, always, "
        "never), weak_spring_factor or weak_spring_limit."
    )
    @guarded
    def weak_springs(study: str) -> dict[str, Any]:
        from puzzle import weak_springs as ws
        from puzzle.commands import resolve
        from puzzle.model.schema import Study
        from puzzle.project.layout import RESULTS_DIR
        from puzzle.results.store import ResultStore

        s = session()
        target = resolve(s.project, study, Study)
        comp = s.project.objects.get(target.component)
        out: dict[str, Any] = {
            "mode": target.weak_springs,
            "factor": target.weak_spring_factor,
            "limit": target.weak_spring_limit,
            "bodies": []
            if target.weak_springs == "never"
            else ws.bodies(s.project, comp, target.weak_springs),
        }
        store = ResultStore(root / RESULTS_DIR)
        try:
            runs = [r for r in store.runs() if r.study_id == str(target.id)]
        except Exception:  # noqa: BLE001 - no results yet
            runs = []
        if runs:
            latest = max(runs, key=lambda r: r.recorded_at)
            results = store.read_results(latest.run_id)
            out["latest_run"] = {
                "run": latest.run_id,
                "carried_N": [
                    {
                        "step": step.label(),
                        "force": float(
                            abs(step.fields["weak_spring_force"].values.sum(axis=0)).max()
                        ),
                    }
                    for step in results.steps
                    if "weak_spring_force" in step.fields
                ],
            }
        return out

    @tool(
        description="A frame study's load combinations (ADR 0079 step 2; PyNite solves "
        "them, one result step each, named): `method` 'LRFD' or 'ASD' generates ASCE 7-16's "
        "(2.3.1, 2.4.1) from the load cases its component's loads use (each load's `case`: "
        "D, L, Lr, S, R, W, E; wind and seismic both ways); or `combinations` = [{name, "
        "factors: {case: factor}}] sets your own; neither lists the cases and the current "
        "ones. Second order (P-Delta) and AISC's Direct Analysis Method are the solve step's "
        "settings second_order and direct_analysis (notional_directions, default 'x,y'). "
        "One edit; at 'propose' a proposal."
    )
    @guarded
    def load_combinations(study: str, method: str | None = None,
                          combinations: list[dict[str, Any]] | None = None,
                          rationale: str = "") -> dict[str, Any]:  # fmt: skip
        from puzzle.commands import resolve
        from puzzle.frames.combinations import asce7, cases_of, combination_ops
        from puzzle.model.schema import Study

        s = session()
        target = resolve(s.project, study, Study)
        comp = s.project.objects.get(target.component)
        cases = cases_of(comp)
        if method is None and combinations is None:
            return {"cases": cases, "combinations": [c.model_dump() for c in target.combinations]}
        try:
            combos = (asce7(cases, method) if method is not None else
                      [(c["name"], c["factors"]) for c in combinations or []])  # fmt: skip
        except (ValueError, KeyError) as exc:
            raise PuzzleError(str(exc), code="invalid") from None
        ops = combination_ops(target, combos)
        command = {"command": "edit", "object": str(target.id),
                   "ops": [op.model_dump() for op in ops]}  # fmt: skip
        out = s.submit(command, actor, rationale=rationale).to_dict()
        return out | {"combinations": [{"name": n, "factors": f} for n, f in combos]}

    @tool(
        description="The element library (ADR 0082): every solver's elements with their "
        "family, cells, formulation (full or reduced integration, incompatible modes, "
        "mixed u-p, thick or thin shell, Euler-Bernoulli or Timoshenko...), the studies "
        "they support, what each is for and not for, whether Puzzle writes it yet, and the "
        "manual's section. Filter by `solver`, `family` (solid_3d, solid_2d, shell, beam, "
        "truss, discrete, thermal, electromagnetic, fluid), `study` (static, modal, "
        "buckling, transient, harmonic, contact, plasticity, large_deflection, heat, "
        "electrostatics, magnetics, flow) or `written`. Choosing an element per body is "
        "not built yet."
    )
    @guarded
    def elements_list(solver: str | None = None, family: str | None = None,
                      study: str | None = None,
                      written: bool | None = None) -> dict[str, Any]:  # fmt: skip
        from dataclasses import asdict

        from puzzle import elements

        rows = elements.query(solver, family, study, written)
        return {"elements": [{"solver": n, **asdict(e)} for n, e in rows]}

    @tool(
        description="Choose the element a body is written with (ADR 0082 step 3): "
        "`selection` a body selection ('Part/Body') or part, `formulation` in neutral "
        "words (incompatible_modes: linear bricks bending without locking, CalculiX "
        "C3D8I; mixed_up: nearly incompressible, Code_Aster 3D_INCO_UPG; euler_bernoulli: "
        "slender beams, Code_Aster POU_D_E; lumped: heat capacity without undershoot, "
        "Code_Aster 3D_DIAG; full, timoshenko, thin_shell: the defaults), `solver_types` "
        "an expert's own {solver: type}. `formulation` 'none' removes the body's choice. "
        "Each solver maps it from the element library (elements_list) or refuses it, "
        "naming what it has. One edit; at 'propose' a proposal."
    )
    @guarded
    def element_choose(component: str, selection: str, formulation: str,
                       solver_types: dict[str, str] | None = None,
                       rationale: str = "") -> dict[str, Any]:  # fmt: skip
        from puzzle.commands import resolve
        from puzzle.model.schema import Component, ElementChoice

        s = session()
        comp = resolve(s.project, component, Component)
        kept = [c.model_dump(mode="json") for c in comp.elements if c.selection != selection]
        if formulation != "none":
            choice = ElementChoice(selection=selection, formulation=formulation,
                                   solver_types=solver_types or {})  # fmt: skip
            kept.append(choice.model_dump(mode="json"))
        command = {"command": "edit", "object": str(comp.id),
                   "ops": [{"op": "set", "path": "elements", "value": kept}]}  # fmt: skip
        return s.submit(command, actor, rationale=rationale).to_dict()

    @tool(
        description="What the project needs next (ADR 0040, the Advisor): the workflow's "
        "steps for a component and study (the first of each if not named) as done, needed "
        "or optional, each with its reason, and `next`, the first needed one. The same "
        "advice the GUI's Advisor shows."
    )
    @guarded
    def next_steps(component: str | None = None, study: str | None = None) -> dict[str, Any]:
        from puzzle.advisor import advise, next_step
        from puzzle.commands import resolve
        from puzzle.model.schema import Component, Study
        from puzzle.project.layout import RESULTS_DIR
        from puzzle.results.store import ResultStore

        s = session()
        comp = resolve(s.project, component, Component) if component else None
        chosen = resolve(s.project, study, Study) if study else None
        try:
            runs = ResultStore(root / RESULTS_DIR).runs()
        except PuzzleError:
            runs = []
        advice = advise(s.project, runs, comp, chosen, root=root)
        first = next_step(advice)
        return {
            "next": first.as_dict() if first else None,
            "steps": [a.as_dict() for a in advice],
        }

    @tool(
        description="An optimisation's points (ADR 0063), from the stored runs, solving "
        "nothing: each point's variables (SI and as labelled), its runs, objective, "
        "constraints, feasible or why not; the best feasible point, the suggestion to "
        "apply it, each variable's sensitivities (correlation with each output), how an "
        "optimiser `ended`, the `pareto` front's point indexes, a response surface's "
        "`surface_fit` (leave-one-out error per output, a share of its range), and robust "
        "design's `robust` (the design chosen; each output's mean, sd and ±3σ range over "
        "the scatter, SI; each constraint's probability of holding; the surface's miss at "
        "each checked point)."
    )
    @guarded
    def optimisation_points(name: str) -> dict[str, Any]:
        return _optimisation_points(session(), name)

    def _optimisation_points(s: Session, name: str) -> dict[str, Any]:
        from puzzle import optimise
        from puzzle.commands import resolve
        from puzzle.model.schema import Optimisation
        from puzzle.project.layout import RESULTS_DIR
        from puzzle.results.store import ResultStore

        opt = resolve(s.project, name, Optimisation)
        pts = optimise.points(s.project, ResultStore(root / RESULTS_DIR), opt)
        top = optimise.best(opt, pts)
        return {"points": [p.to_dict() for p in pts], "ended": pts.ended,
                "best": top.to_dict() if top else None,
                "suggestion": optimise.suggestion(s.project, opt, pts),
                "sensitivities": optimise.sensitivities(opt, pts),
                "pareto": [p.index for p in optimise.pareto(opt, pts)]
                if len(optimise.output_keys(opt)) > 1 else [],
                **({"surface_fit": optimise.fit_quality(opt, pts)}
                   if opt.method == "response_surface" else {}),
                **({"robust": optimise.robust_summary(s.project, opt, pts)}
                   if opt.method == "robust" else {})}  # fmt: skip

    @tool(
        description="Adaptive refinement (ADR 0066 phase 3): solve, estimate the error, "
        "mesh again with each element sized for `target` (0.02 = 2 %), pass after pass, "
        "until the energy-norm error (or, with the study's adapt.criterion 'quantity', "
        "its quantities' change) is at the target or `passes` ran. Settings default to "
        "the study's `adapt`. `method` 'anisotropic' remeshes with MMG along the field's "
        "Hessian (thin layers: heat, flow); 'isotropic' with the recipe's mesher. Each "
        "pass is a stored run, reused next time. Solving needs autonomy 'act'; `solve` "
        "false only reads the passes run."
    )
    @guarded
    def mesh_adapt(study: str, target: float | None = None, passes: int | None = None,
                   method: str | None = None, solve: bool = False) -> dict[str, Any]:  # fmt: skip
        from puzzle import adapt
        from puzzle.commands import resolve
        from puzzle.model.schema import Adapt, Study
        from puzzle.project.layout import RESULTS_DIR, WORK_DIR
        from puzzle.results.store import ResultStore

        s = session()
        if solve and s.project.agent.level not in ("act", "act_in_budget"):
            raise PuzzleError("adaptive refinement solves: it needs autonomy 'act'",
                              code="not_allowed")  # fmt: skip
        st = resolve(s.project, study, Study)
        a = st.adapt or Adapt()
        changes = {k: v for k, v in (("target", target), ("passes", passes),
                                     ("method", method)) if v}  # fmt: skip
        a = Adapt.model_validate({**a.model_dump(), **changes})
        out = adapt.run(s.project, st, root, root / WORK_DIR / "adapt" / str(st.id),
                        store=ResultStore(root / RESULTS_DIR), settings=a,
                        read_only=not solve)  # fmt: skip
        return out.to_dict()

    @tool(
        description="Error estimate (ADR 0066 phase 2) of a study's newest run (or a sweep "
        "point's, `point` as its title), from the stored field: displacement gives the "
        "Zienkiewicz–Zhu energy-norm error, temperature the same on the heat flux, "
        "`field` any nodal field's gradient jumps (relative only). Returns the relative "
        "error, how many elements are too big for `target` (0.02 = 2 %), the hot spots "
        "(element, error, centre in m, selections touched) and notes. Solves nothing."
    )
    @guarded
    def error_estimate(study: str, field: str | None = None, target: float = 0.02,
                       point: str | None = None) -> dict[str, Any]:  # fmt: skip
        from puzzle.commands import resolve
        from puzzle.model.schema import Study
        from puzzle.post import estimates
        from puzzle.post.derived import run_for
        from puzzle.project.layout import RESULTS_DIR
        from puzzle.results.store import ResultStore

        s = session()
        store = ResultStore(root / RESULTS_DIR)
        st = resolve(s.project, study, Study)
        record = run_for(s.project, store, st.id, point)
        est = estimates.of_run(s.project, store, record.run_id, field_name=field)
        mesh = store.read_mesh(record.mesh_id)
        return {
            "run": record.run_id,
            "words": estimates.words(mesh, est, target),
            **estimates.summary(mesh, est, target),
        }

    @tool(
        description="Mesh convergence (ADR 0066): the study (or a mesh study, by name) on "
        "`levels` meshes, each one's sizes divided by `ratio`, its quantities judged by "
        "Roache's grid convergence index: observed order, extrapolated value, GCI against "
        "`target` (0.02 = 2 %). `solve` false (the default) only reads the meshes already "
        "run; solving needs autonomy 'act'. Each level is a stored run, reused next time."
    )
    @guarded
    def mesh_convergence(
        study: str,
        levels: int | None = None,
        ratio: float | None = None,
        target: float | None = None,
        solve: bool = False,
    ) -> dict[str, Any]:
        from puzzle import convergence as conv
        from puzzle.project.layout import RESULTS_DIR, WORK_DIR
        from puzzle.results.store import ResultStore

        s = session()
        if solve and s.project.agent.level not in ("act", "act_in_budget"):
            raise PuzzleError("solving a convergence check needs autonomy 'act'",
                              code="not_allowed")  # fmt: skip
        st, c = conv.settings(s.project, study)
        goal = target or c.target
        lv = conv.run(s.project, st, root, root / WORK_DIR / "convergence" / str(st.id),
                      store=ResultStore(root / RESULTS_DIR), count=levels or c.levels,
                      ratio=ratio or c.ratio, outputs=c.quantities,
                      read_only=not solve)  # fmt: skip
        return conv.report(lv, conv.verdicts(lv, goal), goal)

    @tool(
        description="A magnetic study's skin-depth check (ADR 0047): each conductor's "
        "skin depth δ at the study's highest frequency, the element size its mesh recipe "
        "asks for, whether that is coarser than δ/2, and the refine-near controls to append "
        "to the recipe's `controls` (one Edit) when it is."
    )
    @guarded
    def magnetic_check(study: str) -> dict[str, Any]:
        from puzzle import magnetics
        from puzzle.adapters.eddy import skin_depth
        from puzzle.model.schema import Component, MeshRecipe, Study

        s = session()
        st = s.project.get(study, Study)
        comp = s.project.get(st.component, Component)
        recipe = s.project.get(comp.mesh, MeshRecipe)
        hz = magnetics._frequency(st)
        rows = []
        for body, mu, sigma in magnetics.conductors(s.project, comp):
            delta = skin_depth(hz, mu, sigma) if hz else None
            size = magnetics.recipe_size(recipe, body)
            rows.append({"body": body, "skin_depth_m": delta, "element_size_m": size,
                         "too_coarse": bool(delta and size and size > delta / 2)})  # fmt: skip
        return {
            "frequency_hz": hz,
            "recipe": recipe.name,
            "conductors": rows,
            "controls": [
                c.model_dump(mode="json") for c in magnetics.skin_refinement(s.project, st)
            ],
            "warnings": [w.message for w in magnetics.warnings(s.project) if w.object == st.name],
        }

    @tool(
        description="Run an optimisation's points (ADR 0063; needs autonomy 'act', as "
        "runs do; each point is a stored run, a point run before is reused, so a stopped "
        "optimiser resumes on its path); `budget` solves at most that many new points this "
        "time. Returns the points as optimisation_points."
    )
    @guarded
    def optimisation_run(name: str, budget: int | None = None) -> dict[str, Any]:
        from puzzle import optimise
        from puzzle.commands import resolve
        from puzzle.model.schema import Optimisation
        from puzzle.project.layout import RESULTS_DIR, WORK_DIR
        from puzzle.results.store import ResultStore

        s = session()
        if s.project.agent.level not in ("act", "act_in_budget"):
            raise PuzzleError("running an optimisation needs autonomy 'act'",
                              code="not_allowed")  # fmt: skip
        opt = resolve(s.project, name, Optimisation)
        store = ResultStore(root / RESULTS_DIR)
        optimise.run(s.project, opt, root, root / WORK_DIR / "optimise" / str(opt.id),
                     store=store, budget=budget)  # fmt: skip
        return _optimisation_points(s, name)

    @tool(
        description="Put an optimisation's best feasible point (or point `index`) into "
        "the model's parameters (ADR 0063): one batch, so one undo; a version is saved "
        "first. At 'propose' it becomes a proposal for the user to accept."
    )
    @guarded
    def optimisation_apply(name: str, index: int | None = None,
                           rationale: str = "") -> dict[str, Any]:  # fmt: skip
        from puzzle import optimise
        from puzzle.commands import resolve
        from puzzle.model.schema import Optimisation
        from puzzle.project.layout import RESULTS_DIR
        from puzzle.results.store import ResultStore

        s = session()
        opt = resolve(s.project, name, Optimisation)
        pts = optimise.points(s.project, ResultStore(root / RESULTS_DIR), opt)
        pick = optimise.best(opt, pts) if index is None else next(
            (p for p in pts if p.index == index), None)  # fmt: skip
        if pick is None or not pick.run:
            raise PuzzleError("no such point run yet", code="not_found")
        commands = optimise.apply_commands(s.project, opt, pick)
        if s.project.agent.level in ("act", "act_in_budget"):
            s.save_version(actor, label=f"Before {opt.name}'s point {pick.index}")
        return s.submit_batch(
            commands, actor, rationale=rationale or f"{opt.name}: point {pick.index}, {pick.title}"
        ).to_dict()

    @tool(
        description="An operator's value (ADR 0062): its outputs in SI (a number with its "
        "unit and where a maximum is, a verdict with its margin, a table's rows), what the "
        "schematic node shows, and how it is wired. Without a name: every operator."
    )
    @guarded
    def operator_value(name: str | None = None) -> dict[str, Any]:
        return {"operators": _operator_values(session(), name)}

    @tool(
        description="Write an Export or Report operator's file now (Export now): an export's "
        "value as CSV, Parquet, Excel, a result format or a PNG; a report's checks, numbers, "
        "curves and tables as one HTML file. Always inside the project folder. Returns the "
        "files written, relative to the project."
    )
    @guarded
    def operator_export(name: str) -> dict[str, Any]:
        from puzzle.commands import resolve
        from puzzle.model.schema import Operator
        from puzzle.operators import evaluate as ev
        from puzzle.project.layout import RESULTS_DIR
        from puzzle.results.store import ResultStore

        s = session()
        op = resolve(s.project, name, Operator)
        if op.type not in ("export", "report"):
            raise PuzzleError(f"'{op.name}' is a {op.type}, not an Export or Report",
                              code="invalid")  # fmt: skip
        ctx = ev.Context(s.project, ResultStore(root / RESULTS_DIR))
        written = ev.export(ctx, op, root)
        return {"written": [str(Path(p).relative_to(root)) for p in written]}

    def _operator_values(s: Session, name: str | None) -> list[dict[str, Any]]:
        from puzzle.commands import resolve
        from puzzle.model.schema import Operator
        from puzzle.operators import describe
        from puzzle.operators import evaluate as ev
        from puzzle.project.layout import RESULTS_DIR
        from puzzle.results.store import ResultStore

        ops = [resolve(s.project, name, Operator)] if name else s.project.of_type(Operator)
        ctx = ev.Context(s.project, ResultStore(root / RESULTS_DIR))
        return [ev.summary(ctx, op) | {"wiring": describe(s.project, op)} for op in ops]

    @tool(
        description="Add an operator (ADR 0062): `type` reduce, filter, check, panel, plot, "
        "export (after solving) or number, slider, series, range, expression, table, list, "
        "pairs (data, before solving: Numbers, Sliders and Expressions then act as "
        "parameters by their name); `inputs` by port, each {source: a study, derived "
        "result, result tool, parameter set or operator by name, port: its output} or "
        "{value: '200 MPa'}; `settings` its own fields (quantity, selection, reduction, "
        "step, keep, compare, against, file, format, low, high, increment, expression, "
        "text, operation, column, combination). A study's ports: result, or a field "
        "(stress). Read the skill schematic-operators first, and operator_help for what a "
        "type does and its ports. One undoable command; returns its value."
    )
    @guarded
    def operator_add(
        type: str,  # noqa: A002 - the operator's type, as the schema names it
        name: str | None = None,
        inputs: dict[str, Any] | None = None,
        settings: dict[str, Any] | None = None,
        rationale: str = "",
    ) -> dict[str, Any]:
        from puzzle.operators import TYPES, default_name, inputs_from

        s = session()
        if type not in TYPES:
            raise PuzzleError(f"no operator type '{type}'", code="invalid",
                              details={"types": sorted(TYPES)})  # fmt: skip
        settings = _named_settings(s.project, type, dict(settings or {}))
        data = {"name": name or default_name(s.project, type), "type": type,
                "inputs": inputs_from(s.project, inputs or {}), **settings}  # fmt: skip
        out = s.submit({"command": "create", "kind": "operator", "data": data}, actor,
                       rationale=rationale).to_dict()  # fmt: skip
        if out.get("status") == "applied":
            out["value"] = _operator_values(s, data["name"])[0]
        return out

    @tool(
        description="Wire one object into another (ADR 0071 decision 8), as drawing a wire "
        "in the schematic does: a material onto a component (which part), a mesh recipe "
        "onto a component, a component into a study, a coordinate system or amplitude "
        "onto a component's load, a study onto a result tool or derived result, a "
        "construction plane onto a result tool, a bolt type onto a bolt; with `feature` (a "
        "component's load or support as the schematic names it, 'Pressure on Bracket/Inner'), "
        "onto that load: its coordinate system, amplitude, or (a geometry) the selection it "
        "acts on. Without `which`, "
        "lists what the wire could mean (`choices`, each with its index and label) and "
        "applies it when there is exactly one; with `which` (an index or a label), applies "
        "that one. A wire that fits nowhere says what the target takes. One undoable "
        "command."
    )
    @guarded
    def link(source: str, target: str, which: str | int | None = None,
             feature: str | None = None, rationale: str = "") -> dict[str, Any]:  # fmt: skip
        from puzzle.commands import resolve
        from puzzle.schematic import links

        s = session()
        src, dst = resolve(s.project, source), resolve(s.project, target)
        within = _feature_path(dst, feature)
        found = links.choices(s.project, src, dst, within)
        if not found:
            raise PuzzleError(links.why_not(s.project, src, dst), code="invalid")
        listed = [{"index": i, "label": c.label} for i, c in enumerate(found)]
        if which is None and len(found) > 1:
            return {"status": "choose", "choices": listed}
        pick = _pick(found, which)
        if pick.op == "add_part":
            name = next((n for n in (src.name, f"{src.name} 2") if all(
                p.name != n for p in dst.parts)), f"{src.name} {len(dst.parts) + 1}")  # fmt: skip
            cmd = {"command": "edit", "object": str(dst.id), "ops": [{"op": "append",
                   "path": "parts", "value": {"name": name, "geometry": str(src.id)}}]}  # fmt: skip
            return s.submit(cmd, actor, rationale=rationale).to_dict()
        cmds = links.commands(pick, src, dst)
        return s.submit(cmds[0], actor, rationale=rationale or pick.label).to_dict()

    @tool(
        description="Remove a reference between two objects (ADR 0071 decision 8), as "
        "removing a wire in the schematic does. Without `which`, lists what joins them; "
        "with `which` (an index or label), clears it. A reference an object can't be "
        "without (a study's component) is refused: link another to replace it."
    )
    @guarded
    def unlink(source: str, target: str, which: str | int | None = None,
               feature: str | None = None, rationale: str = "") -> dict[str, Any]:  # fmt: skip
        from puzzle.commands import resolve
        from puzzle.schematic import links

        s = session()
        src, dst = resolve(s.project, source), resolve(s.project, target)
        made = links.existing(s.project, src.id, dst, _feature_path(dst, feature))
        if not made:
            raise PuzzleError(f"{dst.name} doesn't use {src.name}", code="not_found")
        if which is None and len(made) > 1:
            return {
                "status": "choose",
                "choices": [{"index": i, "label": c.label} for i, c in enumerate(made)],
            }
        pick = _pick(made, which)
        cmds = links.clear_commands(pick, dst)
        if not cmds:
            raise PuzzleError(f"{dst.name} needs its {pick.link.name.lower()}: link another "
                              "one in to replace it", code="invalid")  # fmt: skip
        return s.submit(cmds[0], actor, rationale=rationale or f"remove {pick.label}").to_dict()

    def _feature_path(component, feature: str | None) -> str:
        """A load or support of `component` by its name as the schematic shows it
        ("Pressure on Bracket/Inner") or its path ("physics.0.features.1")."""
        if not feature:
            return ""
        if feature.startswith("physics."):
            return feature
        for i, ph in enumerate(getattr(component, "physics", [])):
            for j, f in enumerate(ph.features):
                shown = f"{f.type.replace('_', ' ').capitalize()} on {f.selection}"
                if feature.lower() in (shown.lower(), f.selection.lower()):
                    return f"physics.{i}.features.{j}"
        raise PuzzleError(f"{component.name} has no load or support '{feature}'",
                          code="not_found")  # fmt: skip

    def _pick(found, which):
        if which is None:
            return found[0]
        if isinstance(which, int) or str(which).isdigit():
            i = int(which)
            if not 0 <= i < len(found):
                raise PuzzleError(f"no choice {i}", code="invalid")
            return found[i]
        match = [c for c in found if c.label.lower() == str(which).lower()]
        if not match:
            raise PuzzleError(f"no choice '{which}'", code="invalid",
                              details={"choices": [c.label for c in found]})  # fmt: skip
        return match[0]

    @tool(
        description="What operators do (ADR 0071), before using one: with no arguments "
        "every operator kind (its type for operator_add, its name, group and purpose); "
        "with `type` (or an operator's name as shown) its full card: purpose, an example, "
        "each input (what it takes, needed or not, whether a value can be typed) and "
        "output; with `search`, the kinds whose name, purpose or other names match "
        "(e.g. 'maximum', 'divide', 'pivot')."
    )
    @guarded
    def operator_help(type: str | None = None, search: str | None = None) -> dict[str, Any]:  # noqa: A002
        from puzzle.operators import TYPES, operator_doc

        if type:
            key = type if type in TYPES else next(
                (k for k, t in TYPES.items() if t.label.lower() == type.lower()), None)  # fmt: skip
            if key is None:
                raise PuzzleError(f"no operator type '{type}'", code="not_found",
                                  details={"types": sorted(TYPES)})  # fmt: skip
            return operator_doc(key)
        words = (search or "").lower().split()
        found = []
        for key, t in TYPES.items():
            hay = " ".join([key, t.label, t.group, t.help, *t.aliases]).lower()
            if all(w in hay for w in words):
                found.append({"type": key, "name": t.label, "group": t.group,
                              "purpose": t.help})  # fmt: skip
        return {"operators": found}

    def _named_settings(project, type_: str, settings: dict) -> dict:
        """Names where the schema keeps ids: a Model value's or Measure's `target` by
        name, a Model value's `property` by its label ("Yield strength") or path."""
        from puzzle.commands import resolve
        from puzzle.operators.model_values import readable

        target = settings.get("target")
        if isinstance(target, str) and target:
            try:
                UUID(target)
            except ValueError:
                settings["target"] = str(resolve(project, target).id)
        if type_ == "model_value" and settings.get("property") and settings.get("target"):
            obj = project.objects.get(UUID(settings["target"]))
            wanted = settings["property"]
            found = readable(project, obj)
            match = next((r.path for r in found if wanted in (r.path, r.label)), None)
            match = match or next((r.path for r in found if r.label.lower() == wanted.lower()),
                                  None)  # fmt: skip
            if match is None:
                raise PuzzleError(f"{obj.name} holds no value '{wanted}'", code="not_found",
                                  details={"readable": [r.label for r in found]})  # fmt: skip
            settings["property"] = match
        return settings

    @tool(
        description="What a Model value can read from an object (ADR 0071 step 3): every "
        "value it holds, each with its label (pass it as the operator's `property`), path, "
        "quantity kind and whether it's a table against temperature. Without `object`, "
        "the objects that hold readable values."
    )
    @guarded
    def model_values(object: str | None = None) -> dict[str, Any]:  # noqa: A002
        from puzzle.commands import resolve
        from puzzle.operators.model_values import readable

        s = session()
        if object is None:
            found = [o for o in s.project.objects.values() if readable(s.project, o)]
            return {"objects": [{"name": o.name, "kind": o.kind} for o in found]}
        obj = resolve(s.project, object)
        return {"object": obj.name, "values": [
            {"label": r.label, "path": r.path, "kind": r.kind, "table": r.table}
            for r in readable(s.project, obj)]}  # fmt: skip

    @tool(
        description="The project schematic (ADR 0058): every file, resource, component, "
        "study and result as a node (column, state and its reason, ports), and the wires "
        "between them: `data` wires carry values (transfers, imported fields, runs into "
        "derived results; labelled with what flows), `use` wires say what is built from "
        "what (geometry into components, components into studies). Give `node` (a name) "
        "to get what it is built from (`upstream`) and what would go out of date if it "
        "changed (`downstream`). Use it to explain how a project fits together."
    )
    @guarded
    def schematic(node: str | None = None) -> dict[str, Any]:
        from puzzle import schematic as sch
        from puzzle.project.layout import RESULTS_DIR
        from puzzle.results.store import ResultStore

        s = session()
        try:
            runs = ResultStore(root / RESULTS_DIR).runs()
        except PuzzleError:
            runs = []
        built = sch.build(s.project, runs, root=root)
        names = {n.id: n.label for n in built.nodes.values()}
        out: dict[str, Any] = {
            "nodes": [
                {
                    "name": n.label,
                    "kind": sch.KIND_WORDS.get(n.kind, n.kind),
                    "column": sch.COLUMNS[n.column],
                    "state": n.state,
                    **({"reason": n.reason} if n.reason else {}),
                    **({"details": n.subtitle} if n.subtitle else {}),
                    **({"chips": n.chips} if n.chips else {}),
                    **({"badge": n.badge} if n.badge else {}),
                    "inputs": [p.label for p in n.inputs()],
                    "outputs": [p.label for p in n.outputs()],
                }
                for n in built.nodes.values()
            ],  # fmt: skip
            "wires": [
                {
                    "kind": w.kind,
                    "from": names[w.source],
                    "to": names[w.target],
                    **({"carries": w.label} if w.label else {}),
                }
                for w in built.wires
            ],  # fmt: skip
            "flow": built.flow_lines(),
        }
        if node:
            found = [n for n in built.nodes.values() if n.label == node]
            if not found:
                raise PuzzleError(f"no node named '{node}'", code="not_found",
                                  details={"nodes": sorted(names.values())})  # fmt: skip
            n = found[0]
            out["node"] = {
                "name": n.label,
                "upstream": sorted(names[k] for k in built.upstream(n.id)),
                "downstream": sorted(names[k] for k in built.downstream(n.id)),
            }
        return out

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

    @tool(
        description="Each bolt's loads in a study's latest run (ADR 0050), worst first: "
        "axial (N, + tension), shear (N), torsion and bending at each end (N m), the "
        "stress |N|/As + M/Z (Pa) and its share of the grade's proof and yield strengths."
    )
    @guarded
    def bolt_results(study: str, step: int | None = None) -> dict[str, Any]:
        from puzzle.bolts import bolt_results as found
        from puzzle.project.layout import RESULTS_DIR
        from puzzle.results.store import ResultStore

        record, _, _ = latest(study)
        rows = found(session().project, ResultStore(root / RESULTS_DIR), record.run_id, step)
        return {"run_id": record.run_id, "bolts": [
            {"name": b.name, "axial_N": b.loads.axial[0], "shear_N": b.loads.shear[0],
             "torsion_Nm": b.loads.torsion[0], "bending_first_end_Nm": b.loads.bending[0],
             "bending_second_end_Nm": b.loads.bending[-1], "stress_Pa": b.stress,
             "of_proof": b.proof, "of_yield": b.yielding,
             "thread_loads_N": b.threads, "first_thread_share": b.first_thread,
             "modelled": b.modelled}
            for b in rows
        ]}  # fmt: skip

    @tool(
        description="Each frictionless or frictional contact's results in a study's latest "
        "run (ADR 0056): normal and frictional force (N), area in contact (m^2 and share of "
        "the source face), largest pressure (Pa), penetration, gap and sliding (m), and the "
        "share of the contact area sticking. `step`: a result step, 1-based among those "
        "with contact results (default the last). The fields contact_pressure, "
        "contact_shear, contact_gap, contact_sliding and contact_status (0 open, 1 sliding, "
        "2 sticking) on the source faces are read with the result_* tools."
    )
    @guarded
    def contact_results(study: str, step: int | None = None) -> dict[str, Any]:
        from puzzle.post.contact import run_contact_results
        from puzzle.project.layout import RESULTS_DIR
        from puzzle.results.store import ResultStore

        record, _, _ = latest(study)
        chosen, found, steps = run_contact_results(
            session().project, ResultStore(root / RESULTS_DIR), record.run_id, step
        )
        return {
            "run_id": record.run_id,
            "step": chosen.label(),
            "steps": len(steps),
            "contacts": [r.to_dict() for r in found],
        }

    @tool(
        description="Thread forms (ADR 0076) for `contact_thread`: each form's key, name, "
        "standard, flank angles (load, other; degrees from the radial plane), taper "
        "(diameter per length) and how its size is given; with `form` iso or un, its "
        "standard sizes by name (M16, M16x1.5, 1/2-13 UNC)."
    )
    @guarded
    def thread_forms(form: str | None = None) -> dict[str, Any]:
        from puzzle import thread_setup as ts

        out: dict[str, Any] = {"forms": ts.forms_info()}
        if form is not None:
            out["sizes"] = ts.sizes_for(form)
        return out

    @tool(
        description="Make a contact a thread (ADR 0076): the two smooth coaxial cylinders "
        "stay smooth and the contact acts along the flanks, giving each thread's load, "
        "the nut's wedge and the engagement's stiffness. `form` iso, un, acme, stub_acme, "
        "trapezoidal, buttress, buttress_din, square, npt, bspt or custom (`thread_forms`); "
        "`size` a standard size for iso/un (M16, M16x1.5, 1/2-13 UNC), else `major` and "
        '`pitch` as quantities ({"value": 20, "unit": "mm"}); custom needs '
        "`flank_angles` [load, other] degrees. `starts`, `hand` right/left, `bearing` auto "
        "(the pull decides the side)/load/other, `compliance` teeth/rigid or N/m^3, "
        "`clearance` (backlash), `engaged_length`, `cs` a coordinate system's name whose z "
        "is the axis. `type` changes the contact's type: bonded (ties the bearing flank), "
        "no_separation (both flanks), frictionless (unilateral: nonlinear). `remove` "
        "clears the thread. Returns the resolved numbers (m), notes, and `command`: pass "
        "it to submit. Nothing is changed here."
    )
    @guarded
    def contact_thread(
        component: str,
        contact: str,
        form: str = "iso",
        size: str | None = None,
        pitch: dict | None = None,
        major: dict | None = None,
        starts: int = 1,
        hand: str = "right",
        flank_angles: list[float] | None = None,
        bearing: str = "auto",
        compliance: str | float = "teeth",
        clearance: dict | None = None,
        engaged_length: dict | None = None,
        cs: str | None = None,
        type: str | None = None,
        remove: bool = False,
    ) -> dict[str, Any]:
        from puzzle import thread_setup as ts
        from puzzle.commands import resolve
        from puzzle.model.schema import Component, CoordinateSystem

        s = session()
        comp = resolve(s.project, component, Component)
        index = ts.contact_index(comp, contact)
        if remove:
            command = ts.thread_command(comp, contact, None)
            return {"command": command.model_dump(mode="json"), "notes": []}
        axes = str(resolve(s.project, cs, CoordinateSystem).id) if cs else None
        thread = ts.make_thread(
            form=form, size=size, pitch=pitch, major=major, starts=starts, hand=hand,
            flank_angles=tuple(flank_angles) if flank_angles else None, bearing=bearing,
            compliance=compliance, clearance=clearance, engaged_length=engaged_length,
            cs=axes,
        )  # fmt: skip
        kind = type or comp.contacts[index].type
        command = ts.thread_command(comp, contact, thread, type)
        return {
            "thread": ts.describe(thread),
            "type": kind,
            "written_by": ts.writers(s.project, comp, kind),
            "notes": ts.notes(s.project, comp, thread, kind),
            "command": command.model_dump(mode="json"),
        }

    @tool(
        description="Threaded contacts' results in a study's latest run (ADR 0076): per "
        "thread contact the axial load (N), each engaged thread's axial load and share "
        "(the first engaged, at the mouth, first), the wedge (total radial force, N: the "
        "nut's opening) and the peak flank pressure (Pa). `contact` one of them; `step` "
        "1-based among the steps with thread results (default the last). The nodal fields "
        "thread_force (normal, axial, radial) and thread_pressure are read with result_*."
    )
    @guarded
    def thread_results(study: str, contact: str | None = None,
                       step: int | None = None) -> dict[str, Any]:  # fmt: skip
        from puzzle import thread_setup as ts

        record, mesh, res = latest(study)
        return {"run_id": record.run_id,
                **ts.results(session().project, mesh, res, contact, step)}  # fmt: skip

    @tool(
        description="Plan a weld between two parts (ADR 0078): `part` and the `edges` it runs "
        "along, by name (an edge is named by its two faces, 'B/-x|B/-z'; selections_check "
        "lists them). Of each edge's faces the one lying on another part is the seat; the "
        "other carries the first leg, and the other part's touching face the second. `leg` "
        "a quantity (a fillet's leg, a groove's depth), `leg2` an unequal fillet's other "
        "leg; `type` fillet or groove (`groove` cjp or pjp); `pattern` continuous or "
        "intermittent (`segment`, `pitch`). A light weld (springs across the joint, line "
        "force at stations; CalculiX for now). Returns `batch` to pass to submit (one undo)."
    )
    @guarded
    def weld_add(component: str, part: str, edges: list[str], leg: dict, name: str = "Weld",
                 leg2: dict | None = None, type: str = "fillet", groove: str | None = None,
                 pattern: str = "continuous", segment: dict | None = None,
                 pitch: dict | None = None) -> dict[str, Any]:  # fmt: skip
        from puzzle import weld_generator
        from puzzle.commands import resolve
        from puzzle.model.schema import Component, Quantity

        s = session()
        comp = resolve(s.project, component, Component)
        planned = weld_generator.plan(s.project, comp, root, part, edges)
        q = lambda v: Quantity(**v) if v is not None else None  # noqa: E731
        batch = weld_generator.commands(
            s.project, comp, planned, name=name, leg=q(leg), leg2=q(leg2), type=type,
            groove=groove, pattern=pattern, segment=q(segment), pitch=q(pitch),
        )  # fmt: skip
        return {"joins": [planned.part, planned.other], "leg_faces": planned.leg_faces,
                "base_faces": planned.base_faces,
                "batch": [c.model_dump(mode="json") for c in batch]}  # fmt: skip

    @tool(
        description="Light welds' results in a study's latest run (ADR 0078): per weld its "
        "total force (N), largest line force (N/m) and where, mean line force, welded "
        "length, and each station's line force in the weld's frame (along, throat_normal, "
        "throat_shear, magnitude, x, y, z; twist N·m/m about the path), and `strength`: "
        "AISC 360-16 J2 at the governing station (filler and F_EXX, throat, design and "
        "allowable strength per length, LRFD and ASD utilisations for loads factored that "
        "way; `directional` takes Eq. J2-5's 1 + 0.5 sin^1.5 θ, allowed for linear welds "
        "loaded in plane). `weld` one of them; `step` 1-based among the steps with weld "
        "results (default the last)."
    )
    @guarded
    def weld_results(study: str, weld: str | None = None, step: int | None = None,
                     directional: bool = False) -> dict[str, Any]:  # fmt: skip
        from puzzle.commands import resolve
        from puzzle.model.schema import Component, Study
        from puzzle.post.weld_fields import results

        found = resolve(session().project, study, Study)
        record, mesh, res = latest(study)
        comp = session().project.get(found.component, Component)
        return {"run_id": record.run_id,
                **results(session().project, comp, mesh, res, weld, step, directional)}  # fmt: skip

    @tool(
        description="A solve's own logs, read whole from its run folder (solver.log, "
        "Code_Aster's .mess, CalculiX's .sta/.cvg, OpenFOAM's log.*): `problems` the "
        "error and warning lines by each solver's markers, `pattern` lines matching a "
        "regular expression (any case), with `context` lines around; else each file's last "
        "`tail` lines. `study` reads its newest attempt (failed or stopped too), `run` a "
        "stored run. Use run_progress first for a summary; this is for the solver's words."
    )
    @guarded
    def run_log(study: str | None = None, run: str | None = None, problems: bool = False,
                pattern: str | None = None, context: int = 0, tail: int = 40,
                limit: int = 200) -> dict[str, Any]:  # fmt: skip
        from puzzle import solver_logs

        if study is None and run is None:
            raise PuzzleError("give a study or a run", code="invalid")
        folder = solver_logs.find(root, study=study, run=run)
        found = solver_logs.lines(folder, pattern=pattern, problems=problems,
                                  context=context, tail=tail, limit=min(limit, 1000))  # fmt: skip
        return {
            "folder": str(folder.relative_to(root)),
            "files": [str(p.relative_to(folder)) for p in solver_logs.logs(folder)],
            "counts": solver_logs.counts(folder),
            "lines": [
                {
                    "file": x.file,
                    "line": x.number,
                    "text": x.text.rstrip()[:400],
                    **({"kind": x.kind} if x.kind else {}),
                }
                for x in found
            ],  # fmt: skip
        }

    @tool(
        description="How a study's last solve went or is going (ADR 0054): steps, increments, "
        "cut-backs, iterations, last residuals against their criteria, equations, memory, "
        "tracked probes' last values, the solver's warnings and errors in English, and "
        "diagnoses (what a failure means and what to try). Reads the newest attempt, so a "
        "failed, stopped or running solve is included; `run` picks a stored run instead."
    )
    @guarded
    def run_progress(study: str, run: str | None = None) -> dict[str, Any]:
        from puzzle import progress
        from puzzle.commands import resolve
        from puzzle.model.schema import Study
        from puzzle.project.layout import RESULTS_DIR
        from puzzle.results.store import ResultStore

        s = session()
        found = resolve(s.project, study, Study)
        store = ResultStore(root / RESULTS_DIR)
        if run is not None:
            hits = [r for r in store.runs() if r.run_id.startswith(run)]
            if len(hits) != 1:
                raise PuzzleError(f"run '{run}' matches {len(hits)} runs", code="not_found")
            events, source = store.read_progress(hits[0].run_id), f"stored run {hits[0].run_id}"
        else:
            events, source = progress.latest_attempt(root, found.name), "newest run folder"
            if not events:
                runs = [r for r in store.runs() if r.study_id == str(found.id)]
                if runs:
                    events = store.read_progress(runs[-1].run_id)
                    source = f"stored run {runs[-1].run_id}"
        if not events:
            raise PuzzleError(f"study '{found.name}' has no progress kept yet", code="not_found")
        st = progress.state(events)
        return {
            "study": found.name,
            "source": source,
            "status": "failed"
            if st.failed
            else ("finished" if st.finished else "running or stopped"),
            "now": st.line(),
            "fraction": st.fraction(),
            **progress.summary(events),
            "diagnoses": [{"code": d.code, "text": d.text} for d in progress.diagnose(events)],
        }

    @tool(
        description="Standard structural sections (ADR 0055): `standard` aisc or en; with no "
        "`family`, the families; with one, its designations (filtered by `contains`), each "
        "with depth and width (m), A (m^2), I strong and weak (m^4), J (m^4) and mass (kg/m)."
    )
    @guarded
    def sections_find(standard: str, family: str | None = None,
                      contains: str | None = None) -> dict[str, Any]:  # fmt: skip
        from puzzle import sections

        if standard not in sections.STANDARDS:
            raise PuzzleError(f"no section standard '{standard}': aisc or en", code="invalid")
        if family is None:
            return {"families": sections.families(standard)}
        out = []
        for d in sections.designations(standard, family):
            if contains and contains.lower() not in d.lower():
                continue
            sec = sections.get(standard, d)
            out.append({"designation": d, "depth_m": sec.depth, "width_m": sec.width,
                        "A_m2": sec.A, "I_strong_m4": sec.I1, "I_weak_m4": sec.I2,
                        "J_m4": sec.J, "mass_kg_m": sec.mass,
                        "beams_take_it": sec.shape in sections.SUPPORTED})  # fmt: skip
        return {"sections": out[:60], "more": max(0, len(out) - 60)}

    @tool(
        description="Standard bolt sizes (ADR 0050) for bolt types: `standard` iso_coarse, "
        "iso_fine (M3-M64), unc (to 4 in) or unf; `contains` filters the names. Each with "
        "diameter, pitch, stress area, the bearing diameter joint styles use, the ISO 273 "
        "medium clearance hole, and the `head` and `nut` style's bearing diameter and "
        "height from the standard part tables (head hex, hex_cap, heavy_hex, structural, "
        "socket, button, flat, low_head; nut hex, heavy_hex, thin). All m, m^2."
    )
    @guarded
    def bolt_sizes(standard: str = "iso_coarse", contains: str | None = None,
                   head: str = "hex", nut: str = "hex") -> dict[str, Any]:  # fmt: skip
        from puzzle import bolts, fasteners

        if standard not in bolts.STANDARDS or standard == "custom":
            raise PuzzleError(f"no bolt standard '{standard}': iso_coarse, iso_fine, unc or unf",
                              code="invalid")  # fmt: skip
        if head not in fasteners.HEADS or nut not in fasteners.NUTS:
            raise PuzzleError(f"head one of {', '.join(fasteners.HEADS)}; nut one of "
                              f"{', '.join(fasteners.NUTS)}", code="invalid")  # fmt: skip
        found = [s for s in bolts.sizes(standard)
                 if not contains or contains.lower() in s.name.lower()]  # fmt: skip
        return {"sizes": [fasteners.size_info(s, head, nut) for s in found]}

    @tool(
        description="Standard fastener tables (ADR 0050): screws, nuts and washers as ISO, "
        "DIN, ASME and ASTM tabulate them (from Lacewing Lab's tables). No `table`: each "
        "table's id, title, standard, kind and sizes. With `table` (e.g. iso.hexBolt, "
        "asme.hexNut, iso.washerNormal): its reference, notes, columns and rows (one `size` "
        "as the table names it: M10, 1/2, #10); lengths in m, threads per inch as counts."
    )
    @guarded
    def fastener_tables(table: str | None = None, size: str | None = None) -> dict[str, Any]:
        from puzzle import fasteners

        try:
            return fasteners.table_info(table, size)
        except KeyError as exc:
            raise PuzzleError(str(exc.args[0]), code="not_found") from None

    @tool(
        description="A single-bolt joint by VDI 2230-1:2015 (concentric): bolt and parts' "
        "resiliences (head 0.5 d hex / 0.4 d socket, engaged thread 0.5 d, nut 0.4 d or "
        "tapped 0.33 d, cone and sleeve), load factor Phi (n by `n`, Table 2), assembly "
        "preload FMzul (90 % of Rp0.2 at `mu_thread`), its tightening torque and nut factor "
        "K, embedding loss; with `axial_load` (N) the bolt's extra load, equivalent stress "
        "and fatigue safety; with `tightening_factor` (alpha_A, Table A8) the least preload "
        "and residual clamp load. `size` a bolt_sizes name; `grade` 8.8, 10.9, 12.9 or "
        "`rp02` (Pa); `clamp_length`, `shank_length`, `bearing`, `hole`, `outer_diameter`, "
        "`rz` in m; moduli in Pa; `head` hex or socket; `tapped` true for a cap screw in a "
        "tapped hole. Defaults are listed in `notes`. Nothing is changed."
    )
    @guarded
    def bolt_vdi2230(size: str, clamp_length: float, grade: str | None = "10.9",
                     head: str = "hex", tapped: bool = False, shank_length: float = 0.0,
                     e_bolt: float = 205e9, e_plate: float = 205e9,
                     e_nut: float | None = None, rp02: float | None = None,
                     bearing: float | None = None, hole: float | None = None,
                     outer_diameter: float | None = None, mu_thread: float = 0.12,
                     mu_head: float = 0.12, n: float = 1.0, axial_load: float | None = None,
                     rz: float = 16e-6, interfaces: int = 1,
                     tightening_factor: float | None = None) -> dict[str, Any]:  # fmt: skip
        from puzzle import vdi2230

        try:
            return vdi2230.check(
                size, clamp_length, grade, head, tapped, shank_length, e_bolt, e_plate, e_nut,
                rp02, bearing, hole, outer_diameter, mu_thread, mu_head, n, axial_load, rz,
                interfaces, tightening_factor,
            )  # fmt: skip
        except ValueError as exc:
            raise PuzzleError(str(exc), code="invalid") from None

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
