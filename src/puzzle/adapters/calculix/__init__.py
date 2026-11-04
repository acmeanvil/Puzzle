"""CalculiX adapter: Abaqus-style .inp decks, .frd results. MVP solver (ADR 0010)."""

from __future__ import annotations

import json
from pathlib import Path

from puzzle.adapters.base import (
    HEAT_SOURCE_FEATURES,
    INITIAL_STRESS_FEATURES,
    LIFTING_FEATURES,
    PRETENSION_FEATURES,
    REMOTE_FEATURES,
    RIGID_REMOTE_FEATURES,
    STAGE3_FIELDS,
    STRUCTURAL_FEATURES,
    SUPPORT_FEATURES,
    THERMAL_FEATURES,
    VECTOR_FIELD_FEATURES,
    VECTOR_LOAD_FEATURES,
    Capabilities,
    SolverAdapter,
)
from puzzle.adapters.calculix import spring_beams
from puzzle.adapters.calculix.frd import read_frd
from puzzle.adapters.calculix.inp import (
    InputError,
    fastener_info,
    spring_beam_info,
    write_inp,
)
from puzzle.adapters.common import drop_helper_nodes, mark_mesh_nodes, plastic_strain_tensor
from puzzle.mesh.mesh import Mesh
from puzzle.model.schema import Project, Study
from puzzle.results.model import Results

JOB = "job"


class CalculixAdapter(SolverAdapter):
    name = "calculix"
    capabilities = Capabilities(
        physics={
            "solid_mechanics": frozenset(
                {"stationary", "transient", "modal", "buckling", "frequency_domain"}
            ),
            "heat_transfer": frozenset({"stationary", "transient"}),
        },
        features=STRUCTURAL_FEATURES
        | THERMAL_FEATURES
        | HEAT_SOURCE_FEATURES  # ADR 0047
        | REMOTE_FEATURES
        | RIGID_REMOTE_FEATURES
        | SUPPORT_FEATURES
        | PRETENSION_FEATURES
        | LIFTING_FEATURES
        | VECTOR_LOAD_FEATURES
        | INITIAL_STRESS_FEATURES,  # ADR 0044 stage 3
        feature_steps=True,
        connectors=frozenset({"beam_fastener:rigid", "beam_fastener:distributing"}),
        # ADR 0027, tests/integration/test_beams.py; open table sections as exact
        # springs (ADR 0055, tests/integration/test_beam_sections.py)
        beams=True,
        shared_settings=frozenset(  # ADR 0023
            {"linear_solver", "tolerance", "max_iterations", "output_every"}
        ),
        shells=True,  # ADR 0027, tests/integration/test_shells.py
        dimensions=frozenset({"3d", "2d_planar", "2d_axisymmetric"}),  # ADR 0048
        # ADR 0044: values from imported fields (tests/integration/test_imported_fields.py)
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
        | VECTOR_FIELD_FEATURES  # stage 2: tests/integration/test_imported_vectors.py
        | STAGE3_FIELDS,  # ADR 0044 stage 3 (tests/integration/test_imported_stage3.py)
        # ADR 0044: a face value from a field varying in time, through transient steps
        # (an amplitude per face; tests/integration/test_imported_fields_time.py)
        field_histories=frozenset({"pressure:p", "heat_flux:q"}),
        field_spectra=frozenset({"pressure:p"}),
        # ADR 0037 (heat flux) and 0041 (structural loads in dynamics), benchmarked
        amplitude_features=frozenset({"heat_flux", "force", "pressure"}),
        axes_features=frozenset(
            {
                "gravity:cartesian",  # ADR 0049 step 5: made global by common.globalize
                "acceleration:cartesian",
                "rotation:cartesian",
                "rotation:cylindrical",
                "displacement:cartesian",
                "displacement:cartesian:partial",
                "displacement:cylindrical",
                "displacement:cylindrical:partial",
                "force:cartesian",
            }
        ),  # ADR 0037: local axes
        # Nonconformal bonded maps to *TIE; the others to *CONTACT PAIR (+ *FRICTION).
        contacts=frozenset(
            {"bonded_conformal", "bonded_nonconformal", "frictionless", "frictional"}
        ),
        couplings=frozenset({"thermal_expansion"}),
        # *PLASTIC, stepped static steps, PEEQ out (tests/integration/test_plasticity.py).
        material_models=frozenset(  # ADR 0049 step 4: in a system's axes
            {"plasticity", "orthotropic_elasticity", "anisotropic_conductivity"}
        ),
    )

    def write_input(
        self, project: Project, study: Study, mesh: Mesh, workdir: Path, inputs=()
    ) -> Path:
        path = workdir / f"{JOB}.inp"
        path.write_text(write_inp(project, study, mesh, inputs))
        # Replayed prestress steps come first in the results; the reader skips them.
        # Elastic constants per material, for the plastic strain tensor (read_results).
        elastic = _elastic_sets(project, study, mesh)
        if elastic:
            path.with_suffix(".elastic.json").write_text(json.dumps(elastic))
        # Helper nodes (rigid bodies, spring grounds) come after the mesh's; the reader
        # leaves them out of the results.
        mark_mesh_nodes(path, mesh)
        # Buckling steps (ADR 0028), by the study's step number: the reader drops their
        # static block and numbers modes.
        buckling = [n for n, s in enumerate(study.solve_steps(), start=1) if s.type == "buckling"]
        path.with_suffix(".buckling").write_text(json.dumps(buckling))
        beams = spring_beam_info(project, study, mesh)
        if beams:  # ADR 0055: the reader turns the springs' stretch into section forces
            path.with_name(path.stem + spring_beams.INFO_SUFFIX).write_text(json.dumps(beams))
        ends = fastener_info(project, study, mesh)
        if ends:  # ADR 0027: the reader turns their helper nodes' reactions into forces
            path.with_suffix(".fasteners.json").write_text(json.dumps(ends))
        return path

    def contact_summary(self, project, component, mesh=None) -> dict[str, str]:
        from puzzle.adapters.calculix.inp import contact_summary

        return contact_summary(project, component, mesh)

    def progress(self, input_file: Path, step: int = 1, of: int = 1):
        """Steps, increments, iterations and messages from the log (ADR 0054)."""
        from puzzle.adapters.calculix.progress import CalculixProgress as P

        return P(input_file, step, of)

    def command(self, input_file: Path) -> list[str]:
        return ["ccx", "-i", input_file.with_suffix("").name]

    def settings_schema(self) -> list:
        """CalculiX's own settings (ADR 0023); the shared ones are on each step."""
        from puzzle.adapters.calculix.inp import SOLVERS
        from puzzle.adapters.settings import SolverSetting

        return [
            SolverSetting(
                "solver", "Linear solver", "SOLVER=", "choice", "default", SOLVERS,
                description="The equation solver on each step's procedure card; iterative "
                "ones suit large static models.",
            ),
        ]  # fmt: skip

    def expert_help(self) -> str:
        return (
            "model = cards before the first step; after_procedure = cards after every "
            "step's procedure card; after_procedure:2 = after step 2's only. One card or "
            "data line per line, e.g. *CONTROLS, PARAMETERS=TIME INCREMENTATION"
        )

    def check_expert(self, entries: dict[str, str]) -> list[str]:
        from puzzle.adapters.calculix.inp import EXPERT_KEYS, expert_cards
        from puzzle.adapters.common import InputError

        problems = []
        for key, text in entries.items():
            if not EXPERT_KEYS.match(key):
                problems.append(
                    f"{self.name}: '{key}' is not a place for cards (model, after_procedure, "
                    "after_procedure:N)"
                )
                continue
            try:
                expert_cards(key, text)
            except InputError as exc:
                problems.append(f"{self.name}: {exc}")
        return problems

    def read_results(self, input_file: Path) -> Results:
        frd = input_file.with_suffix(".frd")
        if not frd.exists():
            raise FileNotFoundError(f"CalculiX wrote no results ({frd.name}); see the solver log")
        results = read_frd(frd)
        _spring_beam_forces(results, input_file)  # before the rotation nodes go
        drop_helper_nodes(results, input_file)  # rigid-body nodes, grounds, twins
        elastic = input_file.with_suffix(".elastic.json")
        if elastic.exists():  # CalculiX writes PEEQ only: the tensor comes from S and E
            sets = json.loads(elastic.read_text())
            for step in results.steps:
                if "plastic_strain_equivalent" in step.fields and "strain" in step.fields:
                    step.fields["plastic_strain"] = plastic_strain_tensor(
                        step.fields["strain"], step.fields["stress"], sets
                    )
        _fastener_fields(results, input_file)
        from puzzle.adapters.calculix.inp import step_owners

        owners = step_owners(input_file.read_text()) if input_file.exists() else {}
        # Each result to its study step (the deck's tags); helpers' results go: a
        # replayed prestress step, the modes a superposition reads (ADR 0041).
        results.steps = [s for s in results.steps if owners.get(s.index, 0) > 0]
        for s in results.steps:
            s.index = owners[s.index]
        buckling = input_file.with_suffix(".buckling")
        if buckling.exists():
            results.steps = _buckling_modes(results.steps, set(json.loads(buckling.read_text())))
        _harmonic(results)
        return results


def _harmonic(results) -> None:
    """Steady-state dynamics' real (DISP) and imaginary (DISPI) parts joined: the
    displacement as shown, its amplitude and phase (ADR 0041)."""
    from puzzle.adapters.common import harmonic_fields

    for step in results.steps:
        imag = step.fields.pop("displacement_imag", None)
        real = step.fields.get("displacement")
        if imag is None or real is None:
            continue
        step.kind = "frequency_domain"
        parts = harmonic_fields(
            "displacement", real.ids, real.values, imag.at(real.ids), real.components
        )
        step.fields.update({f.name: f for f in parts})


def _spring_beam_forces(results, input_file: Path) -> None:
    """Beams written as springs (ADR 0055): their section forces as the nodal field
    `beam_forces`, Code_Aster's (N, Vy, Vz, T, My, Mz in each member's axes), and their
    nodes' reaction forces, both from the springs' ends' reactions."""
    marker = input_file.with_name(input_file.stem + spring_beams.INFO_SUFFIX)
    if not marker.exists():
        return
    from puzzle.results.model import Field

    info = json.loads(marker.read_text())
    for step in results.steps:
        rf = step.fields.get("reaction_force")
        if rf is None:  # modes and harmonic steps have no reactions
            continue
        ids, values = spring_beams.section_forces(info, rf.ids, rf.values)
        if len(ids):
            step.fields["beam_forces"] = Field(
                "beam_forces", "node", list(spring_beams.COMPONENTS), ids, values
            )
        values = rf.values.copy()
        row = {int(n): i for i, n in enumerate(rf.ids)}
        for node, force in spring_beams.reactions(info, rf.ids, rf.values).items():
            if node in row:
                values[row[node], :3] = force
        step.fields["reaction_force"] = Field(rf.name, rf.location, rf.components, rf.ids, values)


def _fastener_fields(results, input_file: Path) -> None:
    """Each step's fastener forces (ADR 0027) as a whole-model field, one row per
    fastener (ids 1, 2, ... in the component's order)."""
    marker = input_file.with_suffix(".fasteners.json")
    dat = input_file.with_suffix(".dat")
    if not marker.exists() or not dat.exists():
        return
    import numpy as np

    from puzzle.adapters.calculix.inp import read_fastener_forces
    from puzzle.adapters.common import FASTENER_COMPONENTS
    from puzzle.results.model import Field

    ends = json.loads(marker.read_text())
    blocks = read_fastener_forces(dat.read_text(), ends)
    for step in results.steps:
        hit = [v for n, t, v in blocks if n == step.index and abs(t - step.value) < 1e-9]
        if hit:
            ids = np.arange(1, len(ends) + 1)
            step.fields["fastener"] = Field("fastener", "global", FASTENER_COMPONENTS, ids, hit[-1])


def _buckling_modes(steps, buckling: set[int]) -> list:
    """A *BUCKLE step's blocks are the static solve of its load (value 0), then one per
    mode with its load factor as the value: drop the first, number the rest."""
    out, counts = [], {}
    for s in steps:
        if s.index not in buckling:
            out.append(s)
            continue
        if s.index not in counts:  # the static block comes first
            counts[s.index] = 0
            continue
        counts[s.index] += 1
        s.mode = counts[s.index]
        out.append(s)
    return out


def _elastic_sets(project: Project, study: Study, mesh: Mesh) -> list:
    """(E, nu, node ids) of each plastic material's bodies; [] when nothing yields."""
    from puzzle.model.schema import Component, Material

    comp = project.get(study.component, Component)
    out = []
    for a in comp.materials:
        m = project.objects.get(a.material)
        group = mesh.groups.get(a.selection)
        if not isinstance(m, Material) or m.models.plasticity is None or group is None:
            continue
        e, nu = m.properties["youngs_modulus"].to_si(), m.properties["poissons_ratio"].to_si()
        out.append([e, nu, [int(n) for n in group.node_ids]])
    return out


__all__ = ["CalculixAdapter", "InputError"]
