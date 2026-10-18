"""The `puzzle` command line.

    puzzle studies PROJECT                       list the project's studies
    puzzle run PROJECT --study NAME              solve (or reuse an identical earlier run)
    puzzle runs PROJECT                          list stored runs
    puzzle show PROJECT --study NAME [options]   view a result, or save it with --screenshot
    puzzle export PROJECT --study NAME OUT.pvd   write .vtu/.pvd files for ParaView

PROJECT is a project directory (one TOML file per object). Results go to
PROJECT/results, solver working files to PROJECT/.puzzle/runs.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from puzzle.model.schema import Project, Study
from puzzle.project.store import load_project
from puzzle.results.store import ResultStore, RunRecord

RESULTS_DIR = "results"
WORK_DIR = ".puzzle/runs"


class CliError(Exception):
    pass


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return args.handler(args)
    except CliError as exc:
        print(f"puzzle: {exc}", file=sys.stderr)
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
    return parser


# --- commands -------------------------------------------------------------------


def _studies(args) -> int:
    project = _load(args.project)
    for s in project.of_type(Study):
        steps = ", ".join(step.type for step in s.steps) or "no steps"
        print(f"{s.name}  [{s.solver}]  {steps}")
    return 0


def _run(args) -> int:
    from puzzle.solve import StudyError, run_study

    project = _load(args.project)
    study = _study(project, args.study)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    workdir = args.project / WORK_DIR / f"{_slug(study.name)}-{stamp}"
    print(f"Running '{study.name}' with {study.solver} ...")
    try:
        run = run_study(
            project,
            study.id,
            args.project,
            workdir,
            store=_store(args.project),
            reuse=not args.no_reuse,
            on_output=print if args.verbose else None,
            timeout_s=args.timeout,
        )
    except (StudyError, FileNotFoundError, ValueError) as exc:
        raise CliError(str(exc)) from exc

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


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-") or "study"


if __name__ == "__main__":
    raise SystemExit(main())
