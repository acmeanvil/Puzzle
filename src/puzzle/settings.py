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
    kind: str = "choice"  # choice (choices, or text when none) or colour (a "#rrggbb")


def _core_choices() -> list[tuple[str, int]]:
    """Cores per run: automatic (0, the default: `machine.default_cores`), or a number up
    to the logical cores, from this computer (ADR 0053)."""
    from puzzle.machine import default_cores, logical_cores

    return [(f"Automatic ({default_cores()})", 0)] + [
        (str(n), n) for n in range(1, logical_cores() + 1)
    ]


def _process_choices() -> list[tuple[str, int]]:
    from puzzle.machine import physical_cores

    return [("1 (threads only)", 1)] + [(str(n), n) for n in range(2, physical_cores() + 1)]


def _legend_preferences() -> list[Preference]:
    """The result legend's look (gui Legend…, post/legend.py), kept between sessions."""
    from puzzle.post.legend import BANDS, COLORMAPS, OUTSIDE, POSITIONS, SIZES

    return [
        Preference("results.colormap", "Results", "Colours", "turbo", COLORMAPS),
        Preference("results.invert_colors", "Results", "Colours run", False,
                   [("Low blue, high red (as listed)", False), ("The other way", True)]),
        Preference("results.bands", "Results", "Colour bands", 0, BANDS),
        Preference("results.above_color", "Results", "Above a custom range", "none",
                   OUTSIDE, "Values above the top in one colour."),
        Preference("results.below_color", "Results", "Below a custom range", "none",
                   OUTSIDE, "Values below the bottom in one colour."),
        Preference("results.legend_position", "Results", "Legend position", "left", POSITIONS),
        Preference("results.legend_size", "Results", "Legend size", 1.0, SIZES),
        Preference("results.table_layout", "Results", "Table layout", "long",
                   [("A row per node or element per step (long)", "long"),
                    ("A column per step (wide)", "wide")],
                   "How a result table operator lays out its steps (ADR 0064)."),
        Preference("results.axisymmetric_view", "Results", "Axisymmetric results", "revolved",
                   [("Revolved about the axis", "revolved"), ("Flat (the section)", "flat")],
                   "How a 2D axisymmetric result is drawn (ADR 0048); probes still read "
                   "the section."),
        Preference("results.sweep_angle", "Results", "Revolved through", 270.0,
                   [("90°", 90.0), ("180°", 180.0), ("270°", 270.0), ("360°", 360.0)],
                   "How far an axisymmetric result is turned about its axis."),
        Preference("results.symmetric_view", "Results", "Symmetric models", "whole",
                   [("Drawn whole (mirrored, turned, repeated)", "whole"),
                    ("The part modelled", "part")],
                   "How a model cut by symmetry planes, a cyclic sector or a periodic slice "
                   "is drawn (ADR 0081); probes read the part modelled."),
    ]  # fmt: skip


def _connection_preferences() -> list[Preference]:
    """How imported fields' connections are shown (ADR 0044 decision 8), each on its own."""
    from puzzle.post.legend import OUTSIDE

    on_off = [("Shown", True), ("Hidden", False)]
    return [
        Preference("connections.tree", "Connections", "In the tree and Properties", True,
                   on_off, "A field-fed feature names its source in the tree; Properties "
                   "lists an imported field's uses and a feature's chain from the file to "
                   "the solver."),
        Preference("connections.view", "Connections", "In the 3D view", True, on_off,
                   "Selecting a field-fed feature or an imported field colours the mapped "
                   "values on its faces or bodies, extrapolated areas marked."),
        Preference("connections.points", "Connections", "The data's points in the view",
                   False, on_off, "The imported points drawn over the mapped values."),
        Preference("connections.schematic", "Connections", "In the schematic", True,
                   on_off, "Imported fields and their files as nodes, wired into the "
                   "features, materials and sections they drive (ADR 0058)."),
        Preference("connections.beyond_color", "Connections", "Extrapolated areas", "magenta",
                   [c for c in OUTSIDE if c[1] != "none"],
                   "Where the model is farther from the data than the tolerance."),
    ]  # fmt: skip


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
        "interface.theme",
        "Interface",
        "Theme",
        "system",
        [
            ("Follow the system", "system"),
            ("Light", "light"),
            ("Dark", "dark"),
            ("High contrast", "high_contrast"),
        ],  # fmt: skip
        "Colours of the whole window (ADR 0045). The 3D view keeps a light ground in every "
        "theme, where result colours read best.",
    ),
    Preference(
        "interface.density",
        "Interface",
        "Density",
        "comfortable",
        [("Comfortable", "comfortable"), ("Compact", "compact")],
        "Space around controls and tree rows: compact fits more on a laptop screen.",
    ),
    Preference(
        "interface.text_size",
        "Interface",
        "Text size",
        "system",
        [("As the system", "system"), ("Larger", "larger"), ("Largest", "largest")],
        "The system's font and its text scaling, or a step larger (ADR 0045).",
    ),
    Preference(
        "interface.toasts",
        "Interface",
        "Notes when work finishes",
        True,
        [("Show them", True), ("Status bar only", False)],
        "A short note at the window's corner when a run, mesh or export finishes, with an "
        "action such as Show results.",
    ),
    Preference(
        "interface.heading_colours",
        "Interface",
        "Heading colours",
        True,
        [("By level", True), ("Plain", False)],
        "Headings in the model tree and Properties coloured by depth: the project and its "
        "sections one colour, what is under them the next; items that don't expand stay plain.",
    ),
    Preference(
        "interface.view_background",
        "Interface",
        "3D background",
        "gradient",
        [
            ("Soft gradient", "gradient"),
            ("Plain", "plain"),
            ("A colour of my own", "colour"),
            ("A gradient of my own", "colour_gradient"),
        ],
        "Behind the model in the 3D view (Window → 3D view background… picks a colour).",
    ),
    Preference(
        "interface.view_colour",
        "Interface",
        "My background colour",
        "#2b2f36",
        help="A colour of my own; a gradient of my own runs from it at the bottom.",
        kind="colour",
    ),
    Preference(
        "interface.view_colour_top", "Interface", "My gradient's top", "#6a7382", kind="colour"
    ),
    # ADR 0069: a 6-DOF navigation device (3Dconnexion SpaceMouse and compatibles).
    Preference(
        "mouse3d.enabled",
        "3D mouse",
        "3D mouse",
        True,
        [("On when one is plugged in", True), ("Off", False)],
        "A SpaceMouse or similar steers the 3D view: push, pull and twist the cap.",
    ),
    Preference(
        "mouse3d.speed",
        "3D mouse",
        "Speed",
        1.0,
        [("Slow", 0.4), ("Normal", 1.0), ("Fast", 2.0), ("Fastest", 4.0)],
    ),
    Preference(
        "mouse3d.mode",
        "3D mouse",
        "What moves",
        "object",
        [
            ("The model (push the cap, the model goes that way)", "object"),
            ("The camera (push the cap, the view goes that way)", "camera"),
        ],
    ),
    Preference(
        "mouse3d.motion",
        "3D mouse",
        "Motion",
        "both",
        [("Turn and move", "both"), ("Turn only", "rotate"), ("Move only", "translate")],
    ),
    Preference(
        "mouse3d.dominant",
        "3D mouse",
        "One axis at a time",
        False,
        [("Off", False), ("On: only the strongest push or twist", True)],
        "Steadier for exact moves: the cap's largest motion alone moves the view.",
    ),
    Preference(
        "mouse3d.invert_zoom",
        "3D mouse",
        "Zoom",
        False,
        [("Pull up to bring the model nearer", False), ("Push down to bring it nearer", True)],
    ),
    Preference(
        "mouse3d.button_1",
        "3D mouse",
        "Left button",
        "fit",
        [
            ("Fit all", "fit"),
            ("Isometric", "iso"),
            ("Refresh view", "refresh_view"),
            ("Section cut", "section"),
            ("Nothing", ""),
        ],
    ),
    Preference(
        "mouse3d.button_2",
        "3D mouse",
        "Right button",
        "iso",
        [
            ("Fit all", "fit"),
            ("Isometric", "iso"),
            ("Refresh view", "refresh_view"),
            ("Section cut", "section"),
            ("Nothing", ""),
        ],
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
        "geometry.bolt_colour",
        "Geometry",
        "Bolt colour",
        "#d9a520",
        help="Bolts in the view: their shanks, heads and nuts (drawing only, ADR 0050).",
        kind="colour",
    ),
    Preference(
        "geometry.bolt_opacity",
        "Geometry",
        "Bolt opacity",
        0.5,
        [(f"{p} %", p / 100) for p in (100, 75, 50, 25, 10)],
        "How solid bolts are drawn: see-through shows the holes and parts behind them.",
    ),
    Preference(
        "geometry.bolt_edges",
        "Geometry",
        "Bolt edges",
        True,
        [("Outlined", True), ("None", False)],
        "Bolts' outlines (the shank's rims, the heads' and nuts' corners) drawn as lines.",
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
    *_legend_preferences(),
    Preference(
        "advisor.open_new",
        "Interface",
        "Open the Advisor for new projects",
        True,
        [("Yes", True), ("No", False)],
        "The Advisor lists what a project needs next (Window → Advisor). Closing it once "
        "turns this off.",
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
        "versions.auto",
        "Versions",
        "Automatic versions",
        True,
        [("On", True), ("Off: only the ones I save", False)],
        "Puzzle keeps a version of the project before each run, after a run of changes "
        "and before a restore, so you can roll back (ADR 0059). Versions you save are "
        "always kept.",
    ),
    Preference(
        "versions.every_edits",
        "Versions",
        "A version after this many changes",
        20,
        [(str(n), n) for n in (5, 10, 20, 50, 100)],
        "Or after the time below, whichever comes first.",
    ),
    Preference(
        "versions.every_minutes",
        "Versions",
        "Or after this long",
        15,
        [("5 minutes", 5), ("15 minutes", 15), ("30 minutes", 30), ("1 hour", 60)],
    ),
    Preference(
        "versions.keep_auto",
        "Versions",
        "Automatic versions kept",
        30,
        [("10", 10), ("30", 30), ("100", 100), ("All", 0)],
        "Older automatic versions are thinned to one a day. Yours and the known good "
        "ones are kept until you delete them.",
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
    Preference(
        "solvers.threads",
        "Solvers",
        "Cores per run",
        0,
        _core_choices(),
        "How many processor cores one solver run may use (threads, or threads times "
        "processes). Automatic is 4, or one fewer than the physical cores on a smaller "
        "computer, so it stays usable while solving; finite element solves gain little past "
        "that, and nothing from a second thread per core. Your own OMP_NUM_THREADS or "
        "CCX_NPROC_* win.",
    ),
    Preference(
        "solvers.processes",
        "Solvers",
        "Parallel processes per run",
        1,
        _process_choices(),
        "Splits the model across this many processes (MPI) where the solver can (Code_Aster; "
        "Elmer's 3D magnetic studies); the cores per run are shared out among them, so each "
        "gets cores ÷ "
        "processes threads. One process is threads only.",
    ),
    Preference(
        "solvers.stall_minutes",
        "Solvers",
        "Stall warning",
        10,
        [
            ("Off", 0),
            ("2 minutes", 2),
            ("5 minutes", 5),
            ("10 minutes", 10),
            ("20 minutes", 20),
            ("45 minutes", 45),
        ],  # fmt: skip
        "The Solution panel warns when a running solve has reported nothing (no "
        "increment, iteration or message) for this long; the run keeps going unless you "
        "stop it (ADR 0054).",
    ),
    Preference(
        "solvers.runner",
        "Solvers",
        "Run solves through the job runner",
        True,
        [("On: solves outlive Puzzle", True), ("Off: solves stop when Puzzle closes", False)],
        "On, each solve is handed to Puzzle's job runner (ADR 0067), here or on a remote "
        "host: it keeps going if Puzzle closes, the computer sleeps or the connection "
        "drops, and is picked up again when the project is opened. Off, solves run inside "
        "Puzzle as before.",
    ),
    Preference(
        "solvers.build_pardiso",
        "Solvers",
        "Build CalculiX with PARDISO",
        True,
        [("On: built once in the background", True), ("Off: the plain CalculiX only", False)],
        "On, Puzzle builds CalculiX with Intel MKL's PARDISO on this computer once, in the "
        "background (ADR 0068, 0051): contact and other large solves factor 2 to 4 times "
        "faster, with the same answers. The plain CalculiX is used until it is done, and "
        "if the build fails. Settings > Solvers shows how it stands.",
    ),
    *_connection_preferences(),
    Preference(
        "schematic.uses",
        "Schematic",
        "Use wires",
        "all",
        [("All", "all"), ("For the selected node", "selected"), ("None", "none")],
        "The thin grey wires saying what each thing is built from (a geometry into the "
        "components using it, a component into its studies). All shows sharing at a "
        "glance; the selected node's own wires always come forward (ADR 0058).",
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
            self.data = _flatten(tomllib.loads(self.path.read_text(encoding="utf-8")))

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
        tmp.write_text(tomli_w.dumps(_nest(self.data)), encoding="utf-8")
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
