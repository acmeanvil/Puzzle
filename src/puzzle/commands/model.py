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
    """Copy a component; its local geometry is copied with no link back (ADR 0009)."""

    command: Literal["copy_component"] = "copy_component"
    component: str
    name: str


class RunStudy(_Command):
    """Mesh and solve a study, or reuse an identical earlier run."""

    command: Literal["run_study"] = "run_study"
    study: str
    reuse: bool = True
    timeout_s: float | None = None
    undoable: ClassVar[bool] = False
    edits_model: ClassVar[bool] = False


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
    Create | Edit | Delete | AddLibraryMaterial | CopyComponent | RunStudy | SetAutonomy,
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
