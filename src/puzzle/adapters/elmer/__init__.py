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
    STRUCTURAL_FEATURES,
    THERMAL_FEATURES,
    Capabilities,
    SolverAdapter,
)
from puzzle.adapters.elmer.output import read_results as read_output
from puzzle.adapters.elmer.sif import MESH_DIR, OUTPUT, write_case
from puzzle.jobs.runner import LOG_NAME
from puzzle.mesh.mesh import Mesh
from puzzle.model.schema import Project, Study
from puzzle.results.model import Results

NODE_MAP = "puzzle-node-ids.txt"


class ElmerAdapter(SolverAdapter):
    name = "elmer"
    capabilities = Capabilities(
        physics={
            "solid_mechanics": frozenset({"stationary", "transient", "modal"}),
            "heat_transfer": frozenset({"stationary", "transient"}),
            "electrostatics": frozenset({"stationary"}),
            "electric_currents": frozenset({"stationary"}),
            "magnetic_fields": frozenset({"stationary", "transient", "frequency_domain"}),
            "electromagnetic_waves": frozenset({"frequency_domain"}),
        },
        features=STRUCTURAL_FEATURES | THERMAL_FEATURES | ELECTROMAGNETIC_FEATURES,
        contacts=frozenset({"bonded_conformal", "bonded_nonconformal"}),
        experimental=frozenset({"frictionless", "frictional", "rough"}),
        couplings=frozenset({"thermal_expansion", "joule_heating"}),
        dimensions=frozenset({"3d", "2d_planar", "2d_axisymmetric"}),
    )

    def write_input(self, project: Project, study: Study, mesh: Mesh, workdir: Path) -> Path:
        sif, emesh = write_case(project, study, mesh, workdir)
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
        return read_output(vtus[-1], node_ids, log.read_text() if log.exists() else "")
