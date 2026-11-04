"""Elmer magnetodynamics (ADR 0047): time-harmonic A–V, and static windings.

- WhitneyAVHarmonicSolver, one solve per frequency of a Scanning run (`Angular
  Frequency` a table over the scan index), solved iteratively: UMFPACK fails on the
  harmonic A–V matrix (singular as Elmer passes it), BiCGStab(4) converges in tens of
  iterations. The static solve (WhitneyAVSolver) stays direct.
- A massive conductor (`coil_current`) gets its total current as a uniform normal
  current density on its start face (I / the face's area as meshed, real and imaginary
  parts) and V = 0 on its end face; both faces n × A = 0.
- A winding (`stranded_coil`) runs CoilSolver first (unit conductivity, 1 A, its
  `CoilCurrent` flowing from the end face to the start one; each winding a Component
  with a Coil Type, or CoilSolver scales them all as one coil), then imposes that field
  times −N I (real and imaginary) as the source current density; its body has σ = 0
  in the solve, so carries no eddy current. A winding with no terminal faces is closed
  (`Coil Closed`).
- MagnetoDynamicsCalcFields writes B, J, A and E per element and per node; the reader
  (`read_magnetics`) computes losses, energy and impedance from the element values with
  `adapters.eddy`, the same as FEniCSx.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

from puzzle.adapters import eddy
from puzzle.adapters.common import InputError, frequency_list, surface_area
from puzzle.results.model import Results


def needed(step, features) -> bool:
    """Whether this writer handles the study (else the static one-coil path does)."""
    return step.type == "frequency_domain" or any(f.type == "stranded_coil" for f in features)


def iterative(tolerance: float = 1e-9) -> list[str]:
    return [
        "  Linear System Solver = Iterative",
        "  Linear System Iterative Method = BiCGStabl",
        "  BiCGStabl Polynomial Degree = 4",
        "  Linear System Preconditioning = None",
        "  Linear System Max Iterations = 6000",
        f"  Linear System Convergence Tolerance = {tolerance:g}",
        "  Linear System Residual Output = 50",
    ]


DIRECT = ["  Linear System Solver = Direct", "  Linear System Direct Method = Umfpack"]


def write(project, comp, step, features, mesh, workdir: Path, write_mesh, time_block):
    """The .sif lines and the Elmer mesh; writes the mesh, the element map and the
    reader's metadata."""
    from puzzle.model.schema import Material

    harmonic = step.type == "frequency_domain"
    if step.type not in ("stationary", "frequency_domain"):
        raise InputError(f"Elmer does not run {step.type} magnetic fields yet")
    coils = eddy.coils(project, comp, features, mesh, solver="Elmer")
    if not harmonic:
        if any(c.kind == "massive" for c in coils):
            raise InputError(
                "a static magnetic study takes one massive conductor or windings, not both",
                code="unsupported",
            )
        if any(c.current[1] for c in coils):
            raise InputError("a coil's phase applies in frequency-domain steps only")
    windings = [c for c in coils if c.kind == "stranded"]
    if len({c.closed for c in windings}) > 1:
        raise InputError("Elmer takes windings that are all open or all closed loops for now")
    boundaries = list(dict.fromkeys(f.selection for f in features if f.type not in eddy.COILS))
    bodies = [a.selection for a in comp.materials]
    emesh = write_mesh(mesh, workdir / "mesh", bodies, boundaries)
    eddy.element_table(mesh, bodies, workdir / "mesh" / eddy.ELEMENTS)

    frequencies = frequency_list(step) if harmonic else [0.0]
    winding_of = {c.body: c for c in windings}
    meta_bodies = []
    out = [
        "Header",
        "  CHECK KEYWORDS Warn",
        '  Mesh DB "." "mesh"',
        "End",
        "",
        "Simulation",
        "  Max Output Level = 5",
        "  Coordinate System = Cartesian 3D",
        *time_block(step),
        *(_angular(frequencies) if harmonic else []),
        "End",
        "",
        "Constants",
        "  Permittivity Of Vacuum = 8.8541878128e-12",
        "End",
        "",
    ]
    forces = []
    for i, a in enumerate(comp.materials, start=1):
        m = project.get(a.material, Material)
        p = {k: q.to_si() for k, q in m.properties.items()}
        missing = [k for k in ("relative_permeability", "electrical_conductivity") if k not in p]
        if missing:
            raise InputError(f"material '{m.name}' is missing {', '.join(missing)}")
        sigma = 0.0 if a.selection in winding_of else p["electrical_conductivity"]
        meta_bodies.append(
            {
                "name": a.selection,
                "mu_r": p["relative_permeability"],
                "sigma": p["electrical_conductivity"] if a.selection not in winding_of else 0.0,
            }
        )
        out += [
            f"Body {i}",
            f"  Target Bodies(1) = {i}",
            f"  Equation = {1 if a.selection in winding_of or not windings else 2}",
            f"  Material = {i}",
        ]
        if a.selection in winding_of:
            forces.append(_source(winding_of[a.selection], harmonic))
            out.append(f"  Body Force = {len(forces)}")
        out += [
            "End",
            "",
            f"Material {i}",
            f'  Name = "{m.name}"',
            f"  Relative Permeability = {p['relative_permeability']:.10g}",
            f"  Electric Conductivity = {sigma:.10g}",
            "End",
            "",
        ]

    for k, c in enumerate(windings, start=1):  # each normalised on its own (Coil Type)
        out += [
            f"Component {k}",
            f'  Name = "{c.body}"',
            f"  Master Bodies(1) = {bodies.index(c.body) + 1}",
            '  Coil Type = String "stranded"',
            "  Desired Coil Current = Real 1",
            "End",
            "",
        ]
    solvers = []
    if windings:
        solvers.append(
            [
                '  Equation = "CoilSolver"',
                '  Procedure = "CoilSolver" "CoilSolver"',
                "  Exec Solver = Before All",
                "  Desired Coil Current = Real 1",
                "  Use Unity Conductivity = Logical True",
                *(["  Coil Closed = Logical True"] if windings[0].closed else []),
                "  Calculate Elemental Fields = Logical True",
                '  Linear System Solver = "Iterative"',
                "  Linear System Iterative Method = BiCGStabL",
                "  Linear System Preconditioning = ILU1",
                "  Linear System Max Iterations = 5000",
                "  Linear System Convergence Tolerance = 1e-12",
            ]
        )
    av = (
        [
            '  Variable = "AV[AV re:1 AV im:1]"',
            '  Procedure = "MagnetoDynamics" "WhitneyAVHarmonicSolver"',
            *iterative(),
        ]
        if harmonic
        else [
            '  Variable = "AV"',
            '  Procedure = "MagnetoDynamics" "WhitneyAVSolver"',
            "  Use Magnetic Norm = Logical True",
            *DIRECT,
        ]
    )
    solvers.append(
        [
            '  Equation = "MGDynamics"',
            *av,
            "  Linear System Symmetric = True",
            *(["  Fix Input Current Density = Logical True"] if windings else []),
        ]
    )
    solvers.append(
        [
            '  Equation = "MGDynamicsCalc"',
            '  Procedure = "MagnetoDynamics" "MagnetoDynamicsCalcFields"',
            '  Potential Variable = String "AV"',
            "  Calculate Current Density = Logical True",
            "  Calculate Magnetic Vector Potential = Logical True",
            "  Calculate Nodal Fields = Logical True",
            "  Calculate Elemental Fields = Logical True",
            "  Impose Body Force Current = Logical True",
            "  Linear System Symmetric = True",
            '  Linear System Solver = "Iterative"',
            "  Linear System Iterative Method = CG",
            "  Linear System Preconditioning = None",
            "  Linear System Max Iterations = 5000",
            "  Linear System Convergence Tolerance = 1e-8",
        ]
    )
    solvers.append(
        [
            f"  Exec Solver = {'After Timestep' if harmonic else 'After Simulation'}",
            '  Equation = "Result Output"',
            '  Procedure = "ResultOutputSolve" "ResultOutputSolver"',
            '  Output File Name = "case"',
            "  Vtu Format = True",
            "  Ascii Output = True",
        ]
    )
    n = len(solvers)
    field_solvers = " ".join(str(k) for k in range(2 if windings else 1, n))
    if windings:
        out += ["Equation 1", f"  Active Solvers({n - 1}) = 1 {field_solvers}", "End", ""]
    out += [
        f"Equation {1 + bool(windings)}",
        f"  Active Solvers({n - 1 - bool(windings)}) = {field_solvers}",
        "End",
        "",
    ]
    for k, block in enumerate(forces, start=1):
        out += [f"Body Force {k}", *block, "End", ""]
    for k, block in enumerate(solvers, start=1):
        out += [f"Solver {k}", *block, "End", ""]

    starts = {c.start: c for c in coils if c.kind == "massive"}
    ends = {c.end for c in coils if c.kind == "massive"}
    for name in boundaries:
        kinds = {f.type for f in features if f.selection == name}
        block = [
            f"Boundary Condition {emesh.boundaries[name]}",
            f"  Target Boundaries(1) = {emesh.boundaries[name]}",
            f'  Name = "{name}"',
        ]
        tangential = (
            ["  AV re {e} = Real 0", "  AV im {e} = Real 0"] if harmonic else ["  AV {e} = Real 0"]
        )
        if name in starts:
            c = starts[name]
            area = surface_area(mesh.groups[name], mesh)
            block += [
                f"  Electric Current Density = Real {c.current[0] / area:.10g}",
                f"  Electric Current Density im = Real {c.current[1] / area:.10g}",
                *tangential,
            ]
        elif name in ends:
            block += [
                *tangential,
                *(["  AV re = Real 0", "  AV im = Real 0"] if harmonic else ["  AV = Real 0"]),
            ]
        elif kinds & set(eddy.TERMINALS):  # a winding's terminals
            flag = "Coil Start" if "coil_start" in kinds else "Coil End"
            block += [f"  {flag} = Logical True", *tangential]
        elif "magnetic_insulation" in kinds:
            block += tangential  # B·n = 0
        # magnetic_symmetry: the natural condition (n × H = 0, flux normal): nothing
        out += [*block, "End", ""]
    eddy.write_meta(workdir / "mesh" / eddy.META, meta_bodies, coils, frequencies)
    return out, emesh


def _angular(frequencies: list[float]) -> list[str]:
    """ω for each scan, in the Simulation section so every solver sees it."""
    rows = [f"    {k} {2 * math.pi * f:.12g}" for k, f in enumerate(frequencies, start=1)]
    return ["  Angular Frequency = Variable Time", "    Real", *rows, "    End"]


def _source(c: eddy.Coil, harmonic: bool) -> list[str]:
    """A winding's current density: CoilSolver's 1 A field times −N I (it flows from
    the end face to the start one)."""
    scale = -c.turns * c.phasor
    lines = []
    for part, value in (("", scale.real), (" im", scale.imag)) if harmonic else (("", scale.real),):
        for k in (1, 2, 3):
            lines += [
                f'  Current Density{part} {k} = Variable "CoilCurrent {k}"',
                f'    Real MATC "tx*({value:.12g})"',
            ]
    return lines


def _cells(path: Path, count: int) -> dict[str, np.ndarray]:
    """The first `count` cells' data by name: the volume elements, in mesh.elements'
    order (the boundary elements come after them)."""
    root = ET.parse(path).getroot()
    data = {}
    for d in root.find(".//CellData").iter("DataArray"):
        k = int(d.get("NumberOfComponents", "1"))
        data[d.get("Name")] = np.array((d.text or "").split(), float).reshape(-1, k)[:count]
    return data


def read_magnetics(vtus: list[Path], mesh_dir: Path, node_ids: np.ndarray) -> Results:
    """One result step per frequency (or the static solve) from Elmer's output."""
    from puzzle.adapters.elmer.output import read_vtu

    meta = eddy.read_meta(mesh_dir / eddy.META)
    table = np.load(mesh_dir / eddy.ELEMENTS)
    position = {int(n): i for i, n in enumerate(node_ids)}
    results = Results(solver="elmer")
    for vtu, hz in zip(vtus, meta["frequencies"], strict=True):
        nodal = read_vtu(vtu)
        cells = {k.removesuffix(" e"): v for k, v in _cells(vtu, len(table["ids"])).items()}

        def cplx(data, name, rows):
            """A harmonic run's `<name> re` + j `<name> im`, a static one's `<name>`."""
            re = data.get(f"{name} re", data.get(name))
            if re is None:  # a static run writes no nodal V (no massive conductor reads it)
                return np.zeros((rows, 1), complex)
            im = data.get(f"{name} im")
            return re + 1j * im if im is not None else re + 0j

        n = len(table["ids"])
        V = cplx(nodal, "av", len(node_ids))[:, 0]
        start = {c.body: complex(np.mean([V[position[k]] for k in c.start_nodes]))
                 for c in meta["coils"] if c.kind == "massive"}  # fmt: skip
        results.steps.append(eddy.step_result(
            meta, hz, table, cplx(cells, "magnetic flux density", n),
            cplx(cells, "current density", n), cplx(cells, "magnetic vector potential", n),
            start, node_ids, cplx(nodal, "magnetic flux density", len(node_ids)),
        ))  # fmt: skip
    return results
