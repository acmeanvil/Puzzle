"""Run one study end to end: mesh, write solver input, solve, read results."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from puzzle.adapters import available_adapters
from puzzle.jobs import JobResult, run_job, runtime_for
from puzzle.mesh import MeshResult, generate_mesh
from puzzle.model.schema import Project, Study
from puzzle.results.model import Results


class StudyError(RuntimeError):
    pass


@dataclass
class StudyRun:
    mesh: MeshResult
    job: JobResult
    results: Results
    input_file: Path


def run_study(
    project: Project,
    study_id,
    root: Path,
    workdir: Path,
    *,
    on_output: Callable[[str], None] | None = None,
    timeout_s: float | None = None,
) -> StudyRun:
    """Run `study_id` with its primary solver. Geometry paths resolve against `root`."""
    study = project.get(study_id, Study)
    if study.sweeps():
        raise StudyError("parametric sweeps are not run yet")
    adapters = available_adapters()
    if study.solver not in adapters:
        raise StudyError(f"no adapter installed for {study.solver}")
    adapter = adapters[study.solver]()
    problems = project.validate() + adapter.check(project, study)
    if problems:
        raise StudyError("; ".join(problems))

    workdir.mkdir(parents=True, exist_ok=True)
    mesh = generate_mesh(project, study.component, root, workdir / "mesh")
    input_file = adapter.write_input(project, study, mesh.mesh, workdir)
    job = run_job(
        adapter.command(input_file),
        workdir,
        runtime_for(study.solver),
        on_output=on_output,
        timeout_s=timeout_s,
    )
    if not job.ok:
        tail = "\n".join(job.log.read_text().splitlines()[-20:])
        raise StudyError(f"{study.solver} failed (exit {job.returncode}):\n{tail}")

    results = adapter.read_results(input_file)
    kinds = [s.type for s in study.solve_steps()]
    for step in results.steps:
        if 1 <= step.index <= len(kinds):
            step.kind = kinds[step.index - 1]
    return StudyRun(mesh=mesh, job=job, results=results, input_file=input_file)
