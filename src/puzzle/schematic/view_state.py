"""What a user did to their schematic (ADR 0058 item 6): nodes dragged, components
unfolded, tree groups folded. A per-user view preference in `<user data dir>/
schematic.json`, by project folder and node, never in the project: it never changes a
hash, a run or another user's picture."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


def _path() -> Path:
    from puzzle.materials import user_data_dir

    return user_data_dir() / "schematic.json"


@dataclass
class ViewState:
    positions: dict[str, tuple[float, float]] = field(default_factory=dict)
    unfolded: set[str] = field(default_factory=set)  # components showing their parts
    folded: set[str] = field(default_factory=set)  # tree groups drawn as one node

    def prune(self, nodes, groups=()) -> bool:
        """Forget nodes and groups that are gone; True when anything was."""
        keep = set(nodes)
        before = (len(self.positions), len(self.unfolded), len(self.folded))
        self.positions = {k: v for k, v in self.positions.items() if k in keep}
        self.unfolded &= keep
        self.folded &= set(groups)
        return before != (len(self.positions), len(self.unfolded), len(self.folded))


def _all() -> dict:
    try:
        data = json.loads(_path().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _key(root: Path) -> str:
    return str(Path(root).resolve())


def load(root: Path) -> ViewState:
    entry = _all().get(_key(root)) or {}
    try:
        return ViewState(
            {k: (float(v[0]), float(v[1])) for k, v in entry.get("positions", {}).items()},
            set(entry.get("unfolded", [])),
            set(entry.get("folded", [])),
        )
    except (TypeError, ValueError, IndexError, AttributeError):
        return ViewState()


def save(root: Path, state: ViewState) -> None:
    data = _all()
    entry = {
        "positions": {k: [round(x, 1), round(y, 1)] for k, (x, y) in state.positions.items()},
        "unfolded": sorted(state.unfolded),
        "folded": sorted(state.folded),
    }
    if any(entry.values()):
        data[_key(root)] = entry
    else:
        data.pop(_key(root), None)
    path = _path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1))
        tmp.replace(path)
    except OSError:  # a read-only home: positions are a convenience only
        pass
