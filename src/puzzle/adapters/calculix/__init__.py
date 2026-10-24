"""CalculiX adapter: Abaqus-style .inp decks, .frd results. MVP solver (ADR 0010)."""

from __future__ import annotations

import json
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
)
from puzzle.adapters.calculix.frd import read_frd
from puzzle.adapters.calculix.inp import InputError, prestress_step_count, write_inp
from puzzle.adapters.common import drop_helper_nodes, mark_mesh_nodes, plastic_strain_tensor
from puzzle.mesh.mesh import Mesh
from puzzle.model.schema import Project, Study
from puzzle.results.model import Results

JOB = "job"


class CalculixAdapter(SolverAdapter):
    name = "calculix"
    capabilities = Capabilities(
        physics={
            "solid_mechanics": frozenset({"stationary", "transient", "modal"}),
            "heat_transfer": frozenset({"stationary", "transient"}),
        },
        features=STRUCTURAL_FEATURES
        | THERMAL_FEATURES
        | REMOTE_FEATURES
        | RIGID_REMOTE_FEATURES
        | SUPPORT_FEATURES
        | PRETENSION_FEATURES
        | LIFTING_FEATURES,
        feature_steps=True,
        amplitude_features=frozenset({"heat_flux"}),  # ADR 0037, benchmarked
        axes_features=frozenset(
            {
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
        material_models=frozenset({"plasticity"}),
    )

    def write_input(
        self, project: Project, study: Study, mesh: Mesh, workdir: Path, inputs=()
    ) -> Path:
        path = workdir / f"{JOB}.inp"
        path.write_text(write_inp(project, study, mesh, inputs))
        # Replayed prestress steps come first in the results; the reader skips them.
        path.with_suffix(".prestress").write_text(str(prestress_step_count(project, study, inputs)))
        # Elastic constants per material, for the plastic strain tensor (read_results).
        elastic = _elastic_sets(project, study, mesh)
        if elastic:
            path.with_suffix(".elastic.json").write_text(json.dumps(elastic))
        # Helper nodes (rigid bodies, spring grounds) come after the mesh's; the reader
        # leaves them out of the results.
        mark_mesh_nodes(path, mesh)
        return path

    def command(self, input_file: Path) -> list[str]:
        return ["ccx", "-i", input_file.with_suffix("").name]

    def read_results(self, input_file: Path) -> Results:
        frd = input_file.with_suffix(".frd")
        if not frd.exists():
            raise FileNotFoundError(f"CalculiX wrote no results ({frd.name}); see the solver log")
        results = read_frd(frd)
        drop_helper_nodes(results, input_file)  # rigid-body nodes, grounds, twins
        elastic = input_file.with_suffix(".elastic.json")
        if elastic.exists():  # CalculiX writes PEEQ only: the tensor comes from S and E
            sets = json.loads(elastic.read_text())
            for step in results.steps:
                if "plastic_strain_equivalent" in step.fields and "strain" in step.fields:
                    step.fields["plastic_strain"] = plastic_strain_tensor(
                        step.fields["strain"], step.fields["stress"], sets
                    )
        marker = input_file.with_suffix(".prestress")
        skip = int(marker.read_text()) if marker.exists() else 0
        if skip:
            results.steps = [s for s in results.steps if s.index > skip]
            for s in results.steps:
                s.index -= skip
        return results


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
