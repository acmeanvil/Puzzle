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
        beams=True,  # ADR 0027, tests/integration/test_beams.py
        shared_settings=frozenset(  # ADR 0023
            {"linear_solver", "tolerance", "max_iterations", "output_every"}
        ),
        shells=True,  # ADR 0027, tests/integration/test_shells.py
        # ADR 0037 (heat flux) and 0041 (structural loads in dynamics), benchmarked
        amplitude_features=frozenset({"heat_flux", "force", "pressure"}),
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

    def settings_schema(self) -> list:
        """Code_Aster's own settings (ADR 0023); the shared ones are on each step."""
        from puzzle.adapters.code_aster.comm import METHODS
        from puzzle.adapters.settings import SolverSetting

        return [
            SolverSetting(
                "method", "Linear solver", "SOLVEUR/METHODE", "choice", "MUMPS", METHODS,
                description="The method for every linear system; iterative ones suit large "
                "models and take a preconditioner Code_Aster chooses.",
            ),
            SolverSetting(
                "reac_iter", "Tangent matrix every", "NEWTON/REAC_ITER", "integer", 1,
                unit="iterations", power=True,
                description="How often a nonlinear solve rebuilds its tangent matrix: 1 every "
                "iteration (robust), more to save time when it converges easily.",
            ),
        ]  # fmt: skip

    def expert_help(self) -> str:
        return (
            "COMMAND.KEYWORD or COMMAND.FACTOR.KEYWORD = a Code_Aster value, set in every "
            "call of that command: STAT_NON_LINE.NEWTON.REAC_ITER = 2, "
            "MECA_STATIQUE.SOLVEUR.METHODE = 'MULT_FRONT'"
        )

    def check_expert(self, entries: dict[str, str]) -> list[str]:
        from puzzle.adapters.code_aster.expert import check
        from puzzle.adapters.common import InputError

        problems = []
        for path, value in entries.items():
            try:
                check(path, value)
            except InputError as exc:
                problems.append(f"{self.name}: {exc}")
        return problems

    def read_results(self, input_file: Path) -> Results:
        workdir = input_file.parent
        if not list(workdir.glob("*.csv")):
            raise FileNotFoundError(
                f"Code_Aster wrote no result tables; see {JOB}.mess and the solver log"
            )
        results = read_tables(workdir)
        _harmonic(results)
        _fastener_fields(results, input_file)  # before the bolts' nodes are dropped
        return drop_helper_nodes(results, input_file)


def _harmonic(results) -> None:
    """A harmonic response's real and imaginary tables joined (ADR 0041): each
    frequency's displacement as shown, amplitude and phase fields."""
    from puzzle.adapters.common import harmonic_fields

    for step in results.steps:
        imag = step.fields.pop("displacement_imag", None)
        real = step.fields.get("displacement")
        if imag is None or real is None:
            continue
        step.kind = "frequency_domain"
        for f in harmonic_fields(
            "displacement", real.ids, real.values, imag.at(real.ids), real.components
        ):
            step.fields[f.name] = f


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
        values = fastener_values(reactions, [e for e in ends if not e.get("beam")])
        if any(e.get("beam") for e in ends):  # beam bolts: their own end forces
            solid = iter(values)
            values = np.array([
                _beam_bolt_values(e, step.fields.get("beam_forces")) if e.get("beam")
                else next(solid)
                for e in ends
            ])  # fmt: skip
        ids = np.arange(1, len(ends) + 1)
        step.fields["fastener"] = Field("fastener", "global", FASTENER_COMPONENTS, ids, values)


def _beam_bolt_values(end: dict, forces):
    """A beam bolt's forces at its first end (FASTENER_COMPONENTS) from EFGE_NOEU there:
    N, Vy, Vz, T, My, Mz in the beam's axes (x along it, y the section's 1-axis)."""
    import numpy as np

    if forces is None:
        return np.full(10, np.nan)
    node = end["a"]["nodes"][0]
    at = np.flatnonzero(np.asarray(forces.ids) == node)
    if not len(at):
        return np.full(10, np.nan)
    c = {k: j for j, k in enumerate(forces.components)}
    v = np.asarray(forces.values)[at[0]]
    x = np.asarray(end["axis"], float)
    y = np.asarray(end["side"], float)
    z = np.cross(x, y)
    force = v[c["N"]] * x + v[c["Vy"]] * y + v[c["Vz"]] * z
    moment = v[c["T"]] * x + v[c["My"]] * y + v[c["Mz"]] * z
    return np.array([v[c["N"]], float(np.hypot(v[c["Vy"]], v[c["Vz"]])), v[c["T"]],
                     float(np.hypot(v[c["My"]], v[c["Mz"]])), *force, *moment])  # fmt: skip
