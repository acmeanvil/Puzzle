# 0076. Model threads as a correction on a contact between smooth cylinders

- Status: Accepted; steps 0-3, 5 and 6 built; step 4 (bolts) in progress (2026-11-09)
- Date: 2026-11-08

## Context

Bolts in tapped holes (ADR 0050) and other threaded parts are bonded on the hole wall
today. A bond carries the axial load but loses what the thread does:

- the load share per engaged thread (about 33 % on the first, then 27, 20, 12, 8 % for a
  standard nut);
- the radial wedge that opens the nut or boss;
- the lead's torque.

Meshing the helix is the alternative, but it is costly: 1.1 M elements against 69 k in
Ansys's M120 example.

ADR 0072 3a holds the research:

- **Commercial tools:** Ansys's Bolt Thread geometry correction, COMSOL's Bolt Thread
  Contact and Abaqus's `*CLEARANCE, BOLT` keep smooth cylinders and turn the contact
  normal onto the flank.
- **Their limits:** Ansys takes symmetric straight threads only, and Abaqus single starts.
- **Open source:** none of the open-source solvers has it.
- **Building blocks Puzzle already writes:**
  - CalculiX `*EQUATION`, GAPUNI per node, SPRING2;
  - Code_Aster `LIAISON_DDL`, `DIS_T`/`DIS_CHOC` on point elements (`comm._Points`);
  - contact pairing by KD-tree (`contact_check`);
  - helper nodes past the mesh's ids, dropped from results;
  - tributary face areas (`common.surface_area`).

Every flank direction is known in closed form, so equations and springs can be written in
global components. No `*TRANSFORM` and no solver support for modified normals is needed.

## Decision

1. **The thread is a correction on a contact, as in Ansys.** `Contact.thread: Thread |
   None` turns a contact between two coaxial cylindrical faces into a threaded one.
   - **Contact types:** bonded becomes a tie along the bearing flank, frictionless or
     frictional becomes unilateral flanks, no_separation becomes both flanks tied in
     their normals.
   - **What it reuses:** the Contact tool (0056), checks, schematic links and results.
   - **Bolts:** a bolt type's `thread_form` (and a bolt's own) makes its tapped end
     threaded (step 4).
2. **`Thread`** (in `model/schema.py`) holds:
   - `form`, a name from `threads.FORMS` or `custom`;
   - `pitch`, `starts`, `hand`;
   - `major`, `pitch_diameter`, `minor` (defaults from the form and the size);
   - `taper` (diameter per length, NPT 1/16);
   - `flank_angles` (load, other; degrees from the radial plane);
   - `axis` (fitted to the faces if unset; `cs` for a coordinate system's);
   - `bearing`: `auto` | `load` | `other`;
   - `compliance`: `teeth` | `rigid` | a stiffness per area;
   - `clearance` (backlash, default 0);
   - `engaged_length` (default the overlap).

   The forms:

   | Form | Flank angles (load / other) |
   | --- | --- |
   | `iso` (ISO metric, ISO 68-1) | 30/30 |
   | `un` (UN, ASME B1.1) | 30/30 |
   | `acme` (ASME B1.5) | 14.5/14.5 |
   | `stub_acme` | 14.5/14.5 |
   | `trapezoidal` (ISO 2904) | 15/15 |
   | `buttress` (ANSI B1.9) | 7/45 |
   | `buttress_din` (DIN 513) | 3/30 |
   | `square` | 0/0 |
   | `npt` (ASME B1.20.1) | 30/30 with a 1/16 taper |
   | `bspt` (ISO 7-1) | 27.5/27.5 with a 1/16 taper |

   Diameters and pitches come from `bolts.SIZES` where a size is named.
3. **`puzzle/threads.py`**, pure and solver-neutral (numpy only, no solver import), is the
   one place the geometry lives:
   - `axis_frame(mesh, faces)`: the fitted axis, the radii, the overlap;
   - `pairs(mesh, source, target, thread)`: node pairs at the same (θ, z), by the
     `contact_check` KD-tree, with the target point interpolated on six-node faces;
   - `normals(points, thread)`: n_k = (e_z − (sL/2πr) e_θ ∓ tan α_k e_r)/‖…‖ for each
     flank, where s is ±1 for the hand and L the lead. In 2D axisymmetric e_θ drops out;
   - `areas`: tributary, sec α_k per flank;
   - `stiffness`: the teeth's compliance per area, or rigid as a penalty of 50 E/h as
     today's contacts;
   - `bearing_flank(load direction)` for `load`/`other`;
   - `per_thread(z, forces)`: forces binned by pitch from the mouth.

   Each adapter gets `ThreadPairs` (nodes, directions, areas, stiffness, clearance) and
   writes only those. Adapters do no geometry.
4. **Per solver:**

   | Contact | CalculiX | Code_Aster | FEniCSx | Elmer |
   | --- | --- | --- | --- | --- |
   | bonded (one flank) | `*EQUATION` (u_t − u_s)·n = 0 per pair, or SPRING2 along n with `teeth` compliance | `LIAISON_DDL` per pair, or `DIS_T` along n | penalty term in the weak form | refused at first |
   | no_separation (both flanks) | two equations per pair | two `LIAISON_DDL` | penalty | refused |
   | frictionless / frictional | a GAPUNI per pair and flank along n_k, NLGEOM step, as compression-only supports; friction refused at first | `DIS_CHOC` per pair and flank, its own friction | `ufl.conditional` on each flank's gap; friction refused at first | refused |

   - **Capabilities:** a `Capabilities.threads` set says which contact types each solver
     takes. Others are refused with code `unsupported`, never bonded silently.
   - **Helper nodes:** where a spring or gap needs them, they come from the bolt machinery
     (0037, 0050) and are dropped from results the same way.
   - **Numbering:** `threads.py` numbers pairs, so results map back by pair.
5. **Results:**
   - flank force and pressure per pair (`thread_force`, `thread_pressure`: element-free
     nodal fields on the source face);
   - the global fields `thread_load` (per thread: axial force and share, a table and a
     Curve, ADR 0071) and `thread_wedge` (total radial force);
   - the Contact tool's Results page and a bolt's row in the bolt table show them.
6. **The model's other paths** (ADR 0057, 0071 decision 8):
   - MCP: `contact_thread` (set one up by form and size) and `thread_results`;
   - the `thread-engagement` skill;
   - the GUI: the contact's Properties (form dropdown, angles drawn as a small profile),
     Find contacts proposing a thread for coaxial cylinders of near-equal radius;
   - schematic links for `Contact.thread.cs`;
   - the Advisor: the Contacts step notes a bonded tapped hole and offers the thread.

## Implementation strategy (the next build)

Each step lands with its tests, CLAUDE.md lines and the skill section it adds. Steps 1-3
are the next build; steps 4-6 follow.

0. **Settle the open facts before code (half a day).**
   - Read Yamamoto's tooth-compliance terms and VDI 2230's thread and nut compliance from
     the sources. Put them with their references into `threads.stiffness`'s docstring.
   - Confirm that CalculiX GAPUNI takes a direction per element set. It does for the
     compression-only supports: one `*GAP` card per `E_GAP{i}`.
   - Confirm Code_Aster `DIS_CHOC` friction on point-pair (SEG2) elements against its
     validation test SDND102.
   - Abaqus's and Ansys's internal gap formulas are not needed: Puzzle's is the helicoid
     above.
1. **Model and geometry (no solver).**
   - `Thread`, `Contact.thread`, `threads.FORMS` (diameters per standard: ISO 68-1/261,
     ASME B1.1, B1.5, ISO 2904, B1.9, B1.20.1).
   - `threads.py` as in decision 3.
   - Problems: `thread_axis` (faces not coaxial), `thread_radii` (radii more than 2 % of
     d apart), `thread_mesh` (fewer than four nodes per pitch along the axis, Ansys's p/4
     rule).
   - Tests (`tests/test_threads.py`), against hand values:
     - normals: square threads radial-free, a 60° form's e_r/e_z = tan 30°, left-hand lead
       signs;
     - pair counts on a meshed coaxial pair;
     - tributary areas summing to πdL·sec α;
     - per-thread binning;
     - the forms' table against the standards' basic profiles.
2. **2D axisymmetric on CalculiX: the gate.**
   - The 2D path first: e_θ drops out, the meshes are small and a meshed-thread reference
     is easy to draw (`geometry/planar.py` polygon, sides named).
   - CalculiX writes bonded (equations) and unilateral (GAPUNI) flanks on CAX elements.
     Both models are meshed with quadrilaterals only (CAX8), which 2D models have had
     since 2026-11-08.
   - Benchmark `tests/integration/test_thread_benchmark.py`: an M16×2 cap screw in a steel boss,
     40 kN axial, the true sawtooth thread meshed as the reference, and the smooth model
     with `iso` and `compliance="teeth"`. Accept when:
     - the shank stress is within 1 %;
     - the first-thread share is within 3 points of the reference, and each later thread
       within 5;
     - the boss's hoop stress at the first thread is within 10 % (the wedge);
     - the axial stiffness is within 5 %.
   - The same for `acme`, `trapezoidal`, `buttress` (both load directions: the steep
     flank must bear in one only) and `square` (wedge < 1 % of the 60° one).
   - Also within 10 % of VDI 2230's thread compliance.
   - If `teeth` misses, the stiffness formula is tuned once against the reference and the
     tuning stated in the ADR.
3. **3D on CalculiX and Code_Aster.**
   - 3D pairs with e_θ.
   - CalculiX writes the same cards on C3D10. Code_Aster writes `LIAISON_DDL` for bonded
     and `DIS_CHOC` for unilateral flanks with friction, through `comm._Points`.
   - Benchmarks:
     - the 2D case revolved, matching the 2D per-thread shares within 3 points;
     - the circumferential pressure spread at the first thread under 10 %;
     - CalculiX and Code_Aster agreeing within 2 %;
     - with friction μ = 0.15 on Code_Aster, the torque to turn the screw under preload
       within 5 % of T = F (d₂/2) tan(λ + ρ′), with ρ′ = atan(μ / cos α).
   - Meshing: the screw and boss are bodies of revolution, so they are meshed with hexes
     cut into blocks at mesh time (ADR 0072 section 5, built as part of this step). The
     same blocks all the way round give the circumferential check a clean baseline, and
     the 3D results line up node for node with the 2D section. Tets stay the fallback, and
     the check is run on both.
   - The Contact tool shows thread pairs and flanks.
   - Results fields and the thread table.
4. **Bolts.**
   - `BoltType.thread_form` (default `iso` for ISO sizes, `un` for UNC/UNF), and
     `BeamFastener.thread` to override.
   - A solid bolt (`formulation="solid"`) in a tapped hole gets a threaded contact between
     its shank face and the thread face `with_joints` already makes.
   - A beam bolt's thread end gets a line of axis nodes along the engaged length:
     - tied to the beam's end node, rigid along the axis (a bolt core of the bolt's
       stiffness, `beam_bolt_stiffness`, spread over the engaged length);
     - each node pairing with the ring of thread-face nodes at its z through the flank
       springs.
   - The bolt table's per-thread column, and `bolts.bolt_results` with `thread_load`.
   - Benchmark: the bracket assembly (`examples/bracket_assembly`) with tapped bolts:
     - the beam bolt's thread shares within 5 points of the solid bolt's;
     - the bolt forces unchanged within 1 % from today's bond.
5. **FEniCSx and Elmer.**
   - FEniCSx: a penalty over the interface pairs, written into `case.json` as pair arrays.
     `study.py` adds the flank springs to the stiffness matrix: assembled as a SciPy
     sparse addition after the form, since a UFL integral over paired faces of two
     meshes is awkward. Unilateral flanks use an active-set loop.
   - Elmer: bonded only, as SIF linear constraints if a benchmark passes. Otherwise it
     stays refused, and that is listed in ADR 0072.
6. **The model's other paths and documentation.**
   - MCP tools, the skill (`src/puzzle/agent/skills/thread-engagement.md`, listed in
     `WITH_SKILLS`), GUI forms, Find contacts' proposal, the Advisor note, the
     schematic-parity row.
   - The design doc and CLAUDE.md lines, and this ADR's status to Accepted with the
     measured results.

### Progress (2026-11-09)

- **Step 0, settled.** VDI 2230-1:2015, p. 40, Eqs. 20-27 (the maintainer's copy):
  δ_G = l_G / (E_S A_d3), l_G = 0.5 d; δ_M = l_M / (E_M A_N), A_N = π d²/4, l_M = 0.4 d
  (a nut) or 0.33 d (a tapped thread joint), as `threads.vdi_compliance` has them.
  Yamamoto's tooth terms (1980) as restated by Liu et al., Engineering Structures 315
  (2024) 118486, Eqs. 5-12: δ1 bending, δ2 shear, δ3 root inclination, δ4 root shear,
  δ5 radial; the general forms rebuilt from Eq. 5 reproduce Liu et al.'s ISO
  coefficients (screw 0.015, 0.84, 0.106, 1.246; nut 0.067, 1.164, 0.293, 1.142).
  `teeth` is δ1-δ4 of both sides in series (δ5 is the continuum's own radial give).
- **Step 1, built.** `Thread` and `Contact.thread` (a threaded contact is always meshed
  apart), `puzzle/threads.py` (forms with their basic profiles, `spec`, frames and the
  axis fit, normals, pairs, areas, stiffness, per-thread binning, VDI compliance), and
  `adapters/thread_pairs.py` (pairs, flank normals, areas and stiffness on a mesh; the
  bearing flank faces the way the screw goes on past the engagement; `thread_radii`
  refused). `check_contacts` refuses a thread unless the adapter lists its type, so no
  adapter writes it as a plain tie. Tests: `tests/test_threads.py`, 15, against hand
  values and the standards' basic profiles.
- **Step 2, built.** CalculiX writes threads in 3D and axisymmetric 2D (`inp._threads`):
  - bonded and no_separation: a SPRINGA from the screw node to a helper half a pitch
    out along n, the helper tied to the nut node, or `*EQUATION` when rigid;
  - frictionless: the same spring with a nonlinear law (k in compression past the
    clearance, 1e-4 k in tension), in an NLGEOM step. GAPUNI on a 2D model's nodes,
    and the contact output CalculiX writes for contact pairs, crashed it.
  - An axisymmetric model is a 2° slice to CalculiX, so springs are written × 2/360
    (the first runs had them 180 times too stiff, the teeth as good as rigid).

  The M16×2 benchmark (`tests/integration/test_thread_benchmark.py`, axisymmetric CAX8;
  the meshed reference converged: 0.25 and 0.12 mm elements give 50.0 and 50.1 %, 1.167
  and 1.166 kN/µm). The reference glues its bearing flanks, so the smooth thread ties
  both flanks (no_separation) to compare like with like, the cylinders at d3/2:

  | Smooth model | First thread | Threads 2-8 | Stiffness |
  | --- | --- | --- | --- |
  | Yamamoto's teeth as published | 36.8 % | | 1.107 kN/µm |
  | × 0.25 (`TEETH_FACTOR`, the tuning) | 47.6 % (50.0) | within 4.7 points | 1.197 (+2.6 %) |

  The tuning is stated: Yamamoto's cantilever teeth are softer than teeth in a
  continuum; one factor, 0.25, meets the gate. A bonded thread (the bearing flank's
  normal tied, the flank free to slide, as real threads) gives 38.1 % and 0.993 kN/µm:
  softer than the glued reference, as it should be. Wedges are F tan α to four digits
  (iso 23094 N, acme 10345, trapezoidal 10718, buttress 4911 on its 7° flank and 40000
  on its 45° one, square 0); unilateral flanks bear as the bonded tie under a pull.
  CalculiX's frictionless thread_load sums to 103 % (its nonlinear spring under NLGEOM
  carries about 4 % less than k times the push); bonded and no_separation sum exactly.
  VDI's whole-engagement compliance isn't compared yet.
- **Step 3, CalculiX built.** 3D on second-order tets, the same springs plus a "turn"
  spring per pair: tied along the flanks only, the screw unscrewed under a pull (the
  helix holds it by L / 2πr), the per-thread forces swinging hundreds of percent; a
  circumferential spring stands in for the friction that holds a thread. Every node of
  a six-node face takes a share (corners 1/12, mid-edge 1/4): the consistent shares
  leave the corners without springs. The revolved model matches the 2D shares within
  1.3 points, the wedge within 0.2 % (23056 against 23094 N), the flank
  pressure around the first thread within 8.6 % (eight sectors). Hex blocks of
  revolution (ADR 0072 section 5) are not built: tets passed the check.
- **Step 3, Code_Aster built** (`code_aster/thread_springs.py`): a helper node per pair
  and flank whose DX = √(k/k0) n·(u_nut − u_screw) by LIAISON_DDL, a `K_T_D_N` point
  spring k0 on it (one group per flank); rigid as LIAISON_DDL; frictionless as DIS_CHOC
  (the clearance as COEF_IMPO: `DIST_1` narrowed the gap), 1e-4 k in tension; AXIS per
  radian (the ring's stiffness / 2π). It matches CalculiX to four digits where both are
  linear (2D bonded 38.05/38.07 %, no_separation 47.56/47.62 %, rigid, and 3D bonded
  40.06 %, 0.9800 kN/µm, 23056 N on both). Frictionless, Code_Aster's wedge is F tan 30°
  within 2 %, and with a 0.01 mm clearance its stiffness is the hand value (0.7722 kN/µm
  against 0.772); CalculiX's NLGEOM springs are 3 % off (10 % with the clearance), so
  CalculiX's frictionless threads are the less accurate. 3D threads are slow on
  Code_Aster (about 6 minutes for 2300 pairs: a one-node group per node for
  LIAISON_DDL); the solve itself is seconds.
- **Step 4, beam bolts built on CalculiX.** `BoltType.thread_form` (iso for ISO sizes, un
  for UNC/UNF, none) and `BeamFastener.thread` (over the type's; `bolts.type_thread`,
  filled by `bolts.resolve`). A beam bolt's tapped end (`joint` tapped or stud_tapped)
  is a thread, not a spider on the wall (`inp._thread_end`): the engaged core moves
  level by level rigidly with the end's spider but for an axial slide per level, the
  slides joined by springs E A_d3 / Δz; each wall node bears on its level through a
  flank spring to a helper following that motion (tying helpers to core nodes that
  were themselves dependent left CalculiX decascading for 20 minutes); the RBE3's twist
  row holds the bolt's turning. Benchmark: an M16 8.8 cap screw through a plate into
  the boss shares its thread as the solid smooth screw, measured from the boss's side
  alike, within 2 points per thread (30.8 against 29.6 % on the first). Not done yet:
  threaded ends on Code_Aster and FEniCSx bolts, solid bolts (`formulation="solid"`),
  per-thread loads in the bolt table and `bolt_results` (the core's helpers are dropped
  from results), and the bracket assembly with tapped bolts.
- **Step 5.** FEniCSx writes bonded, no_separation and frictionless threads in 2D
  axisymmetric static steps (`case._threads`, `study.solve_threaded`: k n nᵀ springs
  added to K, an active set for unilateral flanks, rigid as 1e3 × the teeth), matching
  CalculiX to four digits (bonded 38.07/38.08 %, no_separation 47.58/47.61 %); 3D
  refused until benchmarked. Elmer refused: its SIF has no node-pair springs or
  directional constraints (listed in ADR 0072). dolfinx reads no 8-node quadrilaterals,
  so FEniCSx refuses second-order 2D quadrilateral meshes.
- **Step 6, built.** MCP `thread_forms`, `contact_thread` (a command to submit),
  `thread_results`; skill `thread-engagement`; the contact's Thread group in Properties,
  Make it a thread… / Remove the thread, Model → Contacts → Thread on a contact…; Find
  contacts' Thread column for coaxial cylinders; the Advisor's note; schematic wires
  for `contacts[].thread.cs` and `connectors[].thread.cs` (`puzzle/thread_setup.py`).
  The profile drawing in Properties isn't done.

Run keys: `Contact.thread` is in the contact's hash. Changing a form reruns, but naming a
group does not.

## Consequences

- Tapped holes and threaded parts give the per-thread load, the wedge and the lead's
  torque at the smooth model's cost. Buttress and tapered threads are covered, which the
  commercial features refuse.
- The geometry lives in one tested module, and adapters only write pairs. A new solver
  needs equations, springs or gaps, nothing thread-specific.
- Unilateral flanks make a study nonlinear (CalculiX NLGEOM steps, Code_Aster
  `STAT_NON_LINE`), so solves are slower than a bond. Bonded one-flank threads stay
  linear.
- To watch:
  - the teeth's compliance is a formula, and its tuning (step 2) must be stated;
  - thread-root peak stresses are not represented, as in every smooth model: the skill
    says so and points at a meshed thread for fatigue at the root;
  - large rotations of the bolt are outside the small-strain pairing.

## Alternatives considered

- **Meshing the helix:** exact, but 10-20× the elements, and swept helical geometry is
  fragile in OCCT. It stays possible and is the benchmark reference.
- **Solver-native modified normals** (Ansys/Abaqus style): no open-source solver has them,
  and CalculiX's `*CLEARANCE` was 3× off here (0056).
- **A new connector kind instead of a contact correction:** it would duplicate pairing,
  checks, the Contact tool and results. Ansys and COMSOL also put it on the contact.
- **Axisymmetric-only:** the 2D path is the gate, but tapped holes in assemblies are 3D.
- **Bond plus a computed share** (Yamamoto applied after the solve): cheap, but no wedge
  and no effect on the model's stiffness or the boss's stresses.
