# 0076. Model threads as a correction on a contact between smooth cylinders

- Status: Proposed
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
   - Benchmark `tests/integration/test_threads.py`: an M16×2 cap screw in a steel boss,
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
