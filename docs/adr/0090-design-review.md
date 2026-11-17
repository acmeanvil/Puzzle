# 0090. Design review of Puzzle to date: findings, fixes and next steps

- Status: Proposed (review complete 2026-11-16; the fixes are not started)
- Date: 2026-11-16

## Context

ADRs were frozen after 0085 so the code could settle for a full review. The maintainer's
guidance asked for: a review of CLAUDE.md, the ADRs and the skills; a complete code
review (adapters accurate for each solver, GUI free of bugs, function in line with the
ADRs, a backend sound for speed and data integrity); a usability review of the GUI; a
review of the schematic and its operators; a view on the agent interface and whether
Looking Glass fits; then one concise report with fixes in a form separate Opus models
can take, and enhancements toward the state of the art. Numbers 0086–0089 are unused.

**How it was done.** Eleven reviewers (Claude Fable 5.2), each on one area, read-only,
with a scratch copy of the examples to run things on. Four were sent back for a second
pass on files they had not reached. Their full reports are in `docs/adr/0090/`; this
record is the summary and the work list. Every finding there has an ID, a severity,
`file:line`, how to reproduce it, a fix and a test.

| Report (`docs/adr/0090/`) | Area | IDs |
| --- | --- | --- |
| `adapters-calculix-aster.md` | CalculiX, Code_Aster, shared adapter code | CCX, ASTER, COMMON |
| `adapters-elmer-fenicsx.md` | Elmer, FEniCSx, eddy currents | ELMER, FENICS, EDDY, COMMON |
| `adapters-cfd-explicit-frames.md` | OpenFOAM, Code_Saturne, OpenCourant, PyNite | OF, CS, OC, PYN |
| `backend.md` | project store, commands, results, solve, jobs | BE |
| `gui-core-bugs.md` | window, tree, Properties, view, Geometry tab | GUI |
| `gui-panels-bugs.md` | dialogs and docked panels | PANEL |
| `gui-usability.md` | usability, from about 150 screenshots | UX |
| `schematic-operators.md` | schematic and operators | OP, SCH, SCH-UX, OP-NEW |
| `agent-looking-glass.md` | agent interface, Looking Glass | AGENT, UXA |
| `adr-conformance.md`, `adr-early.md` | each ADR's promises against the code | ADR, ADR-E |
| `state-of-the-art.md` | gaps against commercial and open FEM systems | SOTA |

Two reports use `COMMON-1`: in this record `COMMON-1` is the CalculiX/Code_Aster one
(`support_nodes`) and `COMMON-1e` the Elmer/FEniCSx one (`match_points`).

**How far to trust it.**

- The nine critical findings: two were re-run by the compiler of this record (BE-1,
  OP-1); three were checked against the cited code, their measured effect resting on the
  reviewer's run (ELMER-1, FENICS-1, OF-1); GUI-19 was read in the code; PANEL-1, GUI-20
  and GUI-21 rest on the reviewer's reproduction alone.
- Each report says, per finding, whether it was reproduced or only read. Treat "read
  only" findings as leads: reproduce first, then fix.
- Solver behaviour stated from a reviewer's memory is marked so in its report (among
  them OF-1's proposed fix and PYN-2's AISC clause). The state-of-the-art list is from
  the reviewer's own knowledge of those products, not a search.
- Not covered: `form.py`, `feedback.py`, `app.py`, `results_ribbon.py`, `visibility.py`,
  `theme.py`, `ribbon.py`, `spacemouse/`, parts of `window.py`, `menus.py`, `sketcher.py`
  and `mesh_bench.py`; the job runner at run time, WSL, PSI/J and the Windows sandbox;
  macOS and Linux behaviour; hands-on mouse and keyboard use; dark and high-contrast
  themes; Looking Glass was read, not run (it is macOS-only). Only the cantilever
  example was driven through most GUI checks.

## Findings

Counts as the reviewers rated them (usability and improvement items apart):

| Area | Critical | High | Medium | Low |
| --- | --- | --- | --- | --- |
| CalculiX, Code_Aster, shared | 0 | 9 | 11 | 3 |
| Elmer, FEniCSx, eddy | 2 | 3 | 3 | 5 |
| OpenFOAM, Code_Saturne, OpenCourant, PyNite | 1 | 3 | 7 | 6 |
| Backend | 1 | 5 | 12 | 4 |
| GUI core | 3 | 11 | 12 | 4 |
| GUI dialogs and panels | 1 | 5 | 15 | 13 |
| Schematic and operators (bugs) | 1 | 5 | 9 | 13 |
| Agent (bugs) | 0 | 1 | 6 | 6 |
| ADR conformance (85 ADRs) | 0 | 12 | 50 | 29 |

Besides these: 38 usability findings (8 high), 15 agent-interface items, 32 schematic
improvements and 50 state-of-the-art gaps.

**What held.** The reviewers checked by hand and found correct: the elasticity forms,
Newmark/HHT, harmonic response and heat schemes in FEniCSx; the A–V blocks and `eddy`'s
loss, energy and impedance factors; CalculiX's and Elmer's node orders, face numbering
and quadrature; the spring-beam equations, the thread and weld writers and the RBE3
equations; the autonomy levels, key handling and provider wires. ADR 0017 holds with no
finding. The command layer, the refusals and the benchmark habit are the project's
strengths; most findings are places where one of those three was not applied.

### The nine critical findings

| ID | What goes wrong | Where |
| --- | --- | --- |
| BE-1 | A geometry file's contents are in no hash: replace `bar.step` and old results stay "up to date" and are reused | `project/graph.py` |
| GUI-20 | Import geometry overwrites a same-named file an existing geometry reads; not a command, no undo (with BE-1: silently stale results) | `gui/menus.py:1183` |
| GUI-19 | Project → New… into a folder holding a project replaces it with an empty one, no question, no version | `gui/window.py:1284` |
| GUI-21 | A group moved into its own child is written; the tree then hangs on every open | `gui/tree.py:422`, the move command |
| ELMER-1 | With `output_every = k`, Elmer's transient results carry the wrong times and the second half is dropped | `elmer/sif.py:329`, `elmer/output.py:60` |
| FENICS-1 | A boundary feature on an edge or point selection is silently ignored on FEniCSx | `fenicsx/case.py:190`, `study.py` |
| OF-1 | OpenFOAM: a wall heat flux in turbulent flow delivers 2–3 times the flux asked for | `openfoam/case.py:320` |
| PANEL-1 | Linearization: switching "Stresses in" re-reads the allowable in the new unit (138 MPa became 138 ksi) | `gui/linearization_panel.py` |
| OP-1 | A before-solving Formula that raises (`10.0**400`) makes the project unopenable in the GUI and CLI | `operators/evaluate.py`, `parameter_values` |

### The patterns behind the findings

1. **Two definitions of what a run depends on.** `run_study` keys a run from the project
   after parameters, suppression and resolution; status, the tree, MCP and the schematic
   hash the stored project (BE-2, OP-2). File contents are in neither (BE-1). Any data
   operator invalidates every study (OP-3). Passes and levels read "out of date"
   (ADR-47). A mesh made in the GUI is never invalidated (GUI-22).
2. **"Written or refused" is not enforced.** The rule exists; nothing tests it per
   feature, step type and selection kind. So: FEniCSx ignores edge and point conditions
   (FENICS-1); Elmer's modal drops elastic supports (ELMER-3); Code_Aster's transient
   drops plasticity (ASTER-2); CalculiX drops material directions in 2D (CCX-6) and
   settings on three procedures (CCX-5); bolts take no thermal expansion (CCX-2);
   pretension is undefined outside static steps (CCX-9, CCX-10, ASTER-5); couplings are
   accepted and never read (ADR conformance).
3. **The store is not crash- or concurrency-safe.** In-place rewrites of every file per
   command (BE-4), a torn history line blocks everything (BE-5), the lock is stolen by
   age (BE-6), proposals and restores apply piecemeal (BE-7, BE-15), no schema migration
   (BE-14, ADR-E-18).
4. **Some agent paths go round the session.** `optimisation_run`, `mesh_adapt` and
   `mesh_convergence` solve with no budget, history or version (AGENT-1, ADR-44); the
   "agents propose code" gate checks the path, not the value (BE-9, AGENT-5, ADR-1,
   found three times); Stop does not reach a container on Windows (BE-3).
5. **Typed quantities are parsed in many places, each differently.** Bare numbers taken
   as metres or millimetres whatever is shown (PANEL-5, PANEL-21), no dimension check
   (PANEL-11), unit switches that re-read the number (PANEL-1), `5mm` refused (GUI-10),
   and forms that rewrite untouched values at six digits, which moves hashes (PANEL-8,
   PANEL-9, PANEL-27, GUI-29).
6. **One error takes down more than its own panel.** A raising operator stops the
   schematic and the CLI (OP-1, OP-5); a raising panel aborts the whole refresh
   (GUI-24); raw OCP and pydantic text reaches the user (GUI-5, UX-8).
7. **Engineering checks applied outside their scope.** Steel endurance limit on any
   material (ADR-62); AISC steel weld rules and fatigue curves on any base metal
   (ADR-3, ADR-5); the fillet directional increase on PJP welds (ADR-4); torsion
   unchecked where the ADR says refused (ADR-6); compression-only supports reversed on
   inward faces (COMMON-1).
8. **Preferences change results in two places** (ADR-E-6, ADR-E-7), against ADR 0024.
9. **Most solvers are verified only on the maintainer's machine.** CI installs CalculiX
   and FEniCSx; every Code_Aster, Elmer, CFD, PyNite and OpenCourant figure rests on
   local runs, and FEniCSx's SciPy path, the one Windows users get natively, crashes on
   magnetics (FENICS-2) with no CI run to say so.

## Decision

1. **Fix before `main`: work packages 1–4** below (the critical findings and the four
   patterns that make results or projects untrustworthy). The rest follows on
   `development` in the order given. New ADRs open again after the push to `main`
   (CLAUDE.md, Workflow).
2. **Every fix starts by reproducing its finding and ends with the test its report
   names.** A "read only" finding that cannot be reproduced is closed with a note in
   the report file, not fixed on faith.
3. **Looking Glass is not embedded.** It is a two-day-old, macOS-only Rust/GPUI front
   end for coding agents that speaks ACP and has no MCP; its GPU window cannot sit
   inside PySide6 on macOS or Wayland, and a Rust binary per platform is a cost a
   two-contributor project should not take on. Its transcript ideas are ported into the
   Qt agent panel (package 12). A `puzzle acp` endpoint, which would let it and other
   ACP clients front Puzzle's agent, is left for its own ADR.
4. **The schematic is a parametric tool and a results tool** (the maintainer,
   2026-11-16, in place of the reviewer's proposal to narrow it to results): access to
   the underlying data and input variables, used for manipulation further downstream.
   - **Two levels.** A component and a study each draw as one collapsed node by default,
     their loads, supports and other members inside; opened, every member is its own
     node, reachable and wireable one by one. This keeps the canvas from growing hectic,
     which was the struggle in Grasshopper.
   - **One state language** on every node: current, stale, not run, failed. A stale
     result is kept and stays usable. Nothing re-solves by itself: the user chooses to
     bring a chain up to date or to go on with the results there are. Operators may keep
     working on an earlier run while the model is set up for the next solver or system,
     and neither breaks the other. So a result an operator reads is a named run, not
     only "the study's latest", and a stale value is always drawn as stale.
   - A parametric canvas must be exact about what a change invalidates: OP-3 (any data
     operator puts every study out of date) and OP-4 (an operator on a swept study reads
     whichever point ran last) are blockers for it, not polish (packages 1 and 4).
5. **Usability direction:** one command table feeding the menu bar, ribbons and context
   menus (about 150 commands, not 342; operators leave the menus for the palette); a
   workspace strip and a ribbon for Model; a "New analysis…" form; loads placed on a
   pick; an Edit menu with Redo; Properties with an Advanced fold.

### Work packages

Each is sized for one Opus session or a few. "Report" is the file in `docs/adr/0090/`
to read first; it holds the reproduction, the fix and the test for every ID. Work on
`development`; one commit per package; `pixi run -e dev check` green before each.

| # | Package | Findings | Report | Done when |
| --- | --- | --- | --- | --- |
| 1 | **Run keys and staleness, one definition.** One function gives the project a run sees; solve and status both hash it. Geometry and imported files hashed by content. Data operators enter a study's hash only when the model reads them. Passes, levels and GUI meshes keyed the same way. Import geometry becomes a command that refuses or renames on a clash. | BE-1, BE-2, OP-2, OP-3, GUI-20, GUI-22, ADR-47; to check: Puzzle and solver version in the key | `backend.md`, `schematic-operators.md` | Replacing a STEP file makes its studies out of date; a study with a wired parameter reads "up to date" after its run; adding a Number changes no study's key |
| 2 | **A store that survives crashes and two writers.** Write-temp-then-replace for object files, only the changed ones; history appended and fsynced first; a torn last line repaired on open; a lock that checks its owner's pid; proposals, undo and restore all-or-nothing; a schema version that moves, with a migration step and a "newer than this Puzzle" error; New project refuses a folder holding one; a group cannot contain itself. | BE-4 to BE-8, BE-10, BE-13 to BE-16, BE-19, GUI-19, GUI-21, ADR-E-18 | `backend.md`, `gui-core-bugs.md` | Kill tests at each write point leave an openable project; two sessions never lose an edit; the three GUI cases refuse |
| 3 | **Agent gates.** Every solving tool goes through the session (budget, history, version); the code gate inspects values; Stop and timeouts stop containers, streams and solves; resets mid-turn are safe; scripts are sandboxed or refused on every platform. | AGENT-1 to AGENT-7, ADR-1, ADR-44, BE-3, BE-9, BE-22, AGENT-10 to 13 | `agent-looking-glass.md`, `backend.md` | An agent with no budget left cannot start a solve by any tool; a PDE appended by path is proposed; no container outlives Stop |
| 4 | **Operators that cannot take the project down.** One catch in `evaluate.outputs` and `parameter_values` turning any error into the operator's problem; swept, shoot-out and convergence runs chosen explicitly, never "the last"; unit checks on checks; bounded constant folding; Round and Table layout stop reading preferences. | OP-1, OP-4 to OP-17, ADR-E-6, ADR-E-7, ADR-51 | `schematic-operators.md`, `adr-early.md` | `10.0**400` shows as that operator's error; the same project gives the same parameters under any preferences |
| 5 | **"Written or refused", tested.** A test matrix per adapter: every catalog feature × step type × selection kind (face, edge, point, body) × element shape is either written (the input names it) or refused with a code. Then fix what it finds, starting with the listed ones. Pretension gets one helper stating each section's state (closed, driven, locked) for every step type. | FENICS-1, FENICS-3, FENICS-4, ELMER-3, ASTER-1, ASTER-2, CCX-2, CCX-5, CCX-6, CCX-9, CCX-10, CCX-13, ASTER-5, EDDY-1, OF-3, OC-3, capability claims in `adr-conformance.md` | the three adapter reports | The matrix runs in CI without solvers and has no silent cell |
| 6 | **CalculiX and Code_Aster accuracy.** The `.frd` tag past 9,899 increments; `.dat`/`.frd` matched by index, not time; harmonic ranges as listed frequencies; springs that do not turn with the node; set-name collisions; compression-only normals from the face, not the model centre; plastic strain without thermal strain; modal counts that split a pair. | CCX-1, CCX-3, CCX-4, CCX-7, CCX-8, CCX-11, CCX-12, CCX-14, CCX-15, COMMON-1 to 3, ASTER-3, ASTER-4 | `adapters-calculix-aster.md` | Each finding's test passes; the bolt benchmarks still match to their tolerances over two steps |
| 7 | **Elmer, FEniCSx and eddy currents.** Output counts asserted against expected times; `sif_edit` and tables; `match_points` on coincident nodes; `Factored` on SciPy; zero-current coils; the SciPy backend run in CI. | ELMER-1, ELMER-2, ELMER-4, ELMER-5, FENICS-2, FENICS-5, EDDY-2, COMMON-1e, the "to check" list | `adapters-elmer-fenicsx.md` | A transient with `output_every = 2` returns the right times on all four FE solvers |
| 8 | **CFD, explicit and frames.** OpenFOAM's wall flux with the effective diffusivity, and cell matching by cell id or true centroid; Code_Saturne's outlet level and `TurbVisc`; Radioss names escaped; a displacement without an amplitude refused or ramped in explicit steps; shell stress named for what it is; PyNite's offset area; benchmarks for what OpenCourant claims. | OF-1, OF-2, OF-4, CS-1 to CS-3, OC-1, OC-2, OC-4 to OC-7, PYN-1 to PYN-3, ADR-64 | `adapters-cfd-explicit-frames.md` | The heated turbulent channel balances energy on both CFD solvers within the benchmark's tolerance |
| 9 | **Engineering checks within their scope.** Fatigue passes the steel flag (non-steels need an S-N curve); weld strength and fatigue chosen by base metal, PJP without the directional increase; torsion refused where unchecked; the weak-spring warning against the body's own load; Netgen refuses conflicting sizes; a selection that lost members refuses the run; adaptivity refused on meshes it cannot estimate, and `--delete-passes` keeps results when a pass fails. | ADR-2 to ADR-6, ADR-45, ADR-46, ADR-50, ADR-62, ADR-E-9, ADR-E-14, ADR-E-15, PYN-2 | `adr-conformance.md`, `adr-early.md` | Each has a unit test from a hand calculation or a refusal test |
| 10 | **GUI correctness.** One quantity-entry helper used by every form (the unit shown for bare numbers, a dimension check, no rewrite of untouched values); an error boundary per panel in `refresh`; plain messages from `details["errors"]`; Delete and Ctrl+Z scoped to the focused panel; picks, probes on mirrored copies, stale tool panels, selection kept across relabels; the menu fuzz kept as a test, with dialogs cancelled and accepted. | GUI-1 to GUI-18, GUI-23 to GUI-30, PANEL-1 to PANEL-34 | `gui-core-bugs.md`, `gui-panels-bugs.md` | The fuzz passes on both examples; a unit switch never changes a stored value |
| 11 | **Usability.** In the reviewer's order: the Geometry tab's 1597 px minimum; a load on a picked face with its values inline; "New analysis…"; an Edit menu with Redo; the Advisor following the shown study; Properties' Advanced fold; then the command table and the Model ribbon; onboarding (an example on the start page, a dropped STEP file, display units that read as MPa and mm by default). | UX-1 to UX-38 | `gui-usability.md` | The core task (STEP to probe and export) is counted again and is under ten steps |
| 12 | **Agent interface.** Proposal cards with before and after values, a preview and "Show in view"; the selection and the shown result sent as context; the tree and view live during a turn, with what was touched marked; a turn undone as one; conversations kept per project; a plan list; tool groups loaded by skill so a request carries about 25 tools, not 110. | UXA-1 to UXA-15 | `agent-looking-glass.md` | The scripted-provider tests cover each; the prompt is under 8k tokens before the first message |
| 13 | **Schematic.** Two levels (components and studies collapsed, their members reachable when opened); the state language, a stale result kept and usable, operators able to read a named run; stale values drawn as stale, with "Bring up to date" on request; refresh built once and evaluated off the GUI thread; multi-select, Delete, copy and paste, data wires removable; compatible sockets lit while dragging and the operator search on a drop; values edited on the node; a layout that holds at 50 nodes; then the operator set (merges and the missing ones). | SCH-1 to SCH-11, SCH-UX-1 to 18, OP-NEW-1 to 14 | `schematic-operators.md` | A refresh at 55 nodes is under 100 ms; no out-of-date verdict is drawn as current |
| 14 | **Speed and scale.** Commands that cost by what changed, not by project size; a results store that reads and writes by field and step without holding a run in memory; compaction; vectorised transfers and face look-ups; long reads off the GUI thread in the result-tool, export and fatigue forms; no reload after the GUI's own write. | BE-11, BE-12, BE-17, BE-18, BE-20, BE-21, GUI-7, GUI-8, PANEL-13, PANEL-26 | `backend.md` | An edit in a 300-object project is under 50 ms; a 1 GB result stores without doubling memory |
| 15 | **Verification that runs somewhere other than one machine.** A scheduled workflow with the Code_Aster, Elmer, OpenFOAM, Code_Saturne and OpenCourant images and the PyNite environment; a benchmark for each claimed capability or the claim removed; test tolerances brought to the figures the ADRs quote. | ADR conformance section (f), OC-3, ADR-64, ADR-65 | `adr-conformance.md` | Every figure in CLAUDE.md's adapter table has a CI run behind it |
| 16 | **Records and reach.** About twenty stale index rows; notes on the overtaken ADRs (0004, 0007, 0010, 0012, 0015); skills and guides for CFD, custom PDEs, shells and ADR 0065's operations; forms for MCP-only features (thread results, modelled beads); `puzzle:` links and F1 on menu items; the core's fourteen GUI imports removed; before any public installer, the licences folder, Help → Licences and the GPL source offer (ADR-57). | ADR-7, ADR-48, ADR-49, ADR-53 to ADR-61, ADR-63, ADR-66, ADR-E-1, ADR-E-2, ADR-E-11, ADR-E-16, ADR-E-19, ADR-E-21, ADR-E-24, the low findings | `adr-conformance.md`, `adr-early.md` | `tests/test_agent_skills.py` and `tests/test_help.py` list the added ADRs |

Medium and low findings not named in a row belong to the package whose report holds
them. A package that finds its fix needs a decision (a schema change, a new command)
records it by amending the source ADR until the freeze lifts, then as its own ADR.

### Progress

**Package 1 built (2026-11-16):** `graph.keyed(project)` is the one project run keys are
taken from (the stored project with bolts and sweep lists resolved); `run_study` keys
from the project as given, before parameters, suppression or axes are applied, so the
solve and status agree (BE-2, OP-2). `project/files.py` hashes by content every file an
object reads (a geometry's source, history `file` and `dxf` arguments, an imported
field's files, a File operator's pattern), cached by size and time; a missing file is
the problem `missing_file` (BE-1). `Project.root` (never saved) tells hashing where the
folder is. A study's hash covers only the operators and parameter values its model
names, found by word, so a needless "out of date" is possible and a missed one is not
(OP-3); a bound quantity's stored value is left out. Levels, passes and optimisation
points are `graph.variant(base, ...)` keys, current while their study's own key is
(ADR-47). Imports copy with `files.copy_in`, which never replaces a different file of
the same name (GUI-20). The GUI's mesh is kept with `graph.mesh_hash` and dropped when
that changes (GUI-22). Runs record `puzzle_version`. Tests: `tests/test_run_keys.py`.
Left: the import still reads on the GUI thread; multi-file field sources still copy
over a same-named folder; optimisation points read "out of date" in the tree. Found on
the way: the MMG heat-layer benchmark had failed since the 3D metric constant was
corrected to 9/32 (commit 0623431); its bound and measured figures are updated (MMG
0.041 % in 2056 elements, isotropic 0.180 % in 1711).

**Package 2 built (2026-11-17):** `project/atomic.py` holds the two primitives every
writer now uses: a file written whole or not at all (a temporary file beside it, then
`os.replace`, retried while a Windows reader holds it), and a lock naming its holder
(pid, host, token), stale only when that process is gone (or, from another host, not
refreshed for 120 s by its heartbeat), released only while the token is ours, and
checked before every commit (`lock_lost`). A change is one transaction: the changed
files as temporaries, then `.puzzle/journal.json` (the commit point), the history
entries, the swaps, the journal removed; a session finishes a leftover journal on open
and `load_project` reads through one, so a crash leaves the project wholly before or
wholly after (BE-4). Only files whose text changed are written. A torn last history
line goes to `.puzzle/history.torn` and a damaged middle one is the problem
`damaged_history` (BE-5, `project/history.py`). Accept, undo and version restore are
all-or-nothing; undo refuses what later changes use; a restore checks every blob first
(BE-7, BE-8, BE-15). `project/migrations.py`: a registry run on raw data before
validation, `newer_schema` and `unknown_field` in place of tracebacks, a version saved
before a migrated project is first written; `SCHEMA_VERSION` stays 1, since no stored
shape changed (BE-14, ADR-E-18). The result store has a cross-process lock and
`write_mesh` is idempotent (BE-10). Versions leave out `exports/`; an archive without
results never copies them (BE-13, BE-16). `store.create_project` refuses a folder
holding a project (GUI-19); a group cannot contain itself and a loop on disk opens as
the problem `group_cycle` (GUI-21). Tests: `tests/test_store_safety.py` (each operation
failed at every write, reopened: before or after, never a mix),
`tests/test_gui_store_safety.py`. Measured: an edit in a 300-object project 365 ms to
85 ms; on the cantilever 18 ms to 23 ms (two syncs and the lock). Left: versions are
still made under the project lock; pid reuse is not detected; an archive with the
latest results still copies the store, then prunes; on a network share the guard lock
depends on the filesystem.

**Package 3 built (2026-11-17):** every solve an agent starts is a command in the
session: `run_study` and four new ones (`run_optimisation`, `run_convergence`,
`run_adapt`, `run_mesh_shootout`) take one path, `Session._run_command` (level, a
proposal below "act", the pre-run version, one history entry). `puzzle/budget.py` is
the gate round it: `run_study` calls it before each fresh solve, so a multi-run command
is counted solve by solve and stopped when the budget is spent; what it counted goes
into the entry's `spent`, where the budget is read from; a failed run that began a
solve spends too. An agent's tool call that reaches a fresh solve with no gate is
refused (AGENT-1, ADR-44; the mesh shoot-out was ungated as well). The code gate
applies the edits to a copy and compares the scripts and weak forms before and after,
so no path goes round it; an undo or restore that would bring code back is the user's
(BE-9, AGENT-5, ADR-1). Tools with effects outside the model have a least level in
`session.ACTIONS` (AGENT-12). Containers are named and killed on Stop, on a timeout and
at exit; native solves are killed as a tree (`jobs/processes.py`: a job object on
Windows, a process group elsewhere); a warm container's solve is killed as a tree and
keeps its container alive while it runs; a WSL solve is killed inside its distribution
(BE-3; checked on real Docker). Stop closes a streaming reply and is the cancel of the
run a tool started (AGENT-2); a reset during a turn ends the turn, and an empty reply
is never stored (AGENT-3, AGENT-4); one session per MCP server (AGENT-7); the picture
`render` made is shown, and the tool says whether the model was sent it (AGENT-6).
Scripts: `project_root` is required, and where no sandbox exists a script is refused
(`unsandboxed`) unless the preference `scripts.unsandboxed` allows it in trusted
projects. **This departs from ADR 0062**, which ran such scripts with the folder not
given; it awaits the maintainer's word, then 0062 is amended. Tests:
`tests/test_agent_gates.py`, `test_agent_turns.py`, `test_process_trees.py`,
`tests/integration/test_container_stop.py`. Left: a crash mid-run leaves that run's
solves uncounted; outside MCP clients have no Stop; the macOS and Linux sandboxes were
not run (this machine is Windows); AGENT-9 unconfirmed and unchanged.

**Package 4 built (2026-11-17):** an operator's error is a value, as a spreadsheet
cell's is: `evaluate.outputs` and `parameter_values` turn any exception into that
operator's problem (`operator_failed`, the cause in words), and an operator that reads
it says which input failed (OP-1, OP-5). A failed before-solving operator is in
`Project.problems`; only the studies whose model reads it are refused, and operator
problems elsewhere no longer refuse a run. An input from a study says which run it
reads (decision 4): `OperatorInput.point`, `solver` and `run`, written only when set.
By default it is the study's run for its set-up now, by the same keys as status; when
only older runs exist the newest is read and marked stale with its name, on the node,
in `puzzle operators`, in MCP `operator_value`, in a report, and in a verdict ("Pass
(stale: from the run of ...)"); a swept study with no point chosen is refused, not read
at whichever ran last (OP-4). Properties has Run read under each such input; MCP takes
`point`, `solver` or `run` by name; `puzzle operators P NAME --runs` and `--read`.
Checks compare like with like, and a verdict never contradicts its margin (OP-6, OP-7).
Expressions in both languages are bounded in length, nodes and exponents (ADR-51,
OP-16). Round rounds in its own unit, else the column's, else SI; a table's layout is
the operator's own, taken from the preference once when it is made
(`tests/test_preferences_never_change_results.py`: 57 operator types and a results
pipeline under four preference sets, identical parameters, keys and values; ADR-E-6,
ADR-E-7). New kinds `second_moment` and `stress_intensity`. `SCHEMA_VERSION` stays 1:
untouched projects are written as before; one using a run selector is refused by an
older build as `unknown_field`. Tests: `tests/test_operator_safety.py`. Left: derived
results and the GUI's result tools still read the newest run unmarked (package 13);
the generic Structural steel has no yield strength by choice, and the guide's recipe
names A992 or A36 or a typed limit; `problems()` costs 0.3 ms more a refresh.

**The four critical findings outside packages 1-4, built (2026-11-17)**, pulled forward
from packages 5, 7, 8 and 10 since each gave wrong numbers silently:

- ELMER-1: a transient's ResultOutputSolver takes `Exec Intervals = k` and its offset,
  so Elmer writes only the outputs asked for; every Elmer reader refuses a file count
  that differs from the expected times (`output.check_outputs`), never truncates.
  `test_benchmarks.py::test_transient_output_every_keeps_each_field_at_its_time` on
  CalculiX, Elmer, Code_Aster and FEniCSx: each field equal to the every-step run's at
  its time. The others' thinning was checked: labels right (CalculiX also keeps the
  step's last increment, Code_Aster the start).
- FENICS-1: held values (fixed, displacement, temperature, potential, ground, a custom
  `dirichlet`) on edge, point and body selections are applied by `study.held_dofs`
  (points written to the case mesh as vertex cells); natural loads on an edge or point
  are refused by `common.check_selection_kinds`, the one shared check, which
  Code_Aster calls too: it had solved a pressure or a traction on an edge as nothing
  (displacement zero) and crashed on three more cases. `test_edge_conditions.py`:
  FEniCSx and Code_Aster within 5.1e-4 K and 1.9e-6 of the largest displacement of
  CalculiX. Weak springs on FEniCSx are now refused, where they were dropped.
- OF-1: in turbulent flow a wall `heat_flux` is `exprMixed` with gradient
  q / (k + rho cp nut / Pr_t), the diffusivity `scalarTransport` uses on the wall face
  (the reviewer's suggestion, verified in the v2412 image by running).
  `test_turbulent_heated_channel.py`: bulk temperature slope over the energy balance
  0.9993 (k-epsilon) and 0.9994 (SST) on OpenFOAM, where it was 1.56 and 1.47; 1.0010
  and 0.9993 on Code_Saturne.
- PANEL-1: the linearization panel re-renders the allowable from the stored value when
  the unit changes and writes only the field that changed. The same fault in the
  legend: custom ranges were kept in the unit shown (2e6 Pa became 2e6 MPa after a
  change of units) and are now kept in SI.

With these, all nine critical findings are fixed. Left: the Edit panels of coordinate
systems and construction geometry still rewrite untouched values at six digits
(package 10); a force on a point or edge has no benchmark.

**Package 5 built (2026-11-17):** `tests/test_written_or_refused.py` is the matrix
(2,597 cells, 42 s, no solver): each cell writes a minimal case for an adapter, physics
and step kind through `run_study` stopped before the solver, adds one thing and writes
again. The thing must be refused with a coded error or change the solver's input, and
the outcome must agree with `Capabilities`. The axes come from the catalog, so a new
feature is covered without an entry. On the code as it was, 105 cells and three
agreement checks failed, more than the review had listed:

- Pretension (CCX-9, CCX-10, ASTER-5): one definition for every adapter and step type,
  `common.pretension_states`: driven in the first static structural step it is active
  in, closed before (or always, when there is none), locked after. A transient that
  would have to drive one is refused. The reviewer's bolt: first axial mode 33,556 Hz
  with and without the pretension feature, where the open cut had given 21,529 Hz.
- Plasticity in transient steps (ASTER-2, CCX-13) is written: Code_Aster
  `VMIS_ISOT_TRAC` in `DYNA_NON_LINE`, CalculiX automatic incrementation when a material
  yields. A force ramped over 1 s gives the static plastic strain within 0.02 % on both.
- A temperature load with beam bolts, a pretension section, a threaded contact or a
  line weld is refused (CCX-2, ASTER-1; `common.check_thermal_helpers`). Their thermal
  expansion is not built: it goes on ADR 0072's list. The refusal covers a modelled
  bolt's pretension section too, which is broader than measured.
- Shared settings that can do nothing in a step are refused, never dropped (CCX-5;
  `base._setting_void`, `Capabilities.shared_settings_steps`); CalculiX's
  `after_procedure` cards reach its harmonic, modal-transient and coupled steps.
- Elmer's modal step keeps elastic supports (ELMER-3): a block on a foundation,
  1796.18 Hz on all four FE solvers, the closed form to six digits.
- Refused where nothing was written or what was written was wrong: material directions
  on CalculiX's 2D regions, shells and beams (CCX-6); magnetics on hexahedra and
  quadrilaterals (EDDY-1); CFD heat with no inlet temperature (OF-3); line loads and
  self weight on selections with no members (PyNite); a temperature load not covering
  every node (Elmer, an IndexError before).
- Claims withdrawn: `mass_flow_inlet` (Code_Saturne), every coupling but CalculiX's,
  FEniCSx custom PDEs in modal and harmonic steps (FENICS-3), OpenCourant's frictional
  contact (written, not benchmarked). Claims now benchmarked and kept: pressure
  amplitudes on the four FE solvers (1.00 % of the largest), OpenCourant's gravity,
  acceleration, force, pressure and displacement under amplitudes (OC-3).
  `Capabilities.weak_springs` is new. FEniCSx's `solve_solid` ends in a raise, and
  `case.SOLID_3D` refuses up front (FENICS-4).
- A regression of the critical fixes, found here: `check_selection_kinds` refused a
  force along a sheet's edge; a sheet's edge now counts as a boundary.

The matrix catches dropped input, not wrong input, and has no cells for threaded
contacts or modelled welds. Not benchmarked: OpenCourant's rigid-wall friction and mass
scaling. Found and left for package 6: on CalculiX a modal step after a loaded static
step moves a bolt's bending modes with the load (3,404 Hz with none, 11,012 Hz at
10 kN), the axial mode unmoved; the elastic support's springs carrying geometric
stiffness is the suspect, unconfirmed.

### Enhancements toward the state of the art

From `state-of-the-art.md` (50 gaps, each with who has it, which open solver can deliver
it, effort and priority). Confirm each against the products and solvers before starting.
The reviewer's first fifteen:

1. The solver shoot-out as a first-class feature: GUI, CLI, MCP, a difference field, a
   stored report (ADR 0011 promises it; `run_shootout` has no caller today).
2. Hyperelasticity with curve fitting (CalculiX first).
3. Study templates and the 46 planned tutorials.
4. A full report: set-up, mesh, convergence, checks and provenance, as PDF and HTML.
5. One "can I trust this result?" verdict from the existing checks plus reaction balance.
6. Random vibration (PSD) and the rest of ADR 0041 (base excitation, harmonic stresses).
7. Fatigue from histories (rainflow, critical plane) and weld fatigue by IIW and
   Eurocode categories.
8. Thermal radiation: to ambient, then cavities with view factors.
9. Defeaturing by detection, and a watched CAD file that re-imports and reports changes.
10. A submodelling wizard on the existing field mapping.
11. Error estimates and adaptivity on quadrilaterals and hexahedra.
12. The NAFEMS sets across solvers on CI, with a public table per release.
13. Mid-surface extraction for sheet parts.
14. Topology optimisation with smoothed geometry out.
15. Conjugate heat transfer with a CFD-grade mesher.

What Puzzle does that commercial tools do not, to protect in all of this: one model on
several solvers with shoot-outs; an agent that acts under autonomy levels and budgets;
typed, logged commands on text files; an open results store; real solver input a user
can read; verification shipped with the code; joint engineering (bolts, threads, welds);
frames beside continuum; honest refusals; no licence tokens.

## Consequences

- `main` waits for packages 1–4. They are the smallest set after which a result shown
  as current is current, a project survives a crash, an agent stays inside its limits
  and one bad operator cannot lock a project.
- Package 5's matrix will find more silent cells than are listed here; that is its
  purpose, and each one found is a refusal to add, not necessarily a feature to build.
- Several fixes change run keys (package 1) and stored formats (package 2). Existing
  projects' results will read "out of date" once, and the schema version moves for the
  first time; the migration step is part of package 2.
- The work list is long: about 300 findings. Packages 10–13 are where most of the count
  is and can run in parallel once 1–4 are in, since they touch different files.
- The review itself has gaps (listed under Context). A second, narrower review after
  packages 1–5, driven by hand on a real screen and on macOS and Linux, would cover what
  scripts could not.

## Alternatives considered

- **Embed Looking Glass in the Qt window, or ship it as a companion app:** rejected for
  now (decision 3): macOS-only, no MCP, a second toolchain to build and sign on five
  platforms, and its strengths are patterns that Qt can carry.
- **Fix findings in severity order across areas instead of by package:** the criticals
  share causes with dozens of lesser findings (one hash definition, one store, one
  session path); fixing causes closes more than fixing instances.
- **Push to `main` now and fix there:** the critical findings include silent wrong
  results and project loss from ordinary actions; `main` is what others will install.
- **One long report instead of a summary with report files:** the detail runs to 4,700
  lines; a fixer needs one area's reproductions, not all of them.
