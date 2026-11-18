"""What commands would change, field by field (ADR 0090 package 12, UXA-2).

A proposal used to read "Changed Tets (global size)": the object and the field, not the
values. `changes` applies a proposal's commands one by one to a copy of the project and
compares each object's data before and after: a row per changed value, old and new as
text (a quantity as "2.5 mm"), its place named by the names of the list items on the way
(`physics[Structural].features[Tip load].values.F`), never by an index or an id; and
the project's problems before and after. The agent panel's proposal card shows these,
one tick box per command, so a proposal can be accepted in part (`Session.accept(only=)`).
"""

from __future__ import annotations

import copy
from typing import Any
from uuid import UUID

from puzzle.errors import PuzzleError

MAX_ROWS = 12  # per command; the rest summed up in one row


def changes(project, commands: list[dict[str, Any]]) -> dict[str, Any]:
    """Per command: what it is, the objects it touches and a row per value changed;
    with the problems before and after all of them."""
    from puzzle.commands import parse_command
    from puzzle.commands.session import _execute

    trial = copy.deepcopy(project)
    out = []
    for i, raw in enumerate(commands):
        cmd = parse_command(raw)
        entry: dict[str, Any] = {"index": i, "command": cmd.command, "rows": [],
                                 "objects": []}  # fmt: skip
        if cmd.solves:
            entry["runs"] = True
            out.append(entry)
            continue
        if not cmd.edits_model:
            out.append(entry)
            continue
        before = {str(k): o.model_dump(mode="json") for k, o in trial.objects.items()}
        names = {str(k): o.name for k, o in trial.objects.items()}
        try:
            _execute(trial, cmd)
        except PuzzleError as exc:
            entry["error"] = exc.message
            out.append(entry)
            continue
        after = {str(k): o.model_dump(mode="json") for k, o in trial.objects.items()}
        for oid in after.keys() - before.keys():
            obj = trial.objects[UUID(oid)]
            entry["objects"].append({"id": oid, "name": obj.name, "how": "added"})
            entry["rows"].append({"object": obj.name, "field": "", "before": "",
                                  "after": f"new {obj.kind.replace('_', ' ')}"})  # fmt: skip
        for oid in before.keys() - after.keys():
            entry["objects"].append({"id": oid, "name": names[oid], "how": "deleted"})
            entry["rows"].append({"object": names[oid], "field": "", "before": "exists",
                                  "after": "deleted"})  # fmt: skip
        for oid in sorted(before.keys() & after.keys()):
            if before[oid] == after[oid]:
                continue
            name = after[oid].get("name") or names[oid]
            entry["objects"].append({"id": oid, "name": name, "how": "changed"})
            for field, old, new in _differences(before[oid], after[oid]):
                entry["rows"].append({"object": name, "field": field, "before": old,
                                      "after": new})  # fmt: skip
        if len(entry["rows"]) > MAX_ROWS:
            more = len(entry["rows"]) - MAX_ROWS
            entry["rows"] = entry["rows"][:MAX_ROWS] + [
                {"object": "", "field": f"and {more} more values", "before": "", "after": ""}
            ]
        out.append(entry)
    return {
        "commands": out,
        "problems_before": [p.to_dict() for p in project.problems()],
        "problems_after": [p.to_dict() for p in trial.problems()],
    }


def _differences(old, new, path: str = "") -> list[tuple[str, str, str]]:
    """Leaf values that differ, as (place, old text, new text)."""
    if _leaf(old) or _leaf(new) or type(old) is not type(new):
        return [] if old == new else [(path, text(old), text(new))]
    out = []
    if isinstance(old, dict):
        for key in list(old) + [k for k in new if k not in old]:
            if key == "id":
                continue
            a, b = old.get(key, _MISSING), new.get(key, _MISSING)
            if a == b:
                continue
            place = f"{path}.{key}" if path else key
            if a is _MISSING or b is _MISSING:
                out.append((place, text(a), text(b)))
            else:
                out += _differences(a, b, place)
        return out
    # lists: item by item where both have one, then what was added or taken away
    for i in range(max(len(old), len(new))):
        a = old[i] if i < len(old) else _MISSING
        b = new[i] if i < len(new) else _MISSING
        if a == b:
            continue
        place = f"{path}[{_item_name(b if b is not _MISSING else a, i)}]"
        if a is _MISSING or b is _MISSING:
            out.append((place, text(a), text(b)))
        else:
            out += _differences(a, b, place)
    return out


class _Missing:
    def __repr__(self) -> str:
        return "(none)"


_MISSING = _Missing()


def _leaf(v) -> bool:
    return not isinstance(v, dict | list) or _is_quantity(v)


def _is_quantity(v) -> bool:
    return isinstance(v, dict) and "value" in v and set(v) <= {"value", "unit", "parameter"}


def _item_name(item, i: int) -> str:
    if isinstance(item, dict):
        for key in ("name", "selection", "kind", "type"):
            if isinstance(item.get(key), str) and item[key]:
                return item[key]
    return str(i + 1)


def text(v) -> str:
    """A value as a person reads it: a quantity with its unit, a list by its count."""
    if v is _MISSING or v is None:
        return "(none)"
    if _is_quantity(v):
        shown = f"{v['value']:g}" if isinstance(v["value"], int | float) else str(v["value"])
        return f"{shown} {v.get('unit') or ''}".strip()
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:g}"
    if isinstance(v, list):
        return f"{len(v)} item{'s' * (len(v) != 1)}"
    if isinstance(v, dict):
        named = v.get("name") or v.get("kind") or v.get("type")
        return str(named) if named else f"{len(v)} values"
    words = str(v)
    return words if len(words) <= 80 else words[:77] + "…"
