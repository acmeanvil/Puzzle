"""Run one study end to end: mesh, write solver input, solve, read and store results.

A study that takes transfers (ADR 0009) first runs, or reuses, each source study; the
transferred fields are mapped onto its mesh (puzzle.transfer) and handed to its adapter.
"""

from __future__ import annotations

import shutil
import tempfile
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


def run_study(project: Project, study_id, root: Path, workdir: Path, *, host: str | None = None,
              **kwargs) -> StudyRun:  # fmt: skip
    """Run `study_id` (see `_run_study`), on a remote host when its `remote` names one
    or `host` does (ADR 0021): every job's runtime is then that host's, and studies it
    takes transfers from run where their own `remote` says."""
    from puzzle.jobs.runtime import on_host

    study = project.get(study_id, Study)
    name = host or (study.remote.host if study.remote is not None else None)
    if not name:
        with on_host(None):
            return _run_study(project, study_id, root, workdir, **kwargs)
    from puzzle import hosts
    from puzzle.jobs.remote import Resources

    target = hosts.get(name)
    resources = study.remote.resources() if study.remote is not None else Resources()
    with on_host(target, resources):
        return _run_study(project, study_id, root, workdir, **kwargs)


def _run_study(
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
    from puzzle import operators

    project = operators.resolved(project)  # sweeps take their lists' values (ADR 0062)
    project = operators.with_parameters(project)  # values wired to parameters (ADR 0071)
    from puzzle.suppress import applied

    project = applied(project)  # suppressed geometry is left out of meshing and solving
    from puzzle.axes import resolved_project

    project = resolved_project(project, root)  # axes from geometry, parts, others (ADR 0049)
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
    comp = project.objects.get(study.component)
    if comp is not None and any(p.type == "custom_pde" and p.equation for p in comp.physics):
        from puzzle import trust

        try:  # ADR 0062: a weak form is code, run in a trusted project only
            trust.require(project, "custom PDE equation")
        except PuzzleError as exc:
            raise StudyError(exc.message, code=exc.code, path=study.name) from None
    keyed = project  # run keys come from the study as the user set it up
    from puzzle import preload

    # Bolts tightened in a step of their own before the loads, then locked (ADR 0037).
    project, study, preload_at = preload.applied(project, study)
    problems = project.validate() + adapter.check(project, study)
    if problems:
        raise StudyError("; ".join(problems), path=study.name, details={"problems": problems})

    run_key = _point[0] if _point else run_hashes(keyed)[study.id][study.solver][0]
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
        if study.morph is not None:  # ADR 0065 phase 2: moved nodes, the same mesh
            import dataclasses

            from puzzle.mesh.morph import morphed

            meshed = dataclasses.replace(meshed, mesh=morphed(project, study, meshed.mesh))
            record(progress.Message(text="mesh morphed: " + ", ".join(
                f"{h.selection} by {h.distance}" for h in study.morph.handles)))  # fmt: skip
    lap("mesh")
    begin("transfers")
    inputs, heating = [], []
    for t in project.transfers_into(study.id):
        picked = pick(upstream[t.source].results, t.field, t.source_step)
        if t.field == "loss_density":  # ADR 0047: losses heat the target body
            from puzzle.transfer import heat_from_losses

            given, feature = heat_from_losses(t, picked, upstream[t.source].mesh, meshed.mesh)
            inputs.append(given)
            heating.append(("heat_transfer", feature))
            continue
        if t.field == "lorentz_force_density":  # ADR 0047: forces load the target body
            from puzzle.transfer import forces_from_lorentz

            given, feature = forces_from_lorentz(t, picked, upstream[t.source].mesh,
                                                 meshed.mesh)  # fmt: skip
            inputs += given
            heating.append(("solid_mechanics", feature))
            continue
        inputs.append(Input(map_field(picked, upstream[t.source].mesh, meshed.mesh, t.mapping),
                            t.role, t.target_selection, t.name, t.source))  # fmt: skip
    from puzzle.fields import field_inputs

    # ADR 0044: imported fields and body temperatures, mapped onto this mesh.
    made = field_inputs(project, root, study, meshed.mesh)
    if any(i.field.name == "temperature" for i in made) and any(
        i.field.name == "temperature" for i in inputs
    ):
        raise StudyError(
            f"study '{study.name}' takes a temperature load from a transfer and from a body "
            "temperature; keep one",
            code="invalid_input",
        )
    inputs += made
    if heating:  # the heat generation each transferred loss drives, in the target's heat
        project = _with_heat_sources(project, study, heating)
    lap("transfers")
    from puzzle.bolts import with_joints

    # ADR 0050: bolts that bear on a head or nut annulus or a tapped thread get those
    # faces as mesh groups, and their ends point at them.
    written, project = with_joints(project, study.component, meshed.mesh)
    from puzzle.beams import with_orientations

    # ADR 0055: beams oriented by a system, a point, a face or an edge, as vectors.
    written, project = with_orientations(project, study.component, written)
    from puzzle.symmetry import applied as with_symmetry

    # ADR 0081: symmetry planes as supports on each face's normal.
    project = with_symmetry(project, study.component, written, adapter.capabilities)
    from puzzle import weak_springs

    # ADR 0080: bodies held only by sliding contact get weak springs until it closes.
    written, project, springs = weak_springs.applied(
        project, study, written, adapter.capabilities, record
    )
    written = _adjust_contacts(project, study, written, record)
    _contact_warnings(project, study, written, record)
    _magnetic_warnings(project, study, written, record)
    # An adapter that writes one solve step per input runs a multi-step study one step
    # at a time on the same mesh; the steps are independent analyses.
    steps = study.solve_steps()
    limit = getattr(adapter, "max_solve_steps", None)
    chained = limit is not None and len(steps) > limit and adapter.chains(project, study)
    if limit is None or len(steps) <= limit or chained:  # chained: static steps, one job
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
        split = limit is not None and not chained
        seen = _in_step(project, study.component, indices) if split else project
        begin("write")
        from puzzle.jobs import pending

        if pending.attached(where):  # resuming (ADR 0067): the job's folder is left alone
            scratch = Path(tempfile.mkdtemp(prefix=".resume-", dir=where.parent))
            try:
                made = adapter.write_input(
                    seen, part, written, scratch, _step_inputs(inputs, n, parts, steps)
                )
                input_file = where / made.relative_to(scratch)
            finally:
                shutil.rmtree(scratch, ignore_errors=True)
        else:
            input_file = adapter.write_input(
                seen, part, written, where, _step_inputs(inputs, n, parts, steps)
            )
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
            if job.outcome in ("walltime_exceeded", "out_of_memory", "job_lost"):
                raise StudyError(  # the host ended it, not the solver (ADR 0067)
                    job.message or f"{study.solver} ended on its host: {job.outcome}",
                    code=job.outcome, path=study.name,
                    details={"log": str(job.log), "log_tail": tail},
                )  # fmt: skip
            from puzzle.jobs.remote import RemoteRuntime, failure

            if isinstance(runtime, RemoteRuntime):  # ADR 0021: the host's own failures
                said = failure(tail, job.returncode, runtime.host)
                if said is not None:
                    raise StudyError(said.message, code=said.code, path=study.name,
                                     details={**(said.details or {}), "log": str(job.log),
                                              "log_tail": tail})  # fmt: skip
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
        from puzzle.post.contact import finish as contact_fields

        # ADR 0056: contact pressure, status, gap and sliding on the slave nodes.
        contact_fields(part_results, seen, seen.objects[study.component], written)
        from puzzle.post.thread_fields import finish as thread_fields

        # ADR 0076: each threaded contact's flank forces, per-thread load and wedge.
        thread_fields(part_results, seen, seen.objects[study.component], written)
        from puzzle.post.weld_fields import finish as weld_fields

        # ADR 0078: each light weld's line force at its stations.
        weld_fields(part_results, seen, seen.objects[study.component], written)
        # ADR 0080: the force the weak springs carry, and a warning where it is the load.
        weak_springs.finish(part_results, springs, study.weak_spring_limit, record)
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
        if step.index == preload_at:  # shown as "Preload" (StepResult.label)
            step.kind = preload.KIND

    run_id = None
    if store is not None:
        begin("store")
        from puzzle.jobs.versions import identify

        version, digest = identify(study.solver)  # ADR 0072: which solver ran
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
            solver_version=version,
            image_digest=digest,
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


def _run_sweep(project, study, root, workdir, **kwargs):
    """Each point of the sweep as its own run (cached by the point's run key). A sweep
    of materials only meshes once: the geometry is the same at every point. Container
    solves share one running container per image (ADR 0072, `jobs.warm`)."""
    from puzzle.jobs.warm import batch

    with batch(workdir):
        return _sweep_points(project, study, root, workdir, **kwargs)


def _sweep_points(
    project, study, root, workdir, *, store, reuse, on_output, timeout_s, solver,
    on_event=None, cancel=None,
):  # fmt: skip
    """`_run_sweep`'s points."""
    from puzzle import sweep

    keys = run_hashes(project)[study.id][solver]
    runs: list[tuple[dict[str, str], StudyRun]] = []
    shared_mesh = None
    for i, (point, key) in enumerate(zip(study.sweep_points(), keys, strict=True), start=1):
        p = sweep.apply(project, study, point)
        words = sweep.labels(project, point)
        if on_output:
            on_output(f"--- {study.name}: point {i} of {len(keys)}: {sweep.title(words)}")
        # _run_study, not run_study: the host the sweep runs on (Study.remote or the
        # caller's host=) is already set around it, and run_study would reset it.
        run = _run_study(
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


def _step_inputs(inputs: list, n: int, parts: list, steps: list) -> list:
    """The inputs a job sees (ADR 0044): one job per step (or a one-step study) gets its
    own step's values of a field varying in time as plain inputs; one deck for every
    step (CalculiX) gets them all, tagged by step."""
    if len(parts) != len(steps) or not any(i.step is not None for i in inputs):
        return inputs
    from dataclasses import replace

    return [replace(i, step=None) for i in inputs if i.step in (None, n)]


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


def _contact_warnings(project, study, mesh, record) -> None:
    """ADR 0056: the contacts' checks on the mesh solved, logged before the solve (as
    warnings: they don't stop it). A check that fails never stops a run either."""
    from puzzle import contact_check, progress

    comp = project.objects.get(study.component)
    if comp is None or not comp.contacts:
        return
    try:
        found = contact_check.checks(project, comp, mesh, study.solver)
    except Exception as exc:  # noqa: BLE001 - a check, not the solve
        record(progress.Message(level="info", text=f"Contacts not checked: {exc}"))
        return
    for w in found:
        record(progress.Message(level="warning", text=w.message, code=w.code))


def _with_heat_sources(project, study, features):
    """A copy of the project whose study component's physics have the features
    transferred fields drive (ADR 0047): a loss density's heat generation in its heat
    physics, a Lorentz force density's body force in its structure."""
    from puzzle.model.schema import Component

    comp = project.get(study.component, Component)
    physics = list(comp.physics)
    for kind, feature in features:
        at = next((i for i, p in enumerate(physics) if p.type == kind), None)
        if at is None:
            raise StudyError(f"study '{study.name}' takes a {feature.type.replace('_', ' ')} "
                             f"field but solves no {kind.replace('_', ' ')}",
                             code="invalid_input")  # fmt: skip
        physics[at] = physics[at].model_copy(update={"features": [*physics[at].features, feature]})
    copy = replace(project, objects=dict(project.objects))
    copy.objects[comp.id] = comp.model_copy(update={"physics": physics})
    return copy


def _magnetic_warnings(project, study, mesh, record) -> None:
    """ADR 0047: the skin depth against the elements meshed, and the air around the
    coils, logged before the solve (warnings: they don't stop it)."""
    from puzzle import magnetics, progress

    comp = project.objects.get(study.component)
    if comp is None or not any(p.type == "magnetic_fields" for p in comp.physics):
        return
    try:
        found = magnetics.mesh_warnings(project, study, mesh)
    except Exception as exc:  # noqa: BLE001 - a check, not the solve
        record(progress.Message(level="info", text=f"Magnetic checks not made: {exc}"))
        return
    for w in found:
        record(progress.Message(level="warning", text=w.message, code=w.code))


def _adjust_contacts(project, study, mesh, record):
    """ADR 0056: contacts adjusted to touch, or given an offset, get their source nodes
    moved in the mesh written, for every solver alike; logged as it is done."""
    from puzzle import contact_check, progress

    comp = project.objects.get(study.component)
    wanted = ("adjust", "offset")
    if comp is None or not any(getattr(c, "interface", "") in wanted for c in comp.contacts):
        return mesh
    mesh, moved = contact_check.adjusted(comp, mesh)
    for name, (count, largest) in moved.items():
        record(progress.Message(level="info", text=f"Contact '{name}': {count} source nodes "
                                "moved to start touching (or the offset apart), the most by "
                                f"{contact_check.shown_length(largest)}"))  # fmt: skip
    return mesh
