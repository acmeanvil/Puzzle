# Puzzle GUI core: bug hunt

Reviewer scope: `src/puzzle/gui/` window.py, tree.py, properties.py, choices.py, menus.py, main_menu.py, viewport.py, workbench.py, sketcher.py, mesh_bench.py, step_dialog.py, results_ribbon.py, deleting.py, organise.py, docking.py, feedback.py, quick_form.py, form.py, timing.py, app.py.

Nothing in the repository was changed. All scripts and project copies are under
`<scratch>\review\core\` (and `...\review\viewport\` for the viewport checks).

## How this was done, and what it does not cover

- **Run offscreen (Qt `offscreen`, a copy of `examples/cantilever`, scratch `PUZZLE_USER_DIR`):**
  - `fuzz.py empty`: every menu bar action triggered with no project open.
  - `fuzz.py none`: every menu bar action with a project open and nothing selected.
  - `fuzz2.py add` / `delete`: every tree right-click action on each of the 36 tree items; deletes each followed by Undo.
  - In all three, every dialog was answered Cancel (or Yes to a delete question), so **accepted forms were not exercised**.
  - `t1.py`–`t6.py`: targeted scenarios (reload on own edits, tool panels going stale, typed values, a run against a project switch and a window close, the Delete key, a broken file on reload).
- **viewport.py** was read in full and tested by a delegated reviewer (offscreen and on the native Windows platform at devicePixelRatio 1.5). I checked the cited lines against the code but did not rerun those scripts. Those findings are GUI-11 to GUI-18.
- **Not covered:** two further delegated reviews (workbench/sketcher/step_dialog/mesh_bench, and menus/main_menu/deleting/organise/quick_form/form) stopped on an API spend limit before reporting. Those files were only exercised by the fuzz runs and the targeted tests, not read line by line. `sketcher.py`, `mesh_bench.py`, `docking.py`, `organise.py`, `choices.py`, `tree.py`, `quick_form.py` and `form.py` have had no close reading. window.py was read in its core paths (open/refresh/reload, submit, background, meshing, runs, selection, preview, tool hosting), not all 4,275 lines.

## Counts

| Severity | Count | IDs |
| --- | --- | --- |
| Critical | 0 | |
| High | 8 | GUI-1 to GUI-6, GUI-11, GUI-13 |
| Medium | 8 | GUI-7 to GUI-10, GUI-12, GUI-14, GUI-15, GUI-17 |
| Low | 2 | GUI-16, GUI-18 |

---

## High

### GUI-1. The Delete key deletes the Model tree's selected object from any tab, with no question

- **Where:** `window.py:1147` (the `delete` action, `QKeySequence.StandardKey.Delete`, window-wide), `window.py:1543-1574` (`delete_selected`).
- **What is wrong:** the shortcut is a window shortcut, and `delete_selected` always acts on `self.tree.current()`, even when the Model tab is hidden and focus is in another list. A single non-run item is deleted without a question and without a status message.
- **Reproduce:**
  1. Open the cantilever example; select the study "Conduction" in the Model tree.
  2. Go to the Geometry tab, click a step in the part's history list.
  3. Press Delete.
  4. The step stays; the study "Conduction" is gone. Nothing on screen says so.
- **Reproduced:** yes, `t6.py`, with a real key press (`QTest.keyClick`) on the focused `QListWidget`: studies 3 → 2, steps unchanged, `ask_yes` never called, status bar empty.
- **Effect:** silent loss of a model object (Undo brings it back, if the user notices). The same holds for any focused widget that does not consume Delete: the Mesh tab's lists, docked tables, the schematic.
- **Fix:**
  - Give the action `Qt.ShortcutContext.WidgetWithChildrenShortcut` on the model tree (and let the history list, the schematic and tables own their Delete).
  - Or have `delete_selected` return unless the tree has focus and is visible.
  - Say what was deleted in the status bar for a single item too ("Deleted Conduction; Ctrl+Z undoes").
- **Test:** select a study, switch to the Geometry tab, focus `workbench.steps`, `QTest.keyClick(..., Key_Delete)`; assert the study still exists.

### GUI-2. A run that finishes after another project was opened raises `KeyError`; opening is not guarded while a job runs

- **Where:** `window.py:753-787` (`open`), `window.py:1791-1802` (`_after_run`), `window.py:2241-2246` (`_select_run`).
- **What is wrong:** `open()` neither refuses nor detaches running tasks, and does not reset `self.running`, `self.failed_studies` or `self.run_queue`. When the run ends, `_after_run` selects the run in the *new* project's store.
- **Reproduce:** start a run, then Project → Open… (or Save as new project…, which opens the copy) before it ends.
- **Reproduced:** yes, `t5.py` section R2. Traceback: `finish → done → _after_run → _select_run → store.get_run → KeyError: 'no run 8370816c…'`. Before that, the newly opened project showed `running = {study id}` with Mesh and Run greyed. The "Conduction finished / Show results" toast appears in the wrong project, and its button raises the same `KeyError`.
- **Related, by reading:** `_run_next` (`window.py:1692-1720`) continues a Run all queue against `self.session` of the new project; ids that do not resolve are skipped, so the rest of the old project's queue is silently dropped.
- **Fix:**
  - In `open()`: if `self.tasks` holds a locking task, ask (stop it, or keep the project open).
  - Stamp each task with the session root it was started for; in `done`/`failed` drop GUI follow-ups when `self.session.root` differs.
  - Reset `running`, `failed_studies`, `run_queue`, `_queue_cancel` in `open()`.
  - `_select_run` and `_show_run` should treat a missing run as "results no longer there" (status bar), not raise.
- **Test:** start a run with a slow fake adapter, `window.open(other)`, let it finish; assert no exception reached `sys.excepthook` and `window.running == set()`.

### GUI-3. A geometry tool whose reference field has nothing to choose never opens, and leaves the preview broken

- **Where:** `workbench.py:145` (`ToolPanel.__init__` → `preview()`), `workbench.py:174-182` (catches `ValueError` from `history()` only), `window.py:2151-2154` (`preview_history` sets `pending` first), `window.py:3567` (`HistoryStep(**step)`).
- **What is wrong:** with an empty dropdown (`feature` for Pattern, `part` for Section of a part) the form's value is `None`. `HistoryStep` rejects it with a pydantic `ValidationError` inside the constructor, after `window.pending` was already set. The panel is never shown, `workbench.tool` stays `None`, and `pending` is never cleared.
- **Reproduce:** open the cantilever example (one imported part, no history). Geometry → Modify → Pattern, or Geometry → 2D → Section of a part.
- **Reproduced:** yes, `fuzz.py none` and `t2.py` section A. Afterwards `window.pending is not None`, and `window._selected()` raises the same `ValidationError` on every redraw in the Geometry tab; `pending` survives switching to the Model tab. Opening and cancelling any other tool clears it.
- **Fix:**
  - In `_preview` (`window.py:3563-3568`) build the pending history inside `try/except ValidationError` and fall back to the part's own history, reporting the reason in the tool's error label.
  - In `ToolPanel.preview` validate the step before calling `preview_history`.
  - In `start_tool`, clear `pending` if the constructor fails.
  - An empty required dropdown should say why ("This part has no step to repeat yet") and disable Apply.
- **Test:** on a part with no history, `workbench.start_tool("pattern")`; assert a panel is shown with a message, `window.pending` is valid or None, and `window._selected()` does not raise.

### GUI-4. The Deform tool cannot be opened at all

- **Where:** `geometry/steps.py:120-122` (field `corners`, kind `"rule"`, default a list of eight triples), `step_dialog.py:336-338` and `203-209` (`RuleField._load` calls `rule.get`).
- **What is wrong:** a `"rule"` field builds a `RuleField`, which expects a dict; Deform's `corners` default is a list.
- **Reproduce:** Geometry → Modify → Deform, or right-click a geometry → Add step → Deform.
- **Reproduced:** yes, both paths (`fuzz.py none`, `fuzz2.py add`): `AttributeError: 'list' object has no attribute 'get'` at `step_dialog.py:209`. Nothing opens.
- **Fix:** give `corners` its own field kind (eight XYZ rows, or a JSON text field) in `steps.py` and a widget for it in `StepForm._widget`.
- **Test:** build a `StepForm`/`ToolPanel` for every kind in `geometry.steps` with default arguments; assert none raises. This one parametrised test would also have caught GUI-3.

### GUI-5. Every Geometry tab tool fails on an imported part with "[invalid] invalid geometry"

- **Where:** `workbench.py:382-384` (tool buttons enabled for any geometry), `menus.py:1289-1312` (`apply_step` appends to `history`), `model/schema.py:251-252` (a geometry has either a source file or a history), `commands/session.py:1029` and `window.py:3477-3483` (`report` drops `details["errors"]`).
- **What is wrong:** a part imported from a file has `source` set and no history, so appending a step is refused by the schema. The tools are offered anyway, and the only text the user gets is `[invalid] invalid geometry`.
- **Reproduce:** cantilever example → Geometry tab → Move (or Fillet, Chamfer, …) → Apply.
- **Reproduced:** yes, `t3.py` section A2: status bar `'[invalid] invalid geometry'`, the tool's own error label empty, the panel stays open.
- **The general defect:** `report()` shows `exc.message` and never the validation reasons in `exc.details["errors"]`. Typing `5 foo` into a mesh size gives `[invalid] invalid mesh` (reproduced, `t4.py` section E). The square-bracket code is an internal key shown to users.
- **Fix:**
  - For a source part, either convert on first tool use in one command (history = an `import` step of the same file, `source` cleared) or disable the tools with a tooltip offering that conversion.
  - In `report()`, append the first of `details["errors"]` in plain words and drop the `[code]` prefix from the status bar (keep it in the log).
- **Test:** on an imported part, apply a Move through `ToolPanel.apply`; assert either a step was added or the message names the reason. Separately: an invalid `Edit` through `window.submit` puts the validation reason in the status bar.

### GUI-6. With no project open, 206 menu bar actions raise `AttributeError`

- **Where:** `main_menu.py` throughout (e.g. `:166` `current_study`, `:595`, `:720`, `:811`), `window.py:1532-1541` (`undo`), `window.py:2323` (`new_derived`), `window.py:3943` (`PlotsPanel.refresh`), `results_ribbon.py:91`.
- **What is wrong:** on the start page `window.session` is `None`, but the whole menu bar is enabled and almost every action dereferences `window.session.project` or `.root`.
- **Reproduce:** start Puzzle with no project; press Ctrl+Z, or choose Model → Studies → New study…, Results → View → Plots, Geometry → Create → Box, and so on.
- **Reproduced:** yes, `fuzz.py empty`: 206 actions raised (`'NoneType' object has no attribute 'project'` / `'root'` / `'undo'`). Output in `core\empty.txt`.
- **Effect:** nothing happens for the user and a traceback goes to the log for each click.
- **Fix:** in `main_menu._add` (which already takes `free=True` for project-independent items) disable non-free actions while `window.session is None`, refreshed from `open()` and at start. Guard `undo`, `reload`-style toolbar actions the same way.
- **Test:** `MainWindow(None)`; walk the menu bar; for every enabled leaf action, trigger it with dialogs stubbed and assert nothing reaches `sys.excepthook`.

### GUI-11. A real double-click while picking clears the picks

- **Where:** `viewport.py:341-355` (`mouseReleaseEvent` → `on_click`), `424-436` (`mouseDoubleClickEvent`), `1311-1316` (`_click`: replace), `1383-1388` (`_clicked`: toggle).
- **What is wrong:** Qt delivers Press, Release, DblClick, Release. The first release replaces the picks with the face; the double-click then toggles that face off. Every plain double-click ends with nothing picked, including faces picked before.
- **Reproduce:** Pick, Faces, pick some faces, double-click another face. `edit_selection_in_view` tells the user to "double-click to add or take away"; doing so wipes the selection's members from the picks.
- **Reproduced:** by the delegated reviewer on the native platform (`viewport\native3.py`), sending the four mouse events to the real `SceneView`: handlers `[('click', …), ('double', …)]`, final `picked == []`. `QTest.mouseDClick` behaved differently in one case, so a test must send the four events itself.
- **Fix:** the click of a click-then-double-click sequence must not replace: defer the single-click replace by `QApplication.doubleClickInterval()` and cancel it on a double-click, or make the double-click a no-op toggle when the preceding click already changed that entity.
- **Test:** on a real view (`PUZZLE_RENDER_TESTS=1`), pick two faces, send Press/Release/DblClick/Release on a third; assert all three are picked.

### GUI-13. A result probe on a mirrored or repeated copy shows a value from somewhere else

- **Where:** `viewport.py:1785-1793` (`_probe_at`), and `_captured` at `1161-1189`.
- **What is wrong:** `_probe_at` maps a revolved view back with `to_section` but does nothing for a symmetric model drawn whole (ADR 0081, `post/expand.py`). A point on a copy lies outside the modelled part, and `post.probes.at_point` falls back to the nearest point of the modelled part.
- **Reproduce:** a model with a symmetry plane, Settings > Results > Symmetric models = whole; double-click the mirrored half of a result.
- **Reproduced:** by the delegated reviewer offscreen (`viewport\off.py`): one tet with temperatures 10/20/30/40 mirrored across x; a probe at node 2 gives 20.0, at its mirror image 10.0 (`distance` 2.0). The hover read-out reads the expanded grid and is right, so the two disagree at the same spot.
- **Fix:** keep the copy transforms in `result_state`; in `_probe_at` and `_captured` apply the inverse of the copy containing the point before `at_point`, and turn vector and tensor components by that copy's matrix. Failing that, refuse a probe whose returned `distance` exceeds a tolerance.
- **Test:** the `off.py` case, asserting the mirrored probe equals the source value.

---

## Medium

### GUI-7. Every edit made in the GUI reloads the whole project from disk 300 ms later

- **Where:** `window.py:708-711` (watcher on `history.jsonl` → `_reload_soon` → `reload`), `window.py:789-793`.
- **What is wrong:** the watcher cannot tell the GUI's own write from another client's. Each `submit` refreshes, then the watcher fires, a new `Session` re-reads every file, and the window refreshes again.
- **Reproduced:** yes, `t1.py`: one `window.submit(Edit(...))` → `reload` called once, `refresh` twice, `window.session` replaced.
- **Effects:**
  - Twice the redraw cost per edit. The code already notes a reload "redraws the view, seconds on a large model" (`agent_turn`).
  - By reading (not reproduced: deferred deletes do not run under `processEvents`): the second refresh runs `properties.show_object` → `clear()`, which deletes the row a user began editing within 300 ms of the last commit. Enter, click the next field, type: the editor closes and the text is lost.
  - The reload also fires in the middle of a modal form opened right after an edit, rebuilding the tree under it.
- **Fix:** remember the size (or last line's id) of `history.jsonl` after each own `submit`/`undo`; in `_reload_soon` skip when the file has nothing beyond it. Reload only for foreign entries.
- **Test:** count `reload` calls after one `window.submit` and 600 ms of events: expect 0. Append a line as another client: expect 1.

### GUI-8. `reload()` has no error handling

- **Where:** `window.py:789-793`.
- **What is wrong:** `Session(root)` raises `PuzzleError` when a project file is unreadable (another client mid-write, a sync tool, a merge conflict). `reload` runs from a timer and from the Reload action, so the exception goes to the log only; the window keeps the old project with no word to the user, and nothing retries.
- **Reproduced:** yes, `t6.py`: half of a material's TOML written, `window.reload()` raised `PuzzleError: … isn't a readable project file`; status bar empty.
- **Fix:** catch `PuzzleError` in `reload`, `report()` it, and retry once after a short delay (a write in progress); keep the old session meanwhile. `restore_version` (`window.py:1472`) calls `reload()` straight after a restore and has the same gap.
- **Test:** corrupt a file, call `reload()`; assert no exception and a status message; restore it, assert the next reload succeeds.

### GUI-9. An open tool panel goes stale when the project changes under it

- **Where:** `workbench.py:153-169` (`ToolPanel.geometry`, `history`), `workbench.py:209-218` (`apply`), `menus.py:1292-1293` (`geo.history[index]`), `workbench.py:364-384` (`refresh` never looks at `self.tool`), `window.py:753` (`open` leaves the tool and `pending`).
- **Reproduced (`t3.py`):**
  - **B2:** edit the last history step (tool open), press Ctrl+Z so the step's creation is undone, press Apply → `IndexError: list index out of range`.
  - **C:** tool open, open another project → the tool is still shown in the Properties dock, `window.pending` still set; Apply → `AttributeError: 'NoneType' object has no attribute 'history'`.
  - **B:** deleting an earlier step while a later one is being edited shifts the index. Apply was then refused only because step names are unique ("already has a step 'M2'"); the write would otherwise have put one step's arguments on another.
- **Fix:** hold the step by name (names are unique per part) and resolve the index at Apply. In `Workbench.refresh`, close the tool when its geometry or step is gone. Call `workbench.close_tool()` in `open()`. Catch `IndexError`/`AttributeError` in `ToolPanel.preview`/`apply` as "this step no longer exists".
- **Test:** the three sequences above, asserting no exception and that the tool closes with a status message.

### GUI-10. Typed values in Properties fail silently, and two parse paths raise the wrong error

- **Where:** `properties.py:345-384` (`parse_steps`, `parse_value`), `properties.py:597-625` (`_changed`).
- **Reproduced (`t1.py`, `t4.py`):**
  - `5mm` (no space) and `2,5 mm` (decimal comma) on a quantity: the text snaps back to the old value and the status bar stays empty. The user is not told the entry was refused or why. A decimal comma is the normal keypad entry in many locales.
  - `parse_steps(",")` raises `IndexError` (not `ValueError`), which `_changed` does not catch.
  - The sentinels `STEPS = "steps"`, `LISTED = "listed"`, `QUANTITIES = "quantities"`, `TYPED = "typed input"` are compared with `old ==`. A text field whose current value is literally one of those words is parsed as that kind: `parse_value("Other", "steps")` raises `ValueError: invalid literal for int()`, so an object named "steps" cannot be renamed from Properties.
- **By reading:** `parse_value` never checks the unit (`units.known`), unlike `parse_quantities` beside it. `5 foo` is submitted and comes back as `[invalid] invalid mesh` (GUI-5).
- **Fix:** split number and unit with a regex (`^\s*([-+0-9.,eE]+)\s*(.*)$`), accept a single decimal comma, check `units.known`, and on any refusal show a status message ("'5 foo': unknown unit foo"). Make the sentinels unique objects (`STEPS = object()`), not strings. Return `[]` from `parse_steps` when no numbers are found.
- **Test:** table-driven `parse_value` cases (`"5mm"`, `"2,5 mm"`, `"5 foo"`), `parse_steps(",")`, and renaming an object called "steps".

### GUI-12. With any part or body hidden, every pick refits the camera

- **Where:** `viewport.py:1244-1246` (`_picks_changed` re-shows `self.mesh`), `956-963` (the filtered-mesh cache keyed by `id(mesh)`), `2392-2396` (`_frame`); the same from `set_beams_shown` (`:1745`).
- **What is wrong:** `self.mesh` is already the filtered copy, so its `id` misses the cache, `without_parts` builds a new mesh each time, and `_frame` sees a new mesh and resets the camera.
- **Reproduce:** hide a part, enter Pick, zoom in on a detail, click a face: the view jumps back to fit-all.
- **Reproduced:** by the delegated reviewer (`viewport\off.py`: `view.mesh` is a new object after a pick with a hidden part; `viewport\native.py`: camera position moved from 6.86… to 14.59… on a pick, and did not move with nothing hidden).
- **Fix:** keep the unfiltered mesh (`self._source_mesh`) and re-show that from `_picks_changed` and `set_beams_shown`.
- **Test:** offscreen with `hidden_parts` set: `show_mesh(m)`, `shown = view.mesh`, `view.pick([...])`, assert `view.mesh is shown`.

### GUI-14. A custom legend range is stored in display units and reused after the units change

- **Where:** `viewport.py:1440-1443` (`self.ranges[key]` used as is), `legend_panel.py:146`, `188` (written as typed).
- **What is wrong:** a passed-in `clim` is SI and converted for display; the stored range is the number as typed. After Settings → Units changes, or on opening a project with its own unit system (ADR 0030), a 0–200 MPa range is read as 0–200 Pa and the whole model shows the above-range colour. Ranges also outlive `open()`: they belong to the `Viewport`.
- **Reproduced:** by the delegated reviewer offscreen (`viewport\off2.py`): range (0, 0.002) set in m; after switching to mm the result's max is 3.0 and `clim` is still (0.0, 0.002).
- **Fix:** store ranges in SI (convert in `legend_panel` on apply and load, and through `_display` in `show_result`). Clear `ranges` in `Viewport.clear()` or on project open.
- **Test:** the `off2.py` sequence, asserting `shown["clim"] == (0.0, 2.0)` after the switch.

### GUI-15. Wheel, right-drag and pinch do nothing in Orthographic

- **Where:** `viewport.py:397`, `403`, `417` (all `camera.Dolly`), `449-459` (`_pan`, scaled by view angle and distance).
- **What is wrong:** a parallel projection's size is `parallel_scale`; dollying changes nothing visible.
- **Reproduced:** by the delegated reviewer on the native platform (`viewport\native.py`): five wheel notches left the frame pixel-identical and `parallel_scale` unchanged.
- **Fix:** with `camera.parallel_projection` on, divide `parallel_scale` by the factor and pan by `2 * parallel_scale / height`.
- **Test:** real view, `set_style(parallel=True)`, send a `QWheelEvent`, assert `parallel_scale` shrank.

### GUI-17. The hidden-parts cache is keyed by `id(mesh)` of an object it does not keep alive

- **Where:** `viewport.py:959-963`; `clear()` (`:1571-1580`) resets neither `_shown_key` nor `_shown_mesh`.
- **What is wrong:** `_shown_mesh` is a copy, so the original can be freed; a later mesh allocated at the same address with the same hidden parts gets the old filtered mesh.
- **Reproduced:** the mechanism only (`viewport\off2.py`: a second mesh took the first's id and the stale mesh was shown). Not reproduced through the window; how often it happens depends on the allocator. It fits `open()` (`window.py:759-763`: `meshes.clear()` then new meshes) and a remesh.
- **Fix:** store `(mesh, hidden, bodies)` and compare the mesh with `is`; reset in `clear()`.
- **Test:** the `off2.py` loop asserting `view.mesh.coords` matches the new mesh.

---

## Low

### GUI-16. "Element edges" cannot be switched off

- **Where:** `viewport.py:2360-2361` (`_apply_style` only ever sets edge visibility to True).
- **Reproduced:** by the delegated reviewer (`viewport\native.py`): per-actor edge visibility `[False, False]` → on `[True, True]` → off `[True, True]`, until something rebuilds the scene.
- **Fix:** record each actor's own `show_edges` when added and restore it, or redraw on an edges change (`_base = None; refresh_view()`).

### GUI-18. `capture_point` stays armed with no owner (read only, not reproduced)

- **Where:** `viewport.py:1139-1150`, `1191-1198`; armed and never cancelled by `beam_dialog.py:431` and `field_align_panel.py:286`, `294`; `Viewport.clear()` does not reset it.
- **Effect:** while armed, Pick clicks, box select and result probes are dead, and the stale callback fires on the next double-click, even after another project is opened.
- **Fix:** `clear()` cancels it (calling the callback with None); cancel on Esc and when the owning dialog closes.

---

## To check (not verified)

- **Closing the window during a run or meshing** (`window.py:3887-3893`): `closeEvent` neither asks nor sets the task's `cancel`. In `t5.py` R3 no exception followed, but the solve went on; with the job runner off (or for meshing) the process should outlive the window until the thread ends. Decide whether to ask "Stop the run?".
- **`window.pending` in the Model tab:** with a tool open, switching to the Model tab keeps `pending`, and `_preview` applies it to the real component's preview, so the Model view shows a step that has not been applied.
- **`open()` leaves per-project caches:** `previews`, `mesh_names`/`boundaries`/`vertices`/`anchors`/`selections`/`quality`, `_standalones`, `_selections_checked`, `slider_overrides`, `_result_run`. They are keyed by UUID, so they only collide for a Save as copy or an unpacked archive of the same project, where a preview of the other folder's files could be reused.
- **A single item deletes with no question** (`window.py:1562-1568` asks only for several). Combined with GUI-1 this is what makes the loss silent; consider a status message with the name in every case.
- **Animation state across results:** `_frames` is computed once at Play; selecting another run while playing keeps the old frame list (`window.py:2498-2512`).
- **Viewport, from the delegated review:** an all-NaN field shows the raw text "All-NaN slice encountered" in the status bar (`viewport.py:1466`); `clear()` leaves `pins`, `_bolt_marks`, `_extremes`; `without_parts` (`:57-60`) may drop a face shared with a shown body; `CutBar.face` keeps a `(2, tag)` across a remesh; `CutBar.offset` is not rescaled on a unit change; releasing one of two pressed mouse buttons ends the drag.
- **Thread safety:** no call that leaves the GUI thread was found in viewport.py, and the `background` jobs read (`start_meshing`, `_preview`, `_check_selections`, runs) work on deep copies and report through queued signals. The remaining `MainWindow.background` callers in other files were not audited.

---

## Design notes

- **window.py is 4,275 lines and holds a dozen jobs:** project lifecycle, the command gateway, the task runner, the run queue, the preview cache, result display and animation, picking and selection editing, slider previews, layout, and two unrelated panels (`PlotsPanel`, `ProbesPanel`). State is about forty loosely related attributes, many created lazily and read with `getattr(self, "_x", None)` (`_result_run`, `_agent_turn`, `_reload_held`, `_reported_store`, `_plot_axes`). There is no single place that says what must be reset when the project changes, which is the root of GUI-2, GUI-9 and the cache item above. A `ProjectState` object replaced wholesale in `open()` would close that class of bug.
- **No "is a project open" gate.** Each action checks `self.session is None` for itself or forgets to (GUI-6). One gate in `main_menu._add` and the toolbar is cheaper than 200 checks.
- **Background results are not tied to what asked for them.** `done` callbacks close over `self` and run against whatever is current when they arrive. A token (session root plus a generation counter) checked in `finish`/`fail` would make every late result safe.
- **Index paths as identity.** Tool panels, tree nodes and menu lambdas address steps, features and parts as `history.3` / `features.1`. Any change between building and acting shifts them; today only unique step names stand in the way (GUI-9 B). Resolve by name at the moment of the command.
- **Errors lose their reason on the way to the user.** `report()` prints `[code] message`; the validation detail that would tell the user what to fix stays in `details` (GUI-5, GUI-10). Silent reverts in Properties are the same pattern.
- **Window-wide single-key shortcuts** (`Delete`, `P`) act on hidden state (GUI-1). Shortcuts that change the model should be scoped to the widget that shows what they act on.
- **Where tests are thin:**
  - No test walks the menu bar and triggers actions; `tests/test_main_menu.py` checks names and shortcuts. The fuzz in `core\fuzz.py` found GUI-3, GUI-4 and GUI-6 in under a minute and would make a good permanent test (dialogs stubbed, `sys.excepthook` asserted empty), run for: no project, an empty project, nothing selected, each tree item.
  - No test builds a form for every geometry step kind (GUI-4).
  - No test changes the project under an open panel (undo, another client, open another project).
  - Mouse interaction on the real view is untested because GUI tests run offscreen; GUI-11, GUI-12, GUI-15 and GUI-16 need the `PUZZLE_RENDER_TESTS=1` path.
  - Typed-value parsing has no table of bad inputs.

---

# Second pass

Done by me alone (three helper reviews stopped on the spend limit again before reading anything). Scripts are in the same `...\review\core\` folder: `accept.py` (fuzz with dialogs accepted), `empty2.py`, `t7.py`–`t13.py`.

## Correction to the first pass: GUI-6 is low, not high

`main_menu._enable` (`main_menu.py:80-86`) greys out project actions and whole submenus when a menu opens. My first fuzz walked into disabled submenus, so "206 actions" was an overcount. Rerun honouring disabled parents (`empty2.py`), with no project open:

- **Reachable failures: three actions.** Undo (Ctrl+Z), Mesh (Ctrl+M) and Run (Ctrl+R) raise `AttributeError`, from the toolbar buttons, from their shortcuts, and (Undo, Mesh) from the menu.
- **Why those:** they are the window's own toolbar `QAction`s (`window.py:1118-1158`), added to the menus directly, so they carry no `needs_project` property; and `_enable` only runs when a menu is shown, so a shortcut pressed before that is not gated either.
- **Fix:** guard `undo`, `mesh_selected` and `run_selected` with `if self.session is None: return`, and disable the project toolbar actions in `open()` and at start.
- **Severity: low.** Nothing is lost; a traceback goes to the log.

## Updated counts (both passes)

| Severity | Count | IDs |
| --- | --- | --- |
| Critical | 3 | GUI-19, GUI-20, GUI-21 |
| High | 11 | GUI-1 to GUI-5, GUI-11, GUI-13, GUI-22 to GUI-25 |
| Medium | 12 | GUI-7 to GUI-10, GUI-12, GUI-14, GUI-15, GUI-17, GUI-26 to GUI-29 |
| Low | 4 | GUI-6, GUI-16, GUI-18, GUI-30 |

## Critical

### GUI-19. New… into a folder that already holds a project replaces it with an empty one

- **Where:** `window.py:1284-1294` (`new_project` → `save_project(project, root)`), `window.py:1303-1326` (`_new_dialog`: the check asks only for a folder and a name).
- **What is wrong:** nothing checks whether the chosen folder is already a Puzzle project (or holds anything). Saving the new, empty project removes the existing object files.
- **Reproduce:** Project → New…, Browse to an existing project's folder, OK.
- **Reproduced:** yes, `t13.py`, on a copy of the cantilever example: 7 object files before, 0 after; the window shows the empty project "Fresh". No question was asked. The copy had no `versions/` folder afterwards, so nothing was kept to restore from. (A project that already had versions may keep them; not tested.)
- **Fix:** in the form's `check`, refuse a folder that contains `project.toml` ("That folder already holds the project X: open it, or choose another folder") and warn for any non-empty folder. `new_project` itself should raise for an existing project so no other caller can do it.
- **Test:** `new_project` on a copy of an example must refuse and leave every file in place.

### GUI-20. Import geometry overwrites a file of the same name already in the project

- **Where:** `menus.py:1183-1203` (`import_step_file`: `shutil.copyfile(path, target)` then `import_step(target)`).
- **What is wrong:**
  - The file is copied to `<project>/<file name>` with no check. A different file with the same name (two suppliers' `bracket.step`, a revised export) replaces the one an existing geometry reads. That geometry now silently is another shape.
  - The copy is not a command: it is not in the history and Undo does not bring the old file back.
  - If the new file cannot be read, `import_step` raises a bare `Exception`, uncaught: the user sees nothing, and the old file is already gone.
  - `import_step` runs on the GUI thread (a large assembly freezes the window; the convention is `MainWindow.background`).
- **Reproduce:** in the cantilever example, Geometry → Import geometry… and choose another folder's `bar.step`.
- **Reproduced:** yes, `t9.py`: the project's `bar.step` hash changed, no history entry was added, the status bar stayed empty, `Exception: Could not read file …` reached the caller, and the geometry "Bar" still points at `bar.step`.
- **Fix:** if `target` exists and differs in content, copy under a free name (`bar-2.step`) or ask. Read the file from its original place first and copy only once it parsed. Catch the reader's error and `report()` it. Run the read through `background`.
- **Test:** import a different file named like an existing source; assert the existing file's hash is unchanged and the new geometry reads its own file. Import a broken file; assert a status message and no exception.

### GUI-21. Moving a group into a group it contains hangs Puzzle, and the project then hangs on every open

- **Where:** `organise.py:74-88` (`move` refuses only a group into itself), `tree.py:501-502` (the drop refuses only `group in keys`), `organise.py:230-236` (Move to group lists every fitting group but the moved ones), `tree.py:419-422` (`_group` inserts folder items into each other).
- **What is wrong:** with P holding C, moving P into C is accepted and written. `_group` then makes each folder item a child of the other and Qt never returns from `insertChild`.
- **Reproduce:** under Studies make groups P and C; drag C into P; then right-click P → Move to group → C (or drag P onto C).
- **Reproduced:** yes, `t8.py` with a `faulthandler` timeout. The GUI thread sits in `tree.py:422 _group ← _populate ← populate ← window.refresh`. Opening the same project in a fresh `MainWindow` hangs at the same line from `open()`. The cycle is in the project's files, so the GUI cannot open the project again until it is undone from the CLI or by hand.
- **Fix:**
  - `organise.move`, `new_group` and the drop: refuse a target that is the moved group or any group inside it.
  - `tree._group`: skip a membership that would make a cycle (track ancestors), so a project that already has one still opens.
  - Have the session refuse a `TreeGroup` edit that makes a cycle, so no client can write one.
- **Test:** P ⊃ C, then `organise.move(window, [P], C, section)`: assert it is refused. Separately, load a project whose groups hold each other and assert `tree.populate` returns.

## High

### GUI-22. After the geometry or the mesh recipe changes, the Model and Mesh tabs keep showing the old mesh

- **Where:** `window.py:1644` (the only write to `self.meshes`), `window.py:759` (cleared only in `open`), `window.py:1957-1959` (`_show_selected` prefers `self.meshes[cid]` over the preview), `window.py:2073` (`show_mesh_bench` likewise, with the old quality colours), `window.py:3545` ("the real mesh's maps win once it exists").
- **What is wrong:** a mesh made with Mesh is kept by component id and never invalidated. Any later change to the part (a history step, a parameter, a replaced file, Undo, another client) or to the recipe leaves the view on the old mesh, with the old entity-name maps used for picking.
- **Reproduce:** mesh a component built from a history part; change the box's length; select the component in the Model tab.
- **Reproduced:** yes, `t12.py`: preview and mesh at x max 0.05 m; after setting `dx` to 0.1 m the Model tab still shows x max 0.05 (`shown["kind"] == "mesh"`), while the Geometry tab shows 0.1.
- **Effect:** wrong geometry on screen with nothing saying it is out of date; picks and new selections are named from the old mesh's faces.
- **Fix:** store the mesh with the key it was made from (`_preview_key(comp)` plus the recipe's hash) and drop it, or show it marked "out of date: mesh again", when the key no longer matches. The project already computes staleness for runs (`project/status.py`); reuse that.
- **Test:** the `t12.py` sequence, asserting the view's mesh is not the 0.05 m one after the edit.

### GUI-23. The tree loses its selection after any edit that changes the selected item's label

- **Where:** `tree.py:375-383` (`_populate` re-selects with `node == keep`, a dataclass comparison that includes `label`), against `tree.py:726-730` (`_same`, which ignores labels and is what `select` and `item_for` use). Expansion is keyed with the label too (`tree.py:685-688`).
- **What is wrong:** an object's node label is its name; a load's is its selection; a material assignment's is its selection; a step's is its text. Editing any of those in Properties rebuilds the tree, the old node no longer equals the new one, and nothing is selected.
- **Reproduced:** yes, `t7.py` section 1:
  - Rename the selected study through Properties → `tree.current()` is `None`.
  - Change a load's selection from Bar/Root to Bar/Tip → `tree.current()` is `None`.
- **Effect:** `_show_selected` returns early for no selection, so Properties and the 3D view keep the pre-edit state (the highlight stays on the old face). The next menu or Delete acts on nothing, or on whatever is clicked next. An expanded item collapses when renamed.
- **Fix:** compare with `_same(node, keep)` in `_populate`, and key expansion by `(kind, object, path)` (plus the folder key).
- **Test:** select a study, `window._edit(id, "name", "X")`; assert `tree.current().object == id`. Same for a feature after changing its selection.

### GUI-24. One panel's error aborts the whole window refresh; New mesh study… then Undo does it

- **Where:** `window.py:795-850` (`refresh` calls about fifteen panels in a row, unguarded), `convergence_panel.py:373-374` (`conv.settings(s.project, name)` outside the `try`).
- **What is wrong:** the Convergence panel keeps the name of the mesh study it shows. When that object goes (Undo, Delete, another client), `conv.settings` raises `PuzzleError: no study '…'` on every `refresh`. Everything after line 837 is skipped each time: the Jobs and Agent panels, the Geometry tab, the schematic and `_selected()`.
- **Reproduce:** right-click a study → New mesh study… → OK; then Ctrl+Z.
- **Reproduced:** yes, `accept.py` (both the menu bar and the tree run): after that Undo, 67 and 172 later actions each raised the same error from `refresh`. Properties and the view stop following edits until the Convergence dock is closed.
- **Fix:** in `refresh`, run each panel through a guard like `_aside` (log, carry on), with `_selected()` always reached. In the panel, treat a missing check as "No convergence check shown". The Optimisation panel already guards its own lookup (`optimisation_panel.py:329-338`).
- **Test:** show a mesh study in the panel, delete it, call `window.refresh()`; assert no exception and that Properties follows a new selection.

### GUI-25. The tree cannot be built for a project that has a result tool and a swept study's runs

- **Where:** `tree.py:308` (`from puzzle.sweep import title`), `tree.py:320` (`from puzzle.gui.result_tools import title` inside the result tools loop), `tree.py:345` (`point = title(labels)`).
- **What is wrong:** both imports bind the same local name. Once the loop over result tools has run, `title` is `result_tools.title`, and the sweep branch calls it with a dict of point labels.
- **Reproduced:** in part. `result_tools.title({"Bar/Body": "Copper"})` raises `AttributeError: 'dict' object has no attribute 'type'` (`t7.py` section 2). The scoping is certain from reading; I did not build a project with both a result tool and sweep runs.
- **Effect:** `populate` raises after `clear()`, so the tree is left partly built and `refresh` stops (as GUI-24) on every refresh of such a project.
- **Fix:** import them under different names (`from puzzle.sweep import title as point_title`).
- **Test:** a project with one `ResultProbe` and a stored run that has point labels; assert `tree.populate` succeeds and lists the point.

## Medium

### GUI-26. In the sketch, Ctrl+Z and Delete act on the project, not the sketch

- **Where:** `sketcher.py` has no key handling and no undo of its own (deleting is the Delete button, `sketcher.py:880-885`); the window's Undo and Delete are window-wide shortcuts (`window.py:1142`, `1147`).
- **What is wrong:** while drawing, Ctrl+Z undoes the last *project* command (something done before the sketch was opened), and Delete removes the Model tree's selected object (GUI-1). Neither touches the sketch.
- **Confidence:** from reading; GUI-1 reproduces the Delete half from the Geometry tab's list.
- **Fix:** while `workbench.sketching` is set, route Undo and Delete to the workspace (a sketch-local undo stack; `delete_selected`), or disable the two window actions for the duration.
- **Test:** start a sketch, draw a line, send Ctrl+Z; assert the project's history is unchanged.

### GUI-27. A sketch is not ended when the tab changes or another project is opened

- **Where:** `window.py:2028-2046` (`_tab_changed`), `window.py:753` (`open`), `window.py:2169-2183` (`show_sketch`), `workbench.py:602-646`.
- **Reproduced (`t10.py`):**
  - Sketching, then the Model tab: the centre still shows the sketch canvas while the context says "model". Tree selections draw nothing visible until the user returns to the Geometry tab and presses Finish or Cancel.
  - Sketching, then another project opened: the canvas stays over the new project, `workbench.sketching` is still set, and every geometry tool button is disabled.
- **By reading:** Finish then calls `_free_name(None, …)` or `apply_step` on a geometry the new project does not have (`workbench.py:636-640`). `show_sketch(None)` removes the *current* centre widget, so cancelling while the schematic is in the centre leaves the workspace in the stack.
- **Fix:** `open()` calls `workbench.cancel_sketch()`; `_tab_changed` either keeps the user in the Geometry tab while sketching (asking Finish or Cancel) or swaps the centre back and restores it on return. `show_sketch(None)` removes the workspace it was given, not whatever is current.
- **Test:** the two `t10.py` sequences, asserting the centre is the 3D view and `sketching is None`.

### GUI-28. A tool left open keeps its unapplied step in every other view, and a failing preview is retried on every selection

- **Where:** `window.py:3561-3568` (`_preview` applies `self.pending` to any component using that geometry), `window.py:2028-2046` (leaving the Geometry tab does not close the tool), `window.py:3581-3584` (`failed` clears `_previewing`, so the next `_selected` starts it again).
- **Reproduced:** yes, `accept.py` (menu bar run): with the Enclosure tool left open on the imported part, each of about sixty later actions in the Mesh and Model menus logged a new `error: GeometryError: step 'Enclosure 1' (enclosure) failed: AddOptimal_s(): incompatible function arguments … Invoked with: None, <OCP.Bnd.Bnd_Box …>`. That raw binding text is what the user reads in the log and status bar.
- **Effect:** the Model tab previews a step that was never applied (or nothing at all when it fails); one background job and one log error per click.
- **Fix:** use `pending` only while the Geometry tab is current; close or park the tool on leaving the tab. Remember a failed `(cid, key)` and do not retry it until the key changes. Give the step failure a readable sentence (here: "this part comes from a file: no solid to enclose yet").
- **Test:** open a tool, switch to the Model tab, select the component; assert the preview key has no pending part and no background task was started twice for the same key.

### GUI-29. Opening a step's form and applying it rounds every length to six significant digits

- **Where:** `step_dialog.py:351-353` (`f"{value * scale:.6g}"`), `step_dialog.py:470-476` (`_parse`), `menus.py:1297` (Apply writes the whole `args`).
- **Reproduced:** yes, `t11.py`: a box with `dx = 0.0123456789`, `dy = 1/3` comes back from an untouched form as `0.0123457`, `0.333333`.
- **Effect:** editing one field of a step changes the others. Values set by the agent, by a script or converted from inches lose digits, and the geometry's hash changes, so results go out of date for an edit the user did not make.
- **Fix:** keep each field's original value and return it unless its text was edited; or show with full precision (`.15g`, trimmed).
- **Test:** for every step kind, `StepForm(kind, args).values() == args` for arguments with 12 significant digits.

## Low

### GUI-30. Some forms accept defaults the schema then refuses, with no reason given

- **Reproduced (`accept.py`, OK pressed on untouched forms):** New combination…, New equation… and New fatigue tool… each end in `error: [invalid] invalid result`; Add physics → Custom PDE ends in `[invalid] invalid component`.
- **What is wrong:** the dialogs let OK through for input the model rejects, and the user gets only the code (see GUI-5 for the dropped reason).
- **Fix:** validate in the dialog (`check`), naming the missing field; Custom PDE should open its equation form instead of appending an empty physics.

## To check (second pass, not verified)

- **Mesh tab control panels are never deleted:** `mesh_bench.close_tool` (`mesh_bench.py:388-392`) drops its reference but leaves the panel in `window.side` (the Geometry tab calls `deleteLater`). One widget leaks per tool opened.
- **Mesh tab control form with a stale index:** `ControlPanel.apply` sets `controls.{index}` (`mesh_bench.py:205`), the same pattern as GUI-9.
- **Live mesh preview queues meshes:** with Settings > Meshing preview "live", each control edit calls `start_meshing` directly (`mesh_bench.py:351-357`), even while one is running; none is superseded.
- **Contacts appear once under each physics with the same node** (`tree.py:600-612`), so after a refresh the selection jumps to the first physics' copy.
- **Forms keyed by name** (`menus.py:602`, `888-889`, `930`, `1042`): two materials, geometries, recipes or studies with one name collapse to one choice.
- **Study → Materials…** (`menus.py:785-805`): adding a library material inside the dialog is its own command, so OK makes two undo entries and Cancel leaves the material.
- **Saved layouts** (`docking.py:347-353`): names that differ only in case or punctuation, and names with no ASCII letters, share a file and replace each other silently.
- **Polyline points are typed in mm whatever the display unit** (`step_dialog.py:347-348`, labelled "(mm)"), beside length fields in the display unit in the same form.
- **The sketch's point-to-point dimension** asks its kind as typed text ("straight", "across", "up") in one popup and the value in a second (`sketcher.py:813-838`); cancelling the first still goes on to the second.
- **Slider drags** (`window.py:3277-3287`) start a background preview per distinct value; they finish in lock order, not request order. The key check corrects the final state, at the cost of extra previews.

## Coverage after the second pass

- **Fuzz with dialogs accepted on defaults:** done for the menu bar and for every tree right-click action on the cantilever example (`accept.py menubar`, `accept.py tree`), each action followed by Undo. File dialogs still returned nothing, so imports, exports and archives were not driven this way. Destructive and long-running items (delete, run, mesh, optimise, archive, trust, settings, layouts) were skipped. No action added more than one history entry.
- **Read closely:** `deleting.py`, `organise.py`, `tree.py`, `quick_form.py`, `docking.py` (layouts and auto-hide), `workbench.py`, `menus.py` lines 28–1320, `step_dialog.py` (rule field, widgets, values, parsing), and in `window.py` also selection editing, sliders, new project, save as and derived results.
- **Read in part:** `sketcher.py` (constraints, dimensions, delete, problem; not the canvas, drawing tools or the solver model), `mesh_bench.py` (control form, tools, menus; not quality display or seeds), `main_menu.py` (helpers, Project menu, gating; the other menu builders only through the fuzz), `choices.py` (the entry function only).
- **Still not read:** `form.py`, `feedback.py`, `app.py`, `results_ribbon.py`, `menus.py` lines 1320–1707, `choices.py` beyond `choices()`, and in `window.py` the problems list, result properties and probes, fastener and symbol drawing, layout save and restore, and the `PlotsPanel` and `ProbesPanel` classes.
- **The bracket assembly example was not exercised:** every test used copies of `examples/cantilever` plus a history box part made by command.
