"""Running solvers as separate processes, natively or in containers (ADR 0007)."""

from puzzle.jobs.runner import JobResult, run_job
from puzzle.jobs.runtime import (
    ContainerRuntime,
    NativeRuntime,
    Runtime,
    SolverNotFound,
    runtime_for,
)

__all__ = [
    "ContainerRuntime",
    "JobResult",
    "NativeRuntime",
    "Runtime",
    "SolverNotFound",
    "run_job",
    "runtime_for",
]
