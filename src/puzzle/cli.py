"""The `puzzle` command line.

    puzzle studies PROJECT                       list the project's studies
    puzzle run PROJECT --study NAME              solve (or reuse an identical earlier run)
    puzzle runs PROJECT                          list stored runs
    puzzle show PROJECT --study NAME [options]   view a result, or save it with --screenshot
    puzzle export PROJECT --study NAME OUT       ParaView, point clouds, the deformed shape
    puzzle outline PROJECT                       the model tree as text
    puzzle history PROJECT                       what changed, by whom
    puzzle undo PROJECT [--seq N]                undo the latest change (or entry N)
    puzzle proposals PROJECT                     agents' open proposals
    puzzle accept|reject PROJECT ID              decide on a proposal
    puzzle autonomy PROJECT [--level ...]        what agents may do here
    puzzle schema {objects,commands}             JSON schemas for clients and agents
    puzzle mcp PROJECT                           serve the project to agents over MCP (stdio)
    puzzle jobs PROJECT [--resume|--cancel|--dismiss]  solves with a job runner (ADR 0067)

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


def _tolerant_output() -> None:
    """A character the console's code page lacks (an en dash, ², →) is replaced, not
    fatal: Windows' cp1252 console made a print raise and the command fail."""
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")


def main(argv: list[str] | None = None) -> int:
    _tolerant_output()
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


def _versions(args) -> int:
    from puzzle.project import versions

    if args.show:
        v = versions.get(
            args.project, args.show if args.show in ("good", "latest") else int(args.show)
        )
        print(json.dumps(v.to_dict(), indent=2))
        return 0
    from datetime import datetime, timedelta

    def when(text, end=False):
        if not text:
            return None
        t = datetime.fromisoformat(text)
        if end and "T" not in text:  # a date: through the end of that day
            t += timedelta(days=1) - timedelta(microseconds=1)
        return t

    every = versions.versions(args.project)
    unknown = set(args.types or []) - set(versions.TYPES)
    if unknown:
        raise CliError(
            f"unknown type {', '.join(sorted(unknown))}; one of {', '.join(versions.TYPES)}"
        )
    found = versions.select(every, by=args.by, types=args.types, since=when(args.since),
                            until=when(args.until, end=True), good=True if args.good else None,
                            text=args.search)  # fmt: skip
    if not every:
        print("no versions yet (one is made at the first change)")
    elif not found:
        print(f"none of the {len(every)} versions match")
    for v in reversed(found):
        when = v.when.astimezone().strftime("%Y-%m-%d %H:%M")
        good = "  known good" if v.good else ""
        print(f"{v.number:>4}  {when}  {v.by:<8} {v.what}{good}")
    return 0


def _version_save(args) -> int:
    from puzzle.commands import Session

    v = Session(args.project).save_version(label=args.label, note=args.note, good=args.good)
    print(f"saved version {v.number}" + (f": {v.label}" if v.label else ""))
    return 0


def _version_restore(args) -> int:
    from puzzle.commands import Session

    which = args.version if args.version in ("good", "latest") else int(args.version)
    s = Session(args.project)
    s.restore_version(which)
    entry = s.history_entries()[-1]
    print(f"restored version {entry['restored']}; the state replaced is version "
          f"{entry['files_from']}, and undo takes the restore back")  # fmt: skip
    return 0


def _save_as(args) -> int:
    from puzzle.project.copy import save_as

    done = save_as(args.project, args.dest, args.name)
    print(done.summary())
    for was, now in done.brought_in.items():
        print(f"  {was} -> {now}")
    return 0


def _archive(args) -> int:
    from puzzle.project.archive import Include, write

    done = write(args.project, args.file, Include(results=args.results,
                                                  versions=not args.no_versions,
                                                  history=not args.no_history))  # fmt: skip
    print(done.summary())
    return 0


def _unarchive(args) -> int:
    from puzzle.project.archive import read

    opened = read(args.file, args.dest, name=args.name)
    print(opened.summary())
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="puzzle", description="Puzzle FEM multi-solver front end")
    sub = parser.add_subparsers(required=True, metavar="COMMAND")

    p = sub.add_parser("studies", help="list the project's studies")
    p.add_argument("project", type=Path)
    p.set_defaults(handler=_studies)

    p = sub.add_parser("run", help="solve a study")
    p.add_argument("project", type=Path)
    p.add_argument(
        "--study",
        action="append",
        default=[],
        help="study name; give it again for several, run one after another",
    )
    p.add_argument(
        "--all",
        action="store_true",
        help="every study, one after another (sources of transfers first)",
    )
    p.add_argument(
        "--no-reuse", action="store_true", help="solve even if an identical run is stored"
    )
    p.add_argument("--timeout", type=float, help="seconds before the solver is stopped")
    p.add_argument("--host", help="run on this remote host (puzzle hosts list), this once")
    p.add_argument(
        "-v", "--verbose", "--log", action="store_true",
        help="print the solver's whole log instead of a one-line progress status",
    )  # fmt: skip
    p.set_defaults(handler=_run)

    p = sub.add_parser("save-as", help="a full, independent copy of the project in a new folder")
    p.add_argument("project", type=Path)
    p.add_argument("dest", type=Path, help="a new or empty folder outside the project")
    p.add_argument("--name", help="the copy's name (default: the project's)")
    p.set_defaults(handler=_save_as)

    p = sub.add_parser("archive", help="the project as one .puzzle file to share (ADR 0061)")
    p.add_argument("project", type=Path)
    p.add_argument("file", type=Path, help="the archive to write (.puzzle)")
    p.add_argument("--results", default="latest", choices=["none", "latest", "all"],
                   help="which runs go in: none, the latest per study (default), all")  # fmt: skip
    p.add_argument("--no-versions", action="store_true", help="leave the versions out")
    p.add_argument("--no-history", action="store_true", help="leave the history out")
    p.set_defaults(handler=_archive)

    p = sub.add_parser("unarchive", help="open a .puzzle archive as a project (ADR 0061)")
    p.add_argument("file", type=Path)
    p.add_argument("dest", type=Path, help="a new or empty folder")
    p.add_argument("--name", help="the project's name there")
    p.set_defaults(handler=_unarchive)

    p = sub.add_parser("versions", help="the project's versions, newest first (ADR 0059)")
    p.add_argument("project", type=Path)
    p.add_argument("--show", metavar="N", help="one version's summary in full")
    p.add_argument("--by", choices=["you", "puzzle", "agent"], help="who made them")
    p.add_argument("--type", action="append", dest="types", metavar="TYPE",
                   help="user, first, opened, before_run, edits, before_restore, copied "
                   "(repeat for several)")  # fmt: skip
    p.add_argument("--since", help="from this date (YYYY-MM-DD) or time (YYYY-MM-DDTHH:MM)")
    p.add_argument("--until", help="up to this date or time (a date includes its day)")
    p.add_argument("--good", action="store_true", help="known good ones only")
    p.add_argument("--search", default="", help="words in the label, note or changes")
    p.set_defaults(handler=_versions)

    p = sub.add_parser("version-save", help="save a version of the project as it is now")
    p.add_argument("project", type=Path)
    p.add_argument("--label", default="", help="a few words naming it")
    p.add_argument("--note", default="")
    p.add_argument("--good", action="store_true", help="mark it known good")
    p.set_defaults(handler=_version_save)

    p = sub.add_parser(
        "version-restore",
        help="make the project a version's again (the state replaced is kept as a version)",
    )
    p.add_argument("project", type=Path)
    p.add_argument("version", help="its number, or 'good' for the latest known good")
    p.set_defaults(handler=_version_restore)

    p = sub.add_parser("runs", help="list stored runs")
    p.add_argument("project", type=Path)
    p.set_defaults(handler=_runs)

    for name, handler, helptext in (
        ("show", _show, "view a result"),
        ("paraview", _paraview, "open a result in ParaView, its view rebuilt (ADR 0070)"),
        ("export", _export, "results for other software: ParaView, point clouds, CAD"),
        ("progress", _progress, "how a run converged: steps, increments, cut-backs, messages"),
    ):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("project", type=Path)
        target = p.add_mutually_exclusive_group(required=True)
        target.add_argument("--study", help="the study's latest run")
        target.add_argument("--run", help="a run id (from `puzzle runs`)")
        if name == "progress":  # ADR 0054
            target.add_argument(
                "--folder", type=Path, help="a run's folder (.puzzle/runs/...), for a failed run"
            )
            p.add_argument("--plot", type=Path, help="save the convergence plot as a PNG")
            p.add_argument("--json", action="store_true", help="the summary as JSON")
        elif name == "paraview":
            p.add_argument("--field", default="displacement")
            p.add_argument("--scalar", help="array to colour by, e.g. von_mises")
            p.add_argument("--mode", type=int)
            p.add_argument("--step", type=int)
            p.add_argument("--deform", default="auto", help="scale factor, 'auto' or 'none'")
            p.add_argument("--range", help="a custom colour range: bottom,top (the array's units)")
            p.add_argument(
                "--no-launch",
                action="store_true",
                help="write the files and view.py only, don't start ParaView",
            )
        elif name == "show":
            p.add_argument("--field", default="displacement")
            p.add_argument(
                "--scalar", help="array to colour by, e.g. von_mises, displacement_magnitude"
            )
            p.add_argument("--mode", type=int)
            p.add_argument("--step", type=int)
            p.add_argument("--deform", default="auto", help="scale factor, 'auto' or 'none'")
            p.add_argument("--edges", action="store_true", help="show element edges")
            p.add_argument("--screenshot", type=Path, help="save a PNG instead of opening a window")
            p.add_argument("--cut", help="a section cut: axis=fraction, e.g. x=0.5 (ADR 0038)")
            p.add_argument("--range", help="a custom colour range: bottom,top (the array's units)")
            p.add_argument("--animate", type=Path, help="save one loop of the deformation as a GIF")
        else:  # export (ADR 0043)
            from puzzle.post.exports import EXPORTS

            p.add_argument("output", type=Path, help="the file to write (its suffix is set)")
            p.add_argument(
                "--format",
                default="paraview",
                choices=sorted(EXPORTS),
                help="; ".join(f"{k}: {e.label}" for k, e in EXPORTS.items()),
            )
            p.add_argument("--step", type=int, default=-1, help="which step (0 first, -1 last)")
            p.add_argument("--quantity", help="point cloud: a result key, e.g. stress:von_mises")
            p.add_argument("--selection", help="point cloud: only this selection's nodes")
            p.add_argument("--scale", type=float, default=1.0, help="deformed shape: exaggeration")
            p.add_argument(
                "--compensate", action="store_true", help="deformed shape: moved by -u instead"
            )
            p.add_argument("--units", choices=["si", "display"], default="si")
            p.add_argument("--axes", help="a coordinate system: components in its axes")
        p.set_defaults(handler=handler)

    p = sub.add_parser("plot", help="a kept probe over the steps, as CSV")
    p.add_argument("project", type=Path)
    p.add_argument("probe", help="the kept probe's name")
    p.set_defaults(handler=_plot)

    p = sub.add_parser(
        "mesh-shootout", help="run a study on Gmsh's and Netgen's meshes and compare (ADR 0019)"
    )
    p.add_argument("project", type=Path)
    p.add_argument("study")
    p.add_argument(
        "--quantity",
        action="append",
        default=None,
        help="a result field compared by its peak (or global value), or eigenfrequencies; "
        "repeat for several (default: displacement)",
    )
    p.add_argument("--tolerance", type=float, default=0.02, help="relative (default 0.02)")
    p.set_defaults(handler=_mesh_shootout)

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
    p.add_argument("--core-hours", type=float, help="budget: cores × hours on remote hosts")
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

    p = sub.add_parser("check-geometry", help="what would trouble meshing: gaps, slivers, ...")
    p.add_argument("project", type=Path)
    p.add_argument("geometry", nargs="?", help="a geometry's name (default: all)")
    p.set_defaults(handler=_check_geometry)

    p = sub.add_parser("advise", help="what the project needs next (the Advisor, ADR 0040)")
    p.add_argument("project", type=Path)
    p.add_argument("--component", help="a component's name (default: the first)")
    p.add_argument("--study", help="a study's name (default: the component's first)")
    p.set_defaults(handler=_advise)

    p = sub.add_parser(
        "measure", help="exact sizes, distances and angles of named geometry (ADR 0042)"
    )
    p.add_argument("project", type=Path)
    p.add_argument("geometry", help="a geometry's name")
    p.add_argument(
        "picks", nargs="+", help='entity:name, e.g. face:B/+z edge:"B/+x|B/+z" vertex:... body:Body'
    )
    p.set_defaults(handler=_measure)

    p = sub.add_parser(
        "selections", help="how each selection resolves after edits; reattach detached ones"
    )
    p.add_argument("project", type=Path)
    p.add_argument("geometry", nargs="?", help="a geometry's name (default: all)")
    p.add_argument(
        "--accept-all",
        action="store_true",
        help="take every re-found name and nearest candidate, keeping rules where one fits",
    )
    p.set_defaults(handler=_selections)

    p = sub.add_parser(
        "contacts", help="each contact's setup, what the solver is given, its initial state"
    )
    p.add_argument("project", type=Path)
    p.add_argument("component", nargs="?", help="a component's name (default: all with contacts)")
    p.add_argument("--solver", help="one solver's view (default: the component's studies')")
    p.add_argument("--json", action="store_true", help="the report as JSON")
    p.set_defaults(handler=_contacts)

    p = sub.add_parser(
        "split-parts", help="one part per body of a part holding several (an imported assembly)"
    )
    p.add_argument("project", type=Path)
    p.add_argument("component", help="the component's name")
    p.add_argument("part", help="the part to split")
    p.add_argument("--no-bond", action="store_true",
                   help="don't add bonded contacts where the bodies touch")  # fmt: skip
    p.add_argument("--dry-run", action="store_true", help="say what it would make, change nothing")
    p.set_defaults(handler=_split_parts)

    p = sub.add_parser(
        "schematic", help="the project as nodes and wires: what feeds and shares what (ADR 0058)"
    )
    p.add_argument("project", type=Path)
    p.add_argument("--svg", type=Path, metavar="FILE", help="save the picture as SVG")
    p.add_argument("--json", action="store_true", help="the nodes, ports and wires as JSON")
    p.add_argument("--no-uses", action="store_true", help="leave out the grey use wires (SVG)")
    p.set_defaults(handler=_schematic)

    p = sub.add_parser("operators", help="the project's operators and their values (ADR 0062)")
    p.add_argument("project", type=Path)
    p.add_argument("name", nargs="?", help="one operator (default: all)")
    p.add_argument("--json", action="store_true", help="the values as JSON (SI)")
    p.add_argument("--export", action="store_true",
                   help="write each export operator's file (or the one named)")  # fmt: skip
    p.set_defaults(handler=_operators)

    p = sub.add_parser("table", help="a table operator's rows (ADR 0064), in display units")
    p.add_argument("project", type=Path)
    p.add_argument("name", help="the operator")
    p.add_argument("--rows", type=int, default=20, metavar="N", help="rows shown (default 20)")
    p.add_argument("--csv", type=Path, metavar="FILE", help="write it all as CSV")
    p.add_argument("--parquet", type=Path, metavar="FILE", help="write it all as Parquet")
    p.add_argument("--xlsx", type=Path, metavar="FILE", help="write it all as Excel")
    p.set_defaults(handler=_table)

    p = sub.add_parser("optimise", help="run an optimisation's points, or list them (ADR 0063)")
    p.add_argument("project", type=Path)
    p.add_argument("name", help="the optimisation")
    p.add_argument("--points", action="store_true", help="list the points; solve nothing")
    p.add_argument("--budget", type=int, metavar="N", help="solve at most N new points this time")
    p.add_argument(
        "--apply",
        action="store_true",
        help="put the best feasible point's values into the model (one undo)",
    )
    p.add_argument("--json", action="store_true", help="the points as JSON")
    p.set_defaults(handler=_optimise)

    p = sub.add_parser("convergence", help="a study on finer and finer meshes, judged by the "
                       "grid convergence index (ADR 0066)")  # fmt: skip
    p.add_argument("project", type=Path)
    p.add_argument("study", help="the study, or a mesh study")
    p.add_argument("--levels", type=int, metavar="N", help="how many meshes (default 3)")
    p.add_argument("--ratio", type=float, metavar="R", help="each mesh's sizes over the next")
    p.add_argument("--target", type=float, metavar="T", help="the GCI that counts as converged")
    p.add_argument("--points", action="store_true", help="read the meshes run; solve nothing")
    p.add_argument("--json", action="store_true", help="levels and verdicts as JSON")
    p.set_defaults(handler=_convergence)

    p = sub.add_parser("adapt", help="mesh again from the error estimate, pass after "
                       "pass, until it is at a target (ADR 0066)")  # fmt: skip
    p.add_argument("project", type=Path)
    p.add_argument("study")
    p.add_argument("--target", type=float, metavar="T", help="the error to reach (0.02 = 2 %%)")
    p.add_argument("--budget", type=int, metavar="N", help="at most N passes")
    p.add_argument("--max-elements", type=int, metavar="N", help="stop past N elements")
    p.add_argument(
        "--anisotropic",
        action="store_true",
        help="remesh with MMG along the field's Hessian (stretched elements)",
    )
    p.add_argument(
        "--delete-passes",
        action="store_true",
        help="delete the earlier passes' runs at the end (their table is kept)",
    )
    p.add_argument("--points", action="store_true", help="read the passes run; solve nothing")
    p.add_argument("--json", action="store_true", help="the passes as JSON")
    p.set_defaults(handler=_adapt)

    p = sub.add_parser("error-estimate", help="how far a run is from the exact answer, per "
                       "element (Zienkiewicz–Zhu; ADR 0066)")  # fmt: skip
    p.add_argument("project", type=Path)
    p.add_argument("study", help="the study (its newest run)")
    p.add_argument("--field", help="a nodal field's gradient jumps instead (relative only)")
    p.add_argument(
        "--target",
        type=float,
        default=0.02,
        metavar="T",
        help="the error the refinement ratio is for (default 0.02 = 2 %%)",
    )
    p.add_argument("--json", action="store_true", help="the summary as JSON")
    p.set_defaults(handler=_error_estimate)

    p = sub.add_parser("import-field", help="bring in values at points from a file (ADR 0044)")
    p.add_argument("project", type=Path)
    p.add_argument("file", type=Path, nargs="+", help="CSV/TXT/DAT (x, y, z and values), VTU, "
                   "a Fluent profile (.prof), Exodus II (.exo, .e), EnSight Gold (.case) or an "
                   "OpenFOAM case folder; several files (one per time) with --times")  # fmt: skip
    p.add_argument("--name", help="the field's name (default: the file's)")
    p.add_argument("--unit", help="the points' length unit (default: from the header, else m)")
    p.add_argument(
        "--column", action="append", default=[], metavar="NAME:ROLE[:UNIT]",
        help="a column's role (x, y, z, value, time, frequency, ignore) and unit, e.g. "
        "p:value:MPa",
    )  # fmt: skip
    p.add_argument("--region", help="a block, node set, part, patch or profile of the source")
    p.add_argument("--step", help="the source's step to read (Exodus/EnSight number from 1, "
                   "OpenFOAM time directory; default the last)")  # fmt: skip
    p.add_argument(
        "--every-step",
        action="store_true",
        help="vary in time over every step of the source (Exodus, EnSight, OpenFOAM)",
    )
    p.add_argument("--times", nargs="+", type=float, metavar="S",
                   help="seconds: one per file, or per step with --every-step")  # fmt: skip
    p.add_argument("--frequencies", nargs="+", type=float, metavar="HZ",
                   help="in place of --times: the field varies with frequency (Hz), for "
                   "harmonic steps")  # fmt: skip
    p.add_argument("--translate", nargs=3, type=float, metavar=("X", "Y", "Z"), help="metres")
    p.add_argument("--rotate", nargs=3, type=float, metavar=("RX", "RY", "RZ"), help="degrees")
    p.add_argument("--about", nargs=3, type=float, metavar=("X", "Y", "Z"), help="metres")
    p.set_defaults(handler=_import_field)

    p = sub.add_parser("field-check", help="an imported field's verification report")
    p.add_argument("project", type=Path)
    p.add_argument("field", help="the field's name")
    p.add_argument("--on", help="Component or Component/Part/Selection to check coverage on")
    p.add_argument("--tolerance", type=float, help="mm (default: 2 %% of the target, or the "
                   "data's spacing)")  # fmt: skip
    p.add_argument("--json", action="store_true")
    p.set_defaults(handler=_field_check)

    p = sub.add_parser("materials-export", help="your material library as one file to share")
    p.add_argument("path", type=Path, help="the .json file to write")
    p.add_argument("--scope", choices=["yours", "user", "all"], default="user",
                   help="yours: the builder's; user (default): imported and yours; all: with "
                   "the generics")  # fmt: skip
    p.set_defaults(handler=_materials_export)

    p = sub.add_parser("materials-import", help="materials from a library file into yours")
    p.add_argument("path", type=Path, help="a file from materials-export, or a library.json")
    p.add_argument(
        "--into",
        choices=["yours", "library"],
        default="yours",
        help="yours (default): added to your materials, checked, one undo; "
        "library: replaces your imported library (the old one kept as .bak)",
    )
    p.add_argument(
        "--replace",
        action="store_true",
        help="replace materials you already have (default: keep them)",
    )
    p.set_defaults(handler=_materials_import)

    p = sub.add_parser("materials-remove", help="materials removed from your library (one undo)")
    p.add_argument("keys", nargs="*", help="library keys (materials-duplicates lists some)")
    p.add_argument("--restore", action="store_true", help="bring removed ones back instead")
    p.add_argument("--list", action="store_true", help="the materials you removed")
    p.set_defaults(handler=_materials_remove)

    p = sub.add_parser("materials-duplicates", help="library materials with the same values")
    p.set_defaults(handler=_materials_duplicates)

    p = sub.add_parser("settings", help="your preferences (ADR 0024): list them, or set one")
    p.add_argument("key", nargs="?", help="e.g. units.system")
    p.add_argument("value", nargs="?", help="e.g. engineering; omit to show the choices")
    p.set_defaults(handler=_settings)

    p = sub.add_parser("machine", help="Puzzle's Linux machine on Windows: WSL 2 solvers "
                       "(ADR 0068 step 5)")  # fmt: skip
    p.add_argument("action", choices=["list", "install", "remove"])
    p.add_argument("solver", nargs="?", help="install/remove: elmer, openfoam, code_saturne, ...")
    p.add_argument("--image", help="install: the image to export (default: the solver's)")
    p.add_argument("--tarball", type=Path, help="install: a root file system to import instead")
    p.set_defaults(handler=_machine)

    p = sub.add_parser("clusters", help="your operator clusters and blocks to share (ADR 0071)")
    p.add_argument("action", choices=["list", "export", "import", "remove"])
    p.add_argument("name", nargs="?", help="export/remove: the cluster's name or key")
    p.add_argument("file", nargs="?", type=Path, help="export: where; import: the block file")
    p.set_defaults(handler=_clusters)

    p = sub.add_parser("solvers", help="the solvers here and how each runs (ADR 0068)")
    p.add_argument(
        "--build",
        action="store_true",
        help="build CalculiX with PARDISO on this computer now (ADR 0051)",
    )
    p.add_argument("--repair", metavar="ENV",
                   help="reinstall a solver environment of the installed app from its "
                   "package list (calculix, fenicsx, ...)")  # fmt: skip
    p.add_argument("--clear-wait", action="store_true",
                   help="clear the PARDISO build's 24-hour wait after a failure, so it is "
                   "tried the next time Puzzle opens (Settings > Advanced)")  # fmt: skip
    p.add_argument("--json", action="store_true")
    p.set_defaults(handler=_solvers)

    p = sub.add_parser("trust", help="let a project's scripts and equations run (ADR 0062)")
    p.add_argument("project", type=Path)
    p.add_argument("--revoke", action="store_true", help="stop trusting it")
    p.set_defaults(handler=_trust)

    p = sub.add_parser("scripts", help="the Python environment scripts run in (ADR 0062)")
    p.add_argument("action", choices=["list", "add"])
    p.add_argument("package", nargs="?", help="add: a conda-forge package, e.g. sympy")
    p.set_defaults(handler=_scripts)

    p = sub.add_parser("jobs", help="solves with a job runner, not yet home (ADR 0067): list, "
                       "resume, cancel or dismiss them")  # fmt: skip
    p.add_argument("project", type=Path)
    p.add_argument("--live", action="store_true", help="ask remote hosts too (over SSH)")
    p.add_argument("--json", action="store_true")
    act = p.add_mutually_exclusive_group()
    act.add_argument("--resume", nargs="?", const="all", metavar="JOB|STUDY|all",
                     help="pick a job up and bring its results home (default: all of them, "
                     "finished ones first)")  # fmt: skip
    act.add_argument("--cancel", metavar="JOB|STUDY", help="stop a job (it stays listed)")
    act.add_argument("--dismiss", metavar="JOB|STUDY", help="forget a job without its "
                     "results (cancelled first if it runs)")  # fmt: skip
    p.add_argument("--delete-remote", action="store_true",
                   help="with --dismiss: delete the case on the remote host too")  # fmt: skip
    p.add_argument("-v", "--verbose", action="store_true",
                   help="with --resume: print the solver's log")  # fmt: skip
    p.set_defaults(handler=_jobs)

    p = sub.add_parser("hosts", help="remote hosts to run studies on (ADR 0021)")
    p.add_argument("action", choices=["list", "add", "check", "remove"])
    p.add_argument("name", nargs="?", help="the host's name")
    p.add_argument("--address", help="add: an ssh alias, host name or IP address")
    p.add_argument("--user", default="")
    p.add_argument("--port", type=int, default=0)
    p.add_argument("--runtime", default="native", choices=["native", "docker", "podman",
                                                            "apptainer"])  # fmt: skip
    p.add_argument("--envs-dir", default="~/puzzle/.pixi/envs",
                   help="native: the pixi solver environments there")  # fmt: skip
    p.add_argument("--work-dir", default="~/puzzle-runs", help="where runs go there")
    p.add_argument("--cores", type=int, default=0)
    p.add_argument("--memory-gb", type=float, default=0.0)
    p.add_argument("--scheduler", default="none", choices=["none", "slurm", "pbs"])
    p.add_argument("--partition", default="")
    p.add_argument("--account", default="")
    p.add_argument("--walltime", default="01:00:00", help="HH:MM:SS")
    p.set_defaults(handler=_hosts)

    p = sub.add_parser("agent", help="choose the local expert model by measurement (ADR 0022)")
    p.add_argument("action", choices=["pick"])
    p.add_argument("--base-url", default="http://localhost:11434/v1",
                   help="the local endpoint (Ollama, llama.cpp, LM Studio)")  # fmt: skip
    p.add_argument("--model", action="append", help="only these models (repeatable)")
    p.add_argument("--again", action="store_true", help="measure again, ignoring kept results")
    p.add_argument("--use", action="store_true", help="save the recommendation in agent.toml")
    p.add_argument("--workdir", type=Path, help="where the task projects go (default: a temp)")
    p.set_defaults(handler=_agent_pick)

    p = sub.add_parser("agent-key", help="store, check or remove the agent's API key")
    p.add_argument("action", choices=["set", "status", "clear"])
    p.add_argument("--provider", default="anthropic", choices=["anthropic", "openai"])
    p.set_defaults(handler=_agent_key)

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
    """One study, or several one after another (ADR 0073): a failure goes on to the
    next, and the exit status says whether any failed."""
    from puzzle import run_queue

    project = _load(args.project)
    if not args.all and not args.study:
        raise CliError("name a study with --study, or run them all with --all")
    if args.all:
        studies = run_queue.in_order(project)
    else:
        studies = run_queue.in_order(project, [_study(project, n).id for n in args.study])
    if len(studies) == 1:
        return _run_one(args, studies[0])

    def one(study):
        print()
        print(f"== {study.name}")
        return _run_one(args, study)

    done = run_queue.run_all(None, studies, submit=one)
    for o in done:
        if not o.ok:
            print(f"{o.study.name} failed: {o.error}")
    print(run_queue.summary(done))
    return 0 if all(o.ok for o in done) else 1


def _run_one(args, study) -> int:
    from puzzle.commands import Session
    from puzzle.commands.model import RunStudy

    print(f"Running '{study.name}' with {study.solver} ...")
    status = None if args.verbose else _LiveStatus()
    try:
        outcome = Session(args.project).submit(
            RunStudy(
                study=str(study.id), reuse=not args.no_reuse, timeout_s=args.timeout, host=args.host
            ),
            on_output=print if args.verbose else None,
            on_event=status,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise CliError(str(exc)) from exc
    finally:
        if status is not None:
            status.close()
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
        if run.timings:  # where the time went around the solver too
            times = ", ".join(f"{k} {v:.1f} s" for k, v in run.timings.items())
            print(f"  {times} ({run.cores})" if run.cores else f"  {times}")
    _summarise(run.results)
    return 0


class _LiveStatus:
    """`puzzle run`'s one line of progress (ADR 0054): rewritten in place on a terminal,
    a line per increment or step otherwise (logs, CI)."""

    def __init__(self) -> None:
        import sys

        from puzzle.progress import State

        self.state = State()
        self.tty = sys.stdout.isatty()
        self.last = ""
        self.shown = 0.0

    def __call__(self, event) -> None:
        import time

        from puzzle.progress import Converged, Diverged, Increment, Message, Stage, Step

        self.state.add(event)
        if isinstance(event, Message) and event.level == "error":
            self._end_line()
            print(f"  {event.text}")
        line = self.state.line()
        frac = self.state.fraction()
        if frac is not None:
            line = f"{frac * 100:3.0f} %  {line}"
        if self.tty:
            now = time.monotonic()
            if line != self.last and (now - self.shown > 0.2 or not isinstance(event, Message)):
                print("\r" + line[:120].ljust(len(self.last[:120])), end="", flush=True)
                self.last, self.shown = line, now
        elif (isinstance(event, (Step, Increment, Converged, Diverged, Stage))
              and line != self.last
              and (not isinstance(event, Stage) or event.state == "start")):  # fmt: skip
            print(f"  {line}")
            self.last = line

    def _end_line(self) -> None:
        if self.tty and self.last:
            print()
            self.last = ""

    def close(self) -> None:
        self._end_line()


def _progress(args) -> int:
    """How a run converged (ADR 0054): a summary, JSON, or a PNG of the plots."""
    import json

    from puzzle import progress

    if args.folder is not None:
        events, title = progress.read(args.folder), str(args.folder)
        if not events:
            raise CliError(f"no progress kept in {args.folder}")
    else:
        store, record = _target(args)
        events = store.read_progress(record.run_id)
        title = f"{record.study_name} ({record.solver}, run {record.run_id[:8]})"
    summary = progress.summary(events)
    if args.plot is not None:
        from puzzle.gui.solution_panel import save_png

        save_png(events, title, args.plot)
        print(f"Saved {args.plot}")
    if args.json:
        print(json.dumps(summary, indent=2))
        return 0
    if not events:
        print(f"{title}: kept without its progress (an older Puzzle)")
        return 0
    print(title)
    s = progress.state(events)
    print(f"  {s.line()}")
    counts = (
        f"{summary['increments']} increments, {summary['cutbacks']} cut-backs, "
        f"{summary['iterations']} iterations, {summary['converged_increments']} converged"
    )
    print(f"  {counts}")
    if summary.get("stages_s"):
        print("  " + ", ".join(f"{k} {v:.1f} s" for k, v in summary["stages_s"].items()))
    for key, label in (("equations", "equations"), ("memory_mb", "MB"), ("cores", "cores")):
        if key in summary:
            print(
                f"  {summary[key]:,} {label}" if key != "memory_mb" else f"  {summary[key]:,.0f} MB"
            )
    for m in summary.get("messages", []):
        print(f"  {m['level']}: {m['text']}")
    if summary.get("messages_total", 0) > len(summary.get("messages", [])):
        print(f"  ({summary['messages_total']} messages in all; the last 20 shown)")
    return 0


def _runs(args) -> int:
    runs = _store(args.project).runs()
    if not runs:
        print("No runs yet.")
    for r in runs:
        when = r.started_at.astimezone().strftime("%Y-%m-%d %H:%M")
        where = f"[{r.solver}, {r.runtime}]"
        version = f"  {r.solver_version}" if r.solver_version else ""
        print(f"{r.run_id[:8]}  {when}  {r.study_name}  {where}{version}  {r.duration_s:.1f} s")
    return 0


def _show(args) -> int:
    from puzzle.post.view import View, render

    store, record = _target(args)
    deform = (
        None
        if args.deform == "none"
        else (args.deform if args.deform == "auto" else float(args.deform))
    )
    cut = None
    if args.cut:
        from puzzle.post.cuts import Cut

        axis, _, at = args.cut.partition("=")
        if axis not in ("x", "y", "z") or not at:
            raise CliError("--cut takes axis=fraction, e.g. x=0.5")
        cut = Cut(axis, float(at))
    view = View(
        clim=tuple(float(v) for v in args.range.split(",")) if args.range else None,
        field=args.field,
        scalar=args.scalar,
        step=args.step,
        mode=args.mode,
        deform=deform,
        show_edges=args.edges,
        cut=cut,
    )
    try:
        shown = render(
            store.read_mesh(record.mesh_id),
            store.read_results(record.run_id),
            view,
            screenshot=args.screenshot,
            animate=args.animate,
        )
    except KeyError as exc:
        raise CliError(exc.args[0]) from exc
    print(f"{shown['title']}: {shown['min']:.4g} to {shown['max']:.4g}")
    if args.screenshot:
        print(f"Saved {args.screenshot}")
    if args.animate:
        print(f"Saved {args.animate}")
    return 0


def _paraview(args) -> int:
    from puzzle.post import paraview_link

    store, record = _target(args)
    mesh, results = store.read_mesh(record.mesh_id), store.read_results(record.run_id)
    deform = None if args.deform == "none" else (
        args.deform if args.deform == "auto" else float(args.deform))  # fmt: skip
    clim = tuple(float(v) for v in args.range.split(",")) if args.range else None
    try:
        shown, view = paraview_link.from_run(mesh, results, field=args.field,
                                             scalar=args.scalar, step=args.step,
                                             mode=args.mode, deform=deform, clim=clim)  # fmt: skip
    except KeyError as exc:
        raise CliError(exc.args[0]) from exc
    folder = paraview_link.folder_for(Path(args.project), record.run_id)
    script = paraview_link.write(folder, shown, view, mesh, results)
    print(f"Wrote {script.parent}")
    if args.no_launch:
        return 0
    if (why := paraview_link.why_not()) is not None:
        raise CliError(why)
    paraview_link.launch(script)
    print(f"Opened in ParaView: {view.title} ({view.step})")
    return 0


def _mesh_shootout(args) -> int:
    """The study on both meshers' meshes, compared; a Markdown table (ADR 0019)."""
    import tempfile

    from puzzle.commands import resolve
    from puzzle.model.schema import ComparisonMetric, Study
    from puzzle.shootout import run_mesh_shootout

    project = _load(args.project)
    study = resolve(project, args.study, Study)
    metrics = [
        ComparisonMetric(quantity=q, norm="relative_value", tolerance=args.tolerance)
        for q in (args.quantity or ["displacement"])
    ]
    with tempfile.TemporaryDirectory() as work:
        out = run_mesh_shootout(project, study.id, Path(args.project), Path(work), metrics)
    print(out.table().replace("netgen vs gmsh", "Netgen vs Gmsh"))
    return 0 if out.passed else 1


def _plot(args) -> int:
    """A kept probe over the steps, as CSV (ADR 0038)."""
    from puzzle.commands import resolve
    from puzzle.model.schema import ResultProbe
    from puzzle.post.probes import history
    from puzzle.project.layout import RESULTS_DIR
    from puzzle.results.store import ResultStore

    project = _load(args.project)
    probe = resolve(project, args.probe, ResultProbe)
    rows = history(project, ResultStore(Path(args.project) / RESULTS_DIR), probe)
    print("step,value")
    for r in rows:
        print(f"{r['step']:.6g},{r['value']:.6g}")
    return 0


def _export(args) -> int:
    from puzzle.errors import PuzzleError
    from puzzle.post.exports import export

    store, record = _target(args)
    try:
        files = export(
            _load(args.project),
            store,
            record.run_id,
            args.format,
            args.output,
            step=args.step,
            quantity=args.quantity,
            selection=args.selection,
            scale=args.scale,
            compensate=args.compensate,
            units=args.units,
            axes=args.axes,
            root=args.project,
        )
    except PuzzleError as exc:
        print(exc.message)
        return 1
    print(f"Wrote {files[0]}" + (f" and {len(files) - 1} more files" if len(files) > 1 else ""))
    return 0


def _outline(args) -> int:
    from puzzle.commands import outline
    from puzzle.errors import PuzzleError
    from puzzle.project.layout import RESULTS_DIR
    from puzzle.results.store import ResultStore

    try:
        runs = ResultStore(Path(args.project) / RESULTS_DIR).runs()
    except PuzzleError:
        runs = None
    print(outline(_load(args.project), ids=args.ids, runs=runs))
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
    if args.runs is not None or args.solver_seconds is not None or args.core_hours is not None:
        current = project.agent.budget  # what isn't given stays as it is
        budget = {
            "runs": current.runs if args.runs is None else args.runs,
            "solver_seconds": (
                current.solver_seconds if args.solver_seconds is None else args.solver_seconds
            ),
            "core_hours": current.core_hours if args.core_hours is None else args.core_hours,
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

    try:
        print(agent.ask(args.question, show))
    except ProviderError as exc:
        print(exc)
        return 1
    print(
        f"({provider.name} {provider.model}: {agent.tokens['input']} in / "
        f"{agent.tokens['output']} out tokens)"
    )
    return 0


def _number(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None


def _advise(args) -> int:
    from puzzle.advisor import advise, next_step
    from puzzle.errors import PuzzleError
    from puzzle.model.schema import Component, Study
    from puzzle.project.layout import RESULTS_DIR
    from puzzle.results.store import ResultStore

    project = _load(args.project)
    comp = next((c for c in project.of_type(Component) if c.name == args.component), None)
    study = next((s for s in project.of_type(Study) if s.name == args.study), None)
    try:
        runs = ResultStore(Path(args.project) / RESULTS_DIR).runs()
    except PuzzleError:
        runs = []
    advice = advise(project, runs, comp, study, root=args.project)
    first = next_step(advice)
    print(f"Next: {first.label or first.title}" if first else "All set")
    marks = {"done": "✓", "needed": "●", "optional": "○"}
    for a in advice:
        print(f"  {marks[a.state]} {a.title}: {a.detail}")
    return 0


def _measure(args) -> int:
    from puzzle.geometry.measure import MeasureError, measure, rows, text
    from puzzle.geometry.naming import named_geometry
    from puzzle.model.schema import Geometry

    project = _load(args.project)
    geo = next((g for g in project.of_type(Geometry) if g.name == args.geometry), None)
    if geo is None:
        print(f"no geometry '{args.geometry}'")
        return 1
    picks = []
    for pick in args.picks:
        entity, _, name = pick.partition(":")
        entity = {"point": "vertex"}.get(entity, entity)
        if entity not in ("body", "face", "edge", "vertex") or not name:
            print(f"'{pick}': write entity:name, e.g. face:B/+z")
            return 1
        picks.append((entity, name))
    try:
        found = measure(named_geometry(project, geo, args.project), picks)
    except MeasureError as exc:
        print(exc)
        return 1
    for label, value, unit in rows(found):
        print(f"{label}: {text(value, unit)}")
    return 0


def _check_geometry(args) -> int:
    from puzzle.geometry.prep import check_geometry
    from puzzle.model.schema import Geometry

    project = _load(args.project)
    geometries = [g for g in project.of_type(Geometry) if args.geometry in (None, g.name)]
    if not geometries:
        print(f"no geometry '{args.geometry}'")
        return 1
    worst = 0
    for geo in geometries:
        findings = check_geometry(project, geo, args.project)
        print(f"{geo.name}: {'no problems' if not findings else ''}")
        for f in findings:
            print(f"  [{f['severity']}] {f['kind']}: {f['message']}")
            worst = max(worst, {"error": 2, "warning": 1}.get(f["severity"], 0))
    return 1 if worst == 2 else 0


def _selections(args) -> int:
    from puzzle.commands import Session
    from puzzle.commands.model import Edit
    from puzzle.geometry.naming import accept_all, check_selections, reattach_ops, rules_for
    from puzzle.model.schema import Geometry

    project = _load(args.project)
    geometries = [g for g in project.of_type(Geometry) if args.geometry in (None, g.name)]
    if not geometries:
        print(f"no geometry '{args.geometry}'")
        return 1
    detached = 0
    for geo in geometries:
        if not geo.selections:
            continue
        done = check_selections(project, geo, args.project)
        print(f"{geo.name}:")
        for r in done.reports:
            states = [m.status for m in r.members]
            print(f"  {r.selection} ({r.entity}): " + (", ".join(states) or "empty"))
            for m in r.members:
                if m.status in ("refound", "detached"):
                    near = ", ".join(f"{n} ({d:.3g})" for n, d in m.candidates[:3])
                    now = f" -> {m.now}" if m.now else ""
                    print(f"    {m.name}: {m.status}{now}" + (f"; nearest {near}" if near else ""))
                    detached += m.status == "detached"
        if args.accept_all:
            choices = accept_all(done.reports)
            for sel, rule in rules_for(geo, choices, done.named).items():
                choices.setdefault(sel, {})["rule"] = rule
            ops = reattach_ops(geo, choices, done.named, done.reports)
            if ops:
                Session(args.project).submit(Edit(object=str(geo.id), ops=ops))
                print(f"  reattached ({len(choices)} selection(s); undo with puzzle undo)")
    return 1 if detached and not args.accept_all else 0


def _convergence(args) -> int:
    """ADR 0066: solve the levels not run yet (or only read them), then the verdicts."""
    import json

    from puzzle import convergence as conv
    from puzzle.commands import Session
    from puzzle.project.layout import WORK_DIR

    s = Session(args.project)
    study, c = conv.settings(s.project, args.study)
    count = args.levels or c.levels
    ratio = args.ratio or c.ratio
    target = args.target or c.target
    lv = conv.run(s.project, study, args.project, args.project / WORK_DIR / "convergence"
                  / str(study.id), store=_store(args.project), count=count, ratio=ratio,
                  outputs=c.quantities, on_output=None if args.json else print,
                  read_only=args.points)  # fmt: skip
    vs = conv.verdicts(lv, target)
    if args.json:
        print(json.dumps(conv.report(lv, vs, target), indent=1, default=str))
    else:
        print("\n".join(conv.lines(lv, vs)))
    return 0 if vs and all(v.state == "converged" for v in vs) else 1


def _adapt(args) -> int:
    """ADR 0066 phase 3: adaptive refinement passes (stored runs, resumed)."""
    import json

    from puzzle import adapt
    from puzzle.commands import Session, resolve
    from puzzle.model.schema import Adapt, Study
    from puzzle.project.layout import WORK_DIR

    s = Session(args.project)
    study = resolve(s.project, args.study, Study)
    a = study.adapt or Adapt()
    changes = {k: v for k, v in (("target", args.target), ("passes", args.budget),
                                 ("max_elements", args.max_elements)) if v}  # fmt: skip
    if args.delete_passes:
        changes["keep_passes"] = False
    if args.anisotropic:
        changes["method"] = "anisotropic"
    a = Adapt.model_validate({**a.model_dump(), **changes})
    out = adapt.run(s.project, study, args.project, args.project / WORK_DIR / "adapt"
                    / str(study.id), store=_store(args.project), settings=a,
                    on_output=None if args.json else print, read_only=args.points)  # fmt: skip
    if args.json:
        print(json.dumps(out.to_dict(), indent=1))
    else:
        print("\n".join(adapt.lines(out)))
    return 0 if out.converged else 1


def _error_estimate(args) -> int:
    """ADR 0066 phase 2: a study's newest run's error estimate, and where it is largest."""
    import json

    from puzzle.commands import Session, resolve
    from puzzle.model.schema import Study
    from puzzle.post import estimates
    from puzzle.post.derived import run_for

    s = Session(args.project)
    store = _store(args.project)
    study = resolve(s.project, args.study, Study)
    record = run_for(s.project, store, study.id)
    est = estimates.of_run(s.project, store, record.run_id, field_name=args.field)
    mesh = store.read_mesh(record.mesh_id)
    if args.json:
        print(json.dumps(estimates.summary(mesh, est, args.target), indent=1))
    else:
        print(f"{study.name}: {estimates.words(mesh, est, args.target)}")
        for spot in estimates.hot_spots(mesh, est):
            at = ", ".join(spot["selections"]) or "inside"
            xyz = ", ".join(f"{c:.4g}" for c in spot["centre"])
            print(f"  element {spot['element']}: {spot['error']:.3g} at ({xyz}) m, {at}")
        for note in est.notes:
            print(f"  note: {note}")
    return 0 if est.relative <= args.target or est.physics == "field" else 1


def _optimise(args) -> int:
    """ADR 0063: run an optimisation (stored points are reused: resume), list its points,
    or apply its best point."""
    import json

    from puzzle import optimise
    from puzzle.commands import Session, resolve
    from puzzle.model.schema import Optimisation
    from puzzle.project.layout import WORK_DIR

    s = Session(args.project)
    opt = resolve(s.project, args.name, Optimisation)
    store = _store(args.project)
    if args.apply:
        top = optimise.best(opt, optimise.points(s.project, store, opt))
        if top is None:
            print(f"{opt.name} has no feasible point yet")
            return 1
        s.save_version(label=f"Before {opt.name}'s best point")
        s.submit_batch(optimise.apply_commands(s.project, opt, top))
        print(f"applied point {top.index}: {top.title}")
        return 0
    if not args.points:
        optimise.run(s.project, opt, args.project, args.project / WORK_DIR / "optimise"
                     / str(opt.id), store=store, on_output=print,
                     budget=args.budget)  # fmt: skip
        opt = resolve(s.project, args.name, Optimisation)
    pts = optimise.points(s.project, store, opt)
    if args.json:
        print(json.dumps([p.to_dict() for p in pts], indent=1, default=str))
        return 0
    top = optimise.best(opt, pts)
    for p in pts:
        mark = "*" if p is top else " "
        status = "feasible" if p.feasible else (p.reason or "infeasible")
        obj = "" if p.objective is None else f"  objective {p.objective:.6g}"
        origin = f"  ({p.origin})" if p.origin else ""
        print(f"{mark} {p.index:3d}  {p.title}{obj}  [{status}]{origin}")
    if pts.ended:
        print(f"{optimise.METHODS[opt.method]}: {pts.ended}")
    if opt.method == "robust":
        got = optimise.robust_summary(s.project, opt, pts)
        if got:
            if got["labels"]:
                print("robust design: " + ", ".join(f"{k} = {v}" for k, v in got["labels"].items()))
            for k, row in got["outputs"].items():
                held = f", holds {row['holds'] * 100:.2f} %" if "holds" in row else ""
                print(f"  {k}: mean {row['mean']:.6g} ± {row['sd']:.6g} (SI), "
                      f"{row['low']:.6g} to {row['high']:.6g} at ±3σ{held}")  # fmt: skip
    if opt.method == "response_surface":
        fit = optimise.fit_quality(opt, pts)
        print("surface fit (leave-one-out): " + ", ".join(
            f"{k} {v * 100:.1f} %" for k, v in fit.items() if v is not None))  # fmt: skip
    if len(optimise.output_keys(opt)) > 1:
        front = optimise.pareto(opt, pts)
        if front:
            print("Pareto front: points " + ", ".join(str(p.index) for p in front))
    said = optimise.suggestion(s.project, opt, pts)
    if said:
        print(f"best: {said} (--apply to use it)")
    return 0


def _operators(args) -> int:
    """ADR 0062: each operator's wiring and value; --export writes export operators."""
    import json

    from puzzle.commands import resolve
    from puzzle.model.schema import Operator
    from puzzle.operators import describe
    from puzzle.operators import evaluate as ev

    project = _load(args.project)
    ops = ([resolve(project, args.name, Operator)] if args.name
           else project.of_type(Operator))  # fmt: skip
    ctx = ev.Context(project, _store(args.project))
    if args.export:
        for op in ops:
            if op.type in ("export", "report"):
                for path in ev.export(ctx, op, args.project):
                    print(f"wrote {path}")
        return 0
    if args.json:
        print(json.dumps([ev.summary(ctx, op) for op in ops], indent=1, default=str))
        return 0
    if not ops:
        print("no operators")
    for op in ops:
        text = ev.detail(ctx, op)
        print(f"{op.name}: {text}\n    {describe(project, op)}")
    return 0


def _table(args) -> int:
    """ADR 0064: a table operator's rows in the display units, or the whole of it to a
    file."""
    from puzzle.commands import resolve
    from puzzle.model.schema import Operator
    from puzzle.operators import evaluate as ev
    from puzzle.operators import frames

    project = _load(args.project)
    op = resolve(project, args.name, Operator)
    got = ev.value(ev.Context(project, _store(args.project), root=args.project), op)
    if not isinstance(got, ev.Table):
        print(f"{op.name} is not a table: {got.text()}")
        return 1
    for fmt in ("csv", "parquet", "xlsx"):
        path = getattr(args, fmt)
        if path is not None:
            frames.write(got, path, fmt)
            print(f"wrote {path} ({got.frame.height} rows)")
            return 0
    import polars as pl

    shown = frames.shown_frame(got)
    with pl.Config(tbl_rows=args.rows, tbl_cols=-1, tbl_width_chars=160):
        print(shown.head(args.rows))
    print(f"{got.frame.height} rows × {got.frame.width}")
    return 0


def _schematic(args) -> int:
    """ADR 0058: the schematic as text (columns, then what feeds and shares what), JSON or
    an SVG picture."""
    import json

    from puzzle import schematic
    from puzzle.settings import current

    project = _load(args.project)
    try:
        runs = _store(args.project).runs()
    except PuzzleError:  # an old store: the model still shows
        runs = []
    s = schematic.build(project, runs, root=args.project,
                        fields=bool(current().get("connections.schematic")))  # fmt: skip
    if args.svg:
        from puzzle.schematic import view_state
        from puzzle.schematic.layout import layout
        from puzzle.schematic.svg import to_svg

        view = view_state.load(args.project)
        lay = layout(s, view.positions, frozenset(view.unfolded))
        args.svg.write_text(
            to_svg(s, lay, "none" if args.no_uses else "all", project.name), encoding="utf-8"
        )
        print(f"saved {args.svg}")
    if args.json:
        print(json.dumps(s.to_dict(), indent=1))
        return 0
    if args.svg:
        return 0
    from puzzle.project.status import WORDS

    for c, column in enumerate(schematic.COLUMNS):
        nodes = [n for n in s.nodes.values() if n.column == c]
        if not nodes:
            continue
        print(f"{column}:")
        for n in nodes:
            extra = "  ".join(x for x in (n.subtitle, n.badge, *n.chips) if x)
            state = WORDS.get(n.state, n.state).lower()
            reason = f": {n.reason}" if n.reason else ""
            print(f"  {n.label}  [{state}{reason}]" + (f"  {extra}" if extra else ""))
    flow = s.flow_lines()
    if flow:
        print("Flow:")
        print("\n".join(f"  {line}" for line in flow))
    return 0


def _split_parts(args) -> int:
    """One part per body (ADR 0039), as one batch: one undo."""
    from puzzle.commands import resolve
    from puzzle.commands.session import Session
    from puzzle.model.schema import Component
    from puzzle.split_parts import plan

    session = Session(args.project)
    comp = resolve(session.project, args.component, Component)
    made = plan(session.project, comp, args.part, args.project, bond=not args.no_bond)
    print(made.summary())
    for note in made.notes:
        print(f"  note: {note}")
    if args.dry_run:
        return 0
    session.submit_batch(made.commands)
    print("done (puzzle undo takes it back)")
    return 0


def _contacts(args) -> int:
    """ADR 0056: the Contact tool's Setup and Initial state, and the checks."""
    import json

    from puzzle import contact_check
    from puzzle.mesh import generate_mesh
    from puzzle.model.schema import Component
    from puzzle.project.layout import WORK_DIR

    project = _load(args.project)
    comps = [c for c in project.of_type(Component)
             if (args.component in (None, c.name)) and c.contacts]  # fmt: skip
    if not comps:
        print(f"no component '{args.component}' with contacts" if args.component
              else "no contacts")  # fmt: skip
        return 1
    reports = []
    for comp in comps:
        out = args.project / WORK_DIR / "cli-mesh" / str(comp.id)
        mesh = generate_mesh(project, comp.id, args.project, out).mesh
        reports.append(contact_check.report(project, comp, mesh, args.solver))
    if args.json:
        print(json.dumps(reports, indent=2))
        return 0
    length = contact_check.shown_length
    for r in reports:
        print(f"{r['component']}:")
        for c in r["contacts"]:
            friction = f", friction {c['friction']:g}" if c["friction"] is not None else ""
            print(f"  {c['name']}: {c['type']}{friction}, {c['source']} -> {c['target']}")
            for solver, text in c["solver_input"].items():
                print(f"    {solver}: {text}")
            s = c.get("initial", {})
            if s.get("status"):
                gap = s["largest_gap_m"]
                print(f"    starts {s['status']}: {s['shares'].get('closed', 0):.0%} in touch, "
                      f"largest gap {length(gap) if gap is not None else '-'}, penetration "
                      f"{length(s['largest_penetration_m'])}, search distance "
                      f"{length(s['search_distance_m'])}")  # fmt: skip
            elif s.get("note"):
                print(f"    not checked: {s['note']}")
        for w in r["warnings"]:
            print(f"  warning: {w['message']}")
    return 0


def _import_field(args) -> int:
    from puzzle.commands import Session
    from puzzle.commands.model import Create
    from puzzle.fields import new_field_data, sniff

    _load(args.project)
    first, *more = args.file
    for path in args.file:
        if not path.exists():
            raise CliError(f"no file {path}")
    found = sniff(first)
    columns = {c["name"]: dict(c) for c in found["columns"]}
    for spec in args.column:
        name, _, rest = spec.partition(":")
        role, _, unit = rest.partition(":")
        if name not in columns:
            raise CliError(f"no column '{name}' in {first.name} ({', '.join(columns)})")
        columns[name].update({k: v for k, v in (("role", role), ("unit", unit)) if v})
    placement = {k: tuple(v) for k, v in (("translation", args.translate),
                 ("rotation", args.rotate), ("about", args.about)) if v}  # fmt: skip
    data = new_field_data(args.project, first, args.name, length_unit=args.unit,
                          columns=list(columns.values()), placement=placement or None,
                          region=args.region, index=args.step, files=more or None,
                          times=args.times, every_step=args.every_step,
                          frequencies=args.frequencies)  # fmt: skip
    Session(args.project).submit(Create(kind="field", data=data))
    values = [c["name"] for c in data["columns"] if c["role"] == "value"]
    print(f"Imported field '{data['name']}' from {data['file']}: values {', '.join(values)}; "
          f"points in {data['length_unit']}")  # fmt: skip
    if data.get("times") and data.get("axis") == "frequency":
        print("  varies with frequency: " + ", ".join(f"{t['time']:g} Hz" for t in data["times"]))
    elif data.get("times"):
        print("  varies in time: " + ", ".join(f"{t['time']:g} s" for t in data["times"]))
    return 0


def _field_check(args) -> int:
    import json
    import tempfile

    from puzzle.fields import check, target_points
    from puzzle.model.schema import ImportedField

    project = _load(args.project)
    f = next((x for x in project.of_type(ImportedField) if x.name == args.field), None)
    if f is None:
        raise CliError(f"no imported field '{args.field}'")
    targets = None
    if args.on:
        with tempfile.TemporaryDirectory(prefix="puzzle-field-") as work:
            targets = target_points(project, args.project, args.on, Path(work))
    tol = args.tolerance / 1000 if args.tolerance is not None else None
    report = check(project, args.project, f, targets, tol)
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    print(f"{f.name}: {report['points']} points" + (" (sparse)" if report["sparse"] else ""))
    for name, c in report["columns"].items():
        print(f"  {name}: {c['min']:.6g} to {c['max']:.6g}, mean {c['mean']:.6g} {c['unit']}")
    if "coverage" in report:
        far = report["largest_distance"] * 1e3
        print(f"  coverage of {args.on}: {report['coverage']:.1%} within "
              f"{report['tolerance'] * 1e3:.3g} mm; farthest {far:.3g} mm")  # fmt: skip
    for w in report["warnings"]:
        print(f"  warning: {w}")
    return 0


def _trust(args) -> int:
    from puzzle import trust
    from puzzle.project.store import load_project

    project = load_project(args.project)
    if args.revoke:
        trust.revoke(args.project)
        print(f"'{project.name}' is no longer trusted: its scripts and equations won't run")
        return 0
    held = trust.holds_code(project)
    trust.trust(args.project)
    print(f"'{project.name}' is trusted" + (f"; its code can run: {', '.join(held)}" if held
                                            else ""))  # fmt: skip
    return 0


def _jobs(args) -> int:
    """The project's solves with a job runner (ADR 0067): list, resume, cancel, dismiss."""
    from puzzle.jobs import overview

    root = args.project
    _load(root)  # a project, or a clear error
    if args.cancel:
        for e in overview.find(root, args.cancel):
            print(f"{e['job']}: {overview.cancel(root, e['job'])}")
        return 0
    if args.dismiss:
        for e in overview.find(root, args.dismiss):
            out = overview.dismiss(root, e["job"], delete_remote=args.delete_remote)
            done = ", ".join(k for k in ("cancelled", "forgotten", "deleted") if out[k])
            note = f" ({out['note']})" if out["note"] else ""
            print(f"Dismissed {e['job']}{': ' + done if done else ''}{note}")
        return 0
    if args.resume:
        from puzzle.commands import Session

        rows = overview.jobs(root, live=args.live)
        if args.resume != "all":
            wanted = {e["job"] for e in overview.find(root, args.resume)}
            rows = [r for r in rows if r.job in wanted]
        if not rows:
            print("No solves to pick up.")
            return 0
        failed = 0
        for r in overview.resume_order(rows):
            name = r.study or "a removed study"
            print(f"Picking up {name} ({r.job}) on {r.where} ...")
            status = None if args.verbose else _LiveStatus()
            try:
                out = Session(root).resume(r.job, on_output=print if args.verbose else None,
                                           on_event=status)  # fmt: skip
            except PuzzleError as exc:
                failed += 1
                print(f"  {name}: {exc.message} [{exc.code}]")
                continue
            finally:
                if status is not None:
                    status.close()
            run = out.run
            print(f"  {name}: home, run {run.run_id[:8]}" if run is not None and run.run_id
                  else f"  {name}: {out.status}")  # fmt: skip
        return 1 if failed else 0
    rows = overview.jobs(root, live=args.live)
    if args.json:
        print(json.dumps([r.to_dict() for r in rows], indent=2))
        return 0
    if not rows:
        print("No solves are with a job runner for this project.")
        return 0
    for r in rows:
        line = f"{r.job}  {r.study or '(removed study)'}  [{r.solver}]  {r.where}  {r.state}"
        print(line + (f"  {overview.words(r.elapsed)}" if r.elapsed is not None else ""))
        if r.last_line:
            print(f"    {r.last_line}")
        if r.message:
            print(f"    {r.message}")
    if any(r.state == overview.NOT_ASKED for r in rows):
        print("Remote hosts were not asked; --live asks them.")
    return 0


def _hosts(args) -> int:
    """The user's remote hosts: list, add (or replace), check, remove (ADR 0021)."""
    from puzzle import hosts

    if args.action == "list":
        known = hosts.load()
        if not known:
            print(f"no hosts yet ({hosts.hosts_path()}); add one with puzzle hosts add NAME "
                  "--address ADDRESS")  # fmt: skip
        for h in known.values():
            queue = "" if h.scheduler == "none" else f", {h.scheduler} {h.partition}".rstrip()
            print(f"{h.name}: {h.target} ({h.runtime}{queue}), cores {h.cores or 'all'}")
        return 0
    if not args.name:
        raise CliError("name the host")
    if args.action == "add":
        if not args.address:
            raise CliError("--address is needed: an ssh alias, host name or IP address")
        h = hosts.Host(name=args.name, address=args.address, user=args.user, port=args.port,
                       runtime=args.runtime, envs_dir=args.envs_dir, work_dir=args.work_dir,
                       cores=args.cores, memory_gb=args.memory_gb, scheduler=args.scheduler,
                       partition=args.partition, account=args.account,
                       walltime=args.walltime)  # fmt: skip
        print(f"saved {h.name} in {hosts.add(h)}")
        return 0
    if args.action == "remove":
        hosts.remove(args.name)
        print(f"removed {args.name}")
        return 0
    found = hosts.check(hosts.get(args.name))
    print(found.words())
    return 0 if found.reachable else 1


def _agent_pick(args) -> int:
    """Find, fit, measure and rank the endpoint's models; recommend one (ADR 0022)."""
    import sys as _sys

    from puzzle.agent import pick

    _sys.path.insert(0, str(Path(__file__).parents[2] / "tests"))
    try:
        from solvers import available  # CalculiX tasks only where it runs

        solvers = [s for s in ("calculix",) if available(s)]
    except ImportError:
        solvers = []

    def said(model, outcome) -> None:
        print(f"  {model} / {outcome.task}: {'pass' if outcome.passed else 'FAIL'} "
              f"({outcome.seconds:.0f} s)", flush=True)  # fmt: skip

    found = pick.pick(args.base_url, args.workdir, solvers=solvers, only=args.model,
                      again=args.again, on_task=said)  # fmt: skip
    for c in found.candidates:
        size = f"{c.memory / 1e9:.1f} GB" if c.memory else "size unknown"
        print(f"{c.name}: {size}" + ("" if c.fits else f", left out: {c.why}"))
    for m in found.ranked:
        print(f"  {m.model}: {m.passed}/{m.total} tasks, core {m.core_passed}/{m.core_total}, "
              f"{m.median_s:.0f} s a task")  # fmt: skip
    print(f"Recommended: {found.words}")
    if args.use and found.recommended:
        print(f"saved in {pick.choose(found.recommended, args.base_url)}")
    return 0 if found.recommended else 1


def _scripts(args) -> int:
    """List the scripts environment's packages, or add one (pixi, beside Puzzle's)."""
    import subprocess

    from puzzle.operators.scripts import ENVIRONMENT, python

    exe = python()
    if exe is None:
        print("no scripts environment: pixi install -e scripts", file=sys.stderr)
        return 1
    if args.action == "list":
        done = subprocess.run(["pixi", "list", "-e", ENVIRONMENT], capture_output=True,
                              text=True, encoding="utf-8", errors="replace")  # fmt: skip
        print(done.stdout or done.stderr)
        return done.returncode
    if not args.package:
        print("say which package to add", file=sys.stderr)
        return 2
    steps = [["pixi", "add", "--feature", ENVIRONMENT, args.package],
             ["pixi", "install", "-e", ENVIRONMENT]]  # fmt: skip
    for step in steps:
        done = subprocess.run(step)
        if done.returncode:
            return done.returncode
    print(f"{args.package} is in the scripts environment")
    return 0


def _materials_export(args) -> int:
    from puzzle.materials import exchange

    n = exchange.export(args.path, args.scope)
    print(f"exported {n} materials to {args.path}")
    return 0


def _materials_import(args) -> int:
    from puzzle.materials import exchange

    done = exchange.import_file(args.path, args.into, "replace" if args.replace else "keep")
    print(f"imported {args.path.name}: {done.summary()}")
    for key, why in sorted(done.invalid.items()):
        print(f"  {key}: {why}")
    if done.backup:
        print(f"the previous library is kept as {done.backup}")
    return 0


def _materials_remove(args) -> int:
    from puzzle.materials.store import LibraryStore

    store = LibraryStore()
    if args.list or not args.keys:
        for key in store.removed():
            print(key)
        return 0
    if args.restore:
        done = store.restore(args.keys)
        print(f"restored {len(done)}: {', '.join(done)}" if done else "none of those was removed")
        return 0
    done = store.remove_from_library(args.keys)
    print(f"removed {len(done)} from your library: {', '.join(done)}")
    return 0


def _materials_duplicates(args) -> int:
    from puzzle.materials import _library, duplicates

    lib = _library()
    groups = duplicates()
    for keys in groups:
        print("same values:")
        for key in keys:
            print(f"  {key}  ({lib[key]['name']})")
    print(f"{len(groups)} groups")
    return 0


def _machine(args) -> int:
    from puzzle.jobs import machine

    if args.action == "list":
        found = machine.installed()
        if machine.wsl() is None:
            print("WSL isn't here (Windows only; `wsl --install` in an administrator terminal)")
        for solver, data in found.items():
            print(f"{solver:13} {data['distro']}  from {data.get('from', '?')}")
        if not found:
            print("no solvers in Puzzle's Linux machine yet: `puzzle machine install SOLVER`")
        return 0
    if not args.solver:
        raise CliError(f"{args.action} needs the solver")
    try:
        if args.action == "remove":
            machine.remove(args.solver)
            print(f"removed {machine.distro_for(args.solver)}")
            return 0
        if args.tarball:
            data = machine.install_from_tarball(args.solver, args.tarball)
        else:
            data = machine.install_from_image(args.solver, args.image,
                                              say=lambda line: print(line, flush=True))  # fmt: skip
    except machine.MachineError as exc:
        raise CliError(str(exc)) from None
    print(f"{args.solver} runs in {data['distro']} now")
    return 0


def _clusters(args) -> int:
    from puzzle.operators import clusters

    found = clusters.templates()

    def key_of(name):
        if name in found:
            return name
        hits = [k for k, t in found.items() if t["name"] == name]
        if len(hits) != 1:
            raise CliError(f"no single cluster named '{name}'")
        return hits[0]

    if args.action == "list":
        for k, t in found.items():
            mine = "yours " if k.startswith("mine:") else "recipe"
            ports = ", ".join(f"{p['name']} ({p['type']})" for p in t.get("ports", []))
            print(f"{mine}  {t['name']}  [{k}]" + (f"  ports: {ports}" if ports else ""))
        return 0
    if args.action == "import":
        target = args.file or (Path(args.name) if args.name else None)
        if target is None:
            raise CliError("import needs the block file")
        key = clusters.import_file(target)
        print(f"imported {clusters.templates()[key]['name']} as {key}")
        return 0
    if not args.name:
        raise CliError(f"{args.action} needs the cluster's name")
    key = key_of(args.name)
    if args.action == "remove":
        clusters.remove(key)
        print(f"removed {args.name}")
        return 0
    print(f"wrote {clusters.export(key, args.file or Path.cwd())}")
    return 0


def _solvers(args) -> int:
    import json
    from dataclasses import asdict

    from puzzle import solver_builds

    if args.repair:
        from puzzle import solver_repair

        try:
            solver_repair.repair(args.repair, say=lambda line: print(line, flush=True))
        except solver_repair.RepairError as exc:
            raise CliError(str(exc)) from None
        return 0
    if args.clear_wait:
        cleared = solver_builds.clear_wait()
        print("Cleared: the build is tried the next time Puzzle opens" if cleared
              else "No wait to clear: the build hasn't failed")  # fmt: skip
        return 0
    if args.build:
        done = solver_builds.build(say=lambda line: print(line, flush=True))
        print(f"PARDISO: {done.state}: {done.detail}")
        return 0 if done.state == "built" else 1
    rows = solver_builds.solvers()
    pardiso = solver_builds.status()
    if args.json:
        print(json.dumps({"solvers": [asdict(r) for r in rows],
                          "pardiso": asdict(pardiso)}, indent=1))  # fmt: skip
        return 0
    words = {
        "native": "native",
        "container": "in a container",
        "missing": "not available",
        "machine": "Linux machine",
    }
    for r in rows:
        note = f"  [{r.note}]" if r.note else ""
        print(f"{r.name:13} {words[r.how]:15} {r.where}{note}")
    print(f"CalculiX with PARDISO: {pardiso.state}: {pardiso.detail}")
    return 0


def _settings(args) -> int:
    import json

    from puzzle.settings import BY_KEY, PREFERENCES, Settings

    s = Settings()
    if args.key is None:
        for p in PREFERENCES:
            value = s.get(p.key)
            shown = next((t for t, v in p.choices if v == value), value)
            note = "" if p.ready else "  (not used yet)"
            print(f"{p.key} = {json.dumps(value)}    {p.group}: {p.label}: {shown}{note}")
        return 0
    pref = BY_KEY.get(args.key)
    if pref is None:
        print(f"no setting '{args.key}'")
        return 1
    if args.value is None:
        print(f"{pref.label} ({pref.key}), now {json.dumps(s.get(pref.key))}:")
        for text, value in pref.choices:
            print(f"  {json.dumps(value)}: {text}")
        return 0
    value = args.value
    if pref.choices and isinstance(pref.choices[0][1], bool):
        value = value.lower() in ("true", "yes", "1", "on")
    elif pref.choices:  # a number typed for numeric choices (bands 9, size 1.5)
        value = next(
            (
                v
                for _, v in pref.choices
                if str(v) == value
                or json.dumps(v) == value
                or (isinstance(v, int | float) and _number(value) == v)
            ),
            value,
        )
    try:
        s.set(pref.key, value)
    except ValueError as exc:
        print(exc)
        return 1
    s.save()
    print(f"{pref.label}: {next(t for t, v in pref.choices if v == value)}")
    return 0


def _agent_key(args) -> int:
    """Keys go to the OS keychain from a hidden prompt: never a file, never argv."""
    import getpass

    from puzzle.agent.providers import ProviderError
    from puzzle.agent.settings import AgentSettings, anthropic_key_problem, delete_key, store_key

    if args.action == "set":
        key = getpass.getpass(f"{args.provider} API key (input hidden): ").strip()
        if not key:
            print("nothing entered; no key stored")
            return 1
        if args.provider == "anthropic":
            from puzzle.agent.providers import check_anthropic_key

            problem = anthropic_key_problem(key)
            if problem:
                print(f"not stored: {problem}")
                return 1
            try:
                auth = check_anthropic_key(key)
            except ProviderError as exc:
                print(f"not stored: {exc}")
                return 1
            how = "x-api-key" if auth == "api_key" else "bearer token"
            print(f"Anthropic accepts the key ({how})")
        try:
            store_key(args.provider, key)
            if args.provider == "anthropic":
                store_key("anthropic-auth", auth)
        except ProviderError as exc:
            print(exc)
            return 1
        print(f"stored in the keychain (service 'puzzle', account '{args.provider}')")
    elif args.action == "clear":
        removed = delete_key(args.provider)
        delete_key(f"{args.provider}-auth")
        print("removed from the keychain" if removed else "no key in the keychain")
    print(f"{args.provider} key: {AgentSettings(provider=args.provider).key_source()}")
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
            print(f"  {step.label()}")
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
        if "joule_loss" in step.fields:  # ADR 0047: the total is the first entry
            parts.append(f"loss {step.fields['joule_loss'].values[0, 0]:.4g} W")
        if "coil_resistance" in step.fields:
            r, ind = step.fields["coil_resistance"], step.fields["coil_inductance"]
            parts += [f"{c}: R {r.values[0, k]:.4g} Ω, L {ind.values[0, k]:.4g} H"
                      for k, c in enumerate(r.components)]  # fmt: skip
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
