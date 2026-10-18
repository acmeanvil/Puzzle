"""The command layer (ADR 0015): every change to a project goes through a Session.

    session = Session(Path("my-project"))
    session.submit({"command": "edit", "object": "Bracket",
                    "ops": [{"op": "set", "path": "mesh", "value": "..."}]})
    session.submit(cmd, Actor("agent", "claude"))   # proposed, applied or refused by level
    session.undo()

The GUI, CLI, scripts and agents (puzzle.agent.mcp) are all clients of this layer.
"""

from puzzle.commands.model import Command, EditOp, command_schema, parse_command
from puzzle.commands.outline import outline
from puzzle.commands.session import USER, Actor, Outcome, Session, resolve

__all__ = [
    "USER",
    "Actor",
    "Command",
    "EditOp",
    "Outcome",
    "Session",
    "command_schema",
    "outline",
    "parse_command",
    "resolve",
]
