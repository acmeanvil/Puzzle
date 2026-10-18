"""Run one study end to end: mesh, write solver input, solve, read and store results."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from puzzle.adapters import available_adapters
from puzzle.errors import PuzzleError
from puzzle.jobs import JobResult, run_job, runtime_for
from puzzle.mesh import MeshResult, generate_mesh
from puzzle.mesh.mesh import Mesh
from puzzle.model.schema import Project, Study
from puzzle.project.graph import run_hashes
from puzzle.results.model import Results
from puzzle.results.store import ResultStore


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
    started = datetime.now(UTC)
    meshed = generate_mesh(project, study.component, root, workdir / "mesh")
    input_file = adapter.write_input(project, study, meshed.mesh, workdir)
    runtime = runtime_for(study.solver)
    job = run_job(
        adapter.command(input_file), workdir, runtime, on_output=on_output, timeout_s=timeout_s
    )
    if not job.ok:
        tail = job.log.read_text().splitlines()[-20:]
        raise StudyError(
            f"{study.solver} failed (exit {job.returncode}):\n" + "\n".join(tail),
            code="solver_failed",
            path=study.name,
            details={"exit_code": job.returncode, "log": str(job.log), "log_tail": tail},
        )

    results = adapter.read_results(input_file)
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
