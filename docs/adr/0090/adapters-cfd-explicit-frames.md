# Design review: Code_Saturne, OpenFOAM, OpenCourant and PyNite adapters, and `mesh/layer.py`

Reviewer scope: `src/puzzle/adapters/{code_saturne,openfoam,opencourant,pynite}/`, `src/puzzle/explicit.py`, `src/puzzle/mesh/layer.py`, and `src/puzzle/frames/` as far as the PyNite adapter depends on it.

Method: every file in scope was read in full. Where a finding could be checked cheaply it was run: small cases in the solver images already on this machine (Code_Saturne 9.2, OpenFOAM 2412, OpenCourant latest-20261011) and PyNite in the pixi `pynite` env. Each finding says whether it was run or only read. Nothing in the repository was changed. Scratch scripts and run folders are under `...\scratchpad\review\` (`cfd_check.py`, `oc_check.py`, `centroid.py`, `pyn\`, `rev-*`, `oc-*`).

Line numbers are those of the files on `development` at `da26cde`.

## Counts

| Severity | Count | IDs |
| --- | --- | --- |
| Critical | 1 | OF-1 |
| High | 3 | OF-2, OC-1, PYN-1 |
| Medium | 7 | CS-1, OF-3, OC-2, OC-3, OC-4, OC-7, PYN-2 |
| Low | 6 | CS-2, CS-3, OF-4, OC-5, OC-6, PYN-3 |

---

## Critical

### OF-1. OpenFOAM: a wall `heat_flux` in turbulent flow delivers two to three times the flux asked for

- **Where:** `src/puzzle/adapters/openfoam/case.py:320-325` (the `fixedGradient` wall condition), with the diffusivity of `scalarTransport` set at `case.py:394-403`.
- **What is wrong:** the wall gradient is written as `q / conductivity`, the laminar conductivity. `scalarTransport` solves T with diffusivity `alphaD nu + alphaDt nut` (the adapter's own comment says so). On a wall face with `nutkWallFunction`, `nut` is not zero once y+ is past about 11, so the flux the solver actually applies is `rho cp (nu/Pr + nut_wall/Pr_t) q / k = q (1 + (Pr/Pr_t)(nut_wall/nu))`.
- **Failure scenario (run):** air at 30 m/s between plates 10 mm apart, 2D planar, 1 mm quadrilaterals, k-epsilon, inlet 293.15 K, walls `heat_flux` 1000 W/m², 100 mm long. The energy balance requires a bulk temperature rise of `2 q x / (rho cp U H)`.

  | x (m) | Energy balance (K) | OpenFOAM (K) | ratio | Code_Saturne (K) | ratio |
  | --- | --- | --- | --- | --- | --- |
  | 0.03 | 0.165 | 0.317 | 1.92 | 0.157 | 0.95 |
  | 0.05 | 0.275 | 0.656 | 2.38 | 0.262 | 0.95 |
  | 0.07 | 0.385 | 0.884 | 2.29 | 0.381 | 0.99 |
  | 0.09 | 0.495 | 1.204 | 2.43 | 0.485 | 0.98 |

  The written `0/T` has `gradient uniform 38910.5` (= 1000 / k). The final `nut` on the wall patch is about 3e-5 m²/s against nu = 1.5e-5, which gives a face diffusivity 2.7 times the laminar one, matching the measured ratio. The OpenFOAM run had not met its residual targets at 1500 iterations (residuals stalled near 1e-3), so the exact ratio is approximate; the direction and size are not in doubt.
- **Why it was not caught:** the heated-channel benchmark is laminar (`nut` = 0), and the turbulent channel has no heat.
- **Confidence:** high. Verified by running both solvers on the same case and by reading the wall `nut` values.
- **Fix:** make the wall gradient follow the effective diffusivity. Options, in order of preference:
  1. An expression boundary condition that divides by the patch's own diffusivity, e.g. ESI's `exprMixed` with `valueFraction 0` and a gradient expression `q / (rho cp (nu/Pr + nut/Pr_t))`. I believe v2412 has it but did not confirm in the image; check before relying on it.
  2. Refuse `heat_flux` on a wall when `turbulence_model` is not laminar until a benchmark passes, and remove the claim from the capability matrix for that combination.
- **Test:** the scratch case above as `tests/integration/test_turbulent_heated_channel.py`, over `openfoam` and `code_saturne`: bulk temperature rise within 5 % of the energy balance at two stations.

---

## High

### OF-2. OpenFOAM: 3D results cannot be read back for hexahedra, prisms or pyramids that are not parallelepipeds

- **Where:** `src/puzzle/adapters/openfoam/case.py:246-251`; the tolerance is `match_points(..., rel_tol=1e-5)` in `src/puzzle/adapters/common.py:454-482`.
- **What is wrong:** OpenFOAM's `C` is the volume centroid; the reference is the node average. They are equal only for tetrahedra and parallelepipeds. The comment at line 247 says other cells are "close enough for the coordinate match's tolerance on fine meshes", but the tolerance is 1e-5 of the model size.
- **Failure scenario:** any hexahedral recipe on a body that is not a box: the butterfly blocks of a pipe (`mesh/blocks.py`), a diffuser, a boundary-layer prism layer on a curved wall, pyramids. A 10×10×10 block of hexahedra whose cross-section doubles along its length has centroids up to 1.2e-3 of the model size from the node averages, 120 times the tolerance. `match_points` raises `result point ... matches no mesh entity` after simpleFoam has finished, so the solve is lost.
- **Confidence:** high on the mismatch (computed in `centroid.py`, which calls the real `match_points` and gets the `InputError`). Not run through OpenFOAM itself. Code_Saturne is not affected: its reader also uses node averages (`ensight.py:91`).
- **Fix:** match by nearest centre with a one-to-one check, as `layer.match` already does for layered meshes, with the distance checked against each cell's own size instead of the model's. Better still, do not match by coordinates: write the reference centroids the way OpenFOAM computes them (face-pyramid decomposition), or have `postProcess` sample at the reference points.
- **Test:** a unit test with a tapered hexahedral block and a prism layer, with centres computed as volume centroids, mapped back to the right ids; an integration test of laminar pipe flow on a hexahedral recipe against Hagen–Poiseuille.

### OC-1. OpenCourant: a name that starts with `#` changes the deck silently

- **Where:** `src/puzzle/adapters/opencourant/deck.py:56-57` (`_title`), used by every `deck.card` (`:129-130`) and the `/BEGIN` title (`:610`).
- **What is wrong:** Radioss treats a line starting with `#` as a comment. A title made from a user's name (material, amplitude, contact, study, part selection) that starts with `#` is skipped, and the next data line is read as the title. `_title` only removes unprintable characters. A name starting with `/` would be read as a keyword; `$` is likely a comment too (not run).
- **Failure scenarios (both run):**
  - An amplitude named `#1 ramp` with points (0, 0), (40 µs, 1) on a `velocity` of 1 m/s: the `/FUNCT` loses its first point and the ramp becomes a step. The end face moves at 1.0 m/s from the first frame (it should be 0.146 at 5.8 µs, 0.40 at 16 µs). No Starter warning, no error, `run_checks` empty. With the amplitude named `ramp` the same model follows the ramp exactly.
  - A material named `#1 Steel`: the Starter reads E as the density (`INITIAL DENSITY = 200000000000.0`, `YOUNG'S MODULUS = 0`) and stops with error 683. This one fails loudly, but with a message that points nowhere near the cause.
- **Confidence:** high, verified by running in the pinned image.
- **Fix:** in `_title`, replace a leading `#`, `/` or `$` (after stripping leading spaces) with a harmless character, or prefix every title with a fixed word.
- **Test:** a unit test that no title line in the deck starts with `#`, `/` or `$` for names that do; one integration test with a `#`-named amplitude checking the ramp.

### PYN-1. PyNite: a rigid end offset weighs 10,000 times its real mass in modal and response-spectrum runs

- **Where:** `src/puzzle/adapters/pynite/case.py:28` (`RIGID = 1e4`) and `:378-380` (the `<section>R` copy multiplies A, Iy, Iz and J), with `src/puzzle/adapters/pynite/frame.py:142-146` (`add_member_self_weight` in the mass combination).
- **What is wrong:** the rigid offset is a member whose section constants are all multiplied by 1e4, the area included. PyNite's consistent mass is `rho · L · section.A` (`Member3D.consistent_m`), so the offset's mass is multiplied too. The static self-weight path is right: it uses the original section's area (`case.py:479`).
- **Failure scenario (run):** a 2.2 m steel cantilever (A = 0.01 m²) with a 0.2 m rigid offset, built as the adapter builds it and solved by `frame.py`: modal mass 157,105 kg instead of 123 kg, first frequency 1.02 Hz instead of 58.4 Hz. Any frame with `member_offsets` (panel zones) gives wrong periods, participation, base shear and ELF scaling.
- **Confidence:** high, verified by running `frame.py` on a hand-written `case.json` with the `S1R` section exactly as `write_case` makes it.
- **Fix:** give the rigid member its own material with the density divided by `RIGID` (or zero), or scale only Iy, Iz and J and leave A alone (axial rigidity then needs another route, e.g. E scaled in a rigid material with rho scaled down). Either way the mass per length must stay `rho A`.
- **Test:** in `tests/integration/test_seismic_frames.py`, a cantilever with and without a short rigid offset: total modal mass equal, and the frequency the closed form for the shortened flexible length.

---

## Medium

### CS-1. Code_Saturne: the `pressure_outlet` level is lost, so pressures differ from OpenFOAM's by a constant

- **Where:** `src/puzzle/adapters/code_saturne/case.py:147-148`, `cs_setup.py:107-108` (`setReferencePressure`), and the read at `case.py:297-303`.
- **What is wrong (run):** with one outlet at p = 100 Pa, `setup.xml` holds `<dirichlet name="pressure">100</dirichlet>`, yet Code_Saturne reports pressures of 0 to 0.0033 Pa; OpenFOAM reports 100.000 to 100.004 Pa on the same model. Code_Saturne appears to shift outlet pressures so the reference outlet face is at zero (my recollection of its boundary-condition code; the log shows `xyzp0` placed on the outlet). Differences are kept, the level is not.
- **Consequences:** a shoot-out's pressure fields differ by the outlet pressure; a probe of absolute pressure gives a different number per solver. With several outlets at different pressures the differences should hold, but that was not run.
- **Also (read only):** `setReferencePressure` calls `Model().isPositiveFloat(value)`, so a negative gauge pressure would stop `cs_setup.py` with a model error. `cs_setup.py:107` skips the call for p = 0, so only negative values are at risk.
- **Confidence:** high for the single-outlet offset (run); medium for the cause and the negative-pressure crash.
- **Fix:** add the level back when reading: the set pressure of the outlet that holds `xyzp0` (parse it from `run_solver.log`), or the difference between a set outlet pressure and the mean result on that outlet's cells. Write negative values to `setup.xml` directly if the model refuses them.
- **Test:** extend `test_cfd_shootout.py` with an outlet at 100 Pa: both solvers' mean pressure at the outlet within 0.01 Pa of 100; one case with p = −50 Pa.

### OF-3. Heat transfer with no inlet temperature: OpenFOAM assumes 293.15 K silently, Code_Saturne fails inside the solver

- **Where:** `src/puzzle/adapters/openfoam/case.py:346` (`patch.get('T', 293.15)`) and `:275-276`; nothing in `code_saturne/case.py` checks it.
- **What happens (run):** the same model (wall heat flux, no `temperature` on the inlet) gives on OpenFOAM a result with the inlet at 293.15 K and no message. On Code_Saturne the run stops in `cs_boundary_conditions_type` with "inlet ... for which the value of temperature has not been specified".
- **Confidence:** high, both run.
- **Fix:** both adapters refuse it before writing: "heat transfer needs a temperature on every velocity inlet (on 'X')", code `missing_input`. Better as a `Project.problems` entry so the Advisor shows it.
- **Test:** unit tests in `test_openfoam_adapter.py` and `test_code_saturne_adapter.py` expecting the `InputError`.

### OC-2. OpenCourant: a prescribed displacement with no amplitude is applied as a jump at t = 0

- **Where:** `src/puzzle/adapters/opencourant/deck.py:374-385` with `_amplitude` at `:342-349` (no amplitude means the constant function 1).
- **What happens (run):** a 20 mm steel block, one end fixed, the other given `displacement x = 0.01 mm` with no amplitude. The end is moved in the first cycles; peak stress is 2.2 times the static value, the block rings for the rest of the run, and `run_checks` reports the energy balance off by 26 % with the explanation "a time step scale too large, or a contact or wall making or losing energy", which is not the cause. With a ramp amplitude the same model is clean and the check is silent.
- **Why it matters:** in every implicit solver a displacement without an amplitude is the normal, correct input. A user or the agent moving a study to an explicit step gets a shock load with no warning that names it. The skill (`explicit-dynamics.md:39-40`) does not mention it.
- **Confidence:** high, run.
- **Fix:** refuse a nonzero `displacement` without an amplitude in an explicit step (say to add an amplitude or use `velocity`), or ramp it over the step by default and say so in the log. Add "a displacement applied at once" to the energy check's explanation when the deck has an un-ramped `/IMPDISP`.
- **Test:** unit test for the refusal (or the ramp); integration test of a ramped displacement reaching the static stress within a few percent.

### OC-3. OpenCourant claims capabilities that no benchmark covers

- **Where:** `src/puzzle/adapters/opencourant/__init__.py:27-56`; benchmarks in `tests/integration/test_explicit.py`.
- **What is wrong:** the project's rule is that an adapter claims a capability only once a benchmark passes. These are claimed and written but appear in no solved test:
  - `frictional` contact (`/INTER/TYPE7` with Fric; every contact test is frictionless);
  - `displacement` (`/IMPDISP`);
  - `gravity` and `acceleration` (`/GRAV`; only the card's presence is asserted in `test_opencourant.py`);
  - a rigid wall's `mu`;
  - amplitudes on `displacement`, `force`, `pressure`, `gravity`, `acceleration` (only `velocity` is tested with one);
  - `mass_scaling_time_step` (`/DT/NODA/CST`).
- **What I ran ad hoc (all correct):** free fall under gravity matched ½ g t² to 4e-6; a block sliding on a rigid wall with mu = 0.3 decelerated at mu g to four digits; a ramped `/IMPDISP` followed its ramp. These can become the missing tests almost as they are (`oc_check.py`).
- **Not run and worth a real benchmark:** frictional contact between two bodies. The `/INTER/TYPE7` card leaves `Iform` at 0 (`deck.py:538`), which I believe is Radioss's viscous friction formulation; the stiffness formulation (`Iform = 2`) is the usual advice where parts stick. I am not sure of the default's accuracy, so measure it (a block on an incline at mu just above and below tan θ).
- **Confidence:** high that the tests are missing (read); the friction formulation concern is speculative.
- **Fix:** add the benchmarks, or drop the unbenchmarked claims until they exist.
- **Test:** see above; also a test that maps every capability key of an adapter to a named integration test, so a new claim without a benchmark fails.

### OC-4. OpenCourant native driver: the model is split for N processes and then run by a one-process Engine when `mpiexec` is missing

- **Where:** `src/puzzle/adapters/opencourant/native.py`, `main()` (the Starter is run with `-np processes` before `mpiexec` is looked for; the fallback prints "the Engine runs as one process (the model was split in N)").
- **What is wrong:** the Starter writes one restart file per domain for an MPI Engine. I expect the shared-memory Engine to refuse a model split into N domains (or, worse, to read one domain). Not run: this machine uses the container.
- **Failure scenario:** Settings > Solvers > Parallel processes per run = 4, native OpenCourant on a machine with no MPI: the run fails after the Starter, or runs part of the model.
- **Confidence:** medium, read only; the Engine's exact behaviour is from memory.
- **Fix:** look for `mpiexec` first, and run the Starter with `-np 1` when it is missing.
- **Test:** a unit test with `shutil.which` patched to None asserting the Starter's `-np` is 1.

### OC-7. OpenCourant: a shell's `stress` is its membrane stress only

- **Where:** `src/puzzle/adapters/opencourant/deck.py:686` (`/ANIM/SHELL/TENS/STRESS/MEMB`), read at `output.py:109-119` into the field `stress`.
- **What is wrong:** for a plate in bending the membrane stress is near zero while the surface stress is the result that matters. The field is named `stress`, the same as every other solver's, so von Mises, probes and fatigue read a number that is far too low. The pressed-plate benchmark compares displacement only.
- **Confidence:** high on what is written and read (read only); not run.
- **Fix:** also write `/ANIM/SHELL/TENS/STRESS/UPPER` and `/LOWER` and store top and bottom stresses as the other shell adapters do, or at the least rename the field `membrane_stress` and say so in the skill.
- **Test:** the pressed plate against CalculiX's shell surface stress, not only its deflection.

### PYN-2. Direct Analysis Method: τb = 1 is used with 0.002 Yi and no check that it is allowed

- **Where:** `src/puzzle/adapters/pynite/case.py:273` and `:404-413`, `:505-507`; `agent/skills/frames.md:66-70` describes it as "0.8 EI and 0.8 EA (τb = 1), plus notional loads of 0.002".
- **What is wrong:** as I read AISC 360-16 C2.3, τb = 1.0 holds only where αPr/Pns ≤ 0.5; above that τb = 4(αPr/Pns)(1 − αPr/Pns), or τb = 1.0 may be kept for all members if a further 0.001 αYi is added to the notional loads in every combination. The adapter does neither and nothing in `frames/design.py` checks Pr/Pns. The stainless branch does add the extra load its code asks for (0.004), so the carbon-steel branch is the inconsistent one.
- **Failure scenario:** a frame with columns loaded past half their squash load: flexural stiffness is over-stated, second-order moments under-stated, with no warning.
- **Confidence:** medium. The code is as described (read); the clause is from memory and should be checked against the Specification before changing anything.
- **Fix:** either apply 0.003 Yi for carbon steel when τb = 1 is used throughout, or compute τb per member from the first pass's axial forces, or at least report a problem from `member_checks` when αPr/Pns > 0.5 in a DAM run.
- **Test:** a heavily loaded column under DAM against a hand calculation with τb < 1.

---

## Low

### CS-2. Code_Saturne's turbulent viscosity is never read

- **Where:** `src/puzzle/adapters/code_saturne/case.py:57` maps `turbulent_viscosity`.
- **What happens (run):** Code_Saturne 9.2 names the variable `TurbVisc` in `RESULTS_FLUID_DOMAIN.case`, so a k-epsilon run has no `turbulent_viscosity` field (`KeyError` in my script) while OpenFOAM's has one. The OpenFOAM docstring says the field is stored "as Code_Saturne reports it".
- **Fix:** add `"TurbVisc": ("turbulent_viscosity", [""])` (keep the old key). **Test:** assert the field in `test_turbulent_channel.py` on both solvers.

### CS-3. Code_Saturne progress judges a transient run by the steady criterion

- **Where:** `src/puzzle/adapters/code_saturne/progress.py:100` (the step is always announced as `stationary` with an iteration limit) and `:120-138` (`finish`).
- **What is wrong (read only):** a time march's time residuals have no reason to fall three decades. A good transient run ends with "its residual fell only N decades: the result may not have converged" and a `Diverged` event, and `progress._not_converged` can diagnose `not_converged`.
- **Fix:** read `case.json`'s `transient`; announce the step as `transient` with its end time and skip the steady test. **Test:** a recorded transient listing in `tests/data/progress` with no warning expected.

### OF-4. OpenFOAM's convergence targets are rarely met, so ordinary runs end as "not converged"

- **Where:** `src/puzzle/adapters/openfoam/case.py:455-461` (initial residuals p 1e-6, U 1e-7, turbulence 1e-6, T 1e-8) and `:429` (`gradSchemes default Gauss linear`).
- **Observation (run):** the k-epsilon case above stalled with initial residuals near 1e-3 (k, epsilon, Uy) for the last several hundred of 1500 iterations on a coarse unstructured quadrilateral mesh. Unlimited gradients with `linearUpwind` are a usual cause of that kind of stall; `cellLimited Gauss linear 1` for the gradients is the common remedy. I did not test the remedy.
- **Fix:** try limited gradients; consider judging convergence as Code_Saturne's parser does (decades fallen) or looser defaults (1e-4 to 1e-5), keeping `tolerance` as the override. **Test:** the turbulent channel converging within its iteration limit.

### OC-5. Frames past 999 are dropped, and the last frame depends on the time-step estimate

- **Where:** `src/puzzle/adapters/opencourant/output.py:77` and `deck.py:658` (glob `A[0-9][0-9][0-9]`); `deck.py:679` (`past`).
- **What is wrong (read only):** with more than 999 frames the animation files gain a fourth digit and are neither converted nor read. Separately, the run is extended by twice the *estimated* step so the last frame is written; if the solver's real step is more than twice the estimate, the frame at `end_time` is missing and nothing says so.
- **Fix:** glob `A[0-9]*`; after reading, warn if the last frame's time is short of `end_time` by more than half a frame interval. **Test:** unit tests on file names and on a results set with a missing last frame.

### OC-6. A force on an edge or point selection is shared equally among the nodes

- **Where:** `src/puzzle/adapters/opencourant/deck.py:432-433`.
- **What is wrong (read only):** on a shell edge or a beam's edge selection the end nodes get the same share as interior ones, and uneven node spacing is ignored, so the load is not uniform along the edge. The total is right.
- **Fix:** share by tributary length for line groups. **Test:** an edge force on a graded shell edge, nodal forces proportional to tributary lengths.

### PYN-3. Smaller PyNite points

- `case.py:475`: a `gravity` whose selection is not in the mesh applies to every member instead of being refused (a mistyped selection loads the whole frame).
- `frame.py:218`: `analyze_PDelta(check_stability=False)`; an unstable frame under P-Delta gets a solver exception or nonsense instead of PyNite's own message. The linear path keeps the check.
- Both read only.

---

## To check (speculative, not verified)

- **CS: faces outside every selection.** OpenFOAM makes them walls (`Allrun`); Code_Saturne is given no zone for them. Whether it errors or defaults was not run. The two adapters should behave the same, and say so in the log.
- **CS: `setTimePassing(-1)`.** I recall the steady SIMPLE algorithm being deprecated in recent Code_Saturne versions. The benchmarks pass on 9.2, and the adapter already falls back to a pseudo time step on wedges because the steady algorithm "runs away" there; that may be a sign to make the local or constant pseudo time step the default everywhere.
- **Both CFD adapters: label collisions.** `_label` maps `A/B C` and `A_B_C` to the same patch or zone name (`openfoam/case.py:490-491`, `code_saturne/case.py:313-314`). Unlikely, but nothing refuses it.
- **OC: `/INTER/TYPE7` defaults.** `Istf = 0` and `Iform = 0` are Radioss's defaults; see OC-3.
- **OC: `/INTER/TYPE2` with `Ignore = 0`.** A tied node with no target segment within the search distance may stop the Starter; partially overlapping bonded faces are common. Not run.
- **OC: energy check sampling.** `run_checks` reads the energies at the frames only (20 by default) although the full history is in `puzzleT01.csv`; a transient imbalance between frames is missed.
- **Layer: `layer.match`.** Nearest centre with a uniqueness check cannot detect two cells swapped with each other. On the axis pyramids the node average is h/20 from the volume centroid, well inside a cell, so I expect no swaps; a distance bound of half the local cell size would make it certain.
- **PyNite: beam forces averaged at joints.** Documented as matching Code_Aster; at a joint of members with different axes the average has no meaning. `member_forces` is the right field there, which the skill could say.

---

## What is done well

- **Refuse, don't ignore.** Unknown features, local axes, transferred inputs, contacts, second-order meshes, convection on OpenFOAM and mass-flow inlets are all refused with a clear message. I found no feature that is silently dropped except those listed above.
- **Units and signs.** Kinematic pressure and viscosity are converted to Pa and Pa·s on write and read; Code_Saturne's flux sign and percent intensity are handled and commented; OpenFOAM's inward normal velocity sign is right; the laminar benchmarks confirm it.
- **OpenCourant deck.** Card layouts I could check from memory are right: `/MAT/ELAST`, `/MAT/PLAS_TAB`, `/MAT/PLAS_JOHNS`, `/FAIL/JOHNSON` (no title line, correctly), `/PROP/SHELL`, `/BCS`, `/IMPVEL`, `/INIVEL/TRA`, `/CLOAD`, `/GRAV`, `/RWALL/PLANE`, `/INTER/TYPE7`. The tet10 node reorder is correct. Outward segment normals are found robustly. Gravity, wall friction and ramps gave exact answers when run.
- **Energy, hourglass and added-mass checks** after every explicit run, and a cost estimate before it.
- **Progress parsers never stop a solve** (`safe_feed`), and are tested on recorded logs.
- **`layer.py`** is careful: positive volumes for every cell type, the axis collapse cases, ids kept, one-to-one matching, results averaged back.
- **PyNite adapter.** The runner is isolated from Puzzle, releases and orientation are mapped cleanly, self weight is kept apart in modal runs to avoid double counting, and CQC is taken on base shears, not on summed envelopes.
- **Code_Saturne's `setup.xml` is built by its own case model**, so it stays valid for the installed version.

## Design-level recommendations

1. **One boundary-condition check for CFD, shared by both adapters**, run before writing: every inlet has a temperature when heat is solved, at least one outlet exists, uncovered faces are reported, labels are unique. Today each adapter finds these (or doesn't) in its own way and the two disagree (OF-3, CS-1).
2. **Stop matching cells by coordinates with a model-size tolerance.** Use nearest-centre with a one-to-one check and a local-size bound everywhere (OF-2), or carry ids through.
3. **Sanitise every string written into a solver deck in one place** (OC-1). The same question is worth asking of the CalculiX and Code_Aster writers.
4. **Tie capability claims to benchmarks mechanically** (OC-3): a table from capability key to test name, checked by a test.
5. **Turbulent heat transfer on OpenFOAM needs its own benchmark** before it is offered; a passive scalar with wall functions has no thermal wall treatment, and the flux error (OF-1) shows the gap. A buoyant or thermal solver with `alphat` wall functions would be the fuller answer.
6. **Explicit steps should not inherit implicit habits silently** (OC-2): un-ramped displacements, and probably un-ramped pressures and forces on quasi-static studies, deserve a pre-run warning from `explicit.py` beside the cost estimate.
7. **There is no agent skill for CFD.** `velocity_inlet` and `turbulence_model` appear in no file under `agent/skills/` (only in the help guide `analyses.md`). ADR 0057 asks for one per finished feature; inlet values, the outlet pressure's meaning, wall functions and y+, `pseudo_time_step` and the 2D layer belong in it.

## Not covered

- `frames/aisc360.py`, `aisc370.py`, `adm.py`, `design.py`, `seismic.py`, `seismic_frame.py`, `grid.py`: only where the adapter calls them (CQC, ELF scaling, notional loads). Their code-check formulas were not reviewed.
- OpenCourant's MPI run, Apptainer path and the native release fetch were read, not run.
- Code_Saturne transient runs, convection walls (`exchange_coefficient`) and the k-omega SST path were read, not run.
- OpenFOAM's wedge (axisymmetric) path and the expert pass-through were read, not run.
- PyNite plates (MITC4), P-Delta and the response-spectrum path were read; only the modal mass was run.
- Solver behaviour stated from memory and not confirmed here is marked as such in each finding (the Code_Saturne outlet shift, the Engine's handling of a split model, `exprMixed` in v2412, AISC 360 C2.3).
