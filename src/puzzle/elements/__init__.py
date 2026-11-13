"""The element library (ADR 0082 step 1): every solver's elements as data, what each is
for, which studies take it, and whether Puzzle writes it yet.

One module per solver holds its rows (`calculix`, `code_aster`, `elmer`, `fenicsx`,
`code_saturne`, `openfoam`, `pynite`), each citing its manual's section. `listing`
groups them by family and formulation, a solver's type in each row's column, for the
docs (`docs/elements.md`), Help → Element library, `puzzle elements` and MCP
`elements_list`. A test keeps the tables to the vocabularies below and every `written`
row true of its adapter.
"""

from __future__ import annotations

from dataclasses import dataclass, field

FAMILIES = {
    "solid_3d": "3D solids",
    "solid_2d": "2D solids (plane stress, plane strain, axisymmetric)",
    "shell": "Shells",
    "beam": "Beams",
    "truss": "Trusses and cables",
    "discrete": "Springs, gaps, masses and other point elements",
    "thermal": "Heat transfer",
    "electromagnetic": "Electric and magnetic fields",
    "fluid": "Fluid cells (finite volume)",
}
FORMULATIONS = {
    "full": "full integration",
    "reduced": "reduced integration",
    "incompatible_modes": "incompatible modes (bending without locking)",
    "mixed_up": "mixed displacement-pressure (nearly incompressible)",
    "hybrid": "hybrid (incompressible)",
    "thin_shell": "thin shell (Kirchhoff)",
    "thick_shell": "thick shell (Mindlin-Reissner)",
    "euler_bernoulli": "Euler-Bernoulli beam",
    "timoshenko": "Timoshenko beam (shear deformation)",
    "warping": "beam with warping (open sections in torsion)",
    "large_rotation": "large rotations",
    "lumped": "lumped (diagonal) capacity or mass",
    "higher_order": "higher order on the same cells (p-elements)",
    "lagrange": "Lagrange (nodal) unknowns",
    "edge": "edge (Nédélec, Whitney) unknowns",
    "finite_volume": "finite-volume cell",
    "discrete": "point element",
}
STUDIES = {
    "static": "static",
    "modal": "modal",
    "buckling": "buckling",
    "transient": "structural dynamics",
    "harmonic": "harmonic response",
    "contact": "contact",
    "plasticity": "plasticity",
    "large_deflection": "large deflection",
    "heat": "heat transfer",
    "electrostatics": "electrostatics",
    "magnetics": "magnetic fields",
    "flow": "fluid flow",
}
SHAPES = ("tet4", "tet10", "hex8", "hex20", "hex27", "wedge6", "wedge15", "pyramid5",
          "tri3", "tri6", "quad4", "quad8", "quad9", "line2", "line3", "point",
          "polyhedron")  # fmt: skip
SOLVERS = ("calculix", "code_aster", "elmer", "fenicsx", "pynite", "code_saturne", "openfoam")


@dataclass(frozen=True)
class Element:
    native: str  # the solver's own name: C3D20R, 3D_INCO_UPG, 820, P2
    family: str
    shapes: tuple[str, ...]
    formulation: str
    studies: tuple[str, ...]
    use: str  # what it is for, in a line
    avoid: str = ""  # what it is not for
    written: bool = False  # Puzzle writes it today
    reference: str = ""  # the manual's section
    physics: tuple[str, ...] = field(default=("solid_mechanics",))


def table(solver: str) -> list[Element]:
    """A solver's rows."""
    from importlib import import_module

    return list(import_module(f"puzzle.elements.{solver}").ELEMENTS)


def everything() -> dict[str, list[Element]]:
    return {s: table(s) for s in SOLVERS}


def query(solver: str | None = None, family: str | None = None, study: str | None = None,
          written: bool | None = None) -> list[tuple[str, Element]]:  # fmt: skip
    """(solver, element) rows matching every filter given."""
    out = []
    for name, rows in everything().items():
        if solver and name != solver:
            continue
        for e in rows:
            if family and e.family != family:
                continue
            if study and study not in e.studies:
                continue
            if written is not None and e.written != written:
                continue
            out.append((name, e))
    return out


SOLVER_NAMES = {"calculix": "CalculiX", "code_aster": "Code_Aster", "elmer": "Elmer",
                "fenicsx": "FEniCSx", "pynite": "PyNite", "code_saturne": "Code_Saturne",
                "openfoam": "OpenFOAM"}  # fmt: skip


LINEAR = {"tet4", "hex8", "wedge6", "pyramid5", "tri3", "quad4", "line2"}
QUADRATIC = {"tet10", "hex20", "hex27", "wedge15", "tri6", "quad8", "quad9", "line3"}


def order(shapes) -> str:
    """Linear, quadratic, either, or (points and polyhedra) neither."""
    s = set(shapes)
    lin, quad = bool(s & LINEAR), bool(s & QUADRATIC)
    return "linear and quadratic" if lin and quad else "linear" if lin else (
        "quadratic" if quad else "–")  # fmt: skip


def listing(solver: str | None = None, family: str | None = None,
            study: str | None = None) -> str:  # fmt: skip
    """The grouped, distilled listing as Markdown: per family, a table of formulations
    and shapes with each solver's element there (a star where Puzzle writes it), then
    each element's use."""
    rows = query(solver, family, study)
    lines = ["# Element library", "",
             "Every solver's elements, grouped by what they model. ★ marks the ones Puzzle "
             "writes today (ADR 0082); the rest are listed so you can see what each solver "
             "offers and ask for it.", ""]  # fmt: skip
    for fam, title in FAMILIES.items():
        mine = [(n, e) for n, e in rows if e.family == fam]
        if not mine:
            continue
        lines += [f"## {title}", ""]
        solvers = [s for s in SOLVERS if any(n == s for n, _ in mine)]
        keys: dict[tuple[str, str], dict[str, list[str]]] = {}
        for n, e in mine:
            cell = keys.setdefault((e.formulation, order(e.shapes)), {})
            cell.setdefault(n, []).append(e.native + (" ★" if e.written else ""))
        names = " | ".join(SOLVER_NAMES[s] for s in solvers)
        lines += [f"| Formulation | Order | {names} |", "|" + " --- |" * (2 + len(solvers))]
        for (form, level), by in keys.items():
            cells = " | ".join(", ".join(by.get(s, [])) or "–" for s in solvers)
            lines.append(f"| {FORMULATIONS[form]} | {level} | {cells} |")
        lines += [""]
        for n, e in mine:
            studies = ", ".join(STUDIES[s] for s in e.studies)
            text = f"- **{SOLVER_NAMES[n]} {e.native}**: {e.use}"
            if e.avoid:
                text += f" Not for: {e.avoid}"
            text += f" Studies: {studies}."
            if e.reference:
                text += f" ({e.reference})"
            lines.append(text)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
