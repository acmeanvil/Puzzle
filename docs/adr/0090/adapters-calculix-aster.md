# Review: CalculiX and Code_Aster adapters, and the shared adapter code

Scope: `src/puzzle/adapters/calculix/` (`inp.py`, `frd.py`, `__init__.py`), `src/puzzle/adapters/code_aster/` (`comm.py`, `tables.py`, `__init__.py`, `expert.py`), `src/puzzle/adapters/common.py`, `base.py`.
Read-only review. Checks that were run are named in each finding; everything else is from reading the code.
Scratch files for the checks are in `<scratch>\review\` (`ccx1\*.inp`, `read_d.py`, `support_normal.py`, `nodes_check.py`).

## Summary

| Severity | Count | IDs |
| --- | --- | --- |
| Critical | 0 | |
| High | 5 | CCX-1, CCX-2, ASTER-1, ASTER-2, COMMON-1 |
| Medium | 6 | CCX-3, CCX-4, CCX-5, CCX-6, CCX-7, COMMON-2 |
| Low | 2 | ASTER-3, COMMON-3 |

One hypothesis was tested and dropped: that CalculiX's `*NODE PRINT, TOTALS=ONLY` reaction totals come out in local axes for nodes under `*TRANSFORM`. A one-element run showed the totals are global with or without `GLOBAL=YES` (only the per-node rows are local), so `reaction_total` is right.

Not covered in depth: `spring_beams.py`, `thread_pairs.py`, `weld_pairs.py`, `code_aster/thread_springs.py`, `graded.py`, both `progress.py` parsers, the beam-bolt and RBE3 writers (`_beam_bolt`, `_thread_end`, `_rbe3_equations`, `_with_bolts`), and Code_Aster's chained pretension steps. No Code_Aster run was made (it runs in a container here), so every ASTER finding is from reading.

---

## High

### CCX-1: the .frd reader silently drops every result block after the 9,899th output increment

- File: `src/puzzle/adapters/calculix/frd.py:41-46`
- Verified: by running CalculiX and Puzzle's reader.

The reader recognises a result block by `line.split()[0] == "100CL"`. CalculiX writes that line as `"  100CL"` followed by a 5-wide counter (100 + the output increment) and a 12-wide value. From the 9,900th output increment the counter has five digits and fuses with the tag:

```
  100CL 9999 9.89900E-02           8 ...
  100CL10000 9.90000E-02           8 ...
```

The first token is then `100CL10000`, no branch matches, and the block is skipped without a message.

Failure scenario: a structural transient with `time_step = 1e-5`, `end_time = 0.1005` (the deck is written with `INC=1000000`, so nothing stops it). I ran a one-element deck of that shape: CalculiX wrote 10,050 blocks; `read_frd` returned 9,899 steps, the last at t = 0.09899. The final 151 time steps were gone, and the "last step" shown is not the end of the run. The same happens to any study whose total number of output increments (all steps together) passes 9,899.

The same token-splitting has a second hazard, not reproduced: a negative 12-character value (`-1.08870E+10`) touches the counter, so `split()[2]` would return the node count as the step value. I could not get CalculiX to print a negative value (a bar in tension gave positive buckling factors), so this is unconfirmed.

Fix: parse the line by columns, not tokens: tag `line[:7].strip()`, counter `line[7:12]`, value `float(line[12:24])`. Do the same for `1PSTEP` and `1PMODE`.

Test: a unit test on a synthetic .frd with headers `  100CL 9999 ...` and `  100CL10000 ...` (and one with a negative value) asserting both blocks are read with the right value.

### CCX-2: beam fasteners take no thermal expansion under a temperature load, silently

- Files: `src/puzzle/adapters/calculix/inp.py:1369` (bolt material), `:341` (initial temperature on `N_ALL` only), `:1078-1082` (`*TEMPERATURE` on the field's nodes only)
- Verified: by reading only.

With a transferred temperature (role `load`) or a `body_temperature`:

- A solid bolt's material is written by `_material_cards(material, comp)` without `expansion`, so it has no `*EXPANSION` unless the component has the `thermal_expansion` coupling.
- The bolt's nodes are created by `bolt_mesh`, are not in `N_ALL`, and get neither the initial temperature nor a `*TEMPERATURE` row, so even with `*EXPANSION` their temperature change is zero.
- A beam bolt (the default formulation) is six springs, which cannot expand at all.

Nothing refuses the combination. `spring_beams.check` does refuse thermal loads for spring beams ("springs don't expand"), so the rule exists but is not applied to fasteners.

Failure scenario: a steel flange joint with pretensioned steel bolts heated uniformly by 100 K. The true change in bolt force is about zero (same material). In the model the plates grow by α ΔT L (24 µm over a 20 mm grip) and the bolt does not, so the bolt force rises by up to several kN for an M10. With dissimilar materials the error has either sign.

Fix: either refuse a temperature load when `comp.connectors` is non-empty, or implement it: for solid bolts write `*EXPANSION`, the initial temperature and `*TEMPERATURE` (the mean of the end faces' temperatures) on the bolt's nodes; for beam bolts add the free thermal length change α ΔT L as an opening on the axial spring (the pretension reference-node mechanism already exists).

Test: two plates clamped by one pretensioned bolt, same material, uniform ΔT: bolt force unchanged within 1 %. Until implemented, a test that the study is refused with code `unsupported`.

### ASTER-1: a temperature load on a model with any helper node crashes inside the .comm (KeyError)

- File: `src/puzzle/adapters/code_aster/comm.py:2515-2519` (`_temperature_field`), with `:805` (bolt material without ALPHA)
- Verified: by reading only (no Code_Aster run).

`_temperature_field` builds the field with `temperature.setValues([float(_given[_key(c)]) for c in _xyz])` where `_xyz` is every node of the mesh Code_Aster read and `_given` holds only the original mesh's nodes. The written mesh also contains helper nodes at new positions: fastener bolt nodes and master points (`_with_bolts`), pretension reference nodes at the section's centroid (`_cut`), thread and weld helpers. Any of them raises `KeyError` in the command file, so the run fails with a Python traceback in the `.mess` instead of a Puzzle refusal. The initial-stress writer next to it uses `.get(key, zeros)` and does not have this problem.

If the lookup were made tolerant, the CCX-2 problem appears here too: `matf{i}` is written by `_material(m, False)` with no `ALPHA`, under `AFFE_VARC=_F(TOUT='OUI', NOM_VARC='TEMP', ...)`.

Failure scenario: thermal stress of a bolted assembly, or of any part with a `bolt_pretension`, on Code_Aster: the job fails at `setValues`.

Fix: decide the behaviour once for both adapters (see CCX-2). At minimum refuse in `write_case` when `thermal_load` is given and there are fasteners, pretension cuts, threads or welds. If supported: give helper nodes a temperature (nearest mesh node or the reference), and write `ALPHA` on bolt materials.

Test: `write_case` with a temperature input and a `bolt_pretension` either raises `InputError(code="unsupported")` or (integration) solves; never a solver-side traceback.

### ASTER-2: plasticity is silently ignored in transient structural steps

- File: `src/puzzle/adapters/code_aster/comm.py:331-335` (`plastic` only for `stationary`), `:975-977` (`DYNA_NON_LINE` behaviour is always `RELATION='ELAS'`), `:791-795` (the traction curve is written for stationary steps only)
- Verified: by reading only.

`Capabilities.material_models` claims plasticity, and `SolverAdapter._material_problems` accepts it for any step that solves solid mechanics. In a `transient` step the material is written without `TRACTION` and `DYNA_NON_LINE` runs `ELAS`. CalculiX writes `*PLASTIC` in the material for every step type, so its `*DYNAMIC` step yields.

Failure scenario: a suddenly loaded bar past yield, run as a shoot-out. CalculiX returns plastic strain and a lower peak stress; Code_Aster returns an elastic answer with no warning, and no `plastic_strain` fields.

Fix: write `VMIS_ISOT_TRAC` (and the traction function) in `DYNA_NON_LINE` too, with `NEWTON=_F(REAC_ITER=1)` and the `VARI_NOEU`/`EPSP_NOEU` outputs, or refuse plastic materials in transient steps until benchmarked (the adapter rule: claim only what a benchmark passes).

Test: `write_case` for a transient step with a plastic material contains `VMIS_ISOT_TRAC`, or raises `unsupported`. Integration: the existing "slow pull past yield" case as a transient, CalculiX against Code_Aster.

### COMMON-1: a compression-only support acts on the wrong side for faces that look toward the model's centre

- File: `src/puzzle/adapters/common.py:1066-1075` (`support_nodes`), used by `calculix/inp.py:_springs` (GAPUNI direction) and `code_aster/comm.py:_discrete_cara` (DIS_CHOC local x)
- Verified: by running `support_nodes` on a small hand-built mesh.

The support's normal is the corner triangle's normal flipped to "point away from the model's centre" (`mesh.coords.mean`). That equals the solid's outward normal only for convex outer faces. For a bore, a slot floor, the inside of a channel or any face whose outward normal points toward the centroid, the returned normal points into the solid. My check: a face at x = +1 belonging to a tetrahedron at x ≥ 1 (outward normal −x) returned `[1, 0, 0]`.

Both adapters use that direction as "into the foundation". With it reversed the gap closes when the face moves away from the foundation: the support holds in tension and lets go in compression. For a hole off the model's centre, part of the bore is right and part is wrong.

Linear `elastic_support` is unaffected (a spring along ±n is the same spring).

Failure scenario: a lug on a pin modelled as a compression-only support on the bore; a C-channel resting on an inner face. The part is either unsupported (singular or drifting until the far side closes) or held by the wrong half of the bore, with plausible-looking stresses.

Fix: take the outward normal from the parent volume element, as `pressure_forces` already does (away from the parent element's centre via `FaceLookup`). For shell faces keep the element normal and document which side the foundation is on.

Test: unit test of `support_nodes` on a block with a through hole: every bore node's normal points toward the hole axis. Integration: a ring pushed sideways onto a compression-only bore support carries the load on the loaded half only (both solvers).

---

## Medium

### CCX-3: fastener forces and exact reaction totals are lost for increments whose time needs more than six digits

- File: `src/puzzle/adapters/calculix/__init__.py:386` and `:477` (`abs(t - step.value) < 1e-9`)
- Verified: the two formats by running CalculiX; the mismatch by arithmetic.

`.dat` blocks are matched to `.frd` steps by time with an absolute tolerance of 1e-9. Below t = 1 the .frd prints time as `%12.5E` (six significant digits: `3.33333E-03`); the .dat prints seven (`0.3333333E-02`). They agree within 1e-9 only when the time is small or round. For t = 0.0333333... the two differ by 3e-8; for a cut-back increment at t = 0.1140625 by 2.5e-7.

Failure scenario: a transient with `time_step = 1/30 s`, or a contact step that CalculiX cuts back to non-round times. Those steps get no `fastener` field and no `reaction_total`. I did not trace what `results.query.exact_total` does without the field (presumably it falls back to summing 6-digit nodal reactions); the bolt table has nothing for those steps.

Fix: match by order, not by value: within a deck step the k-th printed block belongs to the k-th output increment. If time is kept as a check, use a relative tolerance of 1e-5.

Test: `read_totals`/`read_fastener_forces` against a recorded pair of .frd/.dat with dt = 1/30; every step has `reaction_total`.

### CCX-4: a harmonic step given as a range does not solve the frequencies asked for

- File: `src/puzzle/adapters/calculix/inp.py:1147-1157` (`_steady_state`)
- Verified: by reading only.

Only a `frequencies` list is split into one `*STEADY STATE DYNAMICS` step per frequency. With `start_frequency`/`end_frequency`/`points`/`spacing`, the data line is `min, max, points, 1.`, where CalculiX reads `points` as the number of data points per interval between consecutive eigenfrequencies, with bias 1. The result steps are CalculiX's own frequencies, their count depends on how many modes fall in the range, and `spacing = "log"` is ignored without a refusal. Code_Aster, Elmer and FEniCSx solve exactly `frequency_list(step)`.

Failure scenario: 10 to 1000 Hz, 20 points, log spacing, shoot-out: CalculiX returns a different number of steps at different frequencies, so the comparison by step does not line up.

Fix: always write one step per `frequency_list(step)` value (the code path exists and modal superposition steps are cheap). If CalculiX's clustering near resonances is wanted, make it an explicit CalculiX setting.

Test: writer test: a range step yields `points` steady-state steps whose bounds equal `frequency_list`.

### CCX-5: solver choice, tolerances and expert cards are silently dropped on three procedures

- File: `src/puzzle/adapters/calculix/inp.py:424` (`PROCEDURES`), `:446`, `:478`
- Verified: by reading only.

`with_settings` finds the procedure card by `PROCEDURES = ("*STATIC", "*FREQUENCY", "*BUCKLE", "*DYNAMIC", "*HEAT TRANSFER")`. `*STEADY STATE DYNAMICS`, `*MODAL DYNAMIC` and `*COUPLED TEMPERATURE-DISPLACEMENT` are not in it, so `at` is `None` and both the `SOLVER=` parameter and every collected card (`*CONTROLS` for `tolerance` and `max_iterations`, the time-incrementation settings, `after_procedure` pass-through) are discarded. ADR 0023's rule is "placed or refused, never dropped".

Failure scenario: a coupled thermal-structural step with `tolerance = 1e-8` and `linear_solver = iterative`, or an expert `after_procedure` card on a harmonic study: the deck contains none of it and nothing says so.

Fix: add the three procedures to `PROCEDURES`; if `at` is still `None` and there is anything to place, raise `InputError`.

Test: `with_settings` on a harmonic and a coupled step with an `after_procedure` card: the card is in the output.

### CCX-6: material directions are dropped for 2D regions and shells

- File: `src/puzzle/adapters/calculix/inp.py:711-723`
- Verified: by reading only.

`*ORIENTATION` is written only when `group.dim == 3`. For an orthotropic or anisotropic material on a 2D region or a sheet body:

- with no `assignment.axes`, the material's own `axis1`/`axis2` are ignored and the constants are applied in global axes, silently;
- with `assignment.axes`, the error raised says the material "has no directions for its axes to set", which is untrue.

Code_Aster refuses material directions outside 3D (`_material_axes`), so the two adapters disagree.

Failure scenario: a plane-stress orthotropic plate with `axis1 = (0, 1, 0)`: CalculiX solves it with the stiff direction along x.

Fix: write `*ORIENTATION` for 2D regions (CalculiX takes it on plane elements) and benchmark it, or refuse as Code_Aster does; correct the message.

Test: writer test for a 2D orthotropic assignment: deck has `ORIENTATION=` or the study is refused.

### CCX-7: set names can collide, merging two selections

- File: `src/puzzle/adapters/calculix/inp.py:131-135` (`set_name`), also used by `post/exports/abaqus.py`
- Verified: by reading only.

`set_name` upper-cases and turns every run of non-alphanumerics into `_`. Distinct groups can map to one name: `Plate/Top face` and `Plate/top_face`; part `A B` selection `C` and part `A` selection `B C`; a part named `All` gives `N_ALL`. CalculiX appends a repeated `*NSET`/`*ELSET` to the existing set, so a `fixed`, `displacement` or `temperature` on one selection is applied to the union, and a material's `*SOLID SECTION` to both element sets. Nothing checks for it. Code_Aster's `safe_names` (G1, G2, ...) cannot collide.

Fix: build the name map once per deck and refuse (or suffix) duplicates; refuse names over CalculiX's 80 characters at the same place.

Test: a mesh with groups `P/Top face` and `P/top_face`: `write_inp` yields two distinct set names or raises.

### COMMON-2: the plastic strain tensor is wrong with thermal strain and NaN outside plastic bodies

- File: `src/puzzle/adapters/common.py:501-528` (`plastic_strain_tensor`), `calculix/__init__.py:246-254`, `calculix/inp.py` output request `S, E`
- Verified: by reading only.

CalculiX's `E` (TOSTRAIN) is the total strain, thermal strain included. `plastic_strain = E − C⁻¹σ` therefore contains α ΔT on the diagonal whenever a temperature load acts on a plastic material: a non-zero "plastic strain" where PEEQ is zero. Separately, `young` and `poisson` start as NaN and are filled only for nodes of plastic materials, so in a model with one plastic and one elastic material every node of the elastic body gets NaN components (Code_Aster's EPSP is 0 there).

Fix: request `ME` (mechanical strain, block `MESTRAIN`) for the tensor when a thermal load is present, or subtract α ΔT; restrict the field to the plastic materials' nodes or write zeros elsewhere.

Test: unit test of `plastic_strain_tensor` with two materials (no NaN); integration: a heated, unloaded plastic block has zero plastic strain.

---

## Low

### ASTER-3: results are mapped by "node number = node id" with no guard

- File: `src/puzzle/adapters/code_aster/tables.py:76`, `comm.py:1360-1364` (`_sorted_mesh`)
- Verified: partly. I ran Gmsh for tet4, tet10, order changes, subdivided hex8, hex20 serendipity: tags came out sorted and contiguous from 1 each time.

`int(r["NOEUD"])` is taken as the Puzzle node id. That holds only if the written mesh's node ids are exactly 1..N in order. It is true for the paths I tried, but `Mesh` only promises "sorted, unique", and nothing asserts contiguity before writing. A gap (a future mesher path, an imported mesh, a dropped node) would shift every result onto the wrong node silently.

Fix: assert `node_ids == arange(1, N + 1)` in `write_case`, or write a position-to-id map beside the job and apply it in `read_results`.

Test: `write_case` on a mesh with a gap in its ids raises (or the reader maps correctly).

### COMMON-3: `FaceLookup` picks an arbitrary parent for a face shared by two volume elements

- File: `src/puzzle/adapters/common.py:287-296`
- Verified: by reading only.

The lookup is keyed by the face's corner nodes; for an interior face (two bodies sharing nodes) the last element written wins. A pressure, heat flux, film or contact surface on such a face lands on whichever body was iterated last, which also decides the outward direction in `pressure_forces`.

Fix: keep both parents and choose the one in the selection's part (the group name's prefix); refuse when ambiguous.

Test: two tets sharing a face, a surface block on it in group `A/Face`: the parent returned is A's element.

---

## To check (not confirmed)

- **Negative values in `100CL`** (see CCX-1): would return the node count as the step value. Fixed by the same column parsing.
- **Code_Aster 3D pressure faces are not reoriented.** `ORIE_PEAU` is applied to contact faces and 2D pressure edges only (`comm.py:590-594`). A 3D `PRES_REP` relies on Gmsh's face orientation being outward. I believe `AFFE_CHAR_MECA`'s default `VERI_NORM='OUI'` makes a wrong orientation fail loudly rather than flip the sign, but did not run it. Adding the 3D pressure groups to `ORIE_PEAU` would remove the dependence.
- **Thermal load is not ramped in Code_Aster's nonlinear statics.** `AFFE_VARC` applies the full temperature from the first increment while mechanical loads follow `ramp`; CalculiX ramps `*TEMPERATURE` over a static step. With plasticity the paths differ.
- **Plastic branch replaces `COMPORTEMENT`** (`comm.py:1085-1092`): `parts = [...]` discards the `GROT_GDEP` entries for shells and beams and the `PETIT` entry for point elements built just above, when plasticity and large deflection are combined.
- **`*TIE` has no `POSITION TOLERANCE`** and CalculiX's "no tied MPC" warnings are not surfaced: slave nodes farther than the default tolerance stay untied silently. `contact_check` warns "far apart" before the run; a post-run check of the log would close the loop.
- **NLGEOM persists** in CalculiX once switched on: a later step without `geometric_nonlinearity` still runs nonlinear. Writing `NLGEOM=NO` on such steps would make the setting per step.
- **`displacement` with no component** writes `_F(GROUP_MA='g', )` in Code_Aster (a syntax error at run time) rather than a refusal.
- **Output parity:** CalculiX stores strain and heat flux; Code_Aster stores neither (no `EPSI_NOEU`, no `FLUX_NOEU`). Derived results such as equivalent strain exist on one solver only.

## Done well

- Element node reorders (tet10, hex20, wedge15) and CalculiX face numbering in `FACES` are correct against the solver's definitions (checked by hand).
- Face shape functions and quadrature (tri6, quad8, quad9, tet10) are correct; loads are integrated on curved geometry and totals are preserved.
- The 20-character field limit is respected (13 significant digits, exponent always written for spring stiffness).
- Code_Aster's Rayleigh name swap, `PARM_THETA = 1`, the enthalpy table and the `1PE18.10` table format are handled deliberately.
- Stress and strain components are mapped by name on Code_Aster (SIXZ/SIYZ order differs from Puzzle's) and by documented order on CalculiX.
- Refusals are generally specific and name the alternative solver.
- `OP=NEW` resets per card and helper holds per step are handled correctly.

## Design recommendations

- **One manifest per job** instead of marker files (`.meshnodes`, `.buckling`, `.totals.json`, `.fasteners.json`, `.elastic.json`, `job.chain.json`, the `** puzzle step` tags): a single JSON written by `write_input` and read by `read_results`, including the node map and the list of output increments.
- **Match solver output by index, not by formatted floats** (CCX-3), and parse fixed-width files by column (CCX-1). Stream the .frd instead of `read_text().splitlines()` for large transients.
- **Split `write_case`** (about 1,050 lines in one function, `comm.py:220-1264`) into model, materials, loads and solve builders; the plastic/large-deflection behaviour list (to-check item) is the kind of interaction that hides in it.
- **Replace `id(feature)` keys and module-level `ContextVar`s** in `inp.py` (`GIVEN`, `STEP_INDEX`, `HISTORIES`, `COMPONENT`, `SPRING_BEAMS`; `_rigid_nodes`, `_pretension_nodes` keyed by `id(f)`). They work today because `globalize` does not copy the feature types concerned, but any new `model_copy` breaks the lookup silently. An explicit writer-state object passed down would be testable.
- **A combination matrix test.** Most findings are pairs of features that are each benchmarked alone (thermal load × fasteners, plasticity × transient, orthotropy × 2D, settings × harmonic). A parametrised writer-level test over feature pairs asserting "written or refused, never neither" would catch this class cheaply without solver runs.
- **Share the "what can't expand" rule**: thermal loads against springs, bolts, welds and threads should be decided in one place (`common`), used by both adapters.
