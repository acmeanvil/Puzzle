"""OpenCourant, explicit dynamics (ADR 0085).

The community continuation of OpenRadioss (AGPL-3.0), run as a separate program from
its own image (pinned in `jobs.runtime.DEFAULT_IMAGES`): `run.sh` runs the Starter on
the model deck, the Engine on the run deck, then converts the animation frames to VTK
and the time history to CSV (`deck.py`); `output.py` reads them back.

Claimed only where benchmarked (`tests/integration/test_explicit.py`): explicit steps of
solid mechanics on tet4, tet10 and hex8 solids, tri3/quad4 shells and two-node beams,
elastic, tabulated plastic and Johnson–Cook materials with failure and erosion, fixed
and prescribed displacements, prescribed and initial velocities, forces, pressure
(first-order faces), gravity and acceleration (free bars against momentum), rigid
walls with and without friction (a block slowing at μ g), bonded and frictionless
contacts and self-contact, amplitudes on every load (ADR 0090 OC-3: displacement, force,
pressure, gravity and acceleration ramps measured), and mass scaling (the step held at
its target, the wave slowed by the mass added). Frictional contact between parts is
written but refused until benchmarked.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from puzzle.adapters.base import Capabilities, SolverAdapter, refuse_inputs
from puzzle.adapters.common import InputError
from puzzle.adapters.opencourant import deck, output
from puzzle.mesh.mesh import Mesh
from puzzle.model.schema import Project, Study

FEATURES = frozenset({
    "fixed", "displacement", "force", "pressure", "gravity", "acceleration",
    "velocity", "initial_velocity", "rigid_wall",
})  # fmt: skip


class OpenCourantAdapter(SolverAdapter):
    name = "opencourant"
    max_solve_steps = 1
    parallel_processes = True  # Settings > Solvers > Parallel processes per run (MPI)
    capabilities: ClassVar[Capabilities] = Capabilities(
        physics={"solid_mechanics": frozenset({"explicit"})},
        features=FEATURES,
        material_models=frozenset({"plasticity", "johnson_cook", "failure"}),
        amplitude_features=frozenset(
            {"displacement", "force", "pressure", "gravity", "acceleration", "velocity"}
        ),  # fmt: skip
        field_features=frozenset(),
        shells=True,
        beams=True,
        # ADR 0090 OC-3: frictional contact between parts is written (TYPE7 with
        # Coulomb friction) but has no benchmark yet, so it is refused until one passes.
        contacts=frozenset(
            {
                "bonded_conformal",
                "bonded_nonconformal",
                "frictionless",
                "self_contact",
            }
        ),  # fmt: skip
    )

    def check(self, project: Project, study: Study, *, allow_experimental: bool = False):
        problems = super().check(project, study, allow_experimental=allow_experimental)
        for step in study.steps:
            if getattr(step, "type", None) == "explicit":
                try:
                    deck.settings_of(step)
                except InputError as exc:
                    problems.append(str(exc))
        return problems

    def write_input(self, project: Project, study: Study, mesh: Mesh, workdir: Path,
                    inputs=()) -> Path:  # fmt: skip
        refuse_inputs(inputs, self.name, set())
        steps = [s for s in study.steps if getattr(s, "type", None) == "explicit"]
        if len(steps) != 1 or len(study.steps) != 1:
            raise InputError("OpenCourant takes one explicit step per study", code="unsupported")
        if mesh.planar:
            raise InputError("2D models aren't written for OpenCourant", code="unsupported")
        workdir.mkdir(parents=True, exist_ok=True)
        self._end_time = deck.settings_of(steps[0]).end_time
        return deck.write(project, study, mesh, workdir, steps[0], self.processes(),
                          models=set(self.capabilities.material_models))  # fmt: skip

    def progress(self, input_file: Path, step: int = 1, of: int = 1):
        from puzzle.adapters.opencourant.progress import OpenCourantProgress

        rad = input_file.parent / f"{deck.NAME}_0001.rad"
        end = getattr(self, "_end_time", None)
        if end is None and rad.exists():
            try:
                end = float(rad.read_text(encoding="utf-8").splitlines()[2])
            except (ValueError, IndexError):
                end = None
        return OpenCourantProgress(end or 1.0, step, of)

    def command(self, input_file: Path) -> list[str]:
        """`sh run.sh` in the image (or on a remote host); natively, the driver in
        Puzzle's own Python (native.py), which fetches the release on first use."""
        import sys

        from puzzle.jobs.runtime import NativeRuntime, runtime_for

        if isinstance(runtime_for(self.name), NativeRuntime):
            return [sys.executable, "-m", "puzzle.adapters.opencourant.native"]
        return ["sh", input_file.name]

    def read_results(self, input_file: Path):
        workdir = input_file.parent
        conn, nodes, shells = _model(workdir)
        return output.read(workdir, deck.NAME, conn, nodes, shells)


def _model(workdir: Path):
    """The solid and shell elements' connectivity (Radioss's order is fine for
    averaging), the node ids and the shells' ids, from the Starter deck Puzzle wrote."""
    import numpy as np

    conn: dict[int, np.ndarray] = {}
    nodes: set[int] = set()
    shells: set[int] = set()
    section = None
    for line in (workdir / f"{deck.NAME}_0000.rad").read_text(encoding="utf-8").splitlines():
        if line.startswith("/"):
            key = line.split("/")[1]
            section = key if key in ("NODE", "TETRA4", "TETRA10", "BRICK", "SHELL",
                                     "SH3N") else None  # fmt: skip
            continue
        if section is None or line.startswith("#") or not line.strip():
            continue
        if section == "NODE":
            nodes.add(int(line[:10]))
            continue
        ids = [int(line[k : k + 10]) for k in range(0, len(line.rstrip()), 10)]
        if section == "TETRA10":  # the id on one line, its ten nodes on the next
            if len(ids) == 1:
                last = ids[0]
            else:
                conn[last] = np.array(ids, dtype=np.int64)
            continue
        last = ids[0]
        conn[last] = np.array(ids[1:], dtype=np.int64)
        if section in ("SHELL", "SH3N"):
            shells.add(last)
    return conn, nodes, shells
