# Puzzle design review: conformance with the ADRs

Reviewed 2026-11-16 on `development` at da26cde. Read-only: nothing in the repository was changed.

## How far this review went

The ADRs total about 1 MB, so the work was split into eight slices. Four slices were checked in depth; the other four were only sampled, because the reviewers assigned to them stopped on a spend limit before reporting.

| Slice | Depth | What that means |
| --- | --- | --- |
| 0027-0043 | Deep | Decision, Built and open sections read; code, tests, skills, MCP and CLI checked |
| 0044-0056 | Deep | Same |
| 0057-0063 | Deep | Same, plus a reproduction of ADR-1 on a copy of the cantilever example |
| 0076-0080 | Deep | Same; no tests run |
| 0001-0026 | Sampled | 0004, 0007, 0008, 0011, 0015, 0021, 0023 spot-checked; 0018, 0020, 0025, 0026 only for open items |
| 0064-0066 | Sampled | Open lists against 0072, the error estimate's element scope, the "never eval" claim |
| 0067-0075 | Sampled | LSF, Lima, 0075's status, existence of named tests and tools |
| 0081-0085 | Sampled | OpenCourant's pins, skills and help lists, open lists |

No solver benchmark was run. Every "within x %" figure below was checked only as "the test exists, covers the stated solvers, is not skipped unconditionally, and asserts this tolerance". GUI behaviour was not exercised. The shareable design doc (the Claude Doc) was not compared with the ADRs.

Quick runs that did pass: `tests/test_bolts.py`, `test_vdi2230.py`, `test_progress.py` (67 passed); `test_preload.py`, `test_advisor.py`, `test_contact_search.py`, `test_amplitudes.py`, `test_beam_bolt.py` (27 passed); `test_scripts.py` (10 passed on Windows, sandbox tests included).

## Counts

| Severity | Count | Meaning |
| --- | --- | --- |
| High | 7 | A user would be misled or could get a wrong answer |
| Medium | 25 | A promised feature is absent or partly there |
| Low | 11 | Stale wording, index drift, loose tolerances |

Paths are relative to `c:\Nate\SRC\Git_Repos\Puzzle\`.

---

## (a) Promises not kept or partly kept

### High

**ADR-1. An agent below "act" can write a custom PDE's weak form without a proposal.**
- ADR 0062 phase 4: "Below 'act', an agent's command writing a script or an equation becomes a proposal."
- Evidence: `src/puzzle/commands/session.py:587-608` (`_writes_code`). For an `Edit` it looks for "equation" or a trailing "code" in each op's path, never in the value.
- Reproduced on a trusted copy of `examples/cantilever` at `act_in_budget`, actor agent: `Edit(append, path="physics", value={type: custom_pde, equation: ...})` was applied; `Edit(set, path="physics.0", value={...})` was applied; only `set physics.0.equation.weak_form` was proposed.
- Consequence: the agent can then run the study, which evaluates the weak form in the FEniCSx environment (an `eval` with no sandbox).
- Probably the same hole, not tested: `CopyComponent`, and `restore_version` to a version holding code (allowed at `act_in_budget`, `session.py:431`).
- Fix: decide from the result, not the path. Compare `trust.holds_code` and the code text before and after the command or batch, and propose when anything new appears. Add the three edits to `tests/test_scripts.py::test_agents_below_act_propose_code`, which today tests only a `Create` of a script operator.

**ADR-2. The weak-spring warning is measured against the whole model's reactions, not the body's load.**
- ADR 0080 decision 5: "more than `Study.weak_spring_limit` (default 0.1 %) of the body's applied load or reaction".
- Evidence: `src/puzzle/weak_springs.py:131-134` builds `supports` from every held node of the component; `:183-187` computes `share = carried / (carried + held)`.
- Consequence: a small unseated body in a heavily loaded assembly is diluted below the limit (10 N on springs against 100 kN of reactions is 1e-4), so `weak_springs_carry` is not raised.
- Also: `carried` is the norm of the vector sum over the three nodes, so springs resisting a couple cancel; a self-equilibrated model (net reaction near zero) gives a share near 1 and a false warning.
- Fix: compare per body against that body's applied load or contact force, using per-node spring force norms. Otherwise amend decision 5 to say "of the model's load path" and name the blind spots.

**ADR-3. A weld with its own filler is checked by AISC 360 J2 whatever the base metal.**
- ADR 0078 decision 5: base metals outside the covered groups are "refused with the reason".
- Evidence: `src/puzzle/weld_strength.py:74-80`. With `weld.filler` set the function returns before the group check at `:87-92`; the code label defaults to "AISC 360-16 J2".
- Consequence: a titanium, copper or ungrouped base metal with an explicit filler gets a J2 utilisation. Mixed base metals take 370 if any face is stainless, with no note (the ADR says "take the weaker's rules and say so").
- Fix: refuse groups outside carbon/low-alloy steel, stainless and aluminium even when a filler is given; emit the mixed-metal note.

**ADR-4. A PJP groove weld gets the fillet-only directional increase and the instantaneous-centre method.**
- ADR 0078 step 2 cites Eq. J2-5 and J2.4(b), which apply to fillet welds.
- Evidence: `weld_strength.check` (`:96-120`) refuses only CJP, then applies `directional_factor` whatever `weld.type` is; `post/weld_fields.py:171-184` calls `_ic` for the same weld.
- Consequence: a transversely loaded PJP with `directional=true` is credited up to 1.5 times its strength under the clause "J2.4, Eq. J2-5". A stainless weld's `clause` still reads "AISC 360-16 J2.4" while `code` says 370.
- Fix: gate `directional` and `_ic` on `type == "fillet"`; make the clause follow the code.

**ADR-5. Weld fatigue applies AISC 360 Appendix 3's steel curves to any base metal.**
- ADR 0078 decision 6 offers AWS D1.2 for aluminium; 0072 section 3b lists the AWS codes as still to build.
- Evidence: `post/weld_fields.fatigue` (`:316-372`) never reads the weld's materials. An aluminium weld returns `allowable_range_Pa` with the clause "AISC 360-16 Appendix 3".
- Fix: refuse non-steel base metals in the AISC method, or return `checked: False` with the reason.

**ADR-6. Torsion on stainless and aluminium members is silently not checked; the ADR says refused.**
- ADR 0079 step 5: "torsion (G8, G9), channels, tees and angles are refused."
- Evidence: `frames/aisc370.py:381-383` and `frames/adm.py:454-456` return `None`; `frames/design.py:256-259` then adds no torsion check and no note, and the H3-6 interaction is skipped. Steel I shapes follow the same path (`aisc360.torsion` returns `None`).
- Consequence: a stainless or aluminium HSS under torsion passes with torsion ignored; the same steel HSS is checked.
- Fix: when |T| is nonzero and the code has no torsion check, raise `NotCovered` or attach a note to the member's result.

**ADR-7. The solver shoot-out, Puzzle's headline feature, cannot be run from the GUI, the CLI or MCP.**
- ADR 0011: "A study may carry a `shootout` ... The report shows pass or fail per metric and plots the difference field"; fields are compared "node by node when element types match, and by mapping otherwise".
- Evidence: `shootout.run_shootout` (`src/puzzle/shootout.py:73`) has no caller in `src/`; only `tests/integration/test_cfd_shootout.py` and `test_eddy_currents.py` import it. The CLI, the menus and MCP have `mesh-shootout` only (Gmsh against Netgen). `compare` (`:116-142`) intersects ids and has no mapping and no difference field.
- 0072 knows (section 7, line 425) but schedules it in phase 4 of 6, and ADR 0011's status is a bare "Accepted". No skill or help guide tells a user how to run one, because there is no way.
- Fix: a `RunShootout` command behind `puzzle run --shootout`, a study's Run shoot-out and MCP; a stored comparison report; then the difference field. Move it up 0072's order, since it is what separates Puzzle from every other pre-processor.

### Medium

**ADR-8. On macOS and Linux a script is denied the project folder only when the caller passes the root, and most callers do not.**
- ADR 0062: "On macOS it runs under `sandbox-exec`, which denies reading or writing the project's folder"; on Linux the folder is "an empty tmpfs".
- Evidence: `operators/scripts.py:152-170` adds the deny only `if project_root is not None`. `Context.root` defaults to None (`evaluate.py:257`). Contexts built without a root: `operators/__init__.py:1759,1793` (`parameter_values`, the path a pre-solve script takes into a solve), `optimise.py:249`, `convergence.py:137`, `adapt.py:139`, `post/derived.py:505`, `schematic/__init__.py:593`, `agent/mcp.py:2050,2065`, `cli.py:1603`.
- `tests/test_scripts.py:72-78` calls `scripts.run(..., project_root=tmp_path)` directly, so it never exercises those paths. Windows is unaffected (the AppContainer denies everything not granted). Read from code only; this machine is Windows.
- Fix: give every `Context` the root, and refuse to run a script on macOS or Linux without one.

**ADR-9. Linux without bubblewrap runs scripts unsandboxed and tells nobody.**
- Evidence: `scripts.kind()` returns "none" and `_sandbox` returns the plain argv (`scripts.py:171`). Nothing outside `scripts.py` and the tests reads `kind()`.
- 0072 line 446 lists "ship bwrap"; the silence is not listed.
- Fix: show the sandbox kind in the script's Properties, `puzzle scripts list` and `operator_value`; raise a warning Problem when it is "none".

**ADR-10. MCP `optimisation_run` solves outside the session: no budget, no history entry, no pre-run version.**
- ADR 0063 item 10: "agents need 'act' to run it ... as for runs". ADR 0015: "No client has a private path to the model."
- Evidence: `agent/mcp.py:1975-1990` calls `optimise.run(...)` directly. `Session.budget_used` counts only `run_study` history entries (`session.py:550-566`).
- Consequence: an agent at "act within the budget" can solve up to `max_solves` points without spending a run, a solver minute or a core-hour.
- `mesh_convergence` and `mesh_adapt` (`mcp.py:1852, 1912`) look the same shape; not checked.
- Fix: route all three through a session command that logs solves and duration and checks the budget against the planned fresh points, as sweeps do at `session.py:631`.

**ADR-11. Pressure following an amplitude is claimed on four solvers with no benchmark.**
- ADR 0041: "structural force and pressure may follow an amplitude in transient steps".
- Evidence: `amplitude_features=frozenset({"heat_flux", "force", "pressure"})` in the calculix, code_aster, elmer and fenicsx adapters, three commented "benchmarked". The only implicit structural amplitude benchmark, `test_dynamics.py::test_ramped_load_follows_its_amplitude`, uses a force. Each adapter has its own pressure path (Elmer's `sif.py:663`).
- Fix: add a ramped-pressure case to `test_dynamics.py` over the four solvers, or drop "pressure" until one exists.

**ADR-12. The `dynamics` skill tells agents FEniCSx dynamics is "3D only".**
- Evidence: `src/puzzle/agent/skills/dynamics.md:67,70`. `tests/integration/test_planar_dynamics.py:55` runs the sudden-load and both harmonic tests on FEniCSx in 2D.
- Fix: remove "(3D only)" from both rows.

**ADR-13. Code_Aster's 3D threaded contact is claimed with no benchmark in the repository.**
- ADR 0076 step 3: "3D bonded 40.06 %, 0.9800 kN/µm, 23056 N on both".
- Evidence: the only 3D threaded-contact test is `@over("calculix")` (`test_thread_benchmark.py:318`); the Code_Aster comparison at `:390` is the 2D axisymmetric model. Code_Aster's `THREADS` is not limited to 2D, whereas FEniCSx refuses 3D.
- Fix: add the 3D Code_Aster case (about six minutes), or refuse 3D there until it exists.

**ADR-14. Thread acceptance gates named in ADR 0076 are not asserted, and the index claims lead torque.**
- ADR 0076 step 2: shank stress within 1 %, boss hoop stress within 10 %, VDI 2230 thread compliance within 10 %; step 3: friction torque within 5 %.
- Evidence: `test_thread_benchmark.py:212-216` asserts shares and stiffness only. There is no torque output in `threads.py`, `thread_pairs.py` or `post/thread_fields.py`, and friction is refused on all three solvers. The README row says "per-thread load, wedge and lead torque".
- 0072 section 3a lists friction and 3D on Elmer and FEniCSx, not the gates or the torque.
- Fix: add the asserts, or put the items on 0072 and drop "lead torque" from the row.

**ADR-15. The `thread_axis` and `thread_mesh` problems are not built.**
- ADR 0076 step 1 names both. `errors.py` has only `thread_radii`; `threads.minimum_pitch_nodes` (`threads.py:390`) has no caller in `src/`.
- Fix: raise or warn from `thread_pairs`, or strike both from step 1.

**ADR-16. A threaded contact's results have no GUI.**
- ADR 0076 decision 5: "the Contact tool's Results page and a bolt's row in the bolt table show them".
- Evidence: no `thread` in `gui/contact_tool.py`; no `thread_load`, `thread_wedge` or `thread_results` anywhere in `gui/`. Only beam bolts have the "First thread" column.
- Fix: a Threads page in the Contact tool, or an item on 0072's forms list.

**ADR-17. `Capabilities.weak_springs` does not exist, and `always` is not refused on Elmer or FEniCSx.**
- ADR 0080 decision 4: "The `Capabilities.weak_springs` flag says so ... `always` is refused on them until a benchmark passes."
- Evidence: no such field in `adapters/base.py`. The gate is `"elastic_support" not in caps.features` (`weak_springs.py:122`), which Elmer and FEniCSx both pass. Their writers take a face integral, so a point group is likely written as nothing. Inferred from the writers, not run.
- Fix: add the flag, true on CalculiX and Code_Aster only, and gate on it.

**ADR-18. Weak-spring reporting is short of ADR 0080 decisions 5 and 7.**
- `weak_springs_carry` appears only as a progress message; it is not in `advisor.py`.
- MCP `weak_springs` promises "any weak_springs_carry warning" (`agent/mcp.py:1631`) but returns only `carried_N`; the nodes and stiffnesses before a solve are not returned.
- Fix: return warnings and nodes from MCP, surface the code in the Advisor, amend the decisions to what is built.

**ADR-19. `Weld.sides` is stored and never read.**
- ADR 0078 decision 1: "one side or both (`sides`: arrow, other, both)".
- Evidence: `model/schema.py:1602`; no other `.sides` reference in `src/puzzle`. A "both" weld is modelled and checked as one fillet, so its stiffness and line force are wrong and nothing says so.
- Fix: refuse values other than `arrow` until built, or remove the field.

**ADR-20. Modelled weld beads are MCP-only, and a modelled weld with no bead is not refused.**
- Evidence: `weld_generator.modelled_commands`/`line_commands` have no caller in `gui/`; the Welds panel has Add weld… and Weld fatigue… only (`gui/weld_table.py:59-61`). Properties lets a user set `formulation = modelled` with no bead drawn, and `adapters/base.py:366-373` does not refuse it. The downstream effect was not traced.
- Fix: a "Model the bead / Back to a light weld" menu item as bolts have, and a refusal for a modelled fillet weld without a bead part.

**ADR-21. Member checks on a first-order run carry no warning.**
- ADR 0079 step 3 "Not yet": B1 amplification; "Use the Direct Analysis Method".
- Evidence: `frames/design.py` never reads `second_order` or `direct_analysis`. Ratios from a first-order run with K = 1 come back as plain AISC results.
- Fix: a note on every member's result when the step was neither second order nor direct analysis.

**ADR-22. MCP `render` cannot make the pictures ADR 0038 promises to agents.**
- ADR 0038 decision 4: "Everything has a command-line and MCP twin ... so agents and scripts make the same pictures."
- Evidence: `agent/mcp.py:2915-2937` takes `study, field, scalar, step, mode` only. The CLI has `--cut`, `--range`, `--animate` (`cli.py:313-331`). Not on 0072.
- Fix: add `cut`, `range` and `scale` to `render`, or amend decision 4 and list the gap.

**ADR-23. MCP `export` has no `units` option, though ADR 0043 and the skill say it does.**
- Evidence: `agent/mcp.py:1444-1454`; `skills/exports.md:36,45` list `units`. The wall cloud's reference temperature and the morph tolerance are Python-only.
- Fix: add `units` and the two extras to the tool.

**ADR-24. Error estimates and adaptivity cover tetrahedra only, while hexahedra and quadrilaterals are now a main path.**
- ADR 0066: "Linear and quadratic tets; other element types are left out with a note."
- Evidence: `post/estimates.py:194-197`. 2D models mesh all-quadrilateral by default (CLAUDE.md, ADR 0048), and hexahedral recipes got four rounds of work in November, so `error-estimate`, `adapt` and the Advisor's Results step estimate nothing on the meshes Puzzle now makes by default in 2D.
- On 0072 (line 407, "error estimates beyond tets"). Listed here because the gap has grown since the ADR was written.
- Fix: ZZ recovery for tri3/tri6, quad4/quad8, hex8/hex20; until then the Advisor should say "no estimate on this mesh" in plain words.

**ADR-25. Decision items that are unbuilt and missing from 0072.** Each needs either an item on 0072 or an amended Decision.

| ADR | Promised | Evidence |
| --- | --- | --- |
| 0044 d.3 | "inverse distance or radial basis for scattered points" | `schema.py:1017`: linear, nearest, inverse_distance, conservative |
| 0044 stage 3 | Shell thickness and graded materials from a field on Elmer and FEniCSx | 0072 line 415 lists initial stress only |
| 0047 d.8 | FEniCSx element order 1 or 2 as a solver setting | `fenicsx/__init__.py:108-119` has `linear_method` only |
| 0049 d.3 | Settings > View > Coordinate systems (always, when used, never) | No such preference in `settings.py`; `gui/visibility.py` does it per user |
| 0055 | Members clipped by section cuts | 0072 line 214 has the other three items |
| 0058 | A chip clickable on its own | Not on 0072 |
| 0062 | Filter preview in the 3D view; ASME checks as a Check; renaming an operator renames history arguments; Settings > Scripts | Not on 0072; no "script" in `gui/settings_dialog.py` |
| 0078 | Plug, slot and edge welds; staggered patterns; `Component.weld_rules`; AWS symbols; MCP `weld_types`; `Material.filler`; J4 base-metal check; Table J2.3/J2.4 size warnings; PJP throat by Table J2.1; ADM groove welds | 0072 3b lists AWS fatigue codes, the duty, Elmer and 2D only |
| 0079 | Member diagrams in the view; partial-fixity release springs; releases, offsets, line loads and node springs on CalculiX and Code_Aster ("so every frame result is cross-checked"); point and trapezoidal member loads; MCP `seismic_spectrum`; a GUI or CLI for `seismic_report` | No "diagram" in `gui/` or `post/`; `base.py:412` still refuses |
| 0080 d.7 | Springs drawn as glyphs; the global `weak_springs` field | Neither exists |
| 0077 d.5 | FEniCSx "only bonded heads with threads" | Unbuilt, unlisted |

**ADR-26. ADR 0050 never records the fastener tables or VDI 2230, which CLAUDE.md attributes to it.**
- Evidence: `grep -i vdi docs/adr/0050-bolt-manager.md` is empty; the code exists (`vdi2230.py`, 765 lines; `fasteners/`; MCP `bolt_sizes`, `fastener_tables`, `bolt_vdi2230`; `gui/vdi2230_form.py`; `tests/test_vdi2230.py`).
- Fix: amend 0050's Built section with the scope (concentric single bolt, which equations), tools and tests. No new ADR is needed. Also correct M3-M36 to M64 and "30 entries" to 42.

**ADR-27. ADR 0077's bracket-assembly benchmark has no test and missed its own gate.**
- The ADR asks for "bolt forces within 1 %, the thread's shares within 5 points"; measured -1.2 % and -0.7 %, no thread shares, and no test holds it (8089 s). `test_solid_bolts.py:180,207` asserts preload to 2 % against 1 % stated.
- Fix: state the measured values as the result and the gate as missed, or add a coarse variant that runs.

### Low

**ADR-28. Benchmark tolerances are much looser than the figures the ADRs and help pages quote.** A regression of 1-5 % would pass while the documents still quote the tight numbers.

| ADR | Quoted | Asserted |
| --- | --- | --- |
| 0056 Hertz peak pressure | 0.7 % | `test_hertz.py:139` rel=0.05 |
| 0056 press fit | -0.32 %, -0.20 % | `test_press_fit.py:115` rel=0.03, hoop 0.05 |
| 0055 Code_Aster GENERALE | 0.01 % | `test_beam_sections.py:152` rel=1e-2 |
| 0041 transient, phase | 1.33 %, 0.1° | `test_dynamics.py:90-91,156` 4 %, 2° |
| 0037 remote moment | +0.04 %, 0.00 % | `test_remote_loads.py:122,152` 3 %, 2 % |
| 0027 beam-bolt stretch | 0.01 % | `test_fasteners.py:138` 2 % |
| 0076 wedge | "four digits" | `test_thread_benchmark.py:250,263` 10-15 % |

Fix: continue 0072's second round (tolerances at two to three times the measurement), or word the ADRs as "measured x; the test allows y".

**ADR-29. Open items in ADR bodies that later work closed.**
- 0019 "Not yet: sweeping, and a recipe mode that meshes block-like bodies with hexahedra and the rest with tetrahedra" (built 2026-11-13/14 per 0072).
- 0021 "Reattaching to a queued job after a GUI restart" (built by 0067).
- 0027 "Not yet": the MCP twin of fasteners from holes, the proof check, I/channel/angle sections, real-section display; "refuses pretension in longer studies"; PIPE and BOX on B31/B32 (B32R only).
- 0035 body-load symbols (`symbols.py:172`); 0038 selection reductions over steps.
- 0076 decision 4's table (DIS_CHOC "its own friction", `ufl.conditional`); 0077 decisions 2 and 6 (`formulation="solid"`, `bolt_make_solid`); 0078 step 1's 0.412 utilisation beside the later 0.284.
- Fix: one "since built (ADR n)" note per line.

**ADR-30. ADR 0061's import checks are built but four branches have no test.** `tests/test_archive.py:82-110` covers hash, `../`, a newer format and a missing member; not a link member, an absolute or drive path, the total size or the ratio. The ratio check applies only above 100 MB (`project/archive.py:309`), which the ADR does not say. `_safe("")` would raise IndexError (`:275`).

**ADR-31. ADR 0055's `orient="edge"` has no solve test.** `ORIENTS` in `test_beam_sections.py:208-212` covers vector, axes and toward; `normal` only through the GUI pick test.

**ADR-32. ADR 0047's TEAM 7 benchmark is skipped under 24 GB,** so no CI machine runs it (`test_team7.py:46-50`). Say so on the verification page or add a coarse variant.

---

## (b) Contradictions and stale statuses

**ADR-33 (low). README index rows that no longer match the ADR or the code.**

| ADR | Row says | Actual |
| --- | --- | --- |
| 0048 | "round 1 built" | Rounds 2-5, 2D dynamics, magnetics and CFD built |
| 0049 | "orthotropic on Code_Aster ... open" | Built (`code_aster/__init__.py:112`, `test_orthotropic.py:79`) |
| 0053 | "physical cores by default" | Automatic: 4, or one fewer (`machine.default_cores`) |
| 0054 | "phase 1 built (CalculiX, Code_Aster)" | Seven `adapters/*/progress.py` parsers; CLAUDE.md is right |
| 0057 | "29 skills" | 41 skill files; 40 ADRs in `WITH_SKILLS` |
| 0063 | "Bayesian (optuna)" | No optuna anywhere; `surrogates.fit_gaussian`. ADR lines 54, 135, 174 also say optuna |
| 0067 | "Slurm and PBS" | LSF is wired but untested (ADR line 115); CLAUDE.md lists it without the caveat |
| 0076 | "lead torque" | Not built (ADR-14) |
| 0080 | "a lift-off" benchmark | "Not done" in the ADR and on 0072 line 651 |
| 0023 | "all six solvers" | Eight solvers now; PyNite and OpenCourant settings not checked |
| 0011, 0025, 0026, 0028-0039, 0042, 0046, 0050, 0055 | bare "Accepted" | Each has a Built section |

**ADR-34 (low). Foundational ADRs overtaken without an amendment note.**
- 0004 chooses "build123d for parametric construction" and "FreeCAD as an optional plugin". Neither is used: the only `build123d` in `src/` is a docstring (`geometry/__init__.py:3`), and nothing mentions FreeCAD.
- 0007 says "The default runtime is a container image per solver"; 0013 and the code are native first, with `CONTAINER_FIRST` only on Windows for three solvers.
- 0010 and 0012 name a five-solver line-up; there are eight (OpenFOAM by 0016, PyNite by 0079, OpenCourant by 0085). 0012's row is not marked amended.
- 0015 lists an MCP exposure setting of "off; local only; network, with authentication". No such preference exists in `settings.py` or `agent/settings.py`; the network mode is on 0072 section 9, "off" is not.
- Fix: an "Amended by" line on each.

**ADR-35 (low). CLAUDE.md against the ADRs.**
- The Workflow section says "then ADRs 0075 and 0083", but 0075 is superseded by 0083 (the file and the index both say so). What is outstanding is 0083's tutorials: about 46 worked examples are planned and the repository holds two examples and two benchmarks.
- CLAUDE.md says a version restore is for "agents only at 'act'"; `session.py:431` allows `act_in_budget`; ADR 0059 says "refused below 'act'". Pick one (see ADR-1 for why it matters).
- CLAUDE.md names ADR 0050 as the source of `fasteners/` and `vdi2230.py`; the ADR does not mention them (ADR-26).

---

## (c) Capability claims without a benchmark

"Adapters only claim a capability once a benchmark for it passes." Sampled against `src/puzzle/adapters/*/__init__.py` and `tests/integration/`.

**ADR-36 (medium). Code_Saturne claims `mass_flow_inlet` and refuses it at write time.**
- Evidence: `code_saturne/__init__.py` has `features=FLUID_FEATURES | THERMAL_FEATURES`, and `FLUID_FEATURES` includes `mass_flow_inlet` (`base.py:47-49`). `code_saturne/case.py:98-101` refuses any feature not in `NATURES`, which lacks it. OpenFOAM subtracts it correctly.
- Consequence: `capabilities` (GUI menus, MCP, the agent's solver choice) offers it; the run then fails. No integration test uses it.
- Fix: subtract it as OpenFOAM does until it is written and benchmarked.

**ADR-37 (medium). The `joule_heating` and `non_isothermal_flow` couplings are accepted and never read.**
- Evidence: Elmer and FEniCSx list `couplings={"thermal_expansion", "joule_heating"}`, the CFD adapters `{"non_isothermal_flow"}`. `base.py:379-383` only checks membership. Outside the adapters' `__init__.py`, `comp.couplings` is read only by CalculiX for `thermal_expansion` (`calculix/inp.py:792, 983`). `electric_currents` is planned physics, so Joule heating cannot exist yet. No integration test names either coupling.
- Consequence: a component with `couplings=["joule_heating"]` passes the check on Elmer and nothing is written. That breaks the rule that an adapter refuses what it does not write.
- Fix: remove the two from the matrices until written, or move them to a `planned` set as was done for the planned physics.

**ADR-38 (low). FEniCSx claims `custom_pde` for `frequency_domain` and `modal`; the writer takes stationary and transient only** (`fenicsx/case.py:155`; `test_custom_pde.py` covers those two). Trim the matrix.

Also in this category, reported above: pressure amplitudes on four solvers (ADR-11), Code_Aster 3D threads (ADR-13), weak springs on Elmer and FEniCSx (ADR-17).

Claims sampled and found backed: body loads, remote loads, elastic and compression-only supports, pretension, cylindrical displacements, `Feature.steps`, heat-flux and force amplitudes (ADR 0037's matrix); beams, shells and connectors on CalculiX and Code_Aster with refusal tests elsewhere; buckling; transient and harmonic dynamics on four solvers; orthotropic on CalculiX and Code_Aster; stage 3 fields on CalculiX and Code_Aster only; Code_Saturne transient (`test_cfd_transient.py`).

---

## (d) Reachable by the agent and by a person

**ADR-39 (medium). Features with no agent skill.** `WITH_SKILLS` is a hand-kept list, so the test proves only that listed ADRs have a skill.
- CFD: no skill mentions `velocity_inlet` or `turbulence_model`. An agent setting up a Code_Saturne or OpenFOAM flow has the help guide `analyses.md` only.
- Custom PDEs: `custom_pde` and `weak_form` appear in no skill and no help guide, though ADR 0015 lists "FEniCSx custom PDEs" as work the agent is for.
- Shells (0027): no skill explains `sheet`, `shell_sections`, the pressure sign or the order rules.
- Section cuts (0038), the Advisor (0040), construction geometry (0046), running several studies (0073), cores and processes (0053): no skill, and for 0073, 0038 and 0053 no MCP tool either.
- Heat transfer and electrostatics have no skill of their own; not checked whether `materials` and `structural-loads` cover them well enough.
- Fix: skills for `cfd`, `custom-pde` and `shells`; a rule in the test that every ADR whose index row says built is in `WITH_SKILLS` or in an explicit `EXEMPT` set with a reason.

**ADR-40 (medium). GUI or CLI paths with no MCP tool.**
- The solver shoot-out (no path at all, ADR-7); Run all (0073, CLI and GUI only); Open in ParaView (0070, CLI and GUI); section cuts and animations (ADR-22); per-user preferences such as cores per run (no MCP tool sets a preference; perhaps intended, but no ADR says so); applying a settings preset (0072 section 9); opening an archive (0061; reasonable, since the server is bound to one project, but unstated).

**ADR-41 (low). MCP-only features with no GUI form.**
- Threaded-contact results (ADR-16), modelled weld beads (ADR-20), `seismic_report`, weak springs' nodes and forces.
- The ten forms 0072 planned on 2026-11-15 are all built (`gui/weld_forms.py`, `vdi2230_form.py`, `sweep_form.py`, `selection_rule.py`, `mesh_shootout.py`, and the rest), each with a test comparing its batch with the MCP tool's. Not run here.

**ADR-42 (low). Help coverage.** All 57 ADRs in `WITH_HELP` are named by a page. 0065 has a page but is missing from the list; 0053 and 0073 are in neither list; 0009, 0010, 0012 and 0016 have pages but are not listed.

---

## (e) ADR 0072's open list

- **Listed but now done:** none found in the slices checked in depth. The four slices only sampled were not checked for this.
- **Done and correctly recorded:** the ten forms; the fix-soon list of 2026-11-13 and both rounds of 2026-11-15.
- **Missing from the list:** everything in ADR-25's table, the thread gates and lead torque (ADR-14), the sandbox's silence (ADR-9), `render`'s gaps (ADR-22), `export`'s units (ADR-23), and the capability overclaims in section (c). The last group belongs under "Found while collecting (fix soon)".
- **Order:** the shoot-out's reach (ADR-7) sits in phase 4. It is the product's defining feature and deserves phase 1.
- **Phase 2 is partly open:** section 9's MCP items (`point` and `axes` on `result_*`, a time or mode within a step, presets by command, entity names, edge seeds) are still listed and still absent from `mcp.py`'s `result_*` signatures.

---

## (f) Test-suite health: what CI never runs

CI (`.github/workflows/ci.yml`) installs the pixi environments `dev`, `calculix` and `fenicsx` on Linux and Windows (both Macs on `main`), with no Docker images built. `tests/solvers.py` skips a test whose solver is absent. So:

| Solver or gate | Integration test files that use it | Runs on CI? |
| --- | --- | --- |
| CalculiX | 81 | Yes |
| FEniCSx | 39 | Linux with PETSc; Windows on the SciPy backend, `needs_petsc` tests skipped |
| Code_Aster | 57 | No: the `code-aster` env exists for linux-64 and win-64 but CI does not install it |
| Elmer | 36 | No: Docker image only |
| Code_Saturne, OpenFOAM | 9, 8 | No |
| PyNite | 6 | No: a small pure-Python pixi env that CI does not install |
| OpenCourant | 1 file (`test_explicit.py`) | No |
| `PUZZLE_RENDER_TESTS`, `_BUILD_TESTS`, `_MACHINE_TESTS`, `_PROVIDER_TESTS`, `_LIVE_AGENT_TESTS` | 7, 3, 3, 3, 3 references | No |
| TEAM 7 | 1 | No: needs 24 GB |

Thirteen integration files use no CI solver at all: all CFD (`test_channel_flow`, `test_heated_channel`, `test_turbulent_channel`, `test_cfd_2d`, `test_cfd_shootout`, `test_cfd_transient`, `test_exports_fluid_runs`), all solved frames (`test_frame_analysis`, `test_frame_grid_solve`, `test_member_checks`, `test_seismic_frames`), `test_machine_solve` and `test_warm_containers`.

Claims in CLAUDE.md that therefore rest on the maintainer's machine alone:
- Every Code_Aster figure: LE1 +0.01 %, contact and friction, pretension chaining, plasticity, orthotropy, shells and beams, welds and threads "to four digits", cyclic symmetry, large deflection.
- Every Elmer figure: "agrees with CalculiX to 4 digits", LE1 0.13 %, magnetodynamics, TEAM 7, the planar and axisymmetric eddy-current set.
- All CFD: channel -0.23 %, Nu 8.235, the turbulent channels, the transient front.
- All frame analysis and member design solved on PyNite ("exact" P-Delta, DAM, seismic).
- All explicit dynamics.
- "All four FE solvers" statements (transient heat 0.346 K, body loads, remote loads, 2D dynamics): CI verifies two of the four.
- The script sandbox on macOS and Linux, the Netgen mesher, MMG, the PARDISO build, the WSL machine, every render.

**ADR-43 (medium). Recommendations.**
1. Add `pynite` and `code-aster` to CI's `environments` on Linux; both are pixi envs, and Code_Aster is in 57 test files.
2. A weekly or by-hand job that builds `containers/elmer` and the CFD images (cached) and runs their tests; pull OpenCourant's pinned image for `test_explicit.py`.
3. Print the skip summary (`-rs`) on the full check and fail when the skipped count rises above a recorded baseline, so a solver silently going missing is noticed.
4. Record on each verification page where its benchmark last ran (machine, date, commit), since the pages quote numbers CI cannot reproduce.

---

## ADRs not checked or only sampled

- Only sampled (their reviewers did not report): 0001-0026, 0064, 0064A, 0065, 0066, 0067-0071, 0073, 0074, 0081-0085. Within these: 0064A's 106 rows, 0065's operations, 0068's installer steps, 0069, 0074, 0081's per-solver planes, 0082's "written" stars, 0084's wires and key handling were not verified beyond the existence of the named files. OpenCourant's SHA-256 pin and image digest are present (`opencourant/native.py:33-100`, `jobs/runtime.py:36`).
- Not checkable here: macOS and Linux sandbox behaviour, signed installers, real Slurm or LSF, live provider checks (0084 leaves these to the maintainer), 3D mouse hardware, timing tables in 0051 and 0053.
- 0052 and 0060 make no claims to verify; nothing in `src/` claims a GPU solver or MYSTRAN.
- This list describes the first pass. The second pass below replaces it: see "ADRs not checked after the second pass" at the end.

---

# Second pass

The four slices only sampled in the first pass, checked again. Slices 0064-0066 and 0067-0075 were read in depth by helper reviewers (code and tests read; nothing run beyond a scratch probe of the polars allow-list; all runner tests skip on Windows). The helpers for 0001-0026 and 0081-0085 stopped on the spend limit a second time, so the lead reviewer checked those two slices directly. Numbering continues from ADR-43.

## Slice 0064-0066

**ADR-44 (high). MCP `mesh_convergence`, `mesh_adapt` and `optimisation_run` all solve outside the session. Confirms and widens ADR-10.**
- ADR 0066 decision 6: "MCP `mesh_convergence`, `mesh_adapt` (at "act", as runs are)".
- Evidence: `src/puzzle/agent/mcp.py:1861, 1924, 1983` check only `s.project.agent.level in ("act", "act_in_budget")`, then call `adapt.run`, `conv.run`, `optimise.run` directly (`:1868, :1929, :1988`). Nothing passes through `Session.submit` and `_run_command` (`commands/session.py:150-171`).
- Skipped: `_run_timeout`'s run, solver-second and core-hour budget checks (`session.py:610-637`); the `history.jsonl` entry, so `budget_used()` never charges these solves; the `before_run` version (ADR 0059).
- Consequence: an agent with no budget left can run twelve adaptive passes or any number of convergence levels.
- Fix: route each level, pass or point through the session, or have the tools call the budget check and log a `run_study`-shaped entry with `solved` and `duration_s`.

**ADR-45 (medium). Adaptive passes on a 2D mesh cannot work, though ADR 0066 says they were fixed.**
- ADR 0066: "Also fixed: ... adaptive passes on 2D meshes and on a named field (ADR 0072)".
- Evidence: `adapt.py:205` calls `estimates.of_run` every pass outside any `try`; `estimates.py:193-199` takes only 3D tet4/tet10 blocks and otherwise raises "the error estimate needs tetrahedra"; `size_field.from_estimate` (`size_field.py:66`) and `mmg._tets` (`mmg.py:65-69`) are tets-only. No test in `tests/test_adapt.py` uses a 2D mesh. Not reproduced by running.
- Effect: on a 2D model, pass 1 is solved and stored, then `adapt.run` raises.
- Fix: refuse a 2D or hexahedral study before solving and say so in the ADR, or build tri3/tri6 (see ADR-24).

**ADR-46 (medium). `--delete-passes` deletes every result when a pass fails or is stopped.**
- ADR 0066 decision 4: "The final pass is the study's result ... an option deletes them when the run finishes".
- Evidence: `adapt.py:160-161` appends the pass before solving; a failure (`:190-193`) or cancel (`:180-183`) leaves that empty pass last; `adapt.py:216-219` then deletes `passes[:-1]`, which is every pass that ran. `tests/test_adapt.py:124` covers only the budget ending. Not reproduced by running.
- Fix: delete all but the last pass that has a run.

**ADR-47 (medium). Adapt passes and convergence levels show as "Out of date".**
- Evidence: passes 2+ are keyed from the refined recipe (`adapt.py:171`, `:79`), levels 2+ from the scaled recipe (`convergence.py:177`). `status.run_state` (`project/status.py:150-160`) calls a run up to date only if its key is among the study's current keys. With `keep_passes=False`, pass 1 is deleted, so the study reads out of date straight after a converged adapt, `mesh_adapt(solve=False)` and `puzzle adapt --points` report "0 of up to N passes run", and a re-run solves everything again.
- Fix: have status recognise adapt and level keys, and have the read-only paths fall back to `results/adapt/<id>.json`.

**ADR-48 (medium). Faceted booleans are GUI-only, not a history step, and write a project file outside the session.**
- ADR 0065 decision 1: "Every new operation is a history step ... reachable from ... CLI's and MCP's history editing"; decision 6: a B-rep with a faceted part "tessellates the B-rep first".
- Evidence: the only caller of `meshfile.boolean` is `gui/menus.py:1656-1698`, which writes `imported/<name>.stl` itself and then submits a Create. No MCP tool or CLI command; `agent/skills/geometry-operations.md:53` tells the agent to use the GUI. Only faceted with faceted is offered (`menus.py:1668`). Not on 0072.
- Fix: a command with MCP and CLI twins (or a history step); put the B-rep case on 0072.

**ADR-49 (medium). No help guide names ADR 0065.**
- Evidence: `tests/test_help.py:19-78` lacks 0065; `help/pages/guides/geometry.md:7` has `adrs: 0014, 0017, 0042, 0046` and says nothing on patch, thicken, deform, SubD, the study morph, faceted booleans or Measure's least radius, topology and sharp edges. (Corrects ADR-42, which said a page already named it.)
- Fix: add the section, the `adrs` entry and the `WITH_HELP` entry.

**ADR-50 (medium). The mesh morph checks quality on tetrahedra only.**
- ADR 0065: morphing with "quality refused".
- Evidence: `morph._shape` (`mesh/morph.py:62-80`) skips every other element, so a hexahedral, wedge or shell mesh is morphed with no inversion or quality check, silently.
- Also unbuilt and not on 0072: mirror in a construction plane or of a named body (`sweeps.py:183-216`); morph handles as construction points (`morph.py:96-102`); the optimised shape out as a morphed STEP.
- Fix: refuse or check non-tet meshes in `morphed`; list or strike the rest.

**ADR-51 (low to medium). The polars expression allow-list stops code execution but not unbounded work, and leaks raw exceptions.**
- ADR 0064: "built by walking the `ast` against an allow-list ... so it needs no trust gate".
- No way to run code was found (`frames.py:646-672`). But constants are folded by Python operators with no size limit (`frames.py:628-629`): `2**(10**6)` was computed in a probe, so `9**9**9` or `"a"*10**11` in a Filter rows condition of a received project would hang or exhaust memory. `1/0`, `-"a"` and unknown columns raise `ZeroDivisionError`, `TypeError` and polars' `ColumnNotFoundError`, not a coded `PuzzleError`.
- Fix: cap exponents and repeat sizes; allow methods only on `pl.Expr`; wrap build and evaluation into `PuzzleError("invalid")`.

**ADR-52 (low). Smaller items in 0064, 0064A and 0066.**
- `points_table` has no test (`frames.py:252`, `evaluate.py:322-327`); the other 37 operators ADR 0064 lists exist and are tested, bar the contact and bolt tables 0072 already lists.
- 0064A rows accepted wider than built and not on 0072: D8 (points on a face, radial, random: `geometry_data.py:130-160` does a rectangular grid on a plane or in a box), F4 (edges longer than L, convex edges: `naming.py:376-386` has neither), B4 "done" (only pi and e, `expression.py:7`), C6 "done" (cross and longest-list only, `evaluate.py:1318-1327`). The other 17 of 21 sampled rows check out.
- 0066 wording: a "time" budget that `Adapt` lacks (`schema.py:2176-2198`); size tables by "ids on an earlier mesh" (needs x, y, z, size); the size-field control stores only the table operator's id (`schema.py:621`), so a table fed by a run or file does not stale the recipe. The last is not on 0072.
- 0072:406-407 still lists the plate-with-a-hole and L-corner benchmarks as open; 0072:641-644 and the tests say done. This is the one "listed but done" item found.

## Slice 0067-0075

**ADR-53 (medium). On Windows the job runner is off for remote hosts too.**
- ADR 0067 "Not done yet": "Windows runs solves in-process: the runner needs Unix sockets and process groups."
- Evidence: `jobs/service.py:27-33` `enabled()` returns False whenever `os.name != "posix"`, before `service_for` looks at the runtime, though `RemoteService` talks through a plain ssh subprocess (`runner/client.py:139`).
- Effect: a Windows user's Slurm or PBS run uses the old ADR 0021 shell driver: no resume, no manifest-checked fetch, nothing in the Jobs panel. The README row says "built" with no Windows caveat; 0072 line 114 describes the gap as local only.
- Fix: let `enabled()` pass for `RemoteRuntime` on Windows, or say "POSIX clients only" in the ADR and the row.

**ADR-54 (medium). A failed or cancelled remote job whose fetch fails is orphaned on the host.**
- ADR 0067: "A failed or partial fetch leaves the remote case alone, to be fetched again."
- Evidence: `jobs/runner.py:245-251` wraps `service.finish` in `except Exception: if ok: raise`, then runs `pending.remove(ctx.root, job)`. For a job that did not succeed, a fetch error is swallowed and the pending entry removed, so the failed run's logs never arrive and the job cannot be fetched again or dismissed.
- Fix: keep the pending entry whenever `finish` raises.

**ADR-55 (medium). "Queue for cores and memory" does not hold for memory.**
- ADR 0067 Built: "They queue for the runner's cores ... and memory"; ADR 0073 repeats it.
- Evidence: `LocalService` starts the daemon with cores and no memory (`service.py:101-102, 112-113`), and `daemon.py:174-175` skips the memory test at 0. Remote without a scheduler sends `memory_gb: r.memory_gb or h.memory_gb` (`service.py:197`), so each job claims the whole host and a host with `memory_gb` set runs one job at a time. `tests/test_runner.py:93` tests cores only. The queue also uses `continue`, not `break` (`daemon.py:176`), so a small later job can pass an earlier large one indefinitely.
- Fix: default a job's memory to unknown, pass local memory, add a test; or amend to "cores; memory only where each run states it".

**ADR-56 (medium). The WSL machine is not reached through the job runner, and its copy-back is unchecked.**
- ADR 0068 decision 4: "Puzzle reaches it through the job runner exactly as a remote host ... the case comes home checked against its manifest."
- Evidence: `jobs/machine.py:231-236` is a direct `wsl -d ... --exec`. With staging on, `finish` (`:253-258`) runs `cp -a ... && rm -rf` and on failure only says "Couldn't fetch every file back": no error, no check.
- Fix: amend decision 4, and make a failed copy-back fail the run.

**ADR-57 (medium). Licences (ADR 0068 decision 9) are unbuilt while the workflow already attaches installers to releases.**
- ADR 0068: "The installer collects them into `licenses/`, Help > Licences lists every component ... the release notes carry the GPL's source offer."
- Evidence: `scripts/make_installer.py:332-344` writes only Puzzle's `LICENSE.txt`; no Licences item anywhere in `gui/`; `installers.yml:196-201` uploads installers to a release. 0072 line 149's "Built" note names only side-by-side releases and the launcher as still to do, dropping licences and the release-feed check.
- Fix: build it before the first public release (a GPL obligation for the bundled solvers), and correct 0072's note.

**ADR-58 (medium). The Linux machine's memory preference is absent and not on 0072.**
- ADR 0068: "A preference (Settings > Solvers, 'Memory for Puzzle's Linux environment')".
- Evidence: the only machine preference in `settings.py` is `solvers.wsl_stage` (line 493); nothing in `machine.py` sets memory.
- Fix: list it, or say WSL's own default applies.

**ADR-59 (medium). The tree and schematic parity test sees only id-typed references, and a third of its exemptions are dialogs.**
- ADR 0071 decision 8: "Every reference between objects is a wire that can be drawn"; "A test lists ... every reference field in the schema, and refuses one the schematic can't create or wire."
- Evidence: `tests/test_schematic_parity.py:150` collects a field only `if a is uuid.UUID`, so references held by name (a selection on a feature, contact, weld or section; `Quantity.parameter`) are outside it. `WIRED_ELSEWHERE` (`:116-136`) exempts 20 paths, at least eight of them forms. The "everything the tree can add" test (`:178-198`) compares `menus.build_menu` with itself (`gui/schematic_view.py:1221-1222`).
- Fix: amend decision 8 to "wired, or set in the listed forms"; extend the test to name-typed references.

**ADR-60 (medium). Step 5's operators are not driven through MCP in tests.**
- ADR 0071 decision 6: "tests drive each new operator through MCP as well as the GUI".
- Evidence: across `tests/`, `operator_add` is called over MCP only for `check`, `model_value`, `table` and `utilisation`; no test calls `operator_export` or `recipe_insert`. Report and the five VTK filters are tested through `evaluate` directly (`tests/test_series.py`).
- Fix: MCP round-trips for a field operator, a VTK filter, Report export and a recipe insert.

**ADR-61 (low). Smaller items in 0067-0074.**
- 0067: the manifest skips symlinks and `solver.log` (`runner/jobs.py:113,123`); `kill_at` is lost on a runner restart (`backends.py:107-110`). Verified kept: socket mode 0600, host folder 700, protocol checked in `hello`, delete only for owned cases, job ids sanitised.
- 0068: decision 3 "nothing downloaded at first launch" against 0072:142-143 "fetched, not shipped"; README row "steps 1-4" though step 5's machine, Repair and staging are built; step 4 still says Repair "is not built yet".
- 0071: decision 1's help card and drag-time socket message are tooltips and an after-drop refusal; decision 4's Threshold, Plot over line, Integrate variables and Calculator are aliases (recorded in 0070, not 0071); 0071's skill duty is unenforced because the test takes one ADR per skill.
- 0069: "Moving the device stops an animation or a running pick" and the response-curve setting are not in `gui/spacemouse/controller.py` or the `mouse3d.*` preferences; the Enterprise's key names are not on 0072; no help page mentions the 3D mouse.
- 0074: status "Accepted" with no Built section.
- Help: no guide names 0068, 0069, 0070 or 0074, and no guide or start page mentions PARDISO, Repair or the Linux machine.

## Slice 0001-0026 (lead reviewer; targeted checks, not a full read)

**ADR-62 (high). The fatigue tool applies Shigley's steel endurance estimate to any material.**
- ADR 0026 decision 3: "Honesty about scope ... the tool says so where it would matter."
- Evidence: `materials/fatigue.py:73-100` `endurance_limit(..., steel: bool = True, ...)` refuses a non-steel with "Eq. 6-8 estimates S'e for steels only: enter a measured endurance limit". `post/fatigue_tool.py:125-136` calls it without `steel=`, so the refusal can never fire. `fatigue_fraction` (`fatigue.py:176-183`) uses the same 0.5 Sut.
- Consequence: an aluminium, titanium or copper part with the life model "Shigley" gets S'e = 0.5 Sut and "infinite life" below it. Aluminium has no endurance limit, so the result is unconservative with no warning. The Neuber notch warning for non-steels exists (`fatigue_tool.py:315-317`); the endurance one does not.
- Fix: pass `steel=` from the material's group (as `design.code_for` and `weld_strength.filler` already read it), so a non-steel needs a measured endurance limit or its own S-N curve. Add a test with the generic aluminium.

**ADR-63 (low). ADR 0023's default-reproduction test was not found.**
- ADR 0023 Consequences: "A test checks that the schema's defaults reproduce today's input files byte for byte."
- Evidence: no test under `tests/` matches "byte for byte" or compares a default deck with a stored one; `tests/test_solver_settings.py:87` asserts only that two keywords are absent from a default CalculiX deck. Other adapters were not found to have an equivalent.
- Fix: a per-adapter test that an empty `solver_settings` block writes the same input as no block, or amend the sentence.

Checked and kept in this slice:
- 0008: `runs` rows carry `solver_version` and `image_digest` (`results/store.py:69-70`, `solve.py:459-472`); the store compacts itself (`store.py:425`).
- 0015: autonomy is set by users only (`session.py:268, 575`); unbudgeted "act" needs a second confirmation recorded in history (`session.py:740-760`).
- 0018 and 0025: results on different meshes are mapped (`post/compare.py:71-73`, `post/derived.py:265-284`).
- 0020: `registry.needs`, `needs_for`, `completeness`, `missing` exist as described.
- 0021: Apptainer on hosts is written (`jobs/remote.py:224-226`).
- 0026: the non-proportional warning and the Neuber range warning exist.

Not read in this slice: 0002, 0003, 0005, 0006, 0009, 0013, 0014, 0016, 0017, 0019, 0022, 0024 beyond their open-item lines. The first-pass findings for this slice stand (ADR-7, ADR-33, ADR-34).

## Slice 0081-0085 (lead reviewer; targeted checks, not a full read)

**ADR-64 (medium). OpenCourant claims features that no solved benchmark exercises.**
- Rule: "Adapters only claim a capability once a benchmark for it passes."
- Evidence: `adapters/opencourant/__init__.py:27-56` claims the features `displacement`, `gravity` and `acceleration`, the contact type `frictional`, and amplitudes on `displacement`, `force`, `pressure`, `gravity`, `acceleration` and `velocity`. `tests/integration/test_explicit.py` uses only `fixed`, `velocity`, `initial_velocity`, `rigid_wall`, `force` and `pressure` as features, and `frictionless`, bonded and self contacts; "frictional", "gravity" and a prescribed displacement do not appear in it. Gravity is covered by a deck-writing unit test only (`tests/test_opencourant.py:128`).
- Not established: which features carry an amplitude in the three tests that set one (`test_explicit.py:230, 260, 326`).
- Fix: a falling-body case for gravity, a prescribed-displacement pull, and a sliding block with friction (closed forms all), or trim the claims.

**ADR-65 (low to medium). The element library's "written" stars are tested for two solvers of eight.**
- CLAUDE.md: "`tests/test_elements.py` keeps every ★ true of its adapter".
- Evidence: the test file has `test_calculix_rows_marked_written_are_what_its_writer_names` and the Code_Aster equivalent (`tests/test_elements.py:52, 66`), and a check that chosen elements appear in three benchmark files (`:44-49`). Nothing checks the stars in `elements/elmer.py`, `fenicsx.py`, `pynite.py` or `opencourant.py`.
- Fix: the same check per remaining adapter, or narrow the sentence.

**ADR-66 (low). ADR 0085's refusals are not on 0072.**
- 0072 line 180-183 lists only "beam forces as results" and Help as left.
- Also refused in `adapters/opencourant/deck.py`: pressure on second-order faces (`:449, :480`), which rules out a pressure on the tet10 meshes the adapter otherwise takes; shell offsets (`:247`); a shell thickness from a field (`:250`); second-order beams; threads (`:504`); any contact type beyond the five listed (`:540`).
- Fix: list them under section 3.

Checked and kept in this slice:
- 0081: `Capabilities.cyclic` is solid and heat on CalculiX, solid on Code_Aster, absent elsewhere; the benchmark file's solver lists match the ADR (three planes and antisymmetry on four solvers, the ring sector on three, cyclic and periodic on two); FEniCSx refuses oblique planes by name (`symmetry.py:11-13, 32`).
- 0082: `docs/elements.md` has a staleness test; `test_chosen_elements_have_their_benchmark` exists.
- 0083 and 0084, 0085: all five ADRs are in `WITH_HELP` (`tests/test_help.py:72-76`).
- 0084: keys travel in request headers only (`agent/http.py:82-88`), never in a URL; no MCP tool reads or sets one.
- 0085: the after-run checks have unit tests (`tests/test_opencourant.py:197-214`); the native release is SHA-256 checked before unpacking (`opencourant/native.py:76-100`).

Not read in this slice: 0083's site and PDF build, 0084's four wire formats and capability probe, 0082's large-deflection and brick claims, 0081's expanded display, 0085's MPI and Apptainer paths.

## ADRs not checked after the second pass

- **Checked in depth (code and tests read against the Decision and Built text):** 0027-0043, 0044-0056, 0057-0063, 0064-0066, 0067-0071, 0073-0075, 0076-0080.
- **Checked by targeted claims only:** 0004, 0007, 0008, 0011, 0015, 0018, 0020, 0021, 0023, 0025, 0026, 0081, 0082, 0084, 0085.
- **Still not checked beyond their open-item lines or index rows:** 0002, 0003, 0005, 0006, 0009, 0010, 0012, 0013, 0014, 0016, 0017, 0019, 0022, 0024, 0083.
- **Not checkable on this machine:** macOS and Linux sandbox behaviour; the job runner at run time (its tests skip on Windows); signed installers; real Slurm or LSF; live provider checks; 3D mouse hardware; any solver benchmark's measured value.
- 0001, 0052 and 0060 make no claims to verify.

## Counts after the second pass

| Severity | First pass | Second pass | Total |
| --- | --- | --- | --- |
| High | 7 | 2 | 9 |
| Medium | 25 | 15 | 40 |
| Low | 11 | 6 | 17 |

ADR-51 and ADR-65 are counted as low; ADR-44 widens ADR-10 and raises it to high.
