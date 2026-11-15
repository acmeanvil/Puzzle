# 0085. Explicit dynamics on OpenCourant: an `explicit` step, its adapter and its checks

- Status: Accepted; step 1 built (2026-11-14): solids, elastic and tabulated plastic
  materials, supports, loads, velocities, rigid walls, the checks before a run
- Date: 2026-11-14

## Context

Puzzle's dynamics (ADR 0041, ADR 0082) are implicit: Newmark/HHT on CalculiX and
Code_Aster, solved step by step with a factorised stiffness. That suits vibration and
slow transients. Impact, drop tests, crash and short pulses need explicit time
integration instead: central differences, a lumped mass, no factorisation, a time step
held below the Courant limit (an element's size over its wave speed), millions of
cheap steps. ADR 0072 listed explicit dynamics as not planned; the maintainer took it
out of that group on 2026-11-13: it becomes this record, OpenCourant as a solver with
its adapter and GUI. This ADR and ADR 0084 are the last new records until everything
through 0085 is built and tested (ADR 0072).

**OpenCourant** (checked 2026-11-14):

- the community continuation of OpenRadioss, which Altair open-sourced under the GNU
  AGPL v3 in 2022 and Siemens (Altair's owner) withdrew in October 2026; OpenCourant
  carries the whole commit history, is an incubator project of the Rocky Enterprise
  Software Foundation, AGPL-3.0, and not affiliated with Siemens or Altair;
- releases from its own CI, gated by its regression suite: `latest-20261011` (zips for
  Linux x86-64 and arm64 and Windows x86-64, Apptainer images, each with a SHA-256),
  and multi-arch container images `docker.io/opencourant/opencourant` and
  `ghcr.io/opencourant/opencourant` with dated tags;
- the image holds `starter`, `engine` (OpenMP), `engine_ompi` (MPI), single-precision
  `_sp` builds, `anim_to_vtk_linux64_gf` and `th_to_csv_linux64_gf`, with
  `RAD_CFG_PATH`, `OPENCOURANT_PATH` and `OMP_STACKSIZE` set;
- a run is two programs: the **Starter** reads the model (`<name>_0000.rad`, Radioss's
  block format; LS-DYNA keyword decks are read too) and writes a restart; the
  **Engine** reads the run controls (`<name>_0001.rad`: end time, output, time-step
  control) and integrates. Output: animation frames `<name>A001…` (converted to legacy
  VTK by `anim_to_vtk`: node ids, displacement, velocity, element von Mises and stress
  tensors) and a time history `<name>T01` (converted to CSV by `th_to_csv`: internal,
  kinetic, hourglass, contact and rotational energy, external work, momentum, mass,
  added mass, the time step, plus the nodes, elements and interfaces asked for);
- one closed piece remains in its builds: the input reader library
  (`extlib/hm_reader`), never in OpenRadioss's repository; OpenCourant's releases
  carry an archived copy and track its replacement in the open.

**Measured here (2026-11-14)**: a deck written from scratch (an elastic steel bar of 40
eight-node bricks, ν = 0, one end held and the other driven at 1 m/s, run in the pinned
image with Docker on Windows) terminated normally in 24 cycles; the stress behind the
wave front was −39.49 MPa against ρ c v₀ = 39.62 MPa (−0.33 %), exactly zero ahead of
the front, which travelled at c = √(E/ρ). The chain (write, Starter, Engine, convert,
read) works from Puzzle's side without the GUI or any closed tool of ours.

The QA decks in OpenCourant's repository carry Siemens's copyright under CC BY-NC 4.0:
they are read for the format, never copied into Puzzle.

## Decision

1. **OpenCourant is Puzzle's explicit solver**, adapter `opencourant`. It runs as a
   separate program (never linked, never modified), so Puzzle's GPL-3.0-or-later is
   unaffected and the AGPL's terms are OpenCourant's own; Puzzle names the release,
   its licence and its source in the solver's Settings row and Help.
   - **Runtimes**, in the order `jobs.runtime` tries them:
     - the container image, pinned by tag and digest
       (`docker.io/opencourant/opencourant:latest-20261011@sha256:b3f042fe…`), on every
       platform (Docker or Podman; macOS's arm64 too);
     - natively, the release zip for this platform, fetched on first use into
       `<user data dir>/solvers/opencourant/<tag>/`, checked against its SHA-256 and
       unpacked there (Linux x86-64/arm64, Windows), never in the installer;
     - `PUZZLE_OPENCOURANT_PATH`: a build of the user's own;
     - on remote hosts (ADR 0021, 0067), the Apptainer image or a native install.
   - A newer release is a one-line change (tag, digest, the zips' digests) with the
     benchmarks run again.

2. **An `explicit` solve step** for solid mechanics, its own type beside `transient`
   (whose implicit settings don't apply), offered only where an adapter lists it:
   - `end_time` (required);
   - `frames` (animation frames written, default 20) or `frame_interval`;
   - `history_interval` (the energy and probe histories, default end_time / 1000);
   - `time_step_scale` (the Courant safety factor, default 0.9);
   - `mass_scaling_time_step` (unset: none; set: nodal mass added where an element's
     step would be smaller, `/DT/NODA/CST`, the added mass reported);
   - `precision` (double by default, single for speed).

   Results are one `StepResult` per frame (`value` = time, kind `explicit`, which the
   summary and exports treat as a time series like `transient`), the stress averaged
   at the nodes, the energies as global fields per frame, and the whole history kept
   as a table in the run folder (`puzzleT01.csv`) for operators (series, ADR 0071) and
   the Solution panel.

3. **What is written**, each claimed only once its benchmark passes (ADR 0006):
   - elements: tet4, tet10 and hex8 solids (`/TETRA4`, `/TETRA10`, `/BRICK` with the
     HEPH formulation, Isolid 24, so hourglass is physical, not viscous), and in step 2
     triangle and quad shells (`/SH3N`, `/SHELL`, `/PROP/SHELL`); second-order bricks
     are refused;
   - materials: linear elastic (`/MAT/ELAST`), the material's plasticity table
     (`/MAT/PLAS_TAB`, true stress against plastic strain), and in step 2 Johnson–Cook
     with strain rate (a new `Material.models.johnson_cook`: A, B, n, C, ε̇₀);
   - supports and loads: `fixed` and `displacement` (`/BCS`, `/IMPDISP`), force spread
     over a face's nodes as elsewhere (`/CLOAD`), pressure (`/PLOAD` on `/SURF/SEG`),
     gravity and acceleration (`/GRAV`); every load may follow an `Amplitude`
     (`/FUNCT`);
   - new features for explicit work: `initial_velocity` (a body's, `/INIVEL`),
     `velocity` (imposed on faces, `/IMPVEL`) and `rigid_wall` (an infinite plane
     through a point with an outward normal, optional friction, against a selection's
     nodes, `/RWALL/PLANE`); implicit adapters refuse them;
   - contacts in step 2: bonded (`/INTER/TYPE2`), frictionless and frictional
     (`/INTER/TYPE7` both ways), a body's self-contact;
   - everything else (heat, beams, bolts, welds, threads, symmetry equations, imported
     fields beyond a body temperature) refused by the capability matrix, as elsewhere.

4. **Checks before running**, as Problems and the Advisor's Run step, warnings only:
   - the stable time step: per element L / c with c the dilatational wave speed
     √(E (1 − ν) / (ρ (1 + ν)(1 − 2ν))), L its smallest height (tets) or edge (bricks);
     the smallest, where it is, and the cycles end_time / (scale × Δt);
   - a run longer than 10⁷ cycles is warned, saying the mass scaling that would bring it
     to 10⁶ and the mass it would add;
   - the mesh: elements far smaller than the rest (they set Δt for everyone);
   - MCP `explicit_estimate` gives the same numbers to agents (ADR 0057).

5. **Checks after**, from the history: the energy balance error
   |E_int + E_kin + E_contact + E_hourglass − W_ext − E₀| / max(E) over 5 %; hourglass
   energy over 10 % of internal; added mass over 5 %; each a run warning with its
   diagnosis (`energy_error`, `hourglass`, `added_mass`).

6. **Progress** (ADR 0054): the Engine's cycle lines parsed (cycle, time, time step,
   energy error, internal and kinetic energy), the fraction as time / end time; the
   Starter's and Engine's `ERROR ID` blocks become messages.

7. **GUI, CLI and agent**: the step in Model → Studies and the study's Add step; its
   settings in the step's Properties with units; the results bar steps through frames,
   animates them, and shows the energies (Plots); the Advisor's explicit checks;
   `puzzle solvers` lists OpenCourant and its runtime; skill `explicit-dynamics` (when
   to choose explicit, the time step, mass scaling, what the energies say, the
   benchmarks); nothing GUI-only.

8. **Benchmarks** (`tests/integration/test_explicit.py`), against closed forms or an
   implicit solver:
   - the 1D wave: σ = ρ c v₀ behind a front travelling at c;
   - an elastic bar thrown at a rigid wall: contact lasts 2 L / c and it leaves at v₀;
   - a load applied suddenly to a cantilever: the tip's history against CalculiX's
     implicit transient on the same bar (twice the static deflection at half a period
     holds for one degree of freedom only; the step rings the higher modes too);
   - a bar pulled slowly past yield (its end's speed ramped over 25 wave transits) on a
     flat plasticity curve: the stress holds at the yield stress (a bar struck past
     yield is not the benchmark: in 3D lateral inertia confines the plastic zone and the
     precursor ahead of it carries less than σy, Johnson–Cook and the table alike);
   - tet4, tet10 and hex8 each on at least one of these; in step 2 the shell and contact
     ones (two equal bars colliding exchange their velocities; a bonded split bar
     carries the wave as the whole one).

**Built (2026-11-14), step 1** (`adapters/opencourant/`: `deck.py` the Starter and
Engine decks and `run.sh`, `output.py` frames and history, `progress.py` the Engine's
cycle lines; `puzzle/explicit.py` the estimate; the image in `jobs.runtime`, container
first on every platform; MCP `explicit_estimate`; skill `explicit-dynamics`;
`elements/opencourant.py`). Measured on the pinned image:

| Benchmark | Measured | Expected |
| --- | --- | --- |
| Wave behind the front, hex8 (HEPH) | −39.52 MPa | −39.62 MPa (−0.27 %) |
| Wave behind the front, tet4 | −39.58 MPa | −39.62 MPa (−0.10 %) |
| Bar at a rigid wall, separation | 81.4 µs | 2 L / c = 79.3 µs (+2.7 %) |
| Bar at a rigid wall, leaving speed | 0.964 v₀ (40 along), 0.976 v₀ (80) | v₀: the kinematic wall stops the nodes reaching it |
| Suddenly loaded cantilever, tip peak | 3.1211 mm | CalculiX implicit 3.1267 mm (−0.18 %); 0.28 % of the peak at worst over the swing |
| Slow pull past yield | 245.9 MPa | σy = 250 MPa (−1.6 %) |

The Engine runs two time steps past the end time (it stops at the end before writing a
frame there); `anim_to_vtk` writes single-precision points, so nodes are matched by id.

## Plan

1. The adapter (solids, elastic and tabulated plastic materials, supports, forces,
   pressure, gravity, imposed and initial velocities, rigid walls, amplitudes), the
   `explicit` step and its settings, results and energies, the progress parser, the
   stable-time-step checks and `explicit_estimate`, the container runtime, benchmarks
   one to four; the skill.
2. Shells, contacts, Johnson–Cook, mass scaling's report, the energy-balance checks and
   their benchmarks.
3. Native runs from the release zips (fetched and checked), MPI processes (ADR 0053's
   Parallel processes per run), Apptainer on remote hosts.
4. GUI polish: energy plots in the Solution panel, the Advisor's explicit steps, Help.

## Consequences

- Impact and short-pulse problems get a solver made for them, from the same model the
  implicit ones read; a shoot-out can set an explicit run beside an implicit transient
  for the slow part of a response.
- Puzzle depends on a young fork with one closed library still in its builds. The
  version is pinned, the container is the default, and only text decks pass between
  Puzzle and it, so a later release (or a reader replaced by open code) changes nothing
  in Puzzle but the pin.
- Explicit runs are long and their accuracy hangs on the time step, hourglass and
  added mass; Puzzle says those numbers before and after every run rather than leaving
  them in a listing.
- The adapter is a sixth input format to keep (fixed-width Radioss cards); its tests
  are the benchmarks, run on the pinned release.

## Alternatives considered

- **Implicit dynamics only** (CalculiX, Code_Aster): fine for vibration, but impact on
  them needs tiny steps with a factorisation each, and contact chatter stops Newton.
- **LS-DYNA keyword decks to OpenCourant's Starter**: one format with two readers, but
  the native block format is what the Engine's controls, the time history and the
  output are written in, and its error messages name its own cards.
- **Code_Aster's `DYNA_NON_LINE` explicit scheme** (DIFF_CENT): no rigid walls or
  crash contacts, and slow per step; kept as an implicit solver.
- **A solver of our own (central differences in NumPy)**: easy for the benchmarks, years
  from contacts, element formulations and materials a crash code has.
- **Bundling OpenCourant in the installer**: the AGPL allows it with the source offered,
  but 80-330 MB per platform for a solver many won't use; fetched on first use instead,
  as ADR 0072 phase 5 does for WSL's root file system.
