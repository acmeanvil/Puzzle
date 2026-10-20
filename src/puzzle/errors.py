"""Structured errors (ADR 0015): a stable code, a message, where, and details.

Every error a client (GUI, CLI, script, agent) can act on carries a `code` from CODES,
so a program can react without parsing the message. `path` says where: an object
name or id, optionally followed by a field path ("Capacitor: physics.0.features.1").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CODES = {
    # Commands and the project model
    "not_found": "no object (or field) by that name or id",
    "ambiguous": "the name matches several objects; use the id",
    "invalid": "the value does not fit the schema",
    "inconsistent": "the project fails a consistency check",
    "not_allowed": "the actor's autonomy level does not permit this",
    "budget_exceeded": "the agent's compute budget is spent",
    "confirmation_required": "the change needs a further confirmation",
    "not_undoable": "the command cannot be undone",
    "busy": "another client holds the project lock",
    # Running studies
    "invalid_input": "the study cannot be written for this solver",
    "mesh_failed": "meshing failed",
    "solver_failed": "the solver ran but failed",
    "unsupported": "not implemented yet",
    # Results
    "stale_store": "the results store has an older format; delete it to rebuild",
}


class PuzzleError(Exception):
    """An error with a code from CODES. Subclasses set a default `code`."""

    code = "invalid"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        path: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        assert self.code in CODES, self.code
        self.path = path
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.path:
            out["path"] = self.path
        if self.details:
            out["details"] = self.details
        return out


@dataclass(frozen=True)
class Problem:
    """One finding of `Project.problems()`: a code, the object it concerns, a message."""

    code: str
    object: str  # the object's name
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out = {"code": self.code, "object": self.object, "message": self.message}
        return out | ({"details": self.details} if self.details else {})
