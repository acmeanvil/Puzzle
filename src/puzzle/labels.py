"""Keys as the words people read: "solid_mechanics" -> "Solid mechanics", a solver's key
-> its name, a stored run -> study, solver and local time.

The GUI's choices (`puzzle.gui.choices`), the Advisor, the symbols drawn in the view and
the thread set-up all name things this way; it lives in the core, with no Qt, so that
none of them imports the GUI (ADR 0003; ADR 0090 package 16).
"""

from __future__ import annotations

from typing import Any

from puzzle.adapters import SOLVER_NAMES as SOLVERS

NONE = "(none)"
# Words kept as written when a value is turned into a label.
WORDS = {
    "srss": "SRSS",
    "pde": "PDE",  # "Custom PDE" (UX-38)
    "bdf2": "BDF2",
    "sst": "SST",
    "mcp": "MCP",
    "3d": "3D",
    "2d": "2D",
    "dg": "DG",
    "hht": "HHT",
    "xy": "XY",
    "yz": "YZ",
    "zx": "ZX",
}

# Values whose words aren't their key's (ADR 0050 bolt standards and preloads).
VALUES = {
    # An imported field's file format (ADR 0044)
    "csv": "Delimited text",
    "vtu": "VTK unstructured grid",
    "fluent_profile": "Fluent profile",
    "exodus": "Exodus II",
    "ensight": "EnSight Gold",
    "openfoam": "OpenFOAM",
    # A contact's interface treatment (ADR 0056)
    "as_is": "As meshed",
    "adjust": "Adjust to touch",
    "offset": "Offset (clearance or interference)",
    "iso_coarse": "ISO metric, coarse pitch",
    "iso_fine": "ISO metric, fine pitch",
    "unc": "Unified coarse (UNC)",
    "unf": "Unified fine (UNF)",
    "yield_percent": "Share of the yield load",
    "hole_walls": "Hole wall to hole wall",
    "through_nut": "Bolt and nut (head and nut annuli)",
    "tapped": "Cap screw in a tapped hole (head annulus, thread)",
    "stud_nuts": "Stud with two nuts (nut annuli)",
    "stud_tapped": "Stud in a tapped hole, with a nut (thread, nut annulus)",
    # A modelled bolt's washers (ADR 0077)
    "both": "Under the head and the nut",
    "extra_large": "Extra large",
    # A thread's axis from a coordinate system (ADR 0076: Thread.cs)
    "cs": "Axis from a coordinate system",
    # An optimisation's method and surface (ADR 0063)
    "sobol": "Sobol sequence",
    "cobyla": "COBYLA",
    "nelder_mead": "Nelder–Mead",
    "bayesian": "Bayesian (Gaussian process)",
    "rbf": "Radial basis functions",
    # ADR 0085: the explicit step and its precision
    "explicit": "Explicit dynamics",
}


def label(value: Any) -> str:
    """A key as words: "solid_mechanics" -> "Solid mechanics", "bdf2" -> "BDF2"."""
    if value is None:
        return NONE
    if not isinstance(value, str):
        return str(value)
    if value in VALUES:
        return VALUES[value]
    words = value.replace("_", " ").split(" ")
    out = [WORDS.get(w.lower(), w) for w in words]
    text = " ".join(out)
    return text[:1].upper() + text[1:] if text and text[:1].islower() else text


def solver_label(key: str) -> str:
    return SOLVERS.get(key, label(key))


def run_label(record) -> str:
    """A stored run as panels list it: study, solver by name, local time (PANEL-18)."""
    when = record.recorded_at.astimezone().strftime("%Y-%m-%d %H:%M")
    return f"{record.study_name} ({solver_label(record.solver)}, {when})"
