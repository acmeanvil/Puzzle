"""Which projects may run code (ADR 0062 phase 4): script operators and custom PDE
equations, as VS Code's workspace trust.

A project made by this installation is trusted: built in this process, or saved by it
(its folder is then remembered in `<user data dir>/trusted.json`). One opened from
elsewhere (copied in, unpacked from an archive, ADR 0061) is not until the user trusts
it (`puzzle trust PROJECT`, the GUI's Project → Trust this project…); until then its
scripts and equations are refused with code `untrusted`, and the Advisor says so. An
agent never trusts a project.
"""

from __future__ import annotations

import json
from pathlib import Path

from puzzle.errors import PuzzleError

FILE = "trusted.json"


def _path() -> Path:
    from puzzle.materials import user_data_dir

    return user_data_dir() / FILE


def _load() -> list[str]:
    try:
        return list(json.loads(_path().read_text()).get("projects", []))
    except (OSError, ValueError):
        return []


def _key(root) -> str:
    return str(Path(root).resolve())


def is_trusted(root) -> bool:
    return _key(root) in _load()


def trust(root) -> None:
    """Remember `root` as trusted."""
    found = _load()
    if _key(root) not in found:
        found.append(_key(root))
        _path().parent.mkdir(parents=True, exist_ok=True)
        _path().write_text(json.dumps({"projects": sorted(found)}, indent=1))


def revoke(root) -> None:
    found = [p for p in _load() if p != _key(root)]
    _path().parent.mkdir(parents=True, exist_ok=True)
    _path().write_text(json.dumps({"projects": found}, indent=1))


def holds_code(project) -> list[str]:
    """The objects that hold code: script operators and custom PDE equations."""
    from puzzle.model.schema import Component, Operator

    out = [o.name for o in project.of_type(Operator) if o.type == "script"]
    out += [c.name for c in project.of_type(Component)
            for p in c.physics if p.type == "custom_pde" and p.equation]  # fmt: skip
    return out


def require(project, what: str) -> None:
    """Refuse running `what` (a script, an equation) in an untrusted project."""
    if not getattr(project, "trusted", True):
        raise PuzzleError(
            f"this project is not trusted, so its {what} does not run: trust it first "
            "(Project → Trust this project…, or puzzle trust PROJECT) if you know where "
            "it came from",
            code="untrusted",
        )
