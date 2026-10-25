"""Per-user preferences (ADR 0024): one file, one Settings dialog, never in a project.

`<user data dir>/settings.toml` holds what a person prefers on this machine: display
units, how previews behave, how much of each solver's settings to show. A preference
never changes a result: anything a solver computes from lives in the project and its
run key. The agent's own settings stay in `agent.toml` (ADR 0015) and appear in the
dialog as their own group.

Each preference is defined once (`PREFERENCES`): its key, group, readable name,
choices and default. Unknown keys in the file are kept, so settings written by a newer
Puzzle survive an older one.
"""

from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from puzzle.model.units import CHOICES as UNIT_CHOICES
from puzzle.model.units import DIMENSIONS, SYSTEMS, pretty, system_units


@dataclass(frozen=True)
class Preference:
    key: str
    group: str
    label: str
    default: Any
    choices: list[tuple[str, Any]] = field(default_factory=list)  # (shown, stored); [] = text
    help: str = ""
    ready: bool = True  # False: defined, waiting for the feature that uses it


# The send keys as the platform names them: Qt reports the Mac's Command key as Control.
SEND_KEYS = "⌘+Enter" if sys.platform == "darwin" else "Ctrl+Enter"

PREFERENCES: list[Preference] = [
    Preference(
        "units.system",
        "Units",
        "Display units",
        "si",
        [(label, key) for key, (label, _) in SYSTEMS.items()]
        + [("My own, per quantity", "custom")],
        "How values are shown and what a number typed without a unit means. Values are "
        "stored in SI whatever is shown.",
    ),
    *[
        Preference(
            f"units.custom.{kind}",
            "Units",
            label,
            si,
            [(pretty(u), u) for u in UNIT_CHOICES[kind]],
            "Used when Display units is 'My own, per quantity'.",
        )
        for kind, (label, si) in DIMENSIONS.items()
    ],
    Preference(
        "interface.toolbar_labels",
        "Interface",
        "Toolbar buttons",
        "under",
        [("Icons with labels under them", "under"), ("Icons only (names in tooltips)", "icons")],
        "Tooltips always give a button's name and shortcut.",
    ),
    Preference(
        "geometry.preview",
        "Geometry",
        "Tool preview",
        "live",
        [("As values change", "live"), ("When I press Preview", "on_demand")],
        "A tool's result is previewed in the view before Apply. On demand suits large parts.",
    ),
    Preference(
        "meshing.element_types",
        "Meshing",
        "Element types for new meshes",
        "solver",
        [("Each solver's preferred types", "solver"), ("Puzzle's neutral defaults", "neutral")],
        "New mesh recipes take first-order elements when every study uses a finite-volume "
        "CFD solver, second-order ones otherwise; neutral is always second order.",
    ),
    Preference(
        "meshing.preview",
        "Meshing",
        "Mesh preview",
        "on_demand",
        [("On every change (meshes again)", "live"), ("On demand (the Mesh button)", "on_demand")],
        "The Mesh tab's controls show in the mesh when the component is meshed (ADR 0019).",
    ),
    Preference(
        "meshing.netgen_optimiser",
        "Meshing",
        "Offer Netgen as Gmsh's optimiser",
        True,
        [("Yes", True), ("No", False)],
        "A mesh recipe's optimiser can then be Netgen's (it improves Gmsh's tetrahedra).",
    ),
    Preference(
        "agent.send_key",
        "Agent",
        "Send a message with",
        "modifier",
        [
            (f"{SEND_KEYS} (Enter starts a new line)", "modifier"),
            ("Enter (Shift+Enter starts a new line)", "enter"),
        ],
        "How the agent panel's message box sends what you typed.",
    ),
    Preference(
        "solvers.level",
        "Solvers",
        "Solver settings shown",
        "standard",
        [("Standard", "standard"), ("Power user: every option", "power")],
        "Power user adds each solver's whole option set and the expert pass-through "
        "to a study's Solver settings… (ADR 0023).",
    ),
]
BY_KEY = {p.key: p for p in PREFERENCES}
WAITING: dict[str, str] = {}  # group -> why its preferences don't apply yet


def window_state_path() -> Path:
    """Where the main window keeps its layout (dock sizes and which are open, ADR 0031)."""
    from puzzle.materials import user_data_dir

    return user_data_dir() / "window-layout.bin"


def settings_path() -> Path:
    from puzzle.materials import user_data_dir

    return user_data_dir() / "settings.toml"


class Settings:
    """The preferences, read from the file; `set` and `save` write it back."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else settings_path()
        self.data: dict[str, Any] = {}
        if self.path.exists():
            self.data = _flatten(tomllib.loads(self.path.read_text()))

    def get(self, key: str) -> Any:
        pref = BY_KEY.get(key)
        value = self.data.get(key, pref.default if pref else None)
        if pref and pref.choices and value not in [v for _, v in pref.choices]:
            return pref.default  # a value this version does not offer
        return value

    def set(self, key: str, value: Any) -> None:
        pref = BY_KEY.get(key)
        if pref is None:
            raise KeyError(f"no setting '{key}'")
        if pref.choices and value not in [v for _, v in pref.choices]:
            shown = ", ".join(str(v) for _, v in pref.choices)
            raise ValueError(f"{pref.label} is one of: {shown}")
        self.data[key] = value

    def reset(self, key: str) -> None:
        self.data.pop(key, None)

    def save(self) -> None:
        import tomli_w

        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".toml.tmp")
        tmp.write_text(tomli_w.dumps(_nest(self.data)))
        tmp.replace(self.path)  # never a half-written file

    # what the rest of Puzzle asks
    def display_units(self) -> dict[str, str]:
        """The units shown: the open project's own when it has them on (ADR 0030),
        else this user's preference."""
        if _project_system is not None:
            return system_units(_project_system)
        return self.user_units()

    def user_units(self) -> dict[str, str]:
        custom = {k.split(".")[-1]: self.get(k) for k in BY_KEY if k.startswith("units.custom.")}
        return system_units(self.get("units.system"), custom)


_current: Settings | None = None
_project_system: str | None = None  # the open project's unit system, when it has one on


def use_project(project) -> None:
    """Show `project`'s own units, if it has them switched on (None: the user's)."""
    global _project_system
    units = project.units() if project is not None else None
    _project_system = units.system if units is not None else None


def project_system() -> str | None:
    return _project_system


def current() -> Settings:
    """The settings in effect (read once; `reload` after the dialog or CLI saves)."""
    global _current
    if _current is None:
        _current = Settings()
    return _current


def reload() -> Settings:
    global _current
    _current = Settings()
    return _current


def _flatten(data: dict, prefix: str = "") -> dict[str, Any]:
    out = {}
    for k, v in data.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, key + "."))
        else:
            out[key] = v
    return out


def _nest(flat: dict[str, Any]) -> dict:
    out: dict = {}
    for key, v in sorted(flat.items()):
        node = out
        *path, last = key.split(".")
        for p in path:
            node = node.setdefault(p, {})
        node[last] = v
    return out


def length_unit() -> tuple[str, float]:
    """The display length unit and how many of it make a metre (forms and sketches)."""
    from puzzle.model.units import convert, pretty

    unit = current().display_units()["length"]
    return pretty(unit), convert(1.0, "m", unit)


def area_unit() -> tuple[str, float]:
    from puzzle.model.units import convert, pretty

    unit = current().display_units()["area"]
    return pretty(unit), convert(1.0, "m^2", unit)
