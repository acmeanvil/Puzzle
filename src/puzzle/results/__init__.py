"""Results: solver-neutral fields per step; storage in Delta Lake/Parquet comes next (ADR 0008)."""

from puzzle.results.model import Field, Results, StepResult

__all__ = ["Field", "Results", "StepResult"]
