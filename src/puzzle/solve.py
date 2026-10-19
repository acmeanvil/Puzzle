"""Run one study end to end: mesh, write solver input, solve, read and store results.

A study that takes transfers (ADR 0009) first runs, or reuses, each source study; the
transferred fields are mapped onto its mesh (puzzle.transfer) and handed to its adapter.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
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
) -> StudyRun:
    """Run `study_id` with its primary solver. Geometry paths resolve against `root`.

    With a `store`, the run is saved there, and when `reuse` is set an earlier run
    with the same inputs (same run key, ADR 0009) is returned instead of solving again.
    """
    study = project.get(study_id, Study)
    if study.sweeps():
        raise StudyError("parametric sweeps are not run yet", code="unsupported", path=study.name)
    adapters = available_adapters()
    if study.solver not in adapters:
        raise StudyError(
            f"no adapter installed for {study.solver}", code="unsupported", path=study.name
        )
    adapter = adapters[study.solver]()
    problems = project.validate() + adapter.check(project, study)
    if problems:
        raise StudyError("; ".join(problems), path=study.name, details={"problems": problems})

    run_key = run_hashes(project)[study.id][study.solver][0]
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
            )
    started = datetime.now(UTC)
    meshed = generate_mesh(project, study.component, root, workdir / "mesh")
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
    # An adapter that writes one solve step per input runs a multi-step study one step
    # at a time on the same mesh; the steps are independent analyses.
    steps = study.solve_steps()
    limit = getattr(adapter, "max_solve_steps", None)
    if limit is None or len(steps) <= limit:
        parts = [(study, workdir)]
    else:
        sweeps = study.sweeps()
        parts = [
            (study.model_copy(update={"steps": [*sweeps, step]}), workdir / f"step-{n}")
            for n, step in enumerate(steps, start=1)
        ]
    runtime = runtime_for(study.solver)
    results, job, input_file = None, None, None
    for n, (part, where) in enumerate(parts, start=1):
        where.mkdir(parents=True, exist_ok=True)
        input_file = adapter.write_input(project, part, meshed.mesh, where, inputs)
        job = run_job(
            adapter.command(input_file), where, runtime, on_output=on_output, timeout_s=timeout_s
        )
        if not job.ok:
            tail = log_tail(job.log)
            raise StudyError(
                f"{study.solver} failed (exit {job.returncode}):\n" + "\n".join(tail),
                code="solver_failed",
                path=study.name,
                details={"exit_code": job.returncode, "log": str(job.log), "log_tail": tail},
            )
        part_results = adapter.read_results(input_file)
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
        )
    return StudyRun(
        mesh=meshed.mesh,
        results=results,
        run_key=run_key,
        run_id=run_id,
        mesh_result=meshed,
        job=job,
        input_file=input_file,
    )


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-") or "study"
