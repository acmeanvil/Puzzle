"""Structured errors (ADR 0015): a stable code, a message, where, and details.

Every error a client (GUI, CLI, script, agent) can act on carries a `code` from CODES,
so a program can react without parsing the message. `path` says where: an object
name or id, optionally followed by a field path ("Capacitor: physics.0.features.1").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CODES = {
    # Problems found in the model before a run (Project.problems): the run is refused
    "unconstrained": "a solid-mechanics study on a component with no fixed or displacement "
    "condition: it would move as a rigid body",
    "suppressed": "every part of the study's component is suppressed: nothing to solve",
    "missing_reference": "an object refers to another that no longer exists",
    "missing_file": "a geometry or an imported field reads a file that isn't in the project folder",
    "unknown_selection": "a component uses a selection its geometry doesn't have",
    "foreign_local_geometry": "a component uses geometry local to another component",
    "missing_physics": "a study solves a physics its component doesn't have",
    "duplicate_solver": "a shoot-out lists the same solver twice",
    "sweep_order": "a study's sweep steps come after its solve steps: they must come first",
    "unknown_parameter": "a sweep varies a parameter the project doesn't define",
    "unpinned_transfer": "a transfer from a swept study doesn't say which point it takes "
    "(its source_point)",
    "operator_input": "an operator's required input isn't wired",
    "group_cycle": "tree groups hold each other; the tree shows them side by side",
    "damaged_history": "lines of history.jsonl don't read: they are skipped",
    # Contact warnings (ADR 0056): the run goes ahead
    "contact_far": "a contact's faces start further apart than its search distance",
    "contact_overhang": "a contact's source face reaches past the edge of its target",
    "contact_nonconforming": "a curved source face meets its target along a line or at a "
    "point (a cylinder or ball on a plate), which Code_Aster's contact can't pair",
    "contact_shared_outline": "a contact's two faces have the same outline, where Code_Aster's "
    "3D friction fails",
    "contact_slave_held": "nodes of a contact's source face are also held, or the source of "
    "another contact",
    "contact_slave_bolted": "a contact's source face shares nodes with bolt ends",
    "contact_only_hold": "a body is held only through sliding contact: weak springs hold it "
    "until the contact closes (ADR 0080)",
    "free_body": "a body is neither held nor joined to a held body",
    "weak_springs": "weak springs were added to a body held only by sliding contact (ADR 0080)",
    "large_strain": "large-deflection strains past 5 % on an elastic material (ADR 0082)",
    "explicit_cost": "a long explicit run, or one element setting its time step (ADR 0085)",
    "energy_error": "an explicit run's energy balance off by more than 5 % (ADR 0085)",
    "hourglass": "hourglass energy over 10 % of the internal energy (ADR 0085)",
    "added_mass": "mass scaling added over 5 % of the mass (ADR 0085)",
    "weak_springs_carry": "a body's weak springs carry its load: it never seated (ADR 0080)",
    "thread_radii": "a threaded contact's two cylinders are not one radius (ADR 0076)",
    "invalid_archive": "a project archive damaged, unsafe or from a newer Puzzle (ADR 0061)",
    "unknown_host": "no remote host by that name in your hosts.toml (ADR 0021)",
    "host_unreachable": "ssh could not reach the remote host, or lost it (ADR 0021)",
    "queue_rejected": "the remote host's scheduler refused the job (ADR 0021)",
    "walltime_exceeded": "the job ran past its walltime on the remote host (ADR 0021)",
    "out_of_memory": "the remote host's scheduler stopped the job for using too much memory",
    "job_lost": "the scheduler ended the job itself (a node failure, preemption, a cancel "
    "from outside Puzzle) or its outcome is unknown (ADR 0021)",
    "transfer_failed": "copying a case to or from the remote host failed (ADR 0021)",
    "untrusted": "the project is not trusted: its scripts and equations don't run (ADR 0062)",
    "unsandboxed": "no sandbox for scripts on this computer (Linux without bubblewrap): "
    "install one, or allow scripts without it in Settings (ADR 0062, ADR 0090)",
    "script_failed": "a script operator raised an error, or left no result (ADR 0062)",
    "timeout": "it ran past its time limit and was stopped",
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
    "exists": "the folder or file is already there",
    "lock_lost": "another client took the project lock while this one held it; nothing was written",
    "write_failed": "a file could not be written (a full or read-only disk?)",
    "newer_schema": "the project was saved by a newer Puzzle than this one",
    "unknown_field": "a project file has a field this Puzzle doesn't know (saved by a newer "
    "Puzzle?)",
    # Running studies
    "invalid_input": "the study cannot be written for this solver",
    "mesh_failed": "meshing failed",
    "not_planar": "a 2D model's faces must lie in the XY plane at z = 0",
    "negative_radius": "an axisymmetric model must lie at x >= 0 (Y is the axis)",
    "solver_failed": "the solver ran but failed",
    "stopped": "the run was stopped before it finished",
    "unsupported": "not implemented yet",
    "field_axis": "an imported field's axis (time or frequency) doesn't fit the step it is in",
    "incomplete_material": "a material lacks a property the study needs",
    "no_material": "a part has no material assigned",
    "missing_input": "an operator's input is not chosen or wired yet",
    # Results
    "stale_store": "the results store has an older format; delete it to rebuild",
    "export_failed": "a file could not be written",
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
