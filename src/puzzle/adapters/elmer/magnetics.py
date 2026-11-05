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
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

from puzzle.adapters import eddy
from puzzle.adapters.common import InputError, frequency_list, surface_area
from puzzle.results.model import Results


def needed(step, features) -> bool:
    """Whether this writer handles the study (else the static one-coil path does)."""
    return step.type in ("frequency_domain", "transient") or any(
        f.type == "stranded_coil" for f in features
    )


def iterative(tolerance: float = 1e-9, preconditioner: str = "None") -> list[str]:
    return [
        "  Linear System Solver = Iterative",
        "  Linear System Iterative Method = BiCGStabl",
        "  BiCGStabl Polynomial Degree = 4",
        f"  Linear System Preconditioning = {preconditioner}",
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
    transient = step.type == "transient"  # ADR 0047 stage 2: A–V in time
    coils = eddy.coils(project, comp, features, mesh, solver="Elmer")
    if transient and any(c.current[1] for c in coils):
        raise InputError("a coil's phase applies in frequency-domain steps only")
    if transient and step.settings.get("time_scheme", "backward_euler") != "backward_euler":
        # Measured on the transient skin effect: Elmer's BDF2 on the A–V system lags like
        # backward Euler and worse, not second order (FEniCSx's BDF2 is).
        raise InputError("Elmer solves magnetic transients by backward Euler only; FEniCSx "
                         "also takes bdf2", code="unsupported")  # fmt: skip
    if step.type == "stationary":
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
    times = transient_times(step) if transient else None
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
            *(
                [
                    f"  Coil Center(3) = {' '.join(f'{v:.10g}' for v in c.centre)}",
                    f"  Coil Normal(3) = {' '.join(f'{v:.10g}' for v in c.axis)}",
                ]
                if c.closed and c.centre
                else []
            ),
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
                # A uniform density along the flow carrying the component's 1 A (each
                # Component has Coil Type stranded), scaled by N I in the body force:
                # a winding's turns carry equal currents.
                "  Normalize Coil Current = Logical True",
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
            # A closed winding's source makes UMFPACK's matrix singular: iterate then.
            # In time the σ/Δt term makes BiCGStab converge; UMFPACK took minutes a step.
            # No preconditioner: ILU0 cut the iterations to 15 but diverged after a few
            # steps on a finer mesh (the A–V system is singular in the air, ungauged).
            *(iterative() if transient or any(c.closed for c in windings) else DIRECT),
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
            *_at_outputs(step),
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
            f"  Exec Solver = {'After Timestep' if harmonic or transient else 'After Simulation'}",
            '  Equation = "Result Output"',
            '  Procedure = "ResultOutputSolve" "ResultOutputSolver"',
            '  Output File Name = "case"',
            *_at_outputs(step),
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
            if harmonic:
                block += [
                    f"  Electric Current Density = Real {c.current[0] / area:.10g}",
                    f"  Electric Current Density im = Real {c.current[1] / area:.10g}",
                ]
            else:  # static, or in time: a step from t = 0, or following its amplitude
                from puzzle.adapters.elmer.sif import _in_time

                block += _in_time("Electric Current Density", c.current[0] / area, c.amplitude)
            block += tangential
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
    eddy.write_meta(workdir / "mesh" / eddy.META, meta_bodies, coils, frequencies, times)
    return out, emesh


def _angular(frequencies: list[float]) -> list[str]:
    """ω for each scan, in the Simulation section so every solver sees it."""
    rows = [f"    {k} {2 * math.pi * f:.12g}" for k, f in enumerate(frequencies, start=1)]
    return ["  Angular Frequency = Variable Time", "    Real", *rows, "    End"]


def _at_outputs(step) -> list[str]:
    """A transient's field calculation and output only at its output steps (every
    `output_every`-th: Elmer counts Exec Intervals from step 1, hence the offset)."""
    every = int(step.settings.get("output_every", 1)) if step.type == "transient" else 1
    if every <= 1:
        return []
    return [f"  Exec Intervals = {every}", f"  Exec Intervals Offset = {every - 1}"]


def transient_times(step) -> list[float]:
    """A transient step's output times (s): every `output_every`-th step."""
    from puzzle.adapters.common import time_settings

    dt, _, n = time_settings(step)
    every = int(step.settings.get("output_every", 1))
    return [k * dt for k in range(every, n + 1, every)]


def _source(c: eddy.Coil, harmonic: bool) -> list[str]:
    """A winding's current density: CoilSolver's field, normalised as for a stranded
    coil to a uniform density carrying its component's 1 A (from the end face to the
    start one), times −N I; in time, times its amplitude."""
    scale = -c.turns * c.phasor
    lines = []
    if c.amplitude and not harmonic:
        from puzzle.adapters.elmer.sif import _matc_amplitude

        factor = _matc_amplitude(c.amplitude, "tx(1)")
        for k in (1, 2, 3):
            lines += [f'  Current Density {k} = Variable "CoilCurrent {k}", Time',
                      f'    Real MATC "tx(0)*({scale.real:.12g})*({factor})"']  # fmt: skip
        return lines
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
    times = meta.get("times")
    steps = [(0.0, t) for t in times] if times else [(hz, None) for hz in meta["frequencies"]]
    parts = _partitions(vtus)
    if parts:  # an MPI run: one file per partition and step, merged by position
        coords = np.loadtxt(mesh_dir / "mesh.nodes", usecols=(2, 3, 4), ndmin=2)
        frames = [_merged(files, coords, table) for files in parts]
    else:
        frames = [(read_vtu(v), {k.removesuffix(" e"): a for k, a in
                                 _cells(v, len(table["ids"])).items()}) for v in vtus]  # fmt: skip
    if times and len(frames) > len(times):  # Elmer wrote every step: keep the output steps
        every = len(frames) // len(times)
        frames = frames[every - 1 :: every][: len(times)]
    for (nodal, cells), (hz, time) in zip(frames, steps, strict=True):

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
            start, node_ids, cplx(nodal, "magnetic flux density", len(node_ids)), time,
        ))  # fmt: skip
    return results


PART = re.compile(r"_(\d+)np(\d+)_t(\d+)\.vtu$")  # case_4np2_t0007.vtu: 4 parts, part 2


def _partitions(vtus: list[Path]) -> list[list[Path]]:
    """An MPI run's files grouped by output step (each step's partitions), or []."""
    steps: dict[int, list[Path]] = {}
    for v in vtus:
        m = PART.search(v.name)
        if m:
            steps.setdefault(int(m.group(3)), []).append(v)
    return [sorted(steps[k]) for k in sorted(steps)]


def _merged(files: list[Path], coords: np.ndarray, table) -> tuple[dict, dict]:
    """One step's partitions as whole-mesh arrays: nodal data in Elmer node order
    (points matched to mesh.nodes by position), element data in the element table's
    order (each volume cell matched by its corners' centre)."""
    from puzzle.adapters.common import match_points

    nodal: dict[str, np.ndarray] = {}
    cells: dict[str, np.ndarray] = {}
    n_el = len(table["ids"])
    for f in files:
        root = ET.parse(f).getroot()
        points = np.array(root.find(".//Points/DataArray").text.split(), float).reshape(-1, 3)
        rows = match_points(points, coords, np.arange(len(coords)))
        for d in root.find(".//PointData").iter("DataArray"):
            k = int(d.get("NumberOfComponents", "1"))
            values = np.array((d.text or "").split(), float).reshape(-1, k)
            nodal.setdefault(d.get("Name"), np.zeros((len(coords), k)))[rows] = values
        arrays = {d.get("Name"): d.text or "" for d in root.find(".//Cells").iter("DataArray")}
        types = np.array(arrays["types"].split(), np.int64)
        offsets = np.array(arrays["offsets"].split(), np.int64)
        conn = np.array(arrays["connectivity"].split(), np.int64)
        bulk = np.flatnonzero(np.isin(types, (10, 24)))  # tetrahedra; faces come after
        starts = np.r_[0, offsets[:-1]][bulk]
        centres = points[conn[starts[:, None] + np.arange(4)]].mean(axis=1)
        where = match_points(centres, table["centres"], np.arange(n_el))
        for d in root.find(".//CellData").iter("DataArray"):
            k = int(d.get("NumberOfComponents", "1"))
            values = np.array((d.text or "").split(), float).reshape(-1, k)[bulk]
            name = d.get("Name").removesuffix(" e")
            cells.setdefault(name, np.zeros((n_el, k)))[where] = values
    return nodal, cells


def write_axisymmetric(project, comp, step, features, mesh, workdir: Path, write_mesh,
                       time_block):  # fmt: skip
    """An axisymmetric magnetic study (ADR 0047 item 7): MagnetoDynamics2D(Harmonic) in
    A_φ, `magnetic_insulation` A = 0 (flux along the edge), `magnetic_symmetry` the
    natural condition. A Scanning run of 1 + rings scans per frequency: scan 0 the
    windings' J = N I / section, scan j a unit voltage per turn on solid ring j (J =
    σ / 2πr in it); Puzzle combines them so each ring carries its current
    (`eddy.combine_axisymmetric`). Transients are refused here."""
    from puzzle.model.schema import Material

    if step.type not in ("stationary", "frequency_domain"):
        raise InputError("axisymmetric magnetic fields run static or frequency-domain steps",
                         code="unsupported")  # fmt: skip
    harmonic = step.type == "frequency_domain"
    coils = eddy.coils(project, comp, features, mesh, solver="Elmer")
    if not harmonic and any(c.current[1] for c in coils):
        raise InputError("a coil's phase applies in frequency-domain steps only")
    boundaries = list(dict.fromkeys(f.selection for f in features if f.type not in eddy.COILS))
    bodies = [a.selection for a in comp.materials]
    emesh = write_mesh(mesh, workdir / "mesh", bodies, boundaries)
    eddy.axisymmetric_table(mesh, bodies, workdir / "mesh" / eddy.ELEMENTS)
    table = np.load(workdir / "mesh" / eddy.ELEMENTS)
    frequencies = frequency_list(step) if harmonic else [0.0]
    coil_of = {c.body: c for c in coils}
    rings = [c.body for c in coils if c.kind == "massive"]
    per = 1 + len(rings)  # scans per frequency
    scans = len(frequencies) * per

    def picks(role: int) -> str:
        """1 in this role's scans, else 0 (MATC compares to 1 or 0)."""
        return "+".join(f"(tx(0)=={k * per + role + 1})" for k in range(len(frequencies)))

    rows = [f"    {k * per + r + 1} {2 * math.pi * f:.12g}"
            for k, f in enumerate(frequencies) for r in range(per)]  # fmt: skip
    out = ["Header", "  CHECK KEYWORDS Warn", '  Mesh DB "." "mesh"', "End", "",
           "Simulation", "  Max Output Level = 5", "  Coordinate System = Axi Symmetric",
           "  Simulation Type = Scanning", f"  Timestep Intervals(1) = {scans}",
           "  Timestep Sizes(1) = 1.0", "  Output Intervals(1) = 1",
           "  Steady State Max Iterations = 1",
           *(["  Angular Frequency = Variable Time", "    Real", *rows, "    End"]
             if harmonic else []),
           "End", ""]  # fmt: skip
    forces, meta_bodies = [], []
    for i, a in enumerate(comp.materials, start=1):
        m = project.get(a.material, Material)
        p = {k: q.to_si() for k, q in m.properties.items()}
        missing = [k for k in ("relative_permeability", "electrical_conductivity") if k not in p]
        if missing:
            raise InputError(f"material '{m.name}' is missing {', '.join(missing)}")
        c = coil_of.get(a.selection)
        winding = c is not None and c.kind == "stranded"
        sigma = 0.0 if winding else p["electrical_conductivity"]
        meta_bodies.append({"name": a.selection, "mu_r": p["relative_permeability"],
                            "sigma": sigma})  # fmt: skip
        out += [f"Body {i}", f"  Target Bodies(1) = {i}", "  Equation = 1", f"  Material = {i}"]
        if winding:
            density = c.turns * c.phasor / table["area"][table["body"] == i - 1].sum()
            parts = [("", density.real)] + ([(" im", density.imag)] if harmonic else [])
            forces.append([line for part, value in parts for line in (
                f"  Current Density{part} = Variable Time, Coordinate 1",  # tx(0): the scan
                f'    Real MATC "({picks(0)})*({value:.12g})"')])  # fmt: skip
        elif c is not None:  # a solid ring: unit voltage per turn in its own scan
            if sigma <= 0:
                raise InputError(f"the solid ring '{c.body}' needs a conductivity")
            role = rings.index(c.body) + 1
            unit = f'    Real MATC "({picks(role)})*{sigma:.12g}/(2*pi*tx(1))"'
            forces.append(["  Current Density = Variable Time, Coordinate 1", unit])
        if c is not None:
            out.append(f"  Body Force = {len(forces)}")
        out += ["End", "", f"Material {i}", f'  Name = "{m.name}"',
                f"  Relative Permeability = {p['relative_permeability']:.10g}",
                f"  Electric Conductivity = {sigma:.10g}", "End", ""]  # fmt: skip
    for k, block in enumerate(forces, start=1):
        out += [f"Body Force {k}", *block, "End", ""]
    variable = '"Potential[Potential re:1 Potential im:1]"' if harmonic else "Potential"
    procedure = "MagnetoDynamics2DHarmonic" if harmonic else "MagnetoDynamics2D"
    out += ["Equation 1", "  Active Solvers(2) = 1 2", "End", "",
            "Solver 1", '  Equation = "MGDyn2D"', f"  Variable = {variable}",
            f'  Procedure = "MagnetoDynamics2D" "{procedure}"', *DIRECT, "End", "",
            "Solver 2", "  Exec Solver = After Timestep", '  Equation = "Result Output"',
            '  Procedure = "ResultOutputSolve" "ResultOutputSolver"',
            '  Output File Name = "case"', "  Vtu Format = True", "  Ascii Output = True",
            "End", ""]  # fmt: skip
    for name in boundaries:
        kinds = {f.type for f in features if f.selection == name}
        index = emesh.boundaries[name]
        block = [f"Boundary Condition {index}", f"  Target Boundaries(1) = {index}",
                 f'  Name = "{name}"']  # fmt: skip
        if "magnetic_insulation" in kinds:  # A = 0 along it: the flux stays along it
            block += (["  Potential re = Real 0", "  Potential im = Real 0"] if harmonic
                      else ["  Potential = Real 0"])  # fmt: skip
        out += [*block, "End", ""]
    eddy.write_meta(workdir / "mesh" / eddy.META, meta_bodies, coils, frequencies,
                    axisymmetric=True)  # fmt: skip
    return out, emesh


def read_axisymmetric(vtus: list[Path], mesh_dir: Path, node_ids: np.ndarray, coords) -> Results:
    """A_φ per node from Elmer, then the shared fields (`eddy.axisymmetric_fields`)."""
    from puzzle.adapters.elmer.output import read_vtu

    meta = eddy.read_meta(mesh_dir / eddy.META)
    table = dict(np.load(mesh_dir / eddy.ELEMENTS))
    xy = {int(n): c[:2] for n, c in zip(node_ids, coords, strict=True)}
    results = Results(solver="elmer")
    per = 1 + sum(c.kind == "massive" for c in meta["coils"])  # scans per frequency
    basis = []
    for vtu in vtus:
        nodal = read_vtu(vtu)
        re = nodal.get("potential re", nodal.get("potential"))[:, 0]
        im = nodal.get("potential im", np.zeros((len(re), 1)))[:, 0]
        basis.append(dict(zip(node_ids.tolist(), re + 1j * im, strict=True)))
    for k, hz in enumerate(meta["frequencies"]):
        omega = 2 * math.pi * hz
        potential, volts = eddy.combine_axisymmetric(meta, table, basis[k * per:(k + 1) * per],
                                                     omega)  # fmt: skip
        B, J, A = eddy.axisymmetric_fields(meta, table, xy, potential, omega, volts)
        b_nodal = eddy.nodal_average(table, B, node_ids)
        results.steps.append(eddy.step_result(meta, hz, table, B, J, A, volts, node_ids,
                                              b_nodal))  # fmt: skip
    return results
