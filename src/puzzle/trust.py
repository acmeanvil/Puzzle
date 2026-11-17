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
        return list(json.loads(_path().read_text(encoding="utf-8")).get("projects", []))
    except (OSError, ValueError):
        return []


def _key(root) -> str:
    return str(Path(root).resolve())


def is_trusted(root) -> bool:
    return _key(root) in _load()


def _save(found: list[str]) -> None:
    from puzzle.project import atomic

    atomic.write_text(_path(), json.dumps({"projects": sorted(found)}, indent=1), encoding="utf-8")


def trust(root) -> None:
    """Remember `root` as trusted. The list is read again and replaced whole under a
    lock (BE-19): two Puzzles trusting at once both stay, and a crash never empties it."""
    if _key(root) in _load():
        return
    from puzzle.project import atomic

    _path().parent.mkdir(parents=True, exist_ok=True)
    with atomic.locked(_path(), what="the trusted projects list"):
        found = _load()
        if _key(root) not in found:
            _save([*found, _key(root)])


def revoke(root) -> None:
    from puzzle.project import atomic

    _path().parent.mkdir(parents=True, exist_ok=True)
    with atomic.locked(_path(), what="the trusted projects list"):
        _save([p for p in _load() if p != _key(root)])


def holds_code(project) -> list[str]:
    """The objects that hold code: script operators and custom PDE equations."""
    from puzzle.model.schema import Component, Operator

    out = [o.name for o in project.of_type(Operator) if o.type == "script"]
    out += [c.name for c in project.of_type(Component)
            for p in c.physics if p.type == "custom_pde" and p.equation]  # fmt: skip
    return out


def code_texts(project):
    """Every piece of code the project would run, counted: script operators' code and
    custom PDE weak forms (ADR 0062: what runs, not what is only parsed, as expressions
    and derived-result equations are). A command that raises a count writes new code,
    which an agent below "act" proposes (ADR 0090, BE-9)."""
    from collections import Counter

    from puzzle.model.schema import Component, Operator

    found = Counter(("script", o.code) for o in project.of_type(Operator) if o.type == "script")
    found.update(("equation", p.equation.weak_form) for c in project.of_type(Component)
                 for p in c.physics if p.type == "custom_pde" and p.equation)  # fmt: skip
    return found


def new_code(before, after) -> list[str]:
    """What `after` runs that `before` does not (as words, for a refusal or a proposal)."""
    more = code_texts(after) - code_texts(before)
    return [f"a {'script' if k == 'script' else 'custom PDE equation'}" for k, _ in more]


def require(project, what: str) -> None:
    """Refuse running `what` (a script, an equation) in an untrusted project."""
    if not getattr(project, "trusted", True):
        raise PuzzleError(
            f"this project is not trusted, so its {what} does not run: trust it first "
            "(Project → Trust this project…, or puzzle trust PROJECT) if you know where "
            "it came from",
            code="untrusted",
        )
