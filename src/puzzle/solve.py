"""Run one study end to end: mesh, write solver input, solve, read and store results.

A study that takes transfers (ADR 0009) first runs, or reuses, each source study; the
transferred fields are mapped onto its mesh (puzzle.transfer) and handed to its adapter.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

from puzzle.adapters import available_adapters
from puzzle.errors import PuzzleError
from puzzle.jobs import JobResult, run_job, runtime_for
from puzzle.jobs.runner import log_tail
from puzzle.mesh import MeshResult, generate_mesh
from puzzle.mesh.mesh import Mesh
from puzzle.model.schema import Project, Study
from puzzle.project.graph import run_hashes
from puzzle.results.model import Results
from puzzle.results.store import ResultStore
from puzzle.transfer import Input, map_field, pick


class StudyError(PuzzleError, RuntimeError):
    code = "inconsistent"


@dataclass
class StudyRun:
    mesh: Mesh
    results: Results
    run_key: str
    run_id: str | None = None  # set when the run is in a result store
    cached: bool = False  # True when an identical earlier run was reused
    mesh_result: MeshResult | None = None  # meshing details, for fresh runs
    job: JobResult | None = None  # solver process details, for fresh runs
    input_file: Path | None = None
    # A swept study (ADR 0018): one run per point, with the point in words.
    points: list[tuple[dict[str, str], StudyRun]] | None = None
    # Seconds per stage of a fresh run (mesh, write, solve, read, store): where the
    # time goes around the solver, as well as in it.
    timings: dict[str, float] = field(default_factory=dict)
    cores: str = ""  # what the solve used: "4 cores", "2 processes x 2 threads"
    events: list = field(default_factory=list)  # progress events (ADR 0054)


def _cores(adapter) -> str:
    """What a run may use, in words (Settings > Solvers, ADR 0053)."""
    from puzzle.jobs.runtime import thread_count, threads_per_process

    n = adapter.processes()
    if n > 1:
        return f"{n} processes x {threads_per_process(n)} threads"
    return f"{thread_count()} cores"


def run_study(
    project: Project,
    study_id,
    root: Path,
    workdir: Path,
    *,
    store: ResultStore | None = None,
    reuse: bool = True,
    on_output: Callable[[str], None] | None = None,
    timeout_s: float | None = None,
    upstream: dict | None = None,
    solver: str | None = None,
    meshed: MeshResult | None = None,
    _point: tuple[str, dict[str, str]] | None = None,
    on_event: Callable | None = None,
    cancel: threading.Event | None = None,
) -> StudyRun:
    """Run `study_id` with its primary solver. Geometry paths resolve against `root`.

    With a `store`, the run is saved there, and when `reuse` is set an earlier run
    with the same inputs (same run key, ADR 0009) is returned instead of solving again.
    `solver` runs one of the study's shoot-out solvers instead, and `meshed` reuses a
    mesh already generated for this study (a shoot-out runs every solver on one mesh).
    `on_event` gets the run's progress events (ADR 0054: stages, steps, increments,
    iterations, messages), which are also kept in the run folder's `progress.jsonl` and
    with the stored run; setting `cancel` stops the solver (code `stopped`).
    """
    from puzzle.bolts import resolved

    project = resolved(project)  # bolts take their types' values (ADR 0050)
    from puzzle.suppress import applied

    project = applied(project)  # suppressed geometry is left out of meshing and solving
    study = project.get(study_id, Study)
    if solver is not None and solver != study.solver:
        if solver not in study.all_solvers():
            raise StudyError(
                f"{solver} is neither the primary nor a shoot-out solver of this study",
                code="invalid",
                path=study.name,
            )
        study = study.model_copy(update={"solver": solver})
    if study.sweeps():
        return _run_sweep(
            project,
            study,
            root,
            workdir,
            store=store,
            reuse=reuse,
            on_output=on_output,
            timeout_s=timeout_s,
            solver=study.solver,
            on_event=on_event,
            cancel=cancel,
        )
    adapters = available_adapters()
    if study.solver not in adapters:
        raise StudyError(
            f"no adapter installed for {study.solver}", code="unsupported", path=study.name
        )
    adapter = adapters[study.solver]()
    problems = project.validate() + adapter.check(project, study)
    if problems:
        raise StudyError("; ".join(problems), path=study.name, details={"problems": problems})

    run_key = _point[0] if _point else run_hashes(project)[study.id][study.solver][0]
    if store is not None and reuse:
        hit = store.find_run(run_key)
        if hit is not None:
            return StudyRun(
                mesh=store.read_mesh(hit.mesh_id),
                results=store.read_results(hit.run_id),
                run_key=run_key,
                run_id=hit.run_id,
                cached=True,
            )

    workdir.mkdir(parents=True, exist_ok=True)
    # Source studies first (each run once per chain, or reused from the store).
    upstream = {} if upstream is None else upstream
    for t in project.transfers_into(study.id):
        if t.source not in upstream:
            source = project.get(t.source, Study)
            upstream[t.source] = run_study(
                project,
                t.source,
                root,
                workdir / "upstream" / _slug(source.name),
                store=store,
                reuse=reuse,
                on_output=on_output,
                timeout_s=timeout_s,
                upstream=upstream,
                on_event=on_event,
                cancel=cancel,
            )
    started = datetime.now(UTC)
    timings: dict[str, float] = {}
    clock = time.perf_counter()
    from puzzle import progress

    record = progress.Recorder(on_event)
    record.write_to(workdir)

    def begin(stage: str) -> None:
        record(progress.Stage(name=stage, state="start"))

    def lap(stage: str) -> None:
        nonlocal clock
        now = time.perf_counter()
        timings[stage] = timings.get(stage, 0.0) + now - clock
        clock = now
        record(progress.Stage(name=stage, state="end", seconds=round(timings[stage], 3)))

    begin("mesh")
    if meshed is None:
        meshed = generate_mesh(project, study.component, root, workdir / "mesh")
    lap("mesh")
    begin("transfers")
    inputs = [
        Input(
            map_field(
                pick(upstream[t.source].results, t.field, t.source_step),
                upstream[t.source].mesh,
                meshed.mesh,
                t.mapping,
            ),
            t.role,
            t.target_selection,
            t.name,
            t.source,
        )
        for t in project.transfers_into(study.id)
    ]
    lap("transfers")
    from puzzle.bolts import with_joints

    # ADR 0050: bolts that bear on a head or nut annulus or a tapped thread get those
    # faces as mesh groups, and their ends point at them.
    written, project = with_joints(project, study.component, meshed.mesh)
    from puzzle.beams import with_orientations

    # ADR 0055: beams oriented by a system, a point, a face or an edge, as vectors.
    written, project = with_orientations(project, study.component, written)
    # An adapter that writes one solve step per input runs a multi-step study one step
    # at a time on the same mesh; the steps are independent analyses.
    steps = study.solve_steps()
    limit = getattr(adapter, "max_solve_steps", None)
    if limit is None or len(steps) <= limit:
        parts = [(study, workdir)]
    else:
        sweeps = study.sweeps()
        parts = [
            # A preloaded buckling step takes its static step with it (ADR 0028).
            (
                study.model_copy(
                    update={
                        "steps": [*sweeps, *([steps[n - 2]] if _preloaded(steps, n) else []), step]
                    }
                ),
                workdir / f"step-{n}",
            )
            for n, step in enumerate(steps, start=1)
        ]
    runtime = runtime_for(study.solver)
    results, job, input_file = None, None, None
    for n, (part, where) in enumerate(parts, start=1):
        where.mkdir(parents=True, exist_ok=True)
        # One job per step (or a one-step study): the job sees only the features that
        # apply in its step (Feature.steps, ADR 0037). One deck for all steps (CalculiX)
        # sorts them out itself.
        indices = [n - 1, n] if limit is not None and _preloaded(steps, n) else [n]
        seen = _in_step(project, study.component, indices) if limit is not None else project
        begin("write")
        input_file = adapter.write_input(seen, part, written, where, inputs)
        lap("write")
        begin("solve")
        parser = adapter.progress(input_file, n, len(parts))
        if parser is not None:  # probes followed while solving (ADR 0054)
            from puzzle.trackers import trackers

            parser.trackers, notes = trackers(seen, part, written)
            for note in notes:
                record(progress.Message(level="info", text=f"Not tracked: {note}"))
        job = run_job(
            adapter.command(input_file),
            where,
            runtime,
            on_output=on_output,
            timeout_s=timeout_s,
            cancel=cancel,
            parser=parser,
            on_event=record,
        )
        lap("solve")
        if job.cancelled:
            raise StudyError(
                f"{study.name} was stopped", code="stopped", path=study.name,
                details={"log": str(job.log), "progress": str(workdir / progress.FILE)},
            )  # fmt: skip
        if not job.ok:
            tail = log_tail(job.log)
            raise StudyError(
                f"{study.solver} failed (exit {job.returncode}):\n" + "\n".join(tail),
                code="solver_failed",
                path=study.name,
                details={
                    "exit_code": job.returncode,
                    "log": str(job.log),
                    "log_tail": tail,
                    "progress": progress.summary(record.events),
                },
            )
        begin("read")
        part_results = adapter.read_results(input_file)
        lap("read")
        if len(parts) > 1:
            for step in part_results.steps:
                step.index = n
        if results is None:
            results = part_results
        else:
            results.steps += part_results.steps

    kinds = [s.type for s in study.solve_steps()]
    for step in results.steps:
        if 1 <= step.index <= len(kinds):
            step.kind = kinds[step.index - 1]

    run_id = None
    if store is not None:
        begin("store")
        run_id = store.write_run(
            run_key=run_key,
            study_id=str(study.id),
            study_name=study.name,
            solver=study.solver,
            runtime=job.runtime,
            mesh=meshed.mesh,
            results=results,
            started_at=started,
            duration_s=(datetime.now(UTC) - started).total_seconds(),
            point=_point[1] if _point else None,
        )
        lap("store")
        store.write_progress(run_id, record.events)
    cores = _cores(adapter)
    if on_output is not None and timings:
        on_output("Time: " + ", ".join(f"{k} {v:.1f} s" for k, v in timings.items())
                  + f" ({cores})")  # fmt: skip
    return StudyRun(
        mesh=meshed.mesh,
        results=results,
        run_key=run_key,
        run_id=run_id,
        mesh_result=meshed,
        job=job,
        input_file=input_file,
        timings=timings,
        cores=cores,
        events=record.events,
    )


def _run_sweep(
    project, study, root, workdir, *, store, reuse, on_output, timeout_s, solver,
    on_event=None, cancel=None,
):  # fmt: skip
    """Each point of the sweep as its own run (cached by the point's run key). A sweep
    of materials only meshes once: the geometry is the same at every point."""
    from puzzle import sweep

    keys = run_hashes(project)[study.id][solver]
    runs: list[tuple[dict[str, str], StudyRun]] = []
    shared_mesh = None
    for i, (point, key) in enumerate(zip(study.sweep_points(), keys, strict=True), start=1):
        p = sweep.apply(project, study, point)
        words = sweep.labels(project, point)
        if on_output:
            on_output(f"--- {study.name}: point {i} of {len(keys)}: {sweep.title(words)}")
        run = run_study(
            p,
            study.id,
            root,
            workdir / f"point-{i}",
            store=store,
            reuse=reuse,
            on_output=on_output,
            timeout_s=timeout_s,
            solver=solver,
            meshed=shared_mesh,
            _point=(key, words),
            on_event=on_event,
            cancel=cancel,
        )
        if sweep.materials_only(study) and run.mesh_result is not None:
            shared_mesh = run.mesh_result
        runs.append((words, run))
    last = runs[-1][1]
    return StudyRun(
        mesh=last.mesh,
        results=last.results,
        run_key=last.run_key,
        run_id=last.run_id,
        cached=all(r.cached for _, r in runs),
        mesh_result=last.mesh_result,
        job=last.job,
        input_file=last.input_file,
        points=runs,
    )


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-") or "study"


def _preloaded(steps, n: int) -> bool:
    """Whether solve step n is a buckling step held on the static step before it."""
    step = steps[n - 1]
    return (
        step.type == "buckling"
        and bool(step.settings.get("preload", False))
        and n > 1
        and steps[n - 2].type == "stationary"
    )


def _in_step(project: Project, component_id, indices: list[int]) -> Project:
    """A shallow copy of the project whose component keeps only the features active in
    the given solve steps, renumbered 1, 2, ... as the job's own steps; the same project
    when every feature applies in every step."""
    comp = project.objects.get(component_id)
    if comp is None or not any(f.steps for ph in comp.physics for f in ph.features):
        return project

    def renumbered(f):
        active = [k for k, i in enumerate(indices, start=1) if f.active(i)]
        if not active:
            return None
        return f.model_copy(update={"steps": [] if len(active) == len(indices) else active})

    physics = [
        ph.model_copy(
            update={"features": [g for g in map(renumbered, ph.features) if g is not None]}
        )
        for ph in comp.physics
    ]
    copy = replace(project, objects=dict(project.objects))
    copy.objects[component_id] = comp.model_copy(update={"physics": physics})
    return copy
