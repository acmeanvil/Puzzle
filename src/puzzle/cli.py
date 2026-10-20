"""The `puzzle` command line.

    puzzle studies PROJECT                       list the project's studies
    puzzle run PROJECT --study NAME              solve (or reuse an identical earlier run)
    puzzle runs PROJECT                          list stored runs
    puzzle show PROJECT --study NAME [options]   view a result, or save it with --screenshot
    puzzle export PROJECT --study NAME OUT.pvd   write .vtu/.pvd files for ParaView
    puzzle outline PROJECT                       the model tree as text
    puzzle history PROJECT                       what changed, by whom
    puzzle undo PROJECT [--seq N]                undo the latest change (or entry N)
    puzzle proposals PROJECT                     agents' open proposals
    puzzle accept|reject PROJECT ID              decide on a proposal
    puzzle autonomy PROJECT [--level ...]        what agents may do here
    puzzle schema {objects,commands}             JSON schemas for clients and agents
    puzzle mcp PROJECT                           serve the project to agents over MCP (stdio)

PROJECT is a project directory (one TOML file per object). Results go to
PROJECT/results, solver working files to PROJECT/.puzzle/runs. Every change goes
through the command layer (puzzle.commands) and is logged in PROJECT/history.jsonl.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from puzzle.errors import PuzzleError
from puzzle.model.schema import Project, Study
from puzzle.project.layout import RESULTS_DIR
from puzzle.project.store import load_project
from puzzle.results.store import ResultStore, RunRecord


class CliError(Exception):
    pass


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return args.handler(args)
    except CliError as exc:
        print(f"puzzle: {exc}", file=sys.stderr)
        return 1
    except PuzzleError as exc:
        print(f"puzzle: {exc.message} [{exc.code}]", file=sys.stderr)
        for key, value in exc.details.items():
            if key != "log_tail":
                print(f"  {key}: {value}", file=sys.stderr)
        return 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="puzzle", description="Puzzle FEM multi-solver front end")
    sub = parser.add_subparsers(required=True, metavar="COMMAND")

    p = sub.add_parser("studies", help="list the project's studies")
    p.add_argument("project", type=Path)
    p.set_defaults(handler=_studies)

    p = sub.add_parser("run", help="solve a study")
    p.add_argument("project", type=Path)
    p.add_argument("--study", required=True, help="study name")
    p.add_argument(
        "--no-reuse", action="store_true", help="solve even if an identical run is stored"
    )
    p.add_argument("--timeout", type=float, help="seconds before the solver is stopped")
    p.add_argument("-v", "--verbose", action="store_true", help="print solver output")
    p.set_defaults(handler=_run)

    p = sub.add_parser("runs", help="list stored runs")
    p.add_argument("project", type=Path)
    p.set_defaults(handler=_runs)

    for name, handler, helptext in (
        ("show", _show, "view a result"),
        ("export", _export, "write .vtu/.pvd files for ParaView"),
    ):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("project", type=Path)
        target = p.add_mutually_exclusive_group(required=True)
        target.add_argument("--study", help="the study's latest run")
        target.add_argument("--run", help="a run id (from `puzzle runs`)")
        if name == "show":
            p.add_argument("--field", default="displacement")
            p.add_argument(
                "--scalar", help="array to colour by, e.g. von_mises, displacement_magnitude"
            )
            p.add_argument("--mode", type=int)
            p.add_argument("--step", type=int)
            p.add_argument("--deform", default="auto", help="scale factor, 'auto' or 'none'")
            p.add_argument("--edges", action="store_true", help="show element edges")
            p.add_argument("--screenshot", type=Path, help="save a PNG instead of opening a window")
        else:
            p.add_argument("output", type=Path, help="the .pvd file to write")
        p.set_defaults(handler=handler)

    p = sub.add_parser("outline", help="the model tree as text")
    p.add_argument("project", type=Path)
    p.add_argument("--ids", action="store_true", help="show object ids")
    p.set_defaults(handler=_outline)

    p = sub.add_parser("history", help="what changed, by whom")
    p.add_argument("project", type=Path)
    p.add_argument("-n", type=int, default=20, help="entries to show")
    p.set_defaults(handler=_history)

    p = sub.add_parser("undo", help="undo the latest change, or history entry --seq")
    p.add_argument("project", type=Path)
    p.add_argument("--seq", type=int)
    p.set_defaults(handler=_undo)

    p = sub.add_parser("proposals", help="agents' open proposals")
    p.add_argument("project", type=Path)
    p.set_defaults(handler=_proposals)

    p = sub.add_parser("accept", help="apply a proposal")
    p.add_argument("project", type=Path)
    p.add_argument("proposal")
    p.add_argument("--only", help="comma-separated command numbers, e.g. 0,2")
    p.set_defaults(handler=_accept)

    p = sub.add_parser("reject", help="decline a proposal")
    p.add_argument("project", type=Path)
    p.add_argument("proposal")
    p.add_argument("--reason", default="")
    p.set_defaults(handler=_reject)

    p = sub.add_parser("autonomy", help="show or set what agents may do in the project")
    p.add_argument("project", type=Path)
    p.add_argument("--level", choices=["advise", "propose", "act_in_budget", "act"])
    p.add_argument("--runs", type=int, help="budget: fresh solver runs")
    p.add_argument("--solver-seconds", type=float, help="budget: solver wall time")
    p.add_argument("--mcp", choices=["off", "local", "network"])
    p.add_argument("--confirm", help="the token from the first request (level act)")
    p.set_defaults(handler=_autonomy)

    p = sub.add_parser("schema", help="print JSON schemas")
    p.add_argument("which", choices=["objects", "commands"])
    p.set_defaults(handler=_schema)

    p = sub.add_parser("mcp", help="serve the project to AI agents over MCP (stdio)")
    p.add_argument("project", type=Path)
    p.add_argument("--name", default="mcp-client", help="how the agent appears in the history")
    p.set_defaults(handler=_mcp)

    p = sub.add_parser("ask", help="ask the built-in agent (provider from agent.toml)")
    p.add_argument("project", type=Path)
    p.add_argument("question")
    p.add_argument("--quiet", action="store_true", help="print only the answer")
    p.set_defaults(handler=_ask)

    p = sub.add_parser("gui", help="open the desktop GUI")
    p.add_argument("project", type=Path, nargs="?")
    p.set_defaults(handler=_gui)
    return parser


# --- commands -------------------------------------------------------------------


def _studies(args) -> int:
    project = _load(args.project)
    for s in project.of_type(Study):
        steps = ", ".join(step.type for step in s.steps) or "no steps"
        print(f"{s.name}  [{s.solver}]  {steps}")
    return 0


def _run(args) -> int:
    from puzzle.commands import Session
    from puzzle.commands.model import RunStudy

    study = _study(_load(args.project), args.study)
    print(f"Running '{study.name}' with {study.solver} ...")
    try:
        outcome = Session(args.project).submit(
            RunStudy(study=str(study.id), reuse=not args.no_reuse, timeout_s=args.timeout),
            on_output=print if args.verbose else None,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise CliError(str(exc)) from exc
    run = outcome.run
    if run.cached:
        print(f"Unchanged since run {run.run_id[:8]}: reused its results.")
    else:
        m = run.mesh_result
        nodes = len(run.mesh.node_ids)
        print(f"Meshed {nodes} nodes ({_counts(run.mesh)}), min quality {m.min_quality:.2f}")
        for w in m.warnings:
            print(f"  warning: {w}")
        print(f"Solved in {run.job.duration_s:.1f} s ({run.job.runtime}); run {run.run_id[:8]}")
    _summarise(run.results)
    return 0


def _runs(args) -> int:
    runs = _store(args.project).runs()
    if not runs:
        print("No runs yet.")
    for r in runs:
        when = r.started_at.astimezone().strftime("%Y-%m-%d %H:%M")
        where = f"[{r.solver}, {r.runtime}]"
        print(f"{r.run_id[:8]}  {when}  {r.study_name}  {where}  {r.duration_s:.1f} s")
    return 0


def _show(args) -> int:
    from puzzle.post.view import View, render

    store, record = _target(args)
    deform = (
        None
        if args.deform == "none"
        else (args.deform if args.deform == "auto" else float(args.deform))
    )
    view = View(
        field=args.field,
        scalar=args.scalar,
        step=args.step,
        mode=args.mode,
        deform=deform,
        show_edges=args.edges,
    )
    try:
        shown = render(
            store.read_mesh(record.mesh_id),
            store.read_results(record.run_id),
            view,
            screenshot=args.screenshot,
        )
    except KeyError as exc:
        raise CliError(exc.args[0]) from exc
    print(f"{shown['title']}: {shown['min']:.4g} to {shown['max']:.4g}")
    if args.screenshot:
        print(f"Saved {args.screenshot}")
    return 0


def _export(args) -> int:
    from puzzle.post.vtk import export

    store, record = _target(args)
    files = export(store.read_mesh(record.mesh_id), store.read_results(record.run_id), args.output)
    print(f"Wrote {files[0]} ({len(files) - 1} step files); open it in ParaView.")
    return 0


def _outline(args) -> int:
    from puzzle.commands import outline

    print(outline(_load(args.project), ids=args.ids))
    return 0


def _history(args) -> int:
    from puzzle.commands import Session

    _load(args.project)
    for e in Session(args.project).history(limit=args.n):
        who = e["actor"]["kind"] + (f" {e['actor']['name']}" if e["actor"]["name"] else "")
        what = e["type"]
        if e["type"] == "command":
            what = e["command"]["command"]
            if "approved_by" in e:
                what += f" (proposal {e['proposal']}, approved)"
        elif e["type"] == "proposal":
            what = f"proposal {e['id']}: " + ", ".join(c["command"] for c in e["commands"])
        elif e["type"] == "decision":
            what = f"{'accepted' if e['accepted'] else 'rejected'} proposal {e['proposal']}"
        undone = "  (undone)" if e.get("undone") else ""
        when = e["time"][:19].replace("T", " ")
        print(f"{e['seq']:>4}  {when}  {who:<16} {what}{undone}")
    return 0


def _undo(args) -> int:
    from puzzle.commands import Session

    _load(args.project)
    out = Session(args.project).undo(seq=args.seq)
    names = [o["name"] for o in out.created + out.changed + out.deleted]
    print(f"Undone; restored {', '.join(names) or 'nothing'} (history entry {out.seq}).")
    return 0


def _proposals(args) -> int:
    from puzzle.commands import Session

    _load(args.project)
    proposals = Session(args.project).proposals()
    if not proposals:
        print("No open proposals.")
    for p in proposals:
        print(f"{p['id']}  from {p['actor']['kind']} {p['actor']['name']}: {p['rationale']}")
        for i, c in enumerate(p["commands"]):
            print(f"    {i}: {json.dumps(c)}")
        changed = [o["name"] for o in p["preview"]["created"] + p["preview"]["changed"]]
        if changed:
            print(f"    changes: {', '.join(changed)}")
        for problem in p["preview"]["problems"]:
            print(f"    would leave: [{problem['code']}] {problem['message']}")
    return 0


def _accept(args) -> int:
    from puzzle.commands import Session

    _load(args.project)
    only = [int(i) for i in args.only.split(",")] if args.only else None
    outcomes = Session(args.project).accept(args.proposal, only=only)
    print(f"Accepted proposal {args.proposal}: {len(outcomes)} command(s) applied.")
    return 0


def _reject(args) -> int:
    from puzzle.commands import Session

    _load(args.project)
    Session(args.project).reject(args.proposal, reason=args.reason)
    print(f"Rejected proposal {args.proposal}.")
    return 0


def _autonomy(args) -> int:
    from puzzle.commands import Session

    project = _load(args.project)
    budget = None
    if args.runs is not None or args.solver_seconds is not None:
        current = project.agent.budget
        budget = {
            "runs": current.runs if args.runs is None else args.runs,
            "solver_seconds": (
                current.solver_seconds if args.solver_seconds is None else args.solver_seconds
            ),
        }
    command = {
        "command": "set_autonomy",
        **{
            k: v
            for k, v in (
                ("level", args.level),
                ("mcp", args.mcp),
                ("budget", budget),
                ("confirm", args.confirm),
            )
            if v is not None
        },
    }
    if len(command) > 1:
        try:
            Session(args.project).submit(command)
        except PuzzleError as exc:
            if exc.code != "confirmation_required" or "token" not in exc.details:
                raise
            token = exc.details["token"]
            print("Unbudgeted 'act' lets agents change the model and run solvers unasked.")
            print(f"To confirm, run again with --level act --confirm {token} (within 15 minutes).")
            return 0
    agent = load_project(args.project).agent
    print(
        f"Agents: {agent.level}; budget {agent.budget.runs} runs, "
        f"{agent.budget.solver_seconds:g} s; MCP {agent.mcp}"
    )
    return 0


def _schema(args) -> int:
    if args.which == "commands":
        from puzzle.commands import command_schema

        schema = command_schema()
    else:
        from pydantic import TypeAdapter

        from puzzle.model.schema import OBJECT_TYPES

        schema = {cls.kind: TypeAdapter(cls).json_schema() for cls in OBJECT_TYPES}
    print(json.dumps(schema, indent=2))
    return 0


def _mcp(args) -> int:
    from puzzle.agent.mcp import serve

    _load(args.project)
    serve(args.project, client=args.name)
    return 0


def _ask(args) -> int:
    from puzzle.agent.agent import Agent
    from puzzle.agent.providers import ProviderError
    from puzzle.agent.settings import load_settings, settings_path

    _load(args.project)
    settings = load_settings()
    try:
        provider = settings.build()
    except ProviderError as exc:
        print(f"{exc} (configure {settings_path()})")
        return 1
    agent = Agent(args.project, provider, images=settings.images_allowed(provider))

    def show(e) -> None:
        if args.quiet or e.kind == "text":
            return
        if e.kind == "tool_call":
            print(f"  -> {e.text} {e.data.get('arguments', {})}")
        elif e.kind in ("tool_result", "error"):
            mark = "x" if e.kind == "error" else "<-"
            print(f"  {mark} {(e.text.splitlines() or [''])[0][:120]}")

    print(agent.ask(args.question, show))
    print(
        f"({provider.name} {provider.model}: {agent.tokens['input']} in / "
        f"{agent.tokens['output']} out tokens)"
    )
    return 0


def _gui(args) -> int:
    try:
        from puzzle.gui.app import main as gui
    except ImportError as exc:
        print(f"the GUI needs PySide6 and pyvistaqt (pip install puzzle-fem[gui]): {exc}")
        return 1
    return gui(args.project)


# --- helpers --------------------------------------------------------------------


def _load(path: Path) -> Project:
    if not (path / "project.toml").exists():
        raise CliError(f"{path} is not a Puzzle project (no project.toml)")
    return load_project(path)


def _store(project_dir: Path) -> ResultStore:
    return ResultStore(project_dir / RESULTS_DIR)


def _study(project: Project, name: str) -> Study:
    matches = [s for s in project.of_type(Study) if s.name == name]
    if not matches:
        names = ", ".join(sorted(s.name for s in project.of_type(Study)))
        raise CliError(f"no study named '{name}'; studies: {names}")
    if len(matches) > 1:
        raise CliError(f"{len(matches)} studies are named '{name}'")
    return matches[0]


def _target(args) -> tuple[ResultStore, RunRecord]:
    store = _store(args.project)
    runs = store.runs()
    if args.run:
        hits = [r for r in runs if r.run_id.startswith(args.run)]
        if len(hits) != 1:
            raise CliError(f"run '{args.run}' matches {len(hits)} runs")
        return store, hits[0]
    study = _study(_load(args.project), args.study)
    hits = [r for r in runs if r.study_id == str(study.id)]
    if not hits:
        raise CliError(f"study '{study.name}' has no stored runs; run it first")
    return store, hits[-1]


def _summarise(results) -> None:
    from puzzle.post.vtk import von_mises

    for step in results.steps:
        if step.mode:
            print(f"  mode {step.mode}: {step.value:.6g} Hz")
            continue
        parts = []
        if "displacement" in step.fields:
            u = np.linalg.norm(step.fields["displacement"].values, axis=1).max()
            parts.append(f"max |displacement| {u:.4g} m")
        if "stress" in step.fields:
            parts.append(f"max von Mises {von_mises(step.fields['stress'].values).max():.4g} Pa")
        if "velocity" in step.fields:
            speed = np.linalg.norm(step.fields["velocity"].values, axis=1).max()
            parts.append(f"max |velocity| {speed:.4g} m/s")
        if "pressure" in step.fields:
            p = step.fields["pressure"].values
            parts.append(f"pressure {p.min():.4g} to {p.max():.4g} Pa")
        if "electric_potential" in step.fields:
            v = step.fields["electric_potential"].values
            parts.append(f"potential {v.min():.4g} to {v.max():.4g} V")
        if "electric_energy" in step.fields:
            parts.append(f"stored energy {step.fields['electric_energy'].values[0, 0]:.4g} J")
        if "magnetic_flux_density" in step.fields:
            b = np.linalg.norm(step.fields["magnetic_flux_density"].values, axis=1)
            parts.append(f"max |B| {b.max():.4g} T")
        if "magnetic_energy" in step.fields:
            parts.append(f"magnetic energy {step.fields['magnetic_energy'].values[0, 0]:.4g} J")
        if "temperature" in step.fields:
            t = step.fields["temperature"].values
            parts.append(f"temperature {t.min():.5g} to {t.max():.5g} K")
        if parts:
            print(f"  step {step.index} ({step.kind}): " + ", ".join(parts))


def _counts(mesh) -> str:
    return ", ".join(
        f"{n} {e}"
        for e, n in mesh.element_counts().items()
        if e.startswith(("tet", "hex", "wedge"))
    )


if __name__ == "__main__":
    raise SystemExit(main())
