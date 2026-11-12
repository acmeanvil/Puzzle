"""Elmer adapter: .sif files plus ElmerGrid meshes, VTU results. Core solver from Phase 1.

Elmer is Puzzle's electromagnetics solver as well as a structural and thermal one.

Module choices the adapter makes:

- electrostatics: StatElecSolve; electric_currents: StatCurrentSolve
- magnetic_fields, 3d: MagnetoDynamics (WhitneyAVSolver, WhitneyAVHarmonicSolver for
  frequency domain, MagnetoDynamicsCalcFields for derived fields); edge elements, 3D only
- magnetic_fields, 2d: MagnetoDynamics2D / MagnetoDynamics2DHarmonic
- electromagnetic_waves: VectorHelmholtz (frequency domain)
- solid_mechanics: StressSolve; ElasticSolve with "Apply Contact BCs" for experimental contact

Native runs: Puzzle calls ElmerSolver directly, without activating its conda
environment, so the adapter must set ELMER_HOME to that environment's prefix (the
parent of the directory holding ElmerSolver). The conda package's binaries can't
find their keyword files from the compiled-in path once relocated.

Bonded contact is Elmer's production-ready contact: conformal meshes share nodes. A
nonconformal interface would be tied with mortar boundary conditions ("Mortar BC"
pointing at the target boundary, "Contact Type" = tie), which also carry heat transfer
and magnetodynamics; that tie isn't written yet, so a nonconformal bond is refused and
not claimed (ADR 0072: claimed only once a benchmark passes).

Elmer's source also has slide, friction and stick contact types for ElasticSolve, but they
are documented only through test cases (fem/tests/Contact*) and need a hand-tuned
projector per boundary pair, so they are experimental. General mechanical contact belongs
to CalculiX and Code_Aster; a typical workflow solves electromagnetics on Elmer
and transfers the forces to a CalculiX contact study.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from puzzle.adapters.base import (
    ELECTROMAGNETIC_FEATURES,
    HEAT_SOURCE_FEATURES,
    REMOTE_FEATURES,
    STRUCTURAL_FEATURES,
    SUPPORT_FEATURES,
    SYMMETRY_FEATURES,
    THERMAL_FEATURES,
    VECTOR_FIELD_FEATURES,
    VECTOR_LOAD_FEATURES,
    Capabilities,
    SolverAdapter,
    refuse_inputs,
)
from puzzle.adapters.elmer.output import read_series
from puzzle.adapters.elmer.sif import MESH_DIR, OUTPUT, THERMAL_ZZ, TIMES, write_case
from puzzle.jobs.runner import LOG_NAME
from puzzle.mesh.mesh import Mesh
from puzzle.model.schema import Project, Study
from puzzle.results.model import Results

NODE_MAP = "puzzle-node-ids.txt"


class ElmerAdapter(SolverAdapter):
    name = "elmer"
    max_solve_steps = 1
    capabilities = Capabilities(
        physics={
            "solid_mechanics": frozenset({"stationary", "transient", "modal", "frequency_domain"}),
            "heat_transfer": frozenset({"stationary", "transient"}),
            "electrostatics": frozenset({"stationary"}),
            "electric_currents": frozenset({"stationary"}),
            "magnetic_fields": frozenset({"stationary", "transient", "frequency_domain"}),
            "electromagnetic_waves": frozenset({"frequency_domain"}),
        },
        features=STRUCTURAL_FEATURES
        | SYMMETRY_FEATURES  # ADR 0081
        | THERMAL_FEATURES
        | HEAT_SOURCE_FEATURES  # ADR 0047
        | ELECTROMAGNETIC_FEATURES
        | REMOTE_FEATURES
        | SUPPORT_FEATURES
        | VECTOR_LOAD_FEATURES,  # ADR 0044 stage 2, uniform or from fields
        shared_settings=frozenset(  # ADR 0023
            {"linear_solver", "tolerance", "max_iterations", "output_every"}
        ),
        amplitude_features=frozenset({"heat_flux", "force", "pressure"}),  # benchmarked
        axes_features=frozenset(
            {
                "gravity:cartesian",  # ADR 0049 step 5: made global by common.globalize
                "acceleration:cartesian",
                "rotation:cartesian",
                "rotation:cylindrical",
                "displacement:cartesian",
                "symmetry:oblique",  # Normal-Tangential Displacement (ADR 0081)
                "displacement:cylindrical",
                "force:cartesian",
            }
        ),  # ADR 0037: local axes
        contacts=frozenset({"bonded_conformal"}),  # a mortar tie isn't written yet
        experimental=frozenset({"frictionless", "frictional", "rough"}),
        couplings=frozenset({"thermal_expansion", "joule_heating"}),
        dimensions=frozenset({"3d", "2d_planar", "2d_axisymmetric"}),
        time_schemes=frozenset({"backward_euler", "crank_nicolson", "bdf2", "theta"}),
        # ADR 0044: face values as restart variables the boundaries read with Equals
        # (tests/integration/test_imported_fields_parity.py)
        field_features=frozenset(
            {
                "heat_generation:q",
                "body_temperature:T",
                "pressure:p",
                "heat_flux:q",
                "convection:h",
                "convection:T_inf",
            }
        )
        | VECTOR_FIELD_FEATURES,  # stage 2: tests/integration/test_imported_vectors.py
    )

    def settings_schema(self) -> list:
        """Elmer's own settings (ADR 0023); the shared ones are on each step."""
        from puzzle.adapters.elmer.sif import METHODS, PRECONDITIONERS
        from puzzle.adapters.settings import SolverSetting

        return [
            SolverSetting(
                "linear_method", "Linear solver", "Linear System Solver", "choice", "default",
                METHODS, description="Every solver section's linear system (the magnetic A-V "
                "solve keeps its direct method: iterative ones stall on it).",
            ),
            SolverSetting(
                "preconditioner", "Preconditioner", "Linear System Preconditioning", "choice",
                "default", PRECONDITIONERS, power=True,
                description="For iterative methods: ILU levels trade memory for iterations.",
            ),
            # Convergence of nonlinear solves (temperature tables, nonlinear magnetics).
            SolverSetting(
                "relaxation", "Relaxation factor", "Nonlinear System Relaxation Factor",
                "number", 1.0, maximum=1.0,
                description="Each nonlinear iteration moves this fraction of the way to its "
                "new solution: below 1 steadies an iteration that oscillates.",
            ),
            SolverSetting(
                "newton_after_iterations", "Newton after", "Nonlinear System Newton After "
                "Iterations", "integer", None, unit="iterations",
                description="Switch from fixed-point (Picard) iterations to Newton after "
                "this many: Picard is robust far from the answer, Newton fast near it.",
            ),
            SolverSetting(
                "newton_after_tolerance", "Newton below", "Nonlinear System Newton After "
                "Tolerance", "number", None, power=True,
                description="Switch to Newton once the change between iterations is below "
                "this.",
            ),
        ]  # fmt: skip

    def expert_help(self) -> str:
        return (
            "Section/Keyword = value, one per line: Solver 1/Linear System Max Iterations = "
            "10000; Solver */... for every solver; Simulation/Max Output Level = 5. A keyword "
            "Elmer doesn't list needs its type: Logical True, Real 1e-8."
        )

    def check_expert(self, entries: dict[str, str]) -> list[str]:
        from puzzle.adapters.common import InputError
        from puzzle.adapters.elmer.sif_edit import check

        problems = []
        for path, value in entries.items():
            try:
                check(path, value)
            except InputError as exc:
                problems.append(f"{self.name}: {exc}")
        return problems

    def write_input(
        self, project: Project, study: Study, mesh: Mesh, workdir: Path, inputs=()
    ) -> Path:
        refuse_inputs(inputs, "Elmer", {("load", "temperature"), ("feature", "*")})
        sif, emesh = write_case(project, study, mesh, workdir, inputs)
        np.savetxt(workdir / MESH_DIR / NODE_MAP, emesh.node_ids, fmt="%d")
        return sif

    def progress(self, input_file: Path, step: int = 1, of: int = 1):
        """Time steps, nonlinear iterations and messages from the log (ADR 0054)."""
        from puzzle.adapters.elmer.progress import ElmerProgress

        return ElmerProgress(input_file, step, of)

    parallel_processes = True  # magnetic studies, where an MPI Elmer is present (ADR 0053)

    def processes(self, input_file: Path | None = None) -> int:
        """Processes for a run: Settings > Solvers > Parallel processes per run, for a
        3D magnetic study (its reader merges the partitions) with an MPI Elmer: the
        image's, or ElmerSolver_mpi beside a native one. Else 1."""
        from puzzle.adapters.eddy import META
        from puzzle.jobs.runtime import ContainerRuntime, process_count, runtime_for

        n = process_count()
        if n <= 1 or input_file is None:
            return 1
        mesh_dir = input_file.parent / MESH_DIR
        if not (mesh_dir / META).exists() or (mesh_dir / "puzzle-node-coords.npy").exists():
            return 1  # not magnetics, or an axisymmetric model (small: one process)
        runtime = runtime_for("elmer")
        mpi = isinstance(runtime, ContainerRuntime) or runtime.available("ElmerSolver_mpi")
        return n if mpi else 1

    def command(self, input_file: Path) -> list[str]:
        n = self.processes(input_file)
        if n <= 1:
            return ["ElmerSolver", input_file.name]
        # ElmerGrid splits the mesh (METIS), then one ElmerSolver_mpi per part.
        script = input_file.parent / "run-mpi.sh"
        script.write_text(
            f"ElmerGrid 2 2 {MESH_DIR} -metiskway {n} > partition.log 2>&1 || exit 1\n"
            f"exec mpirun --oversubscribe -np {n} ElmerSolver_mpi {input_file.name}\n",
            encoding="utf-8",
            newline="\n",  # run by sh in a Linux container: CRLF breaks it
        )
        return ["sh", script.name]

    def read_results(self, input_file: Path) -> Results:
        mesh_dir = input_file.parent / MESH_DIR
        vtus = sorted(mesh_dir.glob(f"{OUTPUT}*.vtu"))
        if not vtus:
            raise FileNotFoundError(f"Elmer wrote no results ({OUTPUT}*.vtu); see the solver log")
        log = input_file.parent / LOG_NAME
        node_ids = np.loadtxt(mesh_dir / NODE_MAP, dtype=np.int64, ndmin=1)
        times_file = mesh_dir / TIMES
        times = np.loadtxt(times_file, ndmin=1).tolist() if times_file.exists() else [1.0]
        from puzzle.adapters.eddy import META

        if (mesh_dir / META).exists():  # ADR 0047: magnetodynamics
            from puzzle.adapters.elmer.magnetics import read_axisymmetric, read_magnetics

            if (mesh_dir / "puzzle-node-coords.npy").exists():  # axisymmetric 2D
                coords = np.load(mesh_dir / "puzzle-node-coords.npy")
                return read_axisymmetric(vtus, mesh_dir, node_ids, coords)
            return read_magnetics(vtus, mesh_dir, node_ids)
        results = read_series(
            vtus, times, node_ids, log.read_text(encoding="utf-8") if log.exists() else ""
        )
        if (mesh_dir / THERMAL_ZZ).exists():
            _thermal_zz(results, json.loads((mesh_dir / THERMAL_ZZ).read_text(encoding="utf-8")))
        return results


def _thermal_zz(results: Results, given: dict) -> None:
    """Plane strain with a temperature load (ADR 0048): Elmer's σz is ν (σx + σy), its
    thermal part left out; take E α (T - T_ref) off it at each node."""
    for step in results.steps:
        stress, temperature = step.fields.get("stress"), step.fields.get("temperature")
        if stress is None or temperature is None:
            continue
        values = np.array(stress.values, dtype=float)
        values[:, 2] -= given["e_alpha"] * (temperature.at(stress.ids)[:, 0] - given["reference"])
        stress.values = values
