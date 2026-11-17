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
    SYMMETRY_FEATURES,
    THERMAL_FEATURES,
    VECTOR_FIELD_FEATURES,
    VECTOR_LOAD_FEATURES,
    Capabilities,
    SolverAdapter,
)
from puzzle.adapters.calculix import spring_beams
from puzzle.adapters.calculix.frd import read_frd
from puzzle.adapters.calculix.inp import (
    THREADS,
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
        | SYMMETRY_FEATURES  # ADR 0081
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
        connectors=frozenset(
            {"beam_fastener:rigid", "beam_fastener:distributing", "beam_fastener:thread"}
        ),  # ADR 0076 step 4: tapped ends
        # ADR 0027, tests/integration/test_beams.py; open table sections as exact
        # springs (ADR 0055, tests/integration/test_beam_sections.py)
        beams=True,
        shared_settings=frozenset(  # ADR 0023
            {
                "linear_solver",
                "tolerance",
                "max_iterations",
                "output_every",
                "increments",
                "initial_increment",
                "min_increment",
                "fixed_increments",
            }
        ),
        weak_springs=True,  # ADR 0080: tests/integration/test_weak_springs_solved.py
        thermal_bolts=True,  # ADR 0090 package 6: tests/integration/test_bolt_thermal.py
        # *FREQUENCY, *BUCKLE and the modal superpositions have no iterative solver
        # (CalculiX warns and uses its default): ADR 0090, CCX-5
        shared_settings_steps={"linear_solver": frozenset({"stationary", "transient"})},
        shells=True,  # ADR 0027, tests/integration/test_shells.py
        # ADR 0081 step 3: *EQUATION per node pair (test_symmetry.py)
        cyclic=frozenset({"solid_mechanics", "heat_transfer"}),
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
            {
                "bonded_conformal",
                "bonded_nonconformal",
                "frictionless",
                "frictional",
                "no_separation",
                "rough",
            }
        ),
        threads=THREADS,  # ADR 0076
        welds=frozenset({"line"}),  # ADR 0078: the light weld's springs
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
        path.write_text(write_inp(project, study, mesh, inputs), encoding="utf-8")
        # Replayed prestress steps come first in the results; the reader skips them.
        # Elastic constants per material, for the plastic strain tensor (read_results).
        elastic = _elastic_sets(project, study, mesh)
        if elastic:
            path.with_suffix(".elastic.json").write_text(json.dumps(elastic), encoding="utf-8")
        # Helper nodes (rigid bodies, spring grounds) come after the mesh's; the reader
        # leaves them out of the results.
        mark_mesh_nodes(path, mesh)
        # Buckling steps (ADR 0028), by the study's step number: the reader drops their
        # static block and numbers modes.
        buckling = [n for n, s in enumerate(study.solve_steps(), start=1) if s.type == "buckling"]
        path.with_suffix(".buckling").write_text(json.dumps(buckling), encoding="utf-8")
        beams = spring_beam_info(project, study, mesh)
        if beams:  # ADR 0055: the reader turns the springs' stretch into section forces
            path.with_name(path.stem + spring_beams.INFO_SUFFIX).write_text(
                json.dumps(beams), encoding="utf-8"
            )
        ends = fastener_info(project, study, mesh)
        if ends:  # ADR 0027: the reader turns their helper nodes' reactions into forces
            path.with_suffix(".fasteners.json").write_text(json.dumps(ends), encoding="utf-8")
        from puzzle.adapters.calculix.inp import total_sets

        totals = total_sets(mesh)
        if totals:  # each printed set's selection, for the exact reaction totals
            path.with_suffix(".totals.json").write_text(json.dumps(totals), encoding="utf-8")
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
            # Convergence of nonlinear static steps: *CONTROLS, PARAMETERS=TIME
            # INCREMENTATION (CalculiX's defaults, so unset changes nothing).
            SolverSetting(
                "cutbacks", "Cut-backs per increment", "*CONTROLS I_A", "integer", 5,
                steps=frozenset({"stationary"}),
                description="How many times an increment that does not converge is cut "
                "back and tried again before the solve stops.",
            ),
            SolverSetting(
                "cutback_factor", "Cut-back factor", "*CONTROLS D_f", "number", 0.25,
                maximum=0.99, steps=frozenset({"stationary"}),
                description="A failed increment is retried at this fraction of its size.",
            ),
            SolverSetting(
                "increase_factor", "Growth factor", "*CONTROLS D_D", "number", 1.5,
                steps=frozenset({"stationary"}),
                description="An increment that converges easily is followed by one this "
                "much larger (up to the largest increment).",
            ),
            SolverSetting(
                "reduce_after", "Shrink after", "*CONTROLS I_L", "integer", 10,
                unit="iterations", power=True, steps=frozenset({"stationary"}),
                description="An increment taking more iterations than this is converged, "
                "but the next one is made smaller.",
            ),
            SolverSetting(
                "divergence_check_after", "Divergence check after", "*CONTROLS I_0",
                "integer", 4, unit="iterations", power=True,
                steps=frozenset({"stationary"}),
                description="From this iteration on, residuals growing in two iterations "
                "running cut the increment back; larger is more patient with contact.",
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
        _bolt_thread_loads(results, input_file)  # before the thread ends' helpers go
        drop_helper_nodes(results, input_file)  # rigid-body nodes, grounds, twins
        elastic = input_file.with_suffix(".elastic.json")
        if elastic.exists():  # CalculiX writes PEEQ only: the tensor comes from S and ME
            sets = json.loads(elastic.read_text(encoding="utf-8"))
            for step in results.steps:
                # the mechanical strain (no thermal part, ADR 0090 COMMON-2); a deck
                # written before ME was asked for has the total strain only
                strain = step.fields.pop("mechanical_strain", None) or step.fields.get("strain")
                if "plastic_strain_equivalent" in step.fields and strain is not None:
                    step.fields["plastic_strain"] = plastic_strain_tensor(
                        strain, step.fields["stress"], sets
                    )
        for step in results.steps:
            step.fields.pop("mechanical_strain", None)
        _fastener_fields(results, input_file)
        _reaction_totals(results, input_file)  # by the deck's own step numbers, as above
        _participation(results, input_file)  # modes' participation (puzzle.spectra)
        from puzzle.adapters.calculix.inp import step_owners

        owners = step_owners(input_file.read_text(encoding="utf-8")) if input_file.exists() else {}
        # Each result to its study step (the deck's tags); helpers' results go: a
        # replayed prestress step, the modes a superposition reads (ADR 0041).
        results.steps = [s for s in results.steps if owners.get(s.index, 0) > 0]
        for s in results.steps:
            s.index = owners[s.index]
        buckling = input_file.with_suffix(".buckling")
        if buckling.exists():
            results.steps = _buckling_modes(
                results.steps, set(json.loads(buckling.read_text(encoding="utf-8")))
            )
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

    info = json.loads(marker.read_text(encoding="utf-8"))
    nodes = {int(n) for s in info for n in s["nodes"]}
    deck = input_file.read_text(encoding="utf-8") if input_file.exists() else ""
    loads = spring_beams.deck_loads(deck, nodes)
    # each deck step's span in total time, from its increments: (start, end)
    ends: dict[int, float] = {}
    for step in results.steps:
        if step.mode is None and "reaction_force" in step.fields:
            ends[step.index] = max(ends.get(step.index, step.value), step.value)
    spans, start = {}, 0.0
    for index in sorted(ends):
        spans[index] = (start, ends[index])
        start = ends[index]
    for step in results.steps:
        rf = step.fields.get("reaction_force")
        if rf is None:  # modes and harmonic steps have no reactions
            continue
        begin, end = spans.get(step.index, (0.0, step.value))
        fraction = (step.value - begin) / (end - begin) if end > begin else 1.0
        applied = loads.at(step.index, fraction, step.value - begin)
        ids, values = spring_beams.section_forces(info, rf.ids, rf.values)
        if len(ids):
            step.fields["beam_forces"] = Field(
                "beam_forces", "node", list(spring_beams.COMPONENTS), ids, values
            )
        values = rf.values.copy()
        row = {int(n): i for i, n in enumerate(rf.ids)}
        for node, force in spring_beams.reactions(info, rf.ids, rf.values, applied).items():
            if node in row:
                values[row[node], :3] = force
        step.fields["reaction_force"] = Field(rf.name, rf.location, rf.components, rf.ids, values)


def _bolt_thread_loads(results, input_file: Path) -> None:
    """Each threaded bolt end's load per thread (ADR 0076 step 4), the first engaged
    (at the mouth) first: the flank springs' force k n · (u_helper - u_wall) along the
    bolt's axis, binned by pitch. A whole-model field `bolt_thread_load`, components
    "<bolt> <end>: <k>"."""
    marker = input_file.with_suffix(".fasteners.json")
    if not marker.exists():
        return
    import numpy as np

    from puzzle import threads
    from puzzle.results.model import Field

    ends = [e for e in json.loads(marker.read_text(encoding="utf-8")) if e.get("threads")]
    if not ends:
        return
    for step in results.steps:
        u = step.fields.get("displacement")
        if u is None:
            continue
        names, loads = [], []
        for e in ends:
            for end, t in e["threads"].items():
                wall, helper = np.asarray(t["wall"]), np.asarray(t["helper"])
                dw, dh = u.at(wall)[:, :3], u.at(helper)[:, :3]
                n, axis = np.asarray(t["n"]), np.asarray(t["axis"])
                force = np.asarray(t["k"]) * np.einsum("ij,ij->i", n, dh - dw)
                axial = np.abs(force * (n @ axis))
                per = threads.per_thread(np.asarray(t["s"]), axial, t["pitch"], t["mouth"])
                names += [f"{e['name']} {end}: {k + 1}" for k in range(len(per))]
                loads += per
        if names:
            step.fields["bolt_thread_load"] = Field(
                "bolt_thread_load", "global", names, np.array([0]), np.array([loads])
            )


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

    ends = json.loads(marker.read_text(encoding="utf-8"))
    blocks = read_fastener_forces(dat.read_text(encoding="utf-8"), ends)
    for step, values in matched(results.steps, blocks).items():
        ids = np.arange(1, len(ends) + 1)
        field = Field("fastener", "global", FASTENER_COMPONENTS, ids, values)
        results.steps[step].fields["fastener"] = field


def matched(steps, blocks) -> dict[int, object]:
    """Each .frd step (by position in `steps`) to its .dat block (analysis step, time,
    value), by order within a deck step (ADR 0090 CCX-3): the .frd prints a time to six
    digits, the .dat to seven, so 1/30 s or a cut-back increment matched nothing by an
    absolute 1e-9 and lost its fastener forces and exact totals. Both files are written
    at the same increments (`inp.with_settings` thins them alike); when their counts in
    a deck step still differ, the nearest time within the .frd's rounding is taken."""
    by_step: dict[int, list] = {}
    for block in blocks:
        by_step.setdefault(block[0], []).append(block)
    frd: dict[int, list[int]] = {}
    for k, s in enumerate(steps):
        if s.mode is None:
            frd.setdefault(s.index, []).append(k)
    out: dict[int, object] = {}
    for n, ks in frd.items():
        printed = by_step.get(n, [])
        if not printed:
            continue
        times = [steps[k].value for k in ks]
        if len(printed) == len(ks) and all(
            _close(t, b[1]) for t, b in zip(times, printed, strict=True)
        ):
            out.update({k: b[2] for k, b in zip(ks, printed, strict=True)})
            continue
        for k, t in zip(ks, times, strict=True):
            near = min(printed, key=lambda b: abs(b[1] - t))
            if _close(t, near[1]):
                out[k] = near[2]
    return out


def _close(frd_time: float, dat_time: float) -> bool:
    """Equal within the .frd's rounding (%12.5E: half a unit in the sixth digit)."""
    return abs(frd_time - dat_time) <= 6e-6 * max(abs(frd_time), abs(dat_time)) + 1e-30


def read_participation(text: str) -> dict[int, dict]:
    """The .dat's *FREQUENCY blocks by deck step: {step: {"gamma": {mode: (x, y, z)},
    "meff": {mode: (x, y, z)}, "total": (x, y, z)}} (translations; CalculiX prints the
    rotations too). Its modes are normalised to unit generalised mass."""
    import re

    out: dict[int, dict] = {}
    step = 0
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"\s*S T E P\s+(\d+)", line)
        if m:
            step = int(m.group(1))
        title = " ".join(line.split())
        key = {"P A R T I C I P A T I O N F A C T O R S": "gamma",
               "E F F E C T I V E M O D A L M A S S": "meff",
               "T O T A L E F F E C T I V E M A S S": "total"}.get(title)  # fmt: skip
        if key is None:
            i += 1
            continue
        block = out.setdefault(step, {"gamma": {}, "meff": {}, "total": (0.0, 0.0, 0.0)})
        i += 1
        while i < len(lines):
            words = lines[i].split()
            if (
                words
                and words[0] == "F"
                or (
                    words
                    and words[0].isalpha()
                    and words[0] not in ("MODE", "TOTAL", "X-COMPONENT")
                )
            ):
                break
            try:
                numbers = [float(w) for w in words]
            except ValueError:
                numbers = []
            if key == "total" and len(numbers) >= 3:
                block["total"] = tuple(numbers[:3])
                break
            if key != "total" and len(numbers) >= 4 and words[0].isdigit():
                block[key][int(words[0])] = tuple(numbers[1:4])
            elif key != "total" and block[key] and not words:
                break
            i += 1
    return out


def _participation(results, input_file: Path) -> None:
    """Each mode's participation factor, effective mass and mass fraction (x, y, z) from
    the .dat, as global fields on its modal step (`spectra.participation_fields`)."""
    dat = input_file.with_suffix(".dat")
    if not dat.exists() or not any(s.mode is not None for s in results.steps):
        return
    from puzzle.spectra import participation_fields

    blocks = read_participation(dat.read_text(encoding="utf-8", errors="replace"))
    for s in results.steps:
        block = blocks.get(s.index)
        if s.mode is None or block is None or s.mode not in block["gamma"]:
            continue
        s.fields.update(participation_fields(block["gamma"][s.mode],
                                             block["meff"].get(s.mode, (0.0, 0.0, 0.0)),
                                             block["total"]))  # fmt: skip


def _reaction_totals(results, input_file: Path) -> None:
    """Each step's exact reaction totals per selection (CalculiX's TOTALS=ONLY prints) as
    the whole-model field `reaction_total` (`results.query.exact_total` reads it)."""
    marker = input_file.with_suffix(".totals.json")
    dat = input_file.with_suffix(".dat")
    if not marker.exists() or not dat.exists():
        return
    import numpy as np

    from puzzle.adapters.calculix.inp import read_totals
    from puzzle.results.model import Field
    from puzzle.results.query import selection_id

    groups = json.loads(marker.read_text(encoding="utf-8"))
    blocks = read_totals(dat.read_text(encoding="utf-8", errors="replace"))
    for k, totals in matched(results.steps, blocks).items():
        step = results.steps[k]
        printed = {groups[s]: v for s, v in totals.items() if s in groups}
        if printed:
            names = sorted(printed)
            ids = np.array([selection_id(n) for n in names], dtype=np.int64)
            values = np.array([printed[n] for n in names], float)
            step.fields["reaction_total"] = Field("reaction_total", "global", ["x", "y", "z"],
                                                  ids, values)  # fmt: skip


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
