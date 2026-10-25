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

Bonded contact is Elmer's production-ready contact: conformal meshes share nodes, and
nonconformal interfaces are tied with mortar boundary conditions ("Mortar BC" pointing at
the target boundary, "Contact Type" = tie). Mortar ties also carry heat transfer and
magnetodynamics across nonconformal interfaces.

Elmer's source also has slide, friction and stick contact types for ElasticSolve, but they
are documented only through test cases (fem/tests/Contact*) and need a hand-tuned
projector per boundary pair, so they are experimental. General mechanical contact belongs
to CalculiX and Code_Aster; a typical workflow solves electromagnetics on Elmer
and transfers the forces to a CalculiX contact study.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from puzzle.adapters.base import (
    ELECTROMAGNETIC_FEATURES,
    REMOTE_FEATURES,
    STRUCTURAL_FEATURES,
    SUPPORT_FEATURES,
    THERMAL_FEATURES,
    Capabilities,
    SolverAdapter,
    refuse_inputs,
)
from puzzle.adapters.elmer.output import read_series
from puzzle.adapters.elmer.sif import MESH_DIR, OUTPUT, TIMES, write_case
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
            "solid_mechanics": frozenset({"stationary", "transient", "modal"}),
            "heat_transfer": frozenset({"stationary", "transient"}),
            "electrostatics": frozenset({"stationary"}),
            "electric_currents": frozenset({"stationary"}),
            "magnetic_fields": frozenset({"stationary", "transient", "frequency_domain"}),
            "electromagnetic_waves": frozenset({"frequency_domain"}),
        },
        features=STRUCTURAL_FEATURES
        | THERMAL_FEATURES
        | ELECTROMAGNETIC_FEATURES
        | REMOTE_FEATURES
        | SUPPORT_FEATURES,
        shared_settings=frozenset(  # ADR 0023
            {"linear_solver", "tolerance", "max_iterations", "output_every"}
        ),
        amplitude_features=frozenset({"heat_flux"}),  # ADR 0037, benchmarked
        axes_features=frozenset(
            {"displacement:cartesian", "displacement:cylindrical", "force:cartesian"}
        ),  # ADR 0037: local axes
        contacts=frozenset({"bonded_conformal", "bonded_nonconformal"}),
        experimental=frozenset({"frictionless", "frictional", "rough"}),
        couplings=frozenset({"thermal_expansion", "joule_heating"}),
        dimensions=frozenset({"3d", "2d_planar", "2d_axisymmetric"}),
        time_schemes=frozenset({"backward_euler", "crank_nicolson", "bdf2", "theta"}),
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
        refuse_inputs(inputs, "Elmer", {("load", "temperature")})
        sif, emesh = write_case(project, study, mesh, workdir, inputs)
        np.savetxt(workdir / MESH_DIR / NODE_MAP, emesh.node_ids, fmt="%d")
        return sif

    def command(self, input_file: Path) -> list[str]:
        return ["ElmerSolver", input_file.name]

    def read_results(self, input_file: Path) -> Results:
        mesh_dir = input_file.parent / MESH_DIR
        vtus = sorted(mesh_dir.glob(f"{OUTPUT}*.vtu"))
        if not vtus:
            raise FileNotFoundError(f"Elmer wrote no results ({OUTPUT}*.vtu); see the solver log")
        log = input_file.parent / LOG_NAME
        node_ids = np.loadtxt(mesh_dir / NODE_MAP, dtype=np.int64, ndmin=1)
        times_file = mesh_dir / TIMES
        times = np.loadtxt(times_file, ndmin=1).tolist() if times_file.exists() else [1.0]
        return read_series(vtus, times, node_ids, log.read_text() if log.exists() else "")
