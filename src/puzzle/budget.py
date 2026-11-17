"""What one command's solves may spend, checked before each solve (ADR 0015, ADR 0090
package 3).

An agent's run (a study, an optimisation, a convergence check, adaptive passes, a mesh
shoot-out) is one command in the session, and every solve it makes passes through
`run_study`. The session puts a `Gate` around the command (`gated`); `run_study` calls
`begin` before each fresh solve (never for a run reused from the store) and `end` after
it, so a multi-run command is counted solve by solve and stopped when the agent's
budget is spent, whichever tool started it. The session writes what the gate counted
into the command's history entry, which is where the budget is read from.

A tool call by an agent (`agent_call`) that reaches a fresh solve with no gate around
it is refused: no path solves for an agent without being counted.
"""

from __future__ import annotations

import contextlib
import contextvars
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from puzzle.errors import PuzzleError

_GATE: contextvars.ContextVar = contextvars.ContextVar("puzzle_solve_gate", default=None)
_AGENT: contextvars.ContextVar = contextvars.ContextVar("puzzle_agent_call", default=False)


@dataclass
class Gate:
    """One command's solves: `check` (None: count only) raises `budget_exceeded` when
    another may not start, else returns the solver seconds left (None: no limit)."""

    check: Callable[[Gate, bool], float | None] | None = None
    cores: Callable[[str], int] = lambda host: 1  # a remote host's cores, for core-hours
    solved: int = 0  # fresh solves started
    seconds: float = 0.0  # their solver time
    core_hours: float = 0.0  # of which on remote hosts, cores × hours
    refused: PuzzleError | None = None  # the refusal, kept: a runner that catches it goes on
    _open: list[tuple[float, str | None]] = field(default_factory=list)

    def begin(self, timeout_s: float | None, host: str | None = None) -> float | None:
        """Before a fresh solve (on `host`, None for this computer): refused when the
        budget is spent; the timeout it may run for."""
        if self.refused is not None:
            raise self.refused
        left = None
        if self.check is not None:
            try:
                left = self.check(self, host is not None)
            except PuzzleError as exc:
                self.refused = exc
                raise
        self.solved += 1
        self._open.append((time.monotonic(), host))
        if left is None:
            return timeout_s
        return left if timeout_s is None else min(timeout_s, left)

    def depth(self) -> int:
        return len(self._open)

    def end(self, depth: int, seconds: float | None = None) -> None:
        """The solves begun past `depth` ended; `seconds` the last one's solver time
        (else its wall time since `begin`)."""
        while len(self._open) > depth:
            started, host = self._open.pop()
            took = seconds if seconds is not None else time.monotonic() - started
            seconds = None  # only the innermost has a job's own time
            self.seconds += took
            if host is not None:
                self.core_hours += self.cores(host) * took / 3600

    def spent(self) -> dict[str, float]:
        return {"solves": self.solved, "solver_seconds": round(self.seconds, 3),
                "core_hours": round(self.core_hours, 6)}  # fmt: skip


@contextlib.contextmanager
def gated(gate: Gate):
    """Solves inside are counted by `gate` (and checked against its budget)."""
    token = _GATE.set(gate)
    try:
        yield gate
    finally:
        _GATE.reset(token)


@contextlib.contextmanager
def agent_call():
    """An agent's tool call: a solve inside must go through the session's gate."""
    token = _AGENT.set(True)
    try:
        yield
    finally:
        _AGENT.reset(token)


def current() -> Gate | None:
    return _GATE.get()


def begin(timeout_s: float | None, host: str | None = None) -> tuple[Gate | None, float | None]:
    """`run_study`'s check before a fresh solve: the gate (or None) and the timeout."""
    gate = _GATE.get()
    if gate is None:
        if _AGENT.get():
            raise PuzzleError(
                "an agent's solve goes through the session (submit a run command), so "
                "the autonomy level and the budget apply",
                code="not_allowed",
            )
        return None, timeout_s
    return gate, gate.begin(timeout_s, host)
