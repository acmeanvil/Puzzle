"""The commands every client uses to change a project (ADR 0015).

A command is data: a Pydantic model with a `command` tag, so its JSON schema is
published (`command_schema()`) and an agent, the GUI or a script can build one.
Objects are named by name or id; field paths are dotted with list indices,
e.g. "physics.0.features.1.values.I".
"""

from __future__ import annotations

from typing import Annotated, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from puzzle.model.schema import Budget


class _Command(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Model edits are undoable; runs and settings changes are not.
    undoable: ClassVar[bool] = True
    edits_model: ClassVar[bool] = True
    # Runs: each solve they make is counted against an agent's budget, one history
    # entry for the command, a version before it (ADR 0015, ADR 0090 package 3).
    solves: ClassVar[bool] = False


class Create(_Command):
    """Add an object of `kind` (geometry, material, mesh, component, study, ...)."""

    command: Literal["create"] = "create"
    kind: str
    data: dict[str, Any] = Field(description="the object's fields; `id` is generated if absent")


class EditOp(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["set", "append", "remove"]
    path: str = Field(description='dotted field path, e.g. "physics.0.features.1.values.V"')
    value: Any = None


class Edit(_Command):
    """Change fields of one object; all ops apply together or not at all."""

    command: Literal["edit"] = "edit"
    object: str = Field(description="name or id")
    ops: list[EditOp] = Field(min_length=1)


class Delete(_Command):
    command: Literal["delete"] = "delete"
    object: str


class AddLibraryMaterial(_Command):
    """Copy a material from the materials library into the project."""

    command: Literal["add_library_material"] = "add_library_material"
    key: str
    name: str | None = None


class CopyComponent(_Command):
    """Copy a component; its local geometry is copied with no link back (ADR 0009).
    With `studies`, its studies are duplicated too, on the copy (as Ansys's Duplicate
    of a system)."""

    command: Literal["copy_component"] = "copy_component"
    component: str
    name: str
    studies: bool = False


class CopyStudy(_Command):
    """Duplicate a study under a new name: its steps, settings, sweeps and solver (its
    runs stay the original's)."""

    command: Literal["copy_study"] = "copy_study"
    study: str
    name: str


class RunStudy(_Command):
    """Mesh and solve a study, or reuse an identical earlier run."""

    command: Literal["run_study"] = "run_study"
    study: str
    reuse: bool = True
    timeout_s: float | None = None
    host: str | None = None  # a remote host for this run only (ADR 0021)
    resume: str | None = None  # a job runner's job to pick up instead of solving (ADR 0067)
    undoable: ClassVar[bool] = False
    edits_model: ClassVar[bool] = False
    solves: ClassVar[bool] = True


class RunOptimisation(_Command):
    """Run an optimisation's points not run yet (ADR 0063): each a stored run, those run
    before reused; `budget` solves at most that many new points this time."""

    command: Literal["run_optimisation"] = "run_optimisation"
    optimisation: str
    budget: int | None = Field(default=None, ge=0)
    undoable: ClassVar[bool] = False
    edits_model: ClassVar[bool] = False
    solves: ClassVar[bool] = True


class RunConvergence(_Command):
    """A study (or a mesh study) on finer and finer meshes, judged by the grid
    convergence index (ADR 0066); settings left out are the study's own."""

    command: Literal["run_convergence"] = "run_convergence"
    study: str
    levels: int | None = Field(default=None, ge=2)
    ratio: float | None = Field(default=None, gt=1)
    target: float | None = Field(default=None, gt=0)
    undoable: ClassVar[bool] = False
    edits_model: ClassVar[bool] = False
    solves: ClassVar[bool] = True


class RunAdapt(_Command):
    """Adaptive refinement passes (ADR 0066 phase 3); settings left out are the study's
    `adapt`."""

    command: Literal["run_adapt"] = "run_adapt"
    study: str
    target: float | None = Field(default=None, gt=0)
    passes: int | None = Field(default=None, ge=1)
    method: Literal["isotropic", "anisotropic"] | None = None
    undoable: ClassVar[bool] = False
    edits_model: ClassVar[bool] = False
    solves: ClassVar[bool] = True


class RunMeshShootout(_Command):
    """A study solved on its recipe meshed by Gmsh and by Netgen, compared (ADR 0019)."""

    command: Literal["run_mesh_shootout"] = "run_mesh_shootout"
    study: str
    quantities: list[str] | None = None
    tolerance: float = Field(default=0.02, gt=0)
    undoable: ClassVar[bool] = False
    edits_model: ClassVar[bool] = False
    solves: ClassVar[bool] = True


class SetAutonomy(_Command):
    """Change what agents may do here. Users only; "act" needs a second confirmation."""

    command: Literal["set_autonomy"] = "set_autonomy"
    level: Literal["advise", "propose", "act_in_budget", "act"] | None = None
    budget: Budget | None = None
    mcp: Literal["off", "local", "network"] | None = None
    confirm: str | None = Field(default=None, description="token from the first request")
    undoable: ClassVar[bool] = False
    edits_model: ClassVar[bool] = False


Command = Annotated[
    Create
    | Edit
    | Delete
    | AddLibraryMaterial
    | CopyComponent
    | CopyStudy
    | RunStudy
    | RunOptimisation
    | RunConvergence
    | RunAdapt
    | RunMeshShootout
    | SetAutonomy,
    Field(discriminator="command"),
]
_ADAPTER: TypeAdapter[Command] = TypeAdapter(Command)


def parse_command(data: dict[str, Any] | _Command) -> _Command:
    """A command from its JSON form; raises PuzzleError(code="invalid")."""
    from pydantic import ValidationError

    from puzzle.errors import PuzzleError

    if isinstance(data, _Command):
        return data
    try:
        return _ADAPTER.validate_python(data)
    except ValidationError as exc:
        raise PuzzleError("not a valid command", details={"errors": _errors(exc)}) from None


def command_schema() -> dict[str, Any]:
    return _ADAPTER.json_schema()


def _errors(exc) -> list[dict[str, Any]]:
    return [{"path": ".".join(str(p) for p in e["loc"]), "message": e["msg"]} for e in exc.errors()]
