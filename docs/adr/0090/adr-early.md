# ADR conformance review: the fifteen early ADRs (and 0083)

Reviewer scope: ADRs 0002, 0003, 0005, 0006, 0009, 0010, 0012, 0013, 0014, 0016, 0017,
0019, 0022, 0024, 0083, each read whole and checked against the code on `development`
(da26cde, working tree with only CLAUDE.md modified). Read-only; scratch files under
`scratchpad/review/adr-early/`. Sections are written in the order checked (0083 first).

Not re-reported (already found by the earlier reviewer): foundational ADRs overtaken
without a note (0004, 0007, "five solvers" in 0010/0012), stale README index rows, CI
installing only the dev, calculix and fenicsx environments.


## Summary

| ADR | Verdict |
| --- | --- |
| 0002 GPL-3.0-or-later | holds (dependency table dated) |
| 0003 Python core, PySide6 GUI | holds with gaps: the core imports the GUI, three paths need Qt |
| 0005 meshing layer | holds with gaps: CFD boundary layers never exercised; Elmer mortar tie text stale |
| 0006 model IR, capability matrix | holds with gaps: schema version never bumped, guard inert |
| 0009 project model | holds with gaps: a stale study is not told which input changed |
| 0010 MVP solvers | holds with gaps, all on ADR 0072 |
| 0012 solver line-up | holds (overtaken, already reported) |
| 0013 pixi, native first | holds with gaps, known ones; container-first drift |
| 0014 history geometry, naming | holds with gaps: detached members only warn; anchor tolerance looser than stated |
| 0016 OpenFOAM | holds with gaps: no native Linux environment |
| 0017 geometry workbench | holds |
| 0019 mesh workbench, Netgen | holds with gaps: conflicting sizes not refused on Netgen |
| 0022 local expert model | holds with gaps: suite provenance goes stale |
| 0024 settings | does not hold on "preferences never change results" (two reproduced cases) |
| 0083 help and documentation | holds with gaps: no tool links, F1 on menu items and schematic operators, stale pictures untested |

Findings: 25 (3 high, 10 medium, 12 low).

- High: ADR-E-6 (display units change a parameter through Round), ADR-E-7 (Table
  layout preference changes a result table's columns), ADR-E-9 (conflicting sizes
  meshed silently on Netgen). All three reproduced.
- Medium: ADR-E-1, E-2 (0083), E-11 (0022), E-14, E-15 (0014), E-16 (0003), E-18
  (0006), E-19 (0009), E-21 (0005), E-24 (0016).
- Low: ADR-E-3, E-4, E-5 (0083), E-8 (0024), E-10 (0019), E-12, E-13 (0022), E-17
  (0003), E-20 (0009), E-22 (0005/0010), E-23 (0013), E-25 (0002).

Reproduced by running something: E-1 (grep), E-4, E-6, E-7, E-9, E-11 (settings
level), E-14 (mesh level), E-16, E-18. Read only: E-2, E-3, E-5, E-8, E-10, E-12
(snippet), E-13, E-15, E-17, E-19 to E-25. Repro scripts are in
`scratchpad/review/adr-early/` (`test_round_pref.py`, `test_layout_pref.py`,
`test_netgen_conflict.py`, `test_detached.py`, `picked.py`, `noqt.py`, `hash.py`,
`h1.py`, `ev.py`, `prefs.py`).

---

## ADR 0083: help and documentation

**Verdict: holds with gaps.** Steps 1-5 exist and their tests pass; four sentences of the
Decision are stronger than what is built or tested.

Verified:

- Ran `pixi run -e dev pytest tests/test_help.py tests/test_help_site.py
  tests/test_help_images.py -q`: 27 passed in 28.8 s (includes the staleness test
  `test_the_reference_is_what_the_code_says_now`, the site build, the PDF build and its
  link/bookmark check, the picture tests).
- Pages: 17 guides + getting started, 19 theory notes, 9 verification pages (overview +
  eight), 11 generated reference pages in `src/puzzle/help/reference/` (matches the
  Built text).
- Help keys by kind (snippet over `help.keys()`): mcp 111, feature 92, operator 87,
  problem 62, cli 60, setting 59, result 31, field 29, kind 23, panel 19, property 16,
  capability 9, solver 9.
- `src/puzzle/gui/help_viewer.py`: `HelpWindow` (contents, search, Back, TeX, `help:`
  and `puzzle:` link handling at :197-211), `help_key` (:218-241), `install` (F1 as an
  application shortcut, :314).
- MCP `help(topic)` at `src/puzzle/agent/mcp.py:1743`; skill
  `src/puzzle/agent/skills/help-pages.md`; `puzzle help` (test
  `test_puzzle_help_prints_a_page_and_searches`); knowledge pack indexing
  (`test_the_knowledge_pack_reads_the_help`).
- `.github/workflows/manual.yml` builds the PDF on a published release and attaches it.
- `WITH_HELP` (57 ADRs) is checked by `test_finished_adrs_have_help`.

### ADR-E-1 (medium) No page contains a link that opens a tool

- ADR 0083 decision 2: the Help window has "links that open the tool they describe";
  decision 4: "the GUI steps linked to the tools"; Built: "`help:` and `puzzle:` links".
- Evidence: the handler exists (`help_viewer.py:206-209`, `puzzle:<action>` triggers
  `window.actions_by_name[...]`), but `grep -rE "\(puzzle:" src/puzzle/help` finds no
  link in any of the 58 pages. No test follows a `puzzle:` link (the only mention in the
  tests is `test_help_site.py:35`, which asserts none is left in the site's HTML).
  `actions_by_name` holds only a subset of menu actions (toolbar names, `text.lower()`
  of Window items), so most menu locations could not be linked even if a page tried.
- Reproduced: by grep (count 0) and reading the handler.
- Fix: build it. Give every menu action a stable name (the menu path already is one:
  `main_menu.paths`), let `puzzle:` take a menu path, and have `pictures.with_pictures`
  or the page text turn each named menu location into a link; add a test that every
  `puzzle:` target resolves to an action. Otherwise amend decision 2 to say links open
  pages only.

### ADR-E-2 (medium) F1 on a menu item, and on an operator node in the schematic, does not open that item's page

- ADR 0083 decision 2: "Context help: F1 on any panel, tree item, Properties row, menu
  item or operator opens its page. Each has a help key, and a test refuses one without a
  page."
- Evidence: `help_key` (`help_viewer.py:218-241`) handles a widget's `help` property,
  the Properties tree, the model tree and docks. There is no `menu:` key kind (keys by
  kind listed above) and nothing reads the highlighted `QAction` of an open menu. For
  operators, only the model tree's item gives `operator:<type>` (`_object_key`); the
  schematic canvas and its side panel are both given the fixed key `panel:schematic`
  (`gui/window.py:1188-1192`), and `help_key` returns the first explicit `help`
  property it meets, so F1 with an operator node selected in the schematic opens the
  schematic panel's page, not the operator's entry. `test_f1_knows_what_has_focus`
  covers panels and tree items only.
- Read, not reproduced in a live window.
- Fix: build it for the schematic (the scene's selected node gives `operator:<type>` or
  `kind:<kind>` before the panel key), which is where operators are met; for menu items
  either add a "What's this" path (F1 while a menu is open: Qt's `QMenu` key handling,
  `menu:<path>` keys falling back to the guide that names the location) or amend the
  sentence to drop "menu item".

### ADR-E-3 (low) "a picture is stale" is not tested; only that the set of pictures is right

- ADR 0083 decision 2 (amended 2026-11-16): "a test fails when a page names a menu
  location the menu bar doesn't have, or a picture is stale or missing."
- Evidence: `tests/test_help_images.py` checks names only: every referenced file exists,
  the menu pictures on disk equal `index.json`'s, the locations the pages name equal the
  index's, every operator type has a PNG, no unused screen. Nothing compares a picture
  with what the GUI draws now, so an item added to a menu (every picture of that menu
  changes), an operator's ports changed, or a restyled Advisor leaves old pictures in
  the manual with all tests green. The Built paragraph describes the tests accurately;
  the Decision sentence overstates them.
- Read the tests; ran them (pass).
- Fix: cheapest is a fingerprint per picture in `index.json` of what it shows, written
  by `scripts/build_help_images.py` and recomputed in the test without rendering: for a
  menu picture the ordered list of that menu's item texts, for an operator its label and
  port names, for screens a version string. Or amend the sentence to "missing or no
  longer named".

### ADR-E-4 (low) Elements have one reference page but no per-element entry or test

- ADR 0083 decision 3: "A test fails when a catalog feature, setting, operator, element,
  problem code or MCP tool has no reference entry."
- Evidence: `test_everything_the_code_offers_has_a_reference_entry`
  (`tests/test_help.py:115`) builds `wanted` from features, settings, solvers,
  operators, properties, models, results and problem codes; elements are not in it. The
  generated page has only the page-level keys `element, elements`
  (`help/reference.py:191`); snippet: 82 elements in `elements.query()`, 0 keys of the
  form `element:<name>`. A new element is still caught, indirectly, by the staleness
  test (the page is `elements.listing()`), so nothing can go missing; what is absent is
  a key per element, so `help("element:C3D20R")` or F1 on an element choice lands on the
  page top.
- Reproduced by snippet (`adr-early/h1.py`).
- Fix: amend the Built text (elements are held by the staleness test), or add
  `<!-- key: element:<solver>:<native> -->` per row group and include them in `wanted`.

### ADR-E-5 (low) Decision 2 still says the knowledge pack indexes `docs/help/`

- ADR 0083 decision 2: "The agent: the knowledge pack (ADR 0022) indexes `docs/help/`";
  decision 1 was amended on 2026-11-15 to `src/puzzle/help/`. `docs/help/` does not
  exist. Fix: amend the one path.

Unbuilt and stated as such by the ADR (not findings): the tutorials (decision 4,
`puzzle-examples`, Help → Examples), where the site is published. Both are on ADR 0072
(section 9, lines 493-497).

---

## ADR 0024: application settings

**Verdict: does not hold on decision 3 ("preferences never change results"); the rest
holds.** The file, the dialog, the CLI and the unknown-key rule work; two operator paths
let a per-user preference change computed values, one of them a model parameter.

Verified:

- `src/puzzle/settings.py`: `PREFERENCES` (82 keys in 11 groups, listed by snippet
  `adr-early/prefs.py`), `Settings` over `<user data dir>/settings.toml`.
- Unknown keys kept (reproduced): with `PUZZLE_USER_DIR` at a scratch folder holding a
  `settings.toml` with `future_key = 5` and `[newgroup] thing = "x"`, `puzzle settings
  meshing.preview live` rewrote the file with both unknown entries intact.
- `puzzle settings` lists and sets, and refuses a value not offered ("Mesh preview is
  one of: live, on_demand").
- Meshing and solver preferences are in use: `meshing.preview`
  (`gui/mesh_bench.py:366`), `meshing.netgen_optimiser` (`gui/choices.py:185`),
  `meshing.element_types` (`mesh/generate.py:1155`, a new recipe's default only),
  `solvers.level` (`gui/solver_settings.py:75`); `settings.WAITING` is empty.
- Every non-GUI reader of `settings.current()` was read (contact_check, field_display,
  geometry/measure, jobs/machine, jobs/runtime, jobs/service, mesh/generate,
  operators/evaluate, operators/frames, operators/report, post/expand, post/legend,
  post/revolve, post/symbols, post/exports/cloud and wall, project/versions,
  solver_builds, commands/session). All but the two below only format text, choose a
  default, draw, or choose how a solver is run (threads, processes, runner).

### ADR-E-6 (high) The display-units preference changes a model parameter through the Round operator

- ADR 0024 decision 3: "Preferences never change results. Anything that changes what a
  solver computes stays in the project and its run key"; Context: "the project's results
  must not depend on them".
- Evidence: `operators/frames.py:459-477` (`round_values`) rounds each column "in their
  shown units": `_shown_factor` (:484-493) takes the column's converted unit, else
  `evaluate.display_unit(kind)`, which is the user's Settings > Units. The rounded
  values are stored back in SI and flow on. Round reads no results, so a chain
  Table → Round → To number is a pre-solve operator and therefore a parameter by name
  (`operators.parameter_values`, `operators/__init__.py:1745`), which history arguments,
  quantities (`Quantity.parameter`) and sweeps read, and which studies hash.
- Reproduced (`adr-early/test_round_pref.py`): a typed table `t [mm]` with 4.4 and 6.6,
  Round to 2 decimals, To number (max) named `thick`. `operators.is_pre` is True and
  `parameter_values(project)["thick"]` is **0.01 m with Display units = SI** and
  **0.0066 m with Display units = engineering (mm)**. The same project therefore builds a
  different model, with a different run key, for two users. (A typed table's header unit
  is not kept as the column's shown unit either: `Table.units` was `{}` after `t [mm]`,
  so even the column the user typed in mm is rounded in metres under SI.)
- Also affects post-solve chains: a result table rounded then compared in a Check gives
  a margin that depends on the viewer's units.
- Fix: build. Round must not read the preference: round in the column's own shown unit
  when it has one (keep a typed or file table's header unit in `Table.units`, and
  Convert units' choice), else in SI, or require the unit on the Round operator
  (`unit` field, stored in the project). Add a test that evaluates every operator type
  under two unit systems and two table layouts and asserts equal SI values (the
  repro scripts are a start).

### ADR-E-7 (high) The "Table layout" preference changes what a result table holds, and so what the operators after it compute

- ADR 0024 decision 3, same sentence.
- Evidence: `Operator.layout` defaults to `"setting"` (`model/schema.py:2874`);
  `operators/frames.py:217-222` then reads `current().get("results.table_layout")` and
  pivots to a column per step when it is "wide". The table is not a display: it is the
  input of Filter rows, Group, Top n, To number, Check, Export and the Report.
- Reproduced (`adr-early/test_layout_pref.py`, on `tests/test_operators.py`'s
  hand-made three-step store): a `result_table` of von Mises over all steps, left at its
  default layout, has columns `node, x, y, z, part, step, time, von_mises` with the
  preference at "long" and `node, x, y, z, part, von_mises · step 1..3` at "wide"; the
  Filter rows after it (`von_mises > 150 MPa`) gives 4 rows under "long" and, under
  "wide", `PuzzleError[not_found]: 'hot': no input, parameter or unit 'von_mises'`. A project built by one user fails or reads
  differently for another, and an agent's or CI's evaluation depends on whose
  `settings.toml` is present.
- Fix: build. Make the preference the default written into a new Result table
  (`layout` stored as long or wide when the operator is created, as
  `meshing.element_types` does for a recipe's order), drop `"setting"` as a stored value
  (migrate existing ones to "long"), and keep the preference out of `evaluate`.

### ADR-E-8 (low) The ADR's Built section is stale

- "The meshing and solver preferences are stored and shown greyed, saying which ADR
  brings them into use" and "Results' colour bars still show SI": both are now in use
  (above; `post/legend.py`, `post/quantities.py` draw in display units per CLAUDE.md).
  The dialog is under Project → Settings… (the ADR says File → Settings…), and the
  groups are now eleven (Interface, 3D mouse, Results, Versions, Connections, Schematic
  added). Status is still plain "Accepted". Fix: amend (a dated "since then" paragraph).

Not checked: the Settings dialog itself in a live window (pages generated from the
definitions, Restore defaults, the Agent page writing `agent.toml`); `tests/test_settings.py`
was not run.

---

## ADR 0019: mesh workbench, Gmsh's breadth, Netgen

**Verdict: holds with gaps.** Controls, typed settings, pass-through options, quality,
Netgen, the Mesh tab, the shoot-out and the agent tool exist and pass their tests; the
"conflicting sizes refused" safeguard is missing on the Netgen path.

Verified:

- Ran `pixi run -e dev pytest tests/test_mesh_controls.py -q`: 9 passed in 5.4 s (the
  `netgen` pixi env is installed here, so the three Netgen tests ran): size and
  refine-near, box and ball, transfinite count, the refusals
  (`test_refusals_say_why`: conflicting sizes on Gmsh, unknown Gmsh option, boundary
  layer on Gmsh, quality floor), quality report and `quality.json`, MCP
  `mesh_quality`, Netgen local sizes, boundary-layer node planes, Netgen's refusals.
- Ran `tests/test_gui_mesh_bench.py`: 3 passed (controls made/edited/deleted, quality
  and worst elements, mesher and optimiser choices).
- Code read: `mesh/generate.py:1283-1392` (`_sizes`: unknown selection refused :1295,
  different sizes refused :1309, transfinite off edges refused :1343, boundary layer on
  Gmsh refused :1383); `set_gmsh_option` refusing unknown names (:1225-1236);
  `_netgen_controls` (:1489) refusing refine-near, transfinite, box and ball.
- Reproduced: hexahedra on Netgen refused ("Netgen meshes tetrahedra (and
  boundary-layer prisms); use Gmsh for hexahedra").
- MCP `mesh_quality` (`agent/mcp.py:832`), `mesh_shootout` (:892); CLI
  `mesh-shootout` (`cli.py:359`); skill `agent/skills/meshing.md`; 0019 in
  `WITH_SKILLS` and `WITH_HELP` (guide `guides/meshing.md`).

### ADR-E-9 (high, Netgen path only) Two size controls giving one entity different sizes are meshed silently on Netgen; the last one wins

- ADR 0019 Built: "Refused, never skipped: ... two size controls giving one entity
  different sizes (both named)"; Review notes: "Conflicting local sizes stop the mesh
  with a reason, rather than being changed silently."
- Evidence: the check lives in `_sizes` (`mesh/generate.py:1305-1315`), which the Netgen
  branch never calls (`generate.py:292-300` calls `_netgen_controls` instead).
  `_netgen_controls` (:1489-1536) appends one `{dim, centre, measure, maxh}` per control
  and entity, and `netgen_run.py:67` assigns `hit.maxh = s["maxh"]` for each in turn.
- Reproduced (`adr-early/test_netgen_conflict.py`, using
  `tests/test_mesh_controls._block`): size controls of 1 mm and 2 mm both on `K/End`
  raise `MeshError("... different sizes")` on Gmsh; with `mesher="netgen"` both orders
  mesh with no error and no warning, mean edge length at the face 2.50 mm with (1, 2)
  and 1.38 mm with (2, 1). The mesh therefore depends on the order of the controls.
- Fix: build. Move the conflict check out of `_sizes` into one function both paths call
  before meshing (it needs only `_group_entities` and the size controls), and add the
  Netgen case to `test_refusals_say_why`.

### ADR-E-10 (low) The ADR's text contradicts its own status

- The Decision heading still reads "Decision (proposed)"; the Built section ends "Not
  yet: sweeping, and a recipe mode that meshes block-like bodies with hexahedra and the
  rest with tetrahedra together" and says "Sweeping isn't offered", while the status
  line and CLAUDE.md say swept prisms, revolved profiles and mixed hex-tet recipes were
  built on 2026-11-14 (ADR 0072 section 5). Fix: amend (strike "Not yet", add a dated
  note pointing at 0072 section 5).

To check (not verified): `MeshRecipe.netgen_options` is passed unvalidated into
`geo.GenerateMesh(**params)` (`netgen_run.py:78`), so (a) an unknown name is whatever
Netgen does with it (probably a failed run with Netgen's message, not a readable
refusal as for Gmsh options), (b) `maxh` there overrides the recipe's global size, and
(c) `quad_dominated`, which the field's description offers, yields surface quads on a
tetrahedral volume mesh; whether the reader and the adapters accept that was not run.
Not checked: the mesh shoot-out (`tests/integration/test_mesh_shootout.py` needs
CalculiX runs), the LE1-on-Netgen figure, the Gmsh options table in the GUI, edge seeds
and method detection in a live window.

---

## ADR 0022: local expert model by measurement

**Verdict: holds with gaps.** Pick, the suite, the knowledge pack, re-ranking and
provenance exist and are tested; the provenance's suite note goes stale, and two
Decision sentences are stronger than the build.

Verified:

- Ran `pixi run -e dev pytest tests/test_local_model.py -q`: 20 passed in 8.8 s (stand-in Ollama
  endpoint; fitting, ranking, kept results, endpoint failures, cancel, embedder
  re-ranking, provenance, CLI/MCP/GUI paths).
- Snippet (`adr-early/ev.py`): `SUITE_VERSION` 2026.11.1; eight `FAMILIES`;
  `CORE_FAMILIES` read, edit, batch, recover, refuse; 15 tasks (8 `TASKS` + 7
  `HARD_TASKS`). Knowledge pack: 1975 passages (skills 236, notes 80, help 1020,
  capabilities 8, translations 1, ADRs 630).
- `agent/pick.py`: `CONTEXT = 1.3`, `HEADROOM = 6 GiB` (:39-40), `budget`, `fit`,
  `rank`, `recommend` (smallest at the best score, unknown memory last, :297),
  `choose` writing `picked` (:344-354). `agent/knowledge.py`: BM25, `ollama_embedder`
  (`/api/embed`, :263), re-rank inside `search` falling back to words when no embedder
  answers (:173-179). MCP `knowledge` (`agent/mcp.py:1104`). `Actor.model`/`suite`
  (`commands/session.py:60-71`). Skill `local-models.md`; 0022 in `WITH_SKILLS` and
  `WITH_HELP`.

### ADR-E-11 (medium) The suite recorded in history stays after the model is changed, so entries name a suite the model was never measured by

- ADR 0022 decision 5 and Built "Provenance": "It adds the model and the suite version,
  so a project shows which model made which change"; "The built-in agent fills them
  from its provider and the `picked` setting".
- Evidence: `picked` is written only by `pick.choose` (`agent/pick.py:353`) and never
  cleared. `Agent.__init__` (`agent/agent.py:99-103`) puts its suite part on every
  `Actor` whatever model is running. Changing the model in Settings > Agent
  (`gui/settings_dialog.py:559-562`), by the agent window's model chip
  (`agent/settings.choose_model`) or by editing `agent.toml` leaves it.
- Reproduced at the settings level (`adr-early/picked.py`, scratch user folder):
  `pick.choose("qwen3:8b", ...)` then provider/model set to anthropic /
  claude-sonnet-5-5 and saved; `load_settings().picked` is still
  `'<date> suite 2026.11.1'`. Every later history entry would read model
  claude-sonnet-5-5, suite 2026.11.1: a measurement that never happened. Not run
  through a live agent turn.
- Fix: build. Store the picked model with the note (`picked = {model, date, suite}`)
  and fill `Actor.suite` only when the running provider and model are the picked ones
  (or have a kept record in `agent-evals/` for this suite: `pick.kept_score` already
  reads those); clear it in `choose_model` and the Settings dialog otherwise.

### ADR-E-12 (low) "Every family has graded tasks (easy, hard)" is true of four of the eight families

- ADR 0022 decision 2. Snippet: read {easy}, batch {hard}, sketch {easy}, mesh {easy};
  edit, recover, interpret, refuse have both. Two core families (read, batch) have one
  task each, so one flaky task decides a core family. The Built text ("Every task has a
  family and a difficulty") is accurate. Fix: add the missing grade to each family
  (bumping `SUITE_VERSION`), or amend the sentence.

### ADR-E-13 (low) Decision 3's "an embedding model indexes it" and "English summaries of French manual excerpts" are not what was built

- Built: BM25 is the index; an embedder only re-ranks the top lexical hits, and only
  when `embed_model` is set (off by default). The French content is the progress
  translator's message table (1 passage); the ADR's own "Not done" list says so for the
  summaries. 35 of 80 note passages carry no link (decision: "Each source carries a link
  to the manual it summarises"; the Built text narrows it to "where one applies").
  Neither is on ADR 0072's list. Fix: amend decision 3 to match (words first, meaning as
  an optional re-rank), and put the manual summaries on 0072 or drop them.

Not checked: a real endpoint (no Ollama here), the Pick dialog in a live window,
`puzzle agent pick --remote` (ADR 0084), the quality of retrieval beyond one query
("why does Code_Aster contact fail to converge" returned three skills: solver-settings,
contact-tool, solve-progress).

---

## ADR 0014: geometry as a parametric history, robust naming, bulk reattachment

**Verdict: holds with gaps.** The history, the preparation operations, naming by faces,
anchors, rules and one-review reattachment exist and pass their tests. Two things are
weaker than written: the anchor tolerance is about 100 times looser in position than
the ADR states, and a detached member only warns, so a solve goes ahead on a selection
with fewer faces.

Verified:

- Ran `pixi run -e dev pytest tests/test_naming.py tests/test_selection_naming.py
  tests/test_geometry_ops.py tests/test_sketch.py tests/test_split_face.py -q`:
  49 passed in 5.2 s.
- On a copy of `examples/cantilever` (`adr-early/cant`): `puzzle check-geometry` →
  "Bar: no problems"; `puzzle selections` → Body, Root, Tip each "kept".
- Operations in `geometry/history.py`'s table (:17-58): every kind decision 1 and 2 list
  (import, primitives, sketch → extrude/revolve/sweep, booleans by `mode`, fillet,
  chamfer, move, rotate, pattern, heal, remove, split, split_face, imprint, fragment,
  enclosure).
- Clients: CLI `selections --accept-all` (`cli.py:456`, :1401), MCP `geometry_check`
  (`agent/mcp.py:168`), `selections_check` returning an `accept_all` command (:183-200),
  `sketch_check` (:914); GUI `gui/reattach.py`.
- Unbuilt items are all on ADR 0072 section 5 (lines 287-289): FreeCAD `.FCStd`
  importer, mid-surface extraction, direct face moves and pulls, faceted parts placed
  or with other parts (refused at `mesh/generate.py:122-126`).

### ADR-E-14 (medium) A detached selection member only warns; meshing and solving go on with the faces that are left

- ADR 0014 decision 3.3: "Faces neither tier resolves are flagged as detached, never
  silently guessed"; decision 4 and Consequences: detachment is repaired in "one
  review". The flag exists, but nothing stops a run that uses the selection.
- Evidence: `mesh/generate.py:466-473` (`_source`) appends
  `"selection '<name>': '<member>' is detached"` to `MeshResult.warnings` and builds
  the group from the members that resolved. The warning is printed by `puzzle run`
  (`cli.py:827`) and returned in the run command's data (`commands/session.py:847`);
  it is not a `Problem`, is not stored with the run, and a cached reuse prints nothing.
  The GUI lists detached members in Problems (`gui/window.py:1049`), which informs but
  does not refuse.
- Reproduced at the mesh level (`adr-early/test_detached.py`): a face selection
  `["B/+x", "Gone/wall"]` meshes, with one warning and a group of the 28 nodes of
  `B/+x` only; a selection of `["Gone/wall"]` alone meshes too, with the warning and no
  group at all. A pressure or a fixed support on the first would be applied to part of
  what the user chose, and the result looks normal. Not run through a solver (what an
  adapter does with the missing group in the second case was not checked).
- Fix: build. Make a detached member of a selection that a feature, contact, mesh
  control, bolt, weld or transfer uses a problem that refuses the run (a code such as
  `detached_selection`, as `unconstrained` refuses), naming the selection and "Reattach
  selections…"; keep the warning for selections nothing uses. Add the test above with
  `run_study`.

### ADR-E-15 (medium) Anchors re-find within 0.1 % of the model size in position, not 1e-5 as stated

- ADR 0014 G3: "A name that is gone is re-found when exactly one entity of the same kind
  lies within 0.1% in measure and 1e-5 of the model size in position. Names that only
  count positions (`face-7` of an imported file) are checked against their anchor".
- Evidence: `geometry/naming.py:203-214`: `distance` returns the relative measure
  change **plus** the centre shift over the model size, and the one threshold is
  `SAME = 1e-3 + 1e-5` on that sum (:350, :365). With the measure unchanged, a centre up
  to 1.01e-3 of the model size away passes: 1 mm on a 1 m model, where the ADR says
  0.01 mm. "Exactly one" is implemented as "the next candidate is more than ten times
  farther" (:364). The same threshold is the check on positional names, so a replaced
  file whose `face-7` is another face of equal area within 0.1 % of the model size is
  "kept".
- Read only (arithmetic on the code; the ADR's measured cases, a 10 mm move detached and
  a renumbered file re-found, still hold under either reading).
- Fix: decide which is meant. If the ADR: test the two parts separately
  (`dm <= 1e-3 and shift <= 1e-5`). If the code (a hole nudged 0.05 % follows its
  selection, which Tier 2's "hole moved 0.5 mm: every face re-found" suggests was
  wanted): amend G3's sentence, and keep the tighter test for positional names.

Not checked: `tests/integration/test_history_geometry.py` (solves), the GUI's reattach
dialog in a live window, DXF profiles, heal/remove on real CAD, faceted files.

---

## ADR 0017: the geometry workbench

**Verdict: holds.** Verified:

- Ran `pixi run -e dev pytest tests/test_gui_workbench.py -q`: 4 passed in 10.9 s (a
  part sketched and extruded, a fillet on picked edges, holes on a picked face, history
  ticks and double-click).
- `gui/workbench.py`: ribbon groups Sketch, Create, Modify, Prepare, Selections (and
  Inspect, added since) at :39-71 and :262-294; a 350 ms single-shot timer (:141);
  `geometry.preview` live or on demand (:125); `ToolPanel.apply` is one
  `menus.apply_step` command (:207-218); Cancel is `close_tool`, which drops the
  pending history (`window.preview_history(None, None)`, :512-519).
- "A preview cannot write the project": `MainWindow.pending` (`gui/window.py:394`,
  :2153) is applied to `copy.deepcopy(session.project)` inside the preview job
  (:3556-3567); no `Session` call is on that path. The preview's mesh files go to the
  project's `.puzzle/gui-preview/` work folder, which is not model state.
- Sketch steps: `history.py:58` (kind `sketch`), extrude and revolve take
  `{"sketch": "<name>"}`.
- Sketching on planes not along an axis is still refused (`history.py:322`), as the
  ADR's Consequences say; it is on ADR 0072 (line 288).

No findings. Gap in the tests rather than the build: no test asserts that the project's
files and `history.jsonl` are byte-identical after a previewed tool is cancelled; the
guarantee rests on the code path above.

Not checked: the workbench in a live (non-offscreen) window; "Pick in view" by real
mouse events.

---

## ADR 0003: Python core, PySide6 GUI, VTK viewport

**Verdict: holds with gaps.** Python 3.11+ (`pyproject.toml:13`), Pydantic schema,
PySide6 GUI with off-screen VTK painted as an image (`gui/viewport.py` `SceneView`) are
as stated. The layering rule is broken in fourteen places, three of which need Qt.

### ADR-E-16 (medium) The core imports the GUI; three core paths fail without PySide6

- ADR 0003 Consequences: "GUI code stays separate from the core (`puzzle.gui` imports
  core; never the reverse), so the core can run headless and from scripts."
  `pyproject.toml` makes PySide6 optional (`gui` extra).
- Evidence (grep for `puzzle.gui` imports outside `src/puzzle/gui/`):
  module-level: `post/legend.py:17`, `post/symbols.py:26`, `schematic/__init__.py:29`,
  `schematic/svg.py:8` (all `gui.theme`); in functions: `advisor.py:205,248`,
  `thread_setup.py:185`, `post/symbols.py:145` (`gui.choices`),
  `post/exports/cloud.py:57` and `post/exports/wall.py:320` (`gui.viewport._display`),
  `help/reference.py:286` (`gui.form.DESCRIPTIONS`), `help/site.py:88`
  (`gui.agent_view`), `cli.py:931` (`gui.solution_panel.save_png`).
  `gui/theme.py` and `gui/choices.py` import no Qt, so those work headless;
  `gui/viewport.py`, `gui/form.py` and `gui/agent_view.py` import PySide6 at the top.
- Reproduced (`adr-early/noqt.py`, with `sys.modules["PySide6"] = None`): the core
  modules import, but `import puzzle.gui.viewport`, `puzzle.gui.form` and
  `puzzle.gui.agent_view` raise `ModuleNotFoundError`, and so do
  `help.reference.fields()` and `help.reference.stale()`. So without the `gui` extra:
  a point-cloud or wall export with `units="display"` (CLI `export`, MCP `export`), the
  help reference build and its staleness check, the static site build and
  `puzzle progress --plot` end in an import error, not a `PuzzleError`. The exports
  were not run end to end (they need a stored run).
- Fix: build. Move what the core needs out of `puzzle.gui`: `theme` tokens the
  renderers use and `choices.label/solver_label` to a Qt-free package (e.g.
  `puzzle.labels`, `puzzle.look`), `viewport._display` to `puzzle.model.units` or
  `post/quantities.py`, `form.DESCRIPTIONS` to a data module, the TeX regexes and
  `segments` to `puzzle.help`. Add a test that imports every non-GUI module with
  PySide6 blocked and greps for `puzzle.gui` imports outside `gui/`.

### ADR-E-17 (low) "Not there yet" is stale

- The First implementation section ends "Not there yet: box selection, the schematic
  (ADR 0009), sweeps and shoot-out reports"; all four exist (ADRs 0034, 0058, 0018,
  0011). The Decision's "first through pyvistaqt" note is accurate. Fix: amend.

Not checked: performance paths in Rust/C++ (none exist; the ADR says "may"), a trame
front end (on ADR 0072 line 485).

---

## ADR 0006: solver-neutral model IR with a capability matrix

**Verdict: holds with gaps.** Verified: entry-point group `puzzle.adapters` with eight
adapters (`pyproject.toml:48-56`), read by `adapters/__init__.py:42` with the built-ins
as a fallback; `Capabilities` and `Adapter.check` (`adapters/base.py`), `planned_physics`
refused as planned (:277); quantities converted by `Quantity.to_si()`; extension blocks
are `Study.solver_settings`/`expert_settings` (ADR 0023); `puzzle schema` prints the
JSON schemas.

### ADR-E-18 (medium) The schema is "versioned" by a number that has never changed, so the newer-project guard cannot fire

- ADR 0006 decision: "The model is a typed, versioned schema (`puzzle.model`)".
- Evidence: `SCHEMA_VERSION = 1` (`model/schema.py:32`) since 2026-10-17
  (`git log -S`), through every schema addition of ADRs 0014-0085. `load_project`
  refuses only `schema_version > SCHEMA_VERSION` (`project/store.py:80-84`). Every
  model is `extra="forbid"`.
- Reproduced: on a copy of the cantilever (`adr-early/cant2`), one unknown key added to
  a study's TOML (as a newer Puzzle would write) makes `puzzle studies` end in a raw
  traceback, `pydantic_core.ValidationError: ... Extra inputs are not permitted`, not
  "project schema N is newer than this Puzzle" and not a `PuzzleError`. With installers
  and project archives (ADRs 0061, 0068) two builds will meet one project.
- Not on ADR 0072's list.
- Fix: build. Bump `SCHEMA_VERSION` whenever a field or kind is added (a test can pin
  a hash of `puzzle schema`'s output to the version, failing when one moves without
  the other), write it into `archive.json` too, and wrap validation errors on load in a
  `PuzzleError` that names the file and says whether the project is newer.

Not checked: a third-party adapter registered through the entry point (no test does
this; `BUILT_IN` covers the shipped eight); whether the GUI disables every menu item a
solver lacks (`menus._solver_fits` read only in passing).

---

## ADR 0009: one model tree, references, transfers

**Verdict: holds with gaps.** Verified:

- Snippet (`adr-early/hash.py`) on a copy of the cantilever: renaming every study,
  material, geometry and component leaves the study's content hash and run keys
  unchanged (rule 3, "Renaming changes nothing"); a transfer from a study to itself is
  refused ("transfers form a cycle", `project/graph.py:139`; rule 2).
- One TOML file per object under a folder per kind (`examples/cantilever/`,
  `project/store.py:88-90`); geometry `scope` global/local with an owner
  (`model/schema.py:203-250`); `Project.copy_component` copies local geometry
  (:3352-3362), command `CopyComponent` (`commands/model.py:63`); `source_point`
  required for parameters the target does not sweep (:3625-3631).

### ADR-E-19 (medium) A stale study is not told which input changed

- ADR 0009 rule 3: "A study is stale when its hash differs, and the UI names the changed
  input"; Consequences: "Staleness is precise and explained, down to individual sweep
  points"; Context cause 5 is the thing being designed out ("with no reason given").
- Evidence: `project/status.py:147,160` give the one reason "the model changed since
  the last run" / "since this run". `project/graph.py` has `content_hashes`,
  `run_hashes`, `stale_studies` and `downstream`, and nothing that compares a run's
  recorded input hashes with the current ones; a grep for a reason
  (`changed_inputs`, `what_changed`, `why_stale`, `stale_reason`) finds nothing.
- Read only. Not on ADR 0072's list.
- Fix: build. Store each run's per-input hashes (the run record already holds the run
  key; add `{object id: content hash}` of its inputs), and have `status` diff them:
  "out of date: material Steel and mesh recipe M changed". The CLI `studies`, MCP
  `outline` and the tree's tooltip then say it.

### ADR-E-20 (low) Rule 1 and the title were overtaken without a note

- "edges are transfers only" (title, table, rule 1): the schematic (ADRs 0058, 0062,
  0071) now has data wires from imported fields and runs, use wires, operator wires
  and drawable reference links. "Open: single-file export for sharing projects" is
  ADR 0061's archive. "Selecting a resource highlights its users on demand" became
  `schematic.uses`. Same class as the already-reported overtaken ADRs, not in that
  list. Fix: amend with a dated pointer to 0058/0061/0071.

Not checked: point-to-point transfers between two swept studies, grid versus paired
sweeps, staleness per sweep point (code read only at the level above).

---

## ADR 0005: a separate meshing layer, Gmsh first

**Verdict: holds with gaps.** Verified: `puzzle.mesh` with a recipe (global size,
controls, order, shape), Gmsh in process, Netgen second (ADR 0019 above), MMG for
adaptation (`mesh/mmg.py`, pixi `mmg` env installed here), groups named
`Part/Selection` (seen in `tests/test_mesh_controls.py`: `K/End`, `K/Edge`), SI
throughout, `Contact.meshing` conformal or nonconformal with `effective_meshing`
(`model/schema.py:815-875`), `Geometry.share_faces` on by default (:217) with
overlapping bodies refused (`mesh/generate.py:797-799`).

### ADR-E-21 (medium) Boundary layers for CFD: the recipe item exists, but no CFD run has ever used one, and Gmsh cannot make them

- ADR 0005 decision: the recipe includes "boundary layers", and "CFD meshing for
  Code_Saturne uses Gmsh with boundary layers, exported as MED."
- Evidence: a `boundary_layer` control on Gmsh is refused (`mesh/generate.py:1383-1388`,
  "boundary layers are meshed by Netgen"); Netgen grows prisms
  (`test_netgen_boundary_layers`, checked on node planes only). The only tests naming
  `BoundaryLayerControl` are in `tests/test_mesh_controls.py`; no CFD test or benchmark
  meshes with one (the channel benchmarks use uniform hexahedra, and their wall
  functions are benchmarked at Re 1e5 for that reason). The CFD adapters list prisms
  (`openfoam/case.py:80` accepts `wedge6`; Code_Saturne's reader knows `penta6`), but
  that path is unexercised. Not on ADR 0072's list (searched "boundary layer",
  "inflation").
- Read only; no CFD solve was run.
- Fix: build the check first: one test per CFD solver on a Netgen recipe with a
  boundary layer on the channel's walls (first-order tets and prisms), compared with
  the laminar channel's closed form. Then either claim it or refuse prisms in the CFD
  adapters. Amend the ADR's sentence to "Netgen boundary layers" either way.

### ADR-E-22 (low) Elmer's mortar tie is still described as the nonconformal route

- ADR 0005: "the solver ties them (CalculiX `*TIE`, Elmer mortar tie contact)"; ADR
  0010's table: "bonded, nonconformal | Mortar BC with `Contact Type = tie` |
  Supported". The adapter claims `bonded_conformal` only and refuses the rest
  (`adapters/elmer/__init__.py:101`); ADR 0072 (lines 672-673) records that the claim
  was removed from the code and CLAUDE.md on 2026-11-09 and keeps the tie as open work,
  but the two ADRs' text was not touched. Fix: amend both ("not written yet, see 0072").

---

## ADR 0010: MVP solvers CalculiX and Elmer

**Verdict: holds with gaps, all known.** Both adapters exist and are benchmarked
(CLAUDE.md's adapter table; not re-run here). Of Phase 1's Elmer physics,
electrostatics, magnetostatics, time-harmonic and 2D axisymmetric magnetics are
written; electric currents (StatCurrentSolve) and waves are `planned_physics`
(`adapters/elmer/__init__.py:67-70`, refused as planned by `base.py:277`); the
experimental contact types have a flag (:102) and no writer. Each of these is on ADR
0072 section 3 (lines 161-165, 227). The stale mortar-tie row is ADR-E-22. No new
finding beyond that. Not checked: any Elmer run (container; none started).

## ADR 0012: solver line-up

**Verdict: holds (as overtaken; already reported).** Nothing new: the five named
adapters exist with capability matrices, the plug-in entry point exists (ADR 0006
above), and the three added since (OpenFOAM, PyNite, OpenCourant) are the earlier
reviewer's finding. "FEniCSx carries two jobs" is taken from CLAUDE.md (custom PDEs and
EM), not re-run.

---

## ADR 0013: pixi environments, native first, containers for the gaps

**Verdict: holds with gaps, known ones.** Verified:

- `pixi.toml`: five platforms including linux-aarch64 (:21-27); one environment per
  native solver, `no-default-feature` (`calculix`, `calculix-pardiso`, `fenicsx`,
  `code-aster`, plus `netgen`, `mmg`, `pynite`, `scripts`, `runner`, `packaging`;
  :212-227). Installed here: calculix, calculix-pardiso, code-aster, dev, fenicsx, mmg,
  netgen, packaging, pynite, scripts.
- Runtime per solver: `PUZZLE_<SOLVER>_RUNTIME` then `PUZZLE_RUNTIME`, auto = native
  when the executable is found, else a container whose image an engine has, podman
  before docker (`jobs/runtime.py:435-471`, `ENGINES` :79); each run's runtime is a
  column of the runs table (`results/store.py:57`).
- `packaging/conda-forge/elmerfem` holds the recipe (not submitted: ADR 0072 line 154);
  the arm64 Code_Aster image is on ADR 0072 (line 110; `containers/code_aster/Dockerfile`
  is `--platform=linux/amd64`).

### ADR-E-23 (low) "native when the solver is installed" no longer describes Windows or OpenCourant

- ADR 0013 decision: "Runtime chosen per solver ... native when the solver is installed,
  else a container"; title "native solvers first". `CONTAINER_FIRST`
  (`jobs/runtime.py:57-61`) takes the image first for FEniCSx, Elmer and Code_Aster on
  Windows and for OpenCourant everywhere, and a WSL machine is a third runtime (the ADR
  has it as "possible"). The reasons are measured and recorded in CLAUDE.md. Sibling of
  the already-reported ADR 0007 drift. Fix: amend with a dated note.

Not checked: any platform but this Windows machine; that linux-aarch64 actually solves
(`pixi.lock` not inspected per platform); CI (the earlier reviewer's finding).

---

## ADR 0016: OpenFOAM as the second CFD solver

**Verdict: holds with gaps.** Verified by reading: adapter `adapters/openfoam/`
(`Allrun` with gmshToFoam and foamDictionary, SIMPLEC `consistent` at `case.py:479`),
image `ghcr.io/acmeanvil/puzzle-openfoam:2412` (`jobs/runtime.py:34`,
`containers/openfoam/Dockerfile:8`), shoot-out test
`tests/integration/test_cfd_shootout.py` and the three channel benchmarks exist (not
run: Docker solves). OpenFOAM's missing convection walls, transient runs and mass-flow
inlets are in the ADR's own Consequences and on ADR 0072 (line 169).

### ADR-E-24 (medium) No native Linux path for OpenFOAM is provided

- ADR 0016 decision 1: "It runs natively from conda-forge on Linux, and in
  `containers/openfoam` ... elsewhere"; Alternatives: the Foundation line was rejected
  because it gave "no native Linux path through pixi".
- Evidence: `pixi.toml` has no `openfoam` feature or environment (:212-227; a
  case-insensitive grep finds the word nowhere in the file), so
  `jobs.runtime.pixi_solver_dirs` has nothing to find; a native run happens only if the
  user installs OpenFOAM themselves and `simpleFoam` is on PATH (`EXECUTABLES`,
  `jobs/runtime.py:74`). ADR 0072 lists native OpenFOAM for macOS and Windows only
  (line 153), not Linux.
- Read only (no Linux machine here; whether `Allrun` works natively, with its
  `etc/bashrc` sourcing, was not run).
- Fix: build (an `openfoam` pixi feature for linux-64 with the conda-forge package
  pinned to v2412, and a Linux CI job running the laminar channel natively), or amend
  decision 1 to "container everywhere" and add native Linux to 0072 line 153.

---

## ADR 0002: GPL-3.0-or-later

**Verdict: holds.** `LICENSE` is GPL version 3; `pyproject.toml:10`
`license = "GPL-3.0-or-later"`. Solvers and the other-licensed tools run as separate
processes or in their own environments (Netgen, MMG, PyNite, PSI/J, FEniCSx's
`study.py`, OpenCourant's AGPL binaries). In-process additions since the ADR's table
(polars, xlsxwriter, manifold3d, py-slvs, ParaView/VTK, hidapi, keyring, anthropic,
mcp, markdown-it-py, matplotlib) are permissive, LGPL or GPL-3 as far as I know them;
this was not audited against `pixi.lock` or each package's licence file.

### ADR-E-25 (low) The dependency table is the 2026-10-17 one

- The Context table lists nine dependencies and five solvers; it omits OpenFOAM,
  OpenCourant (AGPL-3.0, with its own note in ADR 0085), PyNite, Netgen (LGPL-2.1, out
  of process for a technical reason), MMG, py-slvs (GPL-3, in process) and MKL
  (proprietary; ADR 0051 explains why the PARDISO build is made on the user's machine
  and never shipped). Fix: amend the table, since it is the place a packager looks.

---

## Decisions unbuilt and not on ADR 0072's list

| ADR | Decision | Finding |
| --- | --- | --- |
| 0083 | Links in the help that open the tool they describe | ADR-E-1 |
| 0083 | F1 on a menu item; F1 on an operator in the schematic | ADR-E-2 |
| 0083 | A test that fails on a stale picture | ADR-E-3 |
| 0022 | An embedding model indexes the pack; English summaries of French manual sections; a link on every note | ADR-E-13 |
| 0022 | Every family graded easy and hard | ADR-E-12 |
| 0009 | The UI names the input that made a study stale | ADR-E-19 |
| 0006 | A schema version that moves with the schema | ADR-E-18 |
| 0005 | Boundary-layer meshes for CFD | ADR-E-21 |
| 0016 | OpenFOAM native on Linux through pixi | ADR-E-24 |

On 0072 already (so not findings): FreeCAD importer, mid-surfaces, direct face moves,
sketches on any plane, faceted parts in assemblies (0014/0017); Elmer's static
currents, waves, circuits, experimental contact, mortar tie (0010); OpenFOAM transient,
convection, mass-flow inlets (0016); arm64 Code_Aster image, elmerfem on conda-forge
(0013); a trame front end (0003); tutorials and where the site is published (0083).

## To check (suspicions, not verified)

1. `MeshRecipe.netgen_options` is unvalidated pass-through to `GenerateMesh(**params)`
   (`mesh/netgen_run.py:78`): an unknown name, a `maxh` that overrides the recipe's
   size, and `quad_dominated` (offered in the field's description) on a tetrahedral
   volume mesh. ADR 0019 promises unknown Gmsh names are refused; nothing is said or
   tested for Netgen's.
2. ADR 0014: what each adapter does when a feature's selection lost every member (the
   mesh has no such group; ADR-E-14's second case): refuse with a readable message, or
   drop the load.
3. ADR 0024 / ADR-E-6: other operators that read the display units while producing
   values rather than text. As far as read, `frames._shown_factor` is used by Round
   only, and `operators/report.py:56` and `evaluate._text` produce text. The Export
   operator writes files in display units (headers carry the unit), so an exported file
   differs by user; whether a `file` table reads one back correctly was not run.
4. ADR 0022 / ADR-E-11: whether the remote pick (`pick_remote`, ADR 0084) writes
   `picked` at all.
5. ADR 0083: `window.actions_by_name` holds only part of the menu bar (seen at
   `main_menu.py:990, 1027, 1076`), so `puzzle:` links could not reach most menu
   locations even if pages used them; not enumerated.
6. ADR 0006: no test registers a third-party adapter through the entry point.
7. ADR 0003 / ADR-E-16: the pip CI job (no optional packages) may skip the affected
   tests through `conftest.OPTIONAL`, which would hide the import errors;
   `puzzle progress --plot` without Qt was read (`cli.py:931` imports
   `gui.solution_panel`, which imports PySide6 at the top), not run.

## What was not checked at all

- No solver was run: every `tests/integration/` benchmark the ADRs cite (LE1 on Netgen,
  the mesh shoot-out, CFD channels and shoot-out, history geometry end to end, Elmer
  and Code_Aster cases) is taken from the ADRs and CLAUDE.md, not re-measured.
- No live GUI window: all GUI evidence is the offscreen tests named above and code
  reading. No real mouse picking, no F1 keypress, no Settings dialog.
- No local model endpoint, no remote provider, no Docker, no Linux or macOS.
- The static site and PDF were built only inside `tests/test_help_site.py` (passed);
  the output was not opened or read.
- `tests/test_settings.py`, `tests/test_gui_geometry.py`, `tests/test_gui_sketch.py`
  and `tests/test_mesh_extras.py` were not run.
