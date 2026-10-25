"""Code_Aster adapter: .comm command file, .export run file and a MED mesh; results as tables.

Code_Aster is Puzzle's nonlinear structural solver: contact with friction, plasticity
and large deformation (STAT_NON_LINE, DYNA_NON_LINE), alongside linear statics,
dynamics, modal (CALC_MODES) and harmonic response, and linear or nonlinear heat
transfer. Material and geometric nonlinearity settings travel in per-solver
extension blocks until the neutral schema covers them.

Contact maps to its contact definitions (frictionless or Coulomb friction);
nonconformal bonded interfaces map to mesh tying (LIAISON_MAIL).
"""

from __future__ import annotations

from pathlib import Path

from puzzle.adapters.base import (
    LIFTING_FEATURES,
    PRETENSION_FEATURES,
    REMOTE_FEATURES,
    RIGID_REMOTE_FEATURES,
    STRUCTURAL_FEATURES,
    SUPPORT_FEATURES,
    THERMAL_FEATURES,
    Capabilities,
    SolverAdapter,
    refuse_inputs,
)
from puzzle.adapters.code_aster.comm import JOB, write_case
from puzzle.adapters.code_aster.tables import read_results as read_tables
from puzzle.adapters.common import drop_helper_nodes, mark_mesh_nodes
from puzzle.mesh.mesh import Mesh
from puzzle.model.schema import Project, Study
from puzzle.results.model import Results


class CodeAsterAdapter(SolverAdapter):
    name = "code_aster"
    max_solve_steps = 1
    capabilities = Capabilities(
        physics={
            "solid_mechanics": frozenset(
                {"stationary", "transient", "modal", "frequency_domain", "buckling"}
            ),
            "heat_transfer": frozenset({"stationary", "transient"}),
        },
        features=STRUCTURAL_FEATURES
        | THERMAL_FEATURES
        | REMOTE_FEATURES
        | RIGID_REMOTE_FEATURES
        | SUPPORT_FEATURES
        | PRETENSION_FEATURES
        | LIFTING_FEATURES,
        connectors=frozenset({"beam_fastener:rigid", "beam_fastener:distributing"}),
        amplitude_features=frozenset({"heat_flux"}),  # ADR 0037, benchmarked
        axes_features=frozenset(
            {"displacement:cartesian", "displacement:cylindrical", "force:cartesian"}
        ),  # ADR 0037: local axes
        contacts=frozenset(
            {"bonded_conformal", "bonded_nonconformal", "frictionless", "frictional"}
        ),
        couplings=frozenset({"thermal_expansion"}),
        # VMIS_ISOT_TRAC in STAT_NON_LINE, p and EPSP out (tests/integration/test_plasticity.py).
        material_models=frozenset({"plasticity"}),
        dimensions=frozenset({"3d", "2d_planar", "2d_axisymmetric"}),
        time_schemes=frozenset({"backward_euler", "crank_nicolson", "theta"}),
    )

    def write_input(
        self, project: Project, study: Study, mesh: Mesh, workdir: Path, inputs=()
    ) -> Path:
        refuse_inputs(inputs, "Code_Aster", {("load", "temperature")})
        path = write_case(project, study, mesh, workdir, inputs)
        mark_mesh_nodes(path, mesh)  # pretension twins and reference nodes come after
        return path

    def command(self, input_file: Path) -> list[str]:
        return ["run_aster", input_file.name]

    def read_results(self, input_file: Path) -> Results:
        workdir = input_file.parent
        if not list(workdir.glob("*.csv")):
            raise FileNotFoundError(
                f"Code_Aster wrote no result tables; see {JOB}.mess and the solver log"
            )
        results = read_tables(workdir)
        _fastener_fields(results, input_file)  # before the bolts' nodes are dropped
        return drop_helper_nodes(results, input_file)


def _fastener_fields(results, input_file: Path) -> None:
    """Each step's fastener forces (ADR 0027): the bolt end faces' reactions summed, as
    a whole-model field, one row per fastener."""
    import json

    import numpy as np

    from puzzle.adapters.common import FASTENER_COMPONENTS, fastener_values
    from puzzle.results.model import Field

    marker = input_file.with_name(f"{JOB}.fasteners.json")
    if not marker.exists():
        return
    ends = json.loads(marker.read_text())
    for step in results.steps:
        rf = step.fields.get("reaction_force")
        if rf is None:
            continue
        reactions = {int(n): v for n, v in zip(rf.ids, rf.values, strict=True)}
        values = fastener_values(reactions, ends)
        ids = np.arange(1, len(ends) + 1)
        step.fields["fastener"] = Field("fastener", "global", FASTENER_COMPONENTS, ids, values)
