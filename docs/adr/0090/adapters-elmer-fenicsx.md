# Review: Elmer and FEniCSx adapters, `eddy.py`, `common.py`

Scope read in full: `adapters/elmer/{sif,magnetics,meshfiles,output,sif_edit,progress,__init__}.py`,
`adapters/fenicsx/{study,case,__init__}.py`, `adapters/eddy.py`; `adapters/common.py` in the
parts these adapters call. Checks were run on this Windows machine (Elmer 26.2 image, FEniCSx
0.11 image, native SciPy-backend FEniCSx, CalculiX native). Scratch scripts are beside this file
(`every.py`, `edge.py`, `sec.py`, `mp.py`).

Counts: 2 critical, 3 high, 3 medium, 5 low; 8 items "to check".

---

## Critical

### ELMER-1: `output_every` labels Elmer's transient results with the wrong times
- **Where:** `src/puzzle/adapters/elmer/sif.py:329-339` (times list), `:893-894`
  (`Simulation/Output Intervals(1)` override), `:299-310` (ResultOutputSolver, `Exec Solver =
  After Timestep`, no `Exec Intervals`); `src/puzzle/adapters/elmer/output.py:60`
  (`zip(vtus, times, strict=False)`).
- **What is wrong:** with `output_every = k`, Puzzle writes the times `k dt, 2k dt, ...` to
  `puzzle-times.txt` and sets `Output Intervals(1) = k`. Elmer's ResultOutputSolver, run as a
  solver after every time step, still writes a `.vtu` at every step. `read_series` pairs the
  first `n/k` files with the thinned times, and `strict=False` hides the count mismatch. The
  second half of the run is dropped and every kept step carries a wrong time.
- **Verified by running** (`every.py`: bar, transient heat, dt 10 s, end 100 s, mean
  temperature per stored step):
  - Elmer, every=1: `10 s: 298.8605, 20 s: 301.3035, 30 s: 303.1292, ... 100 s: 311.1334`
  - Elmer, every=2: `20 s: 298.8605, 40 s: 301.3035, 60 s: 303.1292, 80 s: 304.6476, 100 s: 305.9748`
    (the values of 10, 20, 30, 40, 50 s, labelled 20 to 100 s)
  - FEniCSx, every=2 is correct (`20 s: 301.3086 ... 100 s: 311.1354`).
- **Reach:** every non-magnetic Elmer transient (heat and structural share the path); the live
  trackers too (`progress.py` `_values` indexes the same times file). The magnetics writer
  already handles this (`magnetics._at_outputs` writes `Exec Intervals`, and `read_magnetics`
  thins extra frames), so only `sif.py` is affected.
- **Fix:** in `sif.py` put `Exec Intervals = k` and `Exec Intervals Offset = k - 1` on the
  ResultOutputSolver block (reuse `magnetics._at_outputs`), drop the `Output Intervals`
  override or keep it only as well; in `read_series` use `strict=True` and, as
  `read_magnetics` does, thin when Elmer wrote every step. Raise when counts disagree.
- **Test:** an integration test over `elmer`, `fenicsx` (and CalculiX, Code_Aster) running the
  transient-conduction benchmark with `output_every = 2`: the step values must be
  `2dt, 4dt, ..., END`, and the field at each must equal the every=1 run's at the same time.

### FENICS-1: a condition on an edge or point selection is silently ignored
- **Where:** `src/puzzle/adapters/fenicsx/case.py:190-195` (only checks the group exists);
  `study.py` every `facets.find(tag(f["group"]))` / `ds(tag(...))`, e.g. `:521-523`, `:560`,
  `:1126-1133`, `:1222-1224`, `:1494-1499`.
- **What is wrong:** `study.py` looks every boundary feature up in the facet tags. A selection
  of lower dimension (an edge or point in 3D, a point in a 2D model) has a physical tag that is
  in no facet tag, so `facets.find` returns nothing: the Dirichlet condition holds no dofs and
  a `ds` load integrates to zero. Nothing is refused or logged. Only `force` is caught (through
  `surface_area`). The GUI's add dialog offers faces, but the model, MCP and TOML accept any
  selection, and CalculiX applies such a condition to the edge's nodes.
- **Verified by running** (`edge.py`: steady heat, 100 degC on `Bar/Root`, 20 degC on an edge
  selection at the tip):
  - FEniCSx: `T min 373.150 max 373.150` (the edge condition gone, no message)
  - Elmer: refused, `'Bar/TipEdge' must be a face selection for surface loads`
  - CalculiX: `T min 293.150 max 373.150`
- **Reach:** `fixed`, `displacement`, `pressure`, `traction`, `elastic_support`, `temperature`,
  `heat_flux`, `convection`, electrostatic and magnetic boundary features, custom-PDE
  `dirichlet`/`neumann`. A structure with a second support on an edge solves with that support
  missing; pinning a point in a 2D model does nothing.
- **Fix:** in `write_case`, for every boundary feature call `surface_blocks(mesh.groups[sel],
  mesh)` (as Elmer's `write_mesh` does) so a wrong-dimension selection is refused with code
  `unsupported`; or implement node-located Dirichlet conditions (`locate_dofs_topological` on
  ridge/peak tags, which `read_from_msh` returns) and refuse only the loads. In `study.py`,
  raise when a feature's facet set is empty.
- **Test:** the scenario above over all four FE solvers: each either refuses or returns
  `T min` = 293.15 K; never a uniform field.

---

## High

### FENICS-2: `Factored` crashes on the SciPy backend (2D magnetics, 3D magnetic transients)
- **Where:** `src/puzzle/adapters/fenicsx/study.py:376` (`n = self.solver.shape[0]`);
  `_Factor` (`:81-98`) has no `shape`.
- **What is wrong:** `Factored.solve` on the SciPy path reads `.shape` from a `_Factor`, which
  never defines it. Every solve through `Factored` fails: `solve_magnetic_axisymmetric`
  (axisymmetric and planar magnetics) and `_magnetic_transient`.
- **Verified by running:** `PUZZLE_FENICSX_RUNTIME=native pytest
  tests/integration/test_eddy_axisymmetric.py -k "billet and fenicsx"` fails with
  `AttributeError: '_Factor' object has no attribute 'shape'`. On this machine the default run
  passes only because the container is chosen first; a Windows user without Docker, or anyone
  with `PUZZLE_FENICSX_BACKEND=scipy`, hits it.
- **Fix:** give `_Factor` a `shape` (store `A.shape`), or take `n` from the dof map. Also
  destroy the PETSc matrix and KSP on `refactor` (one is leaked per frequency in
  `solve_magnetic_axisymmetric`).
- **Test:** run the 2D magnetic tests and one magnetic transient with
  `PUZZLE_FENICSX_BACKEND=scipy` in CI (the Linux job can force it), not only where PETSc is
  missing.

### ELMER-3: a modal study on Elmer silently drops elastic supports
- **Where:** `src/puzzle/adapters/elmer/sif.py:125-126`
  (`features = [f for f in features if f.type in HOLDING]`).
- **What is wrong:** `HOLDING` is `fixed`, `displacement`, `remote_displacement`. An
  `elastic_support` adds stiffness (Elmer's `Spring i`), which changes the frequencies, yet the
  modal filter removes it without a message. FEniCSx keeps it in modal steps (`study.py:567-572`
  is not guarded by `not modal`), so a shoot-out disagrees.
- **Failure scenario:** a plate on an elastic foundation, modal step, solver Elmer: frequencies
  of the free plate (rigid-body modes near 0 Hz) are returned as the result.
- **Confidence:** high, by reading; not run.
- **Fix:** keep `elastic_support` (add it to the filter, or define a shared
  `STIFFNESS = HOLDING + ("elastic_support",)` in `common.py` and use it in every adapter's
  modal filter). Refuse, never drop, anything else that changes the matrices.
- **Test:** a bar held only by an axial elastic support `kn` on one end: first axial frequency
  against the closed form (spring-mass limit for a stiff bar), over the four FE solvers.

### EDDY-1: element tables assume tetrahedra and triangles; other shapes give wrong totals
- **Where:** `src/puzzle/adapters/eddy.py:262-269` (`connectivity[:, :4]`, `tet_volumes`),
  `:332-342` (`connectivity[:, :3]`), `:354`/`:430` (`corners`); `fenicsx/study.py:1773`,
  `:1975` (`dofmap[:count, :4]`); `elmer/magnetics.py:430` (VTK types 10 and 24 only).
- **What is wrong:** nothing refuses a hexahedral, wedge or quadrilateral mesh in a magnetic
  study, and `elmer/meshfiles.py` writes hex8/quad4 happily.
  - 3D hex8: the first four nodes are one face, so every "tet volume" is 0: `joule_loss`,
    `magnetic_energy`, resistance and inductance come out 0 (or NaN), silently.
  - 2D quad4 (a 2D recipe with hexahedra meshes all quadrilaterals by default): the area is the
    first three corners' triangle, half the element. On Elmer the winding's current density is
    written as `N I / sum(area)` (`magnetics.py:504`), so it is doubled, and B with it; on both
    solvers the post-processed loss, energy and currents use half the area, and B, J use only
    three corners.
- **Confidence:** high that the arithmetic is wrong for these shapes (reading); medium that no
  earlier check stops such a mesh (I found none in `eddy.py`, `magnetics.py`, `case.py`,
  `puzzle/magnetics.py`).
- **Fix:** in `eddy.element_table` and `axisymmetric_table` refuse any block whose element is
  not tet4/tet10 (tri3/tri6) with code `unsupported` naming the recipe setting, until general
  shapes are written (volumes by quadrature, centres over all corners, `_hoop_mean` over all
  corners).
- **Test:** unit test: `element_table` on a one-hex mesh raises; `axisymmetric_table` on a
  one-quad mesh raises. Later, a hex/quad solenoid against the tetrahedral answer.

---

## Medium

### ELMER-2: `sif_edit` ends a section at a table's `End` and writes overrides inside the table
- **Where:** `src/puzzle/adapters/elmer/sif_edit.py:39` (`line.strip() == "End"`).
- **What is wrong:** tables (`Variable Time / Real / ... / End`) end with an indented `End`,
  which `sections()` takes for the section's end. A keyword that is not already present before
  the table is then appended inside the table's rows; a keyword that sits after the table is
  not found and is duplicated there.
- **Verified by running** (`sec.py`): on a `Solver 1` holding the harmonic `Frequency` table,
  `apply(..., {"Solver */Nonlinear System Max Iterations": "5"})` returns the line between the
  table's last row and its `End`.
- **Failure scenarios:** a structural harmonic study on Elmer with the shared `tolerance` or
  `max_iterations`, or Elmer's own `relaxation`, set; an expert `Material 1/...` entry when the
  material has temperature tables; an expert `Simulation/<new keyword>` in a harmonic magnetic
  study (the `Angular Frequency` table); an expert `Boundary Condition n/...` on a boundary
  with an amplitude table. Elmer then fails to parse the table or reads a broken one.
- **Fix:** end a section only at an unindented `End` (`line.rstrip() == "End"` and
  `not line.startswith(" ")`), and treat table continuation lines as part of their keyword.
- **Test:** unit tests of `apply` on sections with a table before and after the target keyword;
  an Elmer harmonic study with `max_iterations` set whose SIF is parsed back section by section.

### COMMON-1: `match_points` gives two coincident nodes one id
- **Where:** `src/puzzle/adapters/common.py:474-482`; used by `fenicsx/case.py:518`,
  `elmer/magnetics.py:421,433`.
- **What is wrong:** when the reference holds coincident points (two parts touching with no
  contact between them keep their own nodes), both result points take the last one's id.
- **Verified by running** (`mp.py`): reference ids `[10, 20, 30, 40]` with 20 and 30 at one
  place returns `[10, 30, 30, 40]`. A FEniCSx field then has a duplicate id and one node with
  no value; `Field.at` (`results/model.py:20-23`) returns a neighbour's value for a missing id
  without complaint.
- **Fix:** FEniCSx: always write `node_ids` from `dof_nodes(V)` when the space has the mesh's
  degree (already done for threads, `study.py:600-601`) and use positions only as the check.
  In `match_points`, raise (or assign each candidate once) when two reference points lie
  within the tolerance of each other.
- **Test:** two blocks side by side, both fixed, no contact, FEniCSx static: every mesh node
  has exactly one displacement row.

### EDDY-2: a coil carrying zero current crashes the reader
- **Where:** `src/puzzle/adapters/eddy.py:232-235` (`/ current`, `voltage / current`).
- **What is wrong:** for a massive conductor `voltage` and `current` are Python complex, so
  `I = 0` raises `ZeroDivisionError` after the solve has finished; for a winding the numpy
  division gives NaN/inf with a warning.
- **Failure scenario:** two coils, one excited and one at 0 A (an open secondary, a mutual
  inductance set-up): the run fails while reading results.
- **Confidence:** high (Python semantics checked); not run through a solve.
- **Fix:** report NaN resistance and inductance for a coil with no current (or the open-circuit
  voltage as its own field) and skip the division.
- **Test:** unit test of `globals_` with one coil at 0 A.

---

## Low

- **FENICS-3, capability claims not backed by code:** `fenicsx/__init__.py:55` lists
  `custom_pde` for `frequency_domain` and `modal`, while `case.py:155` allows only stationary
  and transient. Now that `planned_physics` exists, move those two there. The module docstring
  (`__init__.py:9`) still says frequency-domain work needs a complex PETSc build; it is solved
  in real blocks.
- **FENICS-4, late refusals inside the solver:** `study.py:1159` (2D feature types), `:704`
  (mixed u-p), `:1894` (magnetic transient scheme) exit with a message after meshing and
  launching. The same lists should be checked in `case.py` so `Adapter.check` and the Problems
  panel show them before a run. `solve_solid` (3D) has no final `else: raise`, so a feature
  type it does not know is dropped; add one.
- **FENICS-5, stress projection cost in dynamics:** `_stress_projector` (`study.py:920-922`)
  LU-factors the 6-component mass matrix (6N x 6N). The six components are uncoupled: factor
  the scalar mass once and solve six right-hand sides, about a sixth of the memory.
- **ELMER-4, linear heat solved twice per step:** `sif.py:569-570` always writes
  `Nonlinear System Max Iterations = 50` with tolerance 1e-10, so a linear conduction step
  runs a second iteration to see no change. Write 1 when the materials have no tables. (Not
  timed; Elmer behaviour from memory.)
- **ELMER-5, fragile file ordering and silent NaN:** `sorted(mesh_dir.glob("case*.vtu"))`
  (`__init__.py:226`) is lexicographic, so more than 9999 outputs would sort `t10000` before
  `t1001` (if Elmer pads to four digits; not checked). `output.py:92` stores NaN frequencies
  when the log holds fewer eigenvalues than modes instead of raising.

---

## To check (not verified; I am unsure of the solver's behaviour or of the path)

1. **Elmer Rayleigh keywords** (`sif.py:229-231`): confirm in `StressSolve.F90` that
   `Rayleigh Damping Alpha` multiplies mass and `Beta` stiffness, with a benchmark where both
   are nonzero and differ in effect.
2. **Shared `max_iterations` on StressSolve** (`sif.py:891-892`): `Solver */Nonlinear System
   Max Iterations` goes on the linear elasticity solver too; check it does not make it solve
   n times.
3. **Free-free modal on FEniCSx** (`study.py:391`, `:407`): shift-invert at sigma = 0 factors a
   singular K. It may pass on round-off pivots; a small negative shift is the usual remedy.
   CLAUDE.md says free modal studies are allowed.
4. **Planar heat and thickness** (`case.py:705-710`, `study.py:1211-1213`): the weak form
   leaves thickness out because "it cancels". It does not across parts of different thickness
   (the flux continuity is `t q`). Compare with CalculiX on a two-thickness strip.
5. **Partial temperature loads:** FEniCSx fills nodes outside the transferred field by
   inverse-distance values (`interpolate_nodal`), Elmer calls `thermal_load.at(all nodes)`
   (`sif.py:183`), which returns neighbours for missing ids. If a transfer can cover only some
   parts, both apply a thermal strain where none was given. Also, `interpolate_nodal`'s
   docstring says "linear"; it is inverse-distance over four nodes.
6. **Custom PDE `neumann` on a vector unknown** (`study.py:1433-1435`): `g * v * ds` with a
   vector `v` is not a scalar integrand; UFL should reject it. Refuse in `case.py` or write
   per-component values.
7. **`hex27` comment** (`meshfiles.py:25-28`): the comment gives a face order (1-2-3-4 first)
   that differs from the reorder list (1-2-6-5 first). The list matches my memory of Elmer's
   `elements.def` and the tests pass; fix the comment.
8. **`support_nodes` normals** (`common.py:1069-1073`): "outward" is taken as away from the
   mesh's node average, which is wrong on a hole's wall or a concave part. Harmless for linear
   springs; check any compression-only use.

---

## What is done well

- Weak forms in `study.py` checked by hand and found correct: 3D elasticity with thermal
  strain; plane stress (reduced lambda, `E/(1-nu)` thermal factor), plane strain and
  axisymmetric (hoop terms, `2 pi r` weights, `sigma_zz`); Newmark/HHT-alpha (beta, gamma,
  load time, effective matrix, consistent initial acceleration, held dofs); harmonic
  `(K + i w C - w^2 M)`; heat theta and BDF2 with the amplitude weights; A-V harmonic real
  blocks (signs of the `omega sigma` coupling and of the V equation), the transient A-V BDF2
  coefficients, the axisymmetric curl form and unit-voltage basis.
- `eddy.globals_`: the half and quarter factors for loss and energy, winding resistance with
  fill, and the flux-linkage inductance are right.
- Elmer node orders (510, 820, 827 as coded, 306, 408, 409) and the boundary file layout are
  right; stresses are read by name, so the `xz`/`zx` naming is safe.
- Refusing rather than ignoring is the norm (`refuse_inputs`, `check_contacts`, the 2D checks,
  amplitudes outside transient steps); the findings above are the exceptions.
- Matrices are assembled once and factored once in structural transients; no Python loops over
  elements in `study.py`.

## Design recommendations

- **`study.py` (1983 lines, one module, closures over `main`):** it cannot import Puzzle, but it
  can be a small package copied whole (`study/` with `linalg.py`, `solid.py`, `heat.py`,
  `magnetics.py`, `custom.py`). The pure-NumPy/SciPy parts (`solve_transient`,
  `solve_harmonic`, `_spring_system`, `_Factor`, `hats`, `_load_vectors`, `interpolate_nodal`)
  take only matrices and can be unit tested in the dev environment without dolfinx; FENICS-2
  would have been caught there.
- **Run the SciPy backend in CI on Linux** (`PUZZLE_FENICSX_BACKEND=scipy`) for one test per
  physics, so the two linear-algebra paths cannot drift.
- **One feature dispatch:** `solve_solid`, `solve_solid_2d` and `solve_solid_mixed` repeat the
  feature loop with different subsets. A table of handlers per feature type, with a final
  refusal, removes the silent fall-through and makes the 2D/3D gaps visible.
- **Validate selections once:** a shared `common.check_selection_kinds(features, mesh)` (face,
  body, edge, point per feature type, from `model/catalog.py`'s `entity`) called by every
  adapter's check would close FENICS-1 for all solvers.
- **Output-time contract:** each adapter should return the times it expects and the reader
  should assert the file count against them (`strict=True` everywhere); ELMER-1 survived
  because the mismatch was tolerated.
- **Map by node id, not position, wherever the solver keeps ids** (FEniCSx `dof_nodes`, Elmer
  `GeometryIds`), with positions as the cross-check.

## Not covered

- The FEniCSx and Elmer `progress.py` parsers were only skimmed.
- Thread, weld, remote-load and imported-field paths in `study.py`/`sif.py` were read but not
  exercised.
- The older one-coil magnetostatic paths and the Elmer MPI partition merge were read only.
- No PETSc-versus-SciPy numerical comparison was run beyond the crash in FENICS-2.
- `common.py` outside what these two adapters call (bolts, fasteners, sections, beams).
