# GUI dialogs and docked panels: bug hunt

Reviewer scope: `src/puzzle/gui/` dialogs and panels, excluding window.py, tree.py, properties.py,
choices.py, menus.py, main_menu.py, viewport.py, workbench.py, sketcher.py, mesh_bench.py,
schematic_view.py, operators.py, agent_panel.py, agent_view.py.

Method: reading, plus small offscreen runs (`QT_QPA_PLATFORM=offscreen`, a scratch
`PUZZLE_USER_DIR`, `MainWindow` on a copy of `examples/cantilever`, its "Tip load" and "Modes"
studies solved once with native CalculiX in the scratch folder). Nothing in the repository was
changed. Scratch scripts are `t1.py` to `t7.py` beside this file.

"Ran" below means the behaviour was reproduced by one of those scripts; "read" means it follows
from the code and was not executed.

An uncaught exception in a Qt slot is not silent in Puzzle: `gui/errors.py` `_hook` shows a
"Something went wrong" message box. So an exception raised while typing in a live form is a popup
per change.

## Counts

| Severity | Count |
| --- | --- |
| Critical | 1 |
| High | 5 |
| Medium | 9 |
| Low | 5 |

---

## Critical

### PANEL-1: Linearized stress: switching "Stresses in" then editing anything rewrites the allowable in the wrong unit

- File: `src/puzzle/gui/linearization_panel.py:310-316` (`set_unit`), `:318-343` (`edited`), `:345-350` (`_to_si`)
- What: `set_unit` changes `self.unit` and calls `fill()`, which redraws the tables but never
  rewrites the `Allowable S` line edit. The field keeps the number typed in the old unit. The next
  `edited()` (any of the five combo boxes, or focus leaving the allowable field) reads that text
  with `_to_si` in the new unit and writes it to the probe. The project's allowable stress changes
  silently and every VIII-2 5.2.2.4 check is then made against the wrong limit.
- Reproduced: ran (`t7.py`). Allowable typed as 138 with the panel in MPa: stored 1.38e8 Pa.
  Units switched to ksi (same panel instance, field still reads "138"). Membrane combo changed:
  the stored allowable became 951476506 Pa (138 ksi), a factor 6.9, so failing checks pass.
- Steps: solve a structural study; Results, Linearized stress; select the tool; type 138 in
  Allowable S (MPa shown); set "Stresses in" to ksi; change "Membrane is" (or click into and out of
  the allowable field). Look at the tool's allowable.
- Confidence: high.
- Fix: in `set_unit`, convert the field before `fill()`: read the probe's stored allowable (SI) and
  `self.allowable.setText(self._shown(probe.allowable))` in the new unit (or convert the typed
  text from the old unit to the new when it differs from the stored one). Also compare in `edited`
  against the stored value with a tolerance, so the 6-digit text does not write a rounded allowable
  when another field changes (137895146 Pa shown as "137.895" comes back as 137895000).
- Test: make a linearized probe with `allowable=1.38e8`, open the panel, set units to ksi, change
  `membrane`; assert the probe's allowable is still 1.38e8 and the field reads "20.0152".

---

## High

### PANEL-2: Export… fails for every format without a step option, including the default (ParaView)

- File: `src/puzzle/gui/export_dialog.py:112` (`step=got["step"]`)
- What: the Step ask is `shown=takes("step")`, and `QuickForm.values()` leaves hidden fields out.
  For formats whose `EXPORTS[...].options` has no `"step"`, `got["step"]` raises `KeyError`, caught
  by the broad `except` and shown as `Export failed: 'step'`. Nothing is written.
- Reproduced: ran (`t6.py`), each format through the dialog on a solved cantilever. Failed:
  paraview, exodus, vtkhdf, unv, ensight, cgns, tecplot, nastran (8 of 17). Worked: point_cloud,
  deformed_stl, deformed_step, abaqus_inp, abaqus_displacement, gltf, morphed_step. The existing
  test (`tests/test_gui_exports.py`) only uses formats that take a step.
- Steps: run a study; Results, Export…; leave Format on ParaView; OK; choose a path.
- Confidence: high.
- Fix: `step=got.get("step", -1)`.
- Test: parametrize `test_export_form` over every key of `EXPORTS` whose physics fits the run and
  assert files are returned (or a coded refusal), never `Export failed: 'step'`.

### PANEL-3: Material builder corrupts kind-less properties stored in a non-SI unit (viscosity, electrical conductivity)

- File: `src/puzzle/gui/material_builder.py:878-884` (`load`), `:938-949` (`material`)
- What: `load` shows `q.value` unconverted when the property has no unit kind
  (`dynamic_viscosity`, `electrical_conductivity`; `p.kind is None`), but labels it with the
  registry's SI unit, and `material()` stores the typed number as that SI unit. A library entry or
  project material holding such a property in another unit is mislabelled on screen and rewritten
  wrong by Save to my materials or Add to project.
- Reproduced: ran (`t4.py`). `dynamic_viscosity = 1 mPa*s` loads as "1 Pa·s" and saves as
  1.0 Pa·s (1000 times too viscous); `electrical_conductivity = 58 MS/m` loads as "58 S/m" and
  saves as 58 S/m (a million times too low).
- Reach: the shipped `generic.toml` is SI throughout, so this needs an entry from
  `materials-import`, a hand-edited `custom.json`, or an agent-made material. When it happens the
  numbers are silently wrong, hence high rather than medium.
- Steps: import a library whose water has `dynamic_viscosity = {value = 1.0, unit = "mPa*s"}`;
  Material builder; choose it; Save to my materials.
- Confidence: high.
- Fix: in `load`, convert every property to `p.unit` first (`convert(q.value, q.unit, p.unit)`),
  then to the display unit when `p.kind` is set.
- Test: load a `Material` with those two properties in mPa·s and MS/m into `MaterialEditor`,
  call `material()`, assert `to_si()` is unchanged for each.

### PANEL-4: Import field wizard: a wrong or missing time, or a non-number in Alignment, raises on Finish after the wizard has closed

- File: `src/puzzle/gui/field_wizard.py:181-189` (`timing_data`), `:256-262` (`placement`),
  `:389-431` (`draft`), `:433-447` (`finish`), `:455` (`wizard.accepted.connect(wizard.finish)`)
- What: no page has a `validatePage`. `draft()` raises `ValueError` for "N files need N times",
  for a step count that differs, for a time such as `5s`, and for a non-number in Move, Rotate or
  About. `finish` is connected to `accepted`, so by the time it raises the wizard is already
  closed: the field is not created and the columns, units and alignment are gone. The Check button
  on the Verification page raises the same errors in its slot.
- Reproduced: ran (`t7.py`): with two files and no times, `draft()` and `finish()` raise
  `ValueError: 2 files need 2 times or frequencies`; `0, 5s` and `abc` raise
  `could not convert string to float`. That the wizard is closed at that point is read from the
  `accepted` connection, not run.
- Steps: Import field…; choose a CSV; Add files… (one more); leave Times empty; Next to the end;
  Finish.
- Confidence: high for the exceptions, medium-high for the lost wizard.
- Fix: validate in `FilePage.validatePage` and `AlignPage.validatePage` (call the same parsing,
  show the message in the page's note, return False); make `finish` run from an overridden
  `accept()` that only calls `super().accept()` when the Create succeeded.
- Test: build the wizard offscreen, load a CSV, `set_more([other])`, assert
  `wizard.file.validatePage()` is False with a message, and that `wizard.accept()` leaves the
  wizard open and the project without a field.

### PANEL-5: Member design… reads a bare number as metres whatever units are shown, and accepts any unit text

- File: `src/puzzle/gui/member_design.py:48-52` (`_quantity`), `:55-71` (`_check_form`)
- What: `_quantity` uses `unit.strip() or "m"`. The project rule (ADR 0024) is that a bare number
  is in the unit shown. With millimetres or inches shown, typing 3000 for the unbraced length
  stores 3000 m. The unit is never checked: `10 feets` and `10 kg` pass `_check_form` (only
  `float()` is tried) and are stored; `Quantity` does not validate units.
- Reproduced: ran (`t4.py`): `_quantity("3000")` gives `Quantity(3000.0, "m")`;
  `_quantity("10 feets")` and `_quantity("10 kg")` are returned; `_check_form` returns None for
  `10 feets`.
- Effect: buckling and lateral-torsional lengths wrong by the display factor (member ratios far
  too high), or a stored unit that fails or converts wrongly later in `frames.design`.
- Steps: Settings, Units: engineering (mm); Model, Components, Member design…; Unbraced length
  3000; OK; look at the entry's `unbraced_length`.
- Confidence: high.
- Fix: use `settings.length_unit()[0]` for a bare number and show that unit in the labels; refuse
  a unit that is unknown or not a length (`units.known` plus a dimension check, see PANEL-11).
- Test: with the `engineering_units` fixture, fill the form with `unbraced_length="3000"` and
  assert `to_si() == 3.0`; assert `10 kg` and `10 feets` block OK.

### PANEL-6: Member design… replaces an existing entry with the form's blanks

- File: `src/puzzle/gui/member_design.py:74-131` (`edit_design`), the replace at `:128`
- What: the form always opens with the same defaults (blank lengths, K = 1, no deflection limit)
  and on OK drops the existing entry for the chosen selection and writes the new one. Reopening it
  to add a deflection limit to "Every member" silently resets K factors and unbraced lengths set
  before. A K of 2.1 on a cantilever column going back to 1 is unconservative.
- Reproduced: read only.
- Steps: Member design…: Every member, K strong axis 2.1, OK. Member design… again: Every member,
  Deflection limit 360, OK. `comp.member_design` now has K = 1.
- Confidence: high.
- Fix: make the fields' defaults functions of the chosen selection (`Ask.follow`, or choices
  rebuilt from `comp.member_design`), so the form starts from the existing entry; or merge the
  typed fields onto the existing entry and say which were kept.
- Test: submit an entry with `k_x=2.1`, run the form again changing only `deflection_limit`,
  assert `k_x` is still 2.1.

---

## Medium

### PANEL-7: Material builder: editing the hardening curve deletes the curves at other temperatures

- File: `src/puzzle/gui/material_builder.py:1034-1049` (`edit_curve`)
- What: the table shows `self.curves[0]["points"]` only, and on OK sets
  `self.curves = [{"temperature": None, "points": points}]`. A material with several
  `HardeningCurve`s (the label says ", N temperatures") loses all but the first, and the first
  loses its temperature. `build_curve` does the same by design (it builds one curve), but
  `edit_curve` is an edit.
- Reproduced: ran (`t3.py`): two curves at 293.15 K and 573.15 K; `edit_curve` accepted with the
  rows unchanged; `material().models.plasticity.curves` is one curve with `temperature=None`.
- Fix: edit curve `i` in place (a temperature picker when there are several), keeping its
  temperature and the others.
- Test: as the script: load two curves, accept the table unchanged, assert both remain equal.

### PANEL-8: Material builder rounds every value to six significant digits on any save

- File: `src/puzzle/gui/material_builder.py:884` (`f"{v:.6g}"`), `:239-241` (`TableEditor._row`),
  `:899`, `:914` (model fields, strain life)
- What: values are shown with `.6g` and `material()` rebuilds the material from the text, so
  opening a material and saving it (a new description, say) rewrites every property, table value,
  curve point and model constant at six digits. The content hash changes and results go out of
  date; values that came from unit conversions drift.
- Reproduced: ran (`t3.py`): E = 206842718795.3 Pa saved as 206843000000.0 (1.4e-6 relative);
  36 ksi yield saved as 248211000 from 248211262.55.
- Fix: keep the stored value beside each field and only replace it when the text was edited
  (`textEdited`), or show `repr`-length digits (`.12g`); same in `TableEditor`.
- Test: load a material with 12-digit values, call `material()` untouched, assert equality.

### PANEL-9: Edit sweep… rewrites the swept values at six significant digits

- File: `src/puzzle/gui/sweep_form.py:114-121` (`_text`)
- What: reopening a sweep step fills each list from `f"{v.value:g}"`; OK parses the text back and
  sets the study's steps. A value of 0.0012345678 m comes back as 0.00123457 m, which changes the
  points' run keys (stored runs no longer match) without the user touching the field.
- Reproduced: ran (`t2.py`) for `_text`; the write-back on OK is read.
- Fix: `repr(float)`-style text (`f"{v.value!r}"` or `.15g`), or keep the old parameter when its
  text is unedited.
- Test: a sweep with `[0.0012345678 m]`, reopen and accept; assert the step is unchanged and no
  command is submitted (or values equal).

### PANEL-10: The Welds and Members panels are never refreshed after a change; a deleted run raises

- File: `src/puzzle/gui/weld_table.py:81-105, 111-117`; `src/puzzle/gui/member_design.py:160-195`;
  the missing calls are in `MainWindow.refresh` (`window.py:828-839`, which refreshes plots,
  sections, bolts, contacts, versions, optimisation, convergence, jobs but not `welds`/`members`)
- What: both panels refresh only on their dock's `visibilityChanged`. While shown they keep the
  old run list and numbers after a new run, an undo, a member-design edit, a unit change or another
  client's change. Their `show_results` also does
  `record = next(r for r in store.runs() if r.run_id == run_id)` outside the `try`, so choosing a
  run that was deleted since raises `StopIteration` in the combo's slot.
- Reproduced: ran (`t4.py`): with both docks visible, `window.refresh()` called
  `welds.refresh` and `members.refresh` zero times. The `StopIteration` is read.
- Steps: Window, Members (or Welds); run the study again, or change Member design…; the table
  does not change until the dock is hidden and shown.
- Fix: refresh both in `MainWindow.refresh` when not hidden (as optimisation and convergence),
  and move the `record` lookup inside the `try` using `next(..., None)`.
- Test: show the dock, count `refresh` calls across `window.refresh()`; delete the shown run and
  switch the combo without an exception.

### PANEL-11: `live_form.quantity` accepts any known unit of any dimension

- File: `src/puzzle/gui/live_form.py:78-87`; used by `vdi2230_form.arguments`,
  `weld_forms._weld_args` and `evaluate`, `sweep_form._values`/`_range`
- What: only `known(unit)` is checked, and the unit is everything after the first space. A length
  typed as `30 mm mm` is 30 mm², `6 kN` is taken for a weld leg, `16 mm` for an axial load is
  0.016 N after `to_si()`. The forms then compute or store with a value of the wrong dimension and
  say nothing.
- Reproduced: ran (`t1.py`, `t2.py`): `quantity("30 mm mm", "mm")` returns unit `mm mm`, and the
  VDI check with that clamp length returns a full result with a different bolt resilience
  (8.5e-10 against 2.8e-9 m/N); `Quantity(**quantity("6 kN", "mm")).to_si()` is 6000.
- Fix: give `quantity` the expected kind (or the default unit's dimensionality) and raise
  `ValueError("'kN' is not a length")` when pint's dimensionality differs.
- Test: `quantity("6 kN", "mm")` and `quantity("30 mm mm", "mm")` raise; the VDI form shows the
  message instead of a result.

### PANEL-12: VDI 2230 check… raises ZeroDivisionError while typing, and takes impossible values

- File: `src/puzzle/gui/vdi2230_form.py:269-273` (`show` catches `ValueError, KeyError` only),
  `:57-81` (`arguments`)
- What: `vdi2230.check` divides by the moduli, Rp0.2 and the tightening factor. A zero in any of
  them (the state of the field on the way to typing `0.94 GPa` for Rp0.2, or `0.2…`) raises
  `ZeroDivisionError` out of the preview slot, which Puzzle shows as "Something went wrong".
  Separately, a negative or `nan` friction, `inf` clamp length and a negative or fractional
  interface count are accepted (`int(1.7)` becomes 1 silently) and a verdict is shown; with `nan`
  the verdict line reads "= nan (fails)".
- Reproduced: ran (`t1.py`) at the level of `vdi2230.check(**arguments(values))`: e_bolt `0`,
  e_plate `0 GPa`, rp02 `0`, tightening factor `0` with a load, and `1e400 GPa` all raise
  `ZeroDivisionError`; mu_thread `-1` and `nan`, interfaces `-1` and `1.7`, clamp length `inf`
  return results. The popup itself is read from `gui/errors.py`.
- Fix: catch `ArithmeticError` in `show` too, and validate in `arguments` (moduli, Rp0.2, αA > 0;
  0 ≤ μ; interfaces a whole number ≥ 0; finite values) with messages naming the field.
- Test: set each of those fields through `dialog.set` and assert `view.result is None` with a
  message, and no exception.

### PANEL-13: Result forms re-read the whole mesh and results from the store on every keystroke, on the GUI thread

- File: `src/puzzle/gui/result_tools.py:60-66` (`_run_of`), `:68-93` (`selection_choices`),
  `:95-101` (`quantity_choices`); `src/puzzle/gui/export_dialog.py:20-27`;
  `src/puzzle/gui/mesh_shootout.py:19-37`
- What: these are `Ask.choices` callbacks. `QuickForm._refresh` calls every callable `choices`
  on every change of any field, including each character typed in Name. Each call does
  `store.read_mesh` and `store.read_results` for the whole run.
- Reproduced: ran (`t4.py`) on the 13 190-node cantilever with one result step: Result on a
  selection: 2 mesh and 2 result reads on opening, 10 of each for five characters typed in Name
  (1.0 s); Path: 5 of each (0.5 s). The cost grows with nodes times steps, so a transient or large
  run makes the form unusable. The Export and Mesh shoot-out callbacks are the same pattern, read
  only (the shoot-out ran on a study with no run).
- Fix: cache per (source id, run id) for the life of the form (a dict closed over by the lambdas),
  and read names only: `ResultStore.field_catalog` for quantities and steps, the groups table for
  selections.
- Test: patch `ResultStore.read_results` with a counter, open the form, type in Name, assert the
  count does not grow.

### PANEL-14: Header sorting compares text: numbers sort wrongly, and the Table viewer sorts only the 500 rows drawn

- File: `src/puzzle/gui/table_viewer.py:74-75, 89` (`sortItems`);
  `src/puzzle/gui/bolt_table.py:199, 207` (`setSortingEnabled(True)` on text items)
- What: cells are `QTableWidgetItem(text)`, whose ordering is by string. In the Table viewer
  "1e+06" sorts before "2" and "10" before "9", and `sortItems` works on the first `ROWS` = 500
  rows drawn, so sorting a result table by stress does not bring the table's maximum to the top
  (the title only says "the first 500 shown"). In the bolt table Diameter, Length, Preload and
  Thread Kf sort as text ("12 mm" before "8 mm").
- Reproduced: read only (Qt's documented item ordering).
- Fix: Table viewer: sort the polars frame (`t.frame.sort(name)`) then redraw `head(ROWS)`;
  toggle the direction on a second click. Bolt table: set a numeric sort key
  (`item.setData(Qt.EditRole, number)` with a display delegate, or a `QTableWidgetItem` subclass
  with `__lt__` on the SI value).
- Test: a table operator with 600 rows whose largest value is in row 550; click the header twice;
  assert the first drawn row holds it. Bolt table: M8 and M12 bolts, sort by Diameter, assert
  M8 first.

### PANEL-15: Cyclic symmetry… resets an axis or origin it cannot show

- File: `src/puzzle/gui/symmetry_dialog.py:75, 101-102, 115-121`
- What: the form offers X, Y, Z through the origin only. For an existing `CyclicSymmetry` with
  another axis (0, 0, −1; a skew axis) or a non-zero origin (set by an agent or MCP
  `symmetry_repeat`), the Axis dropdown falls back to its first entry and OK writes
  `origin (0, 0, 0)` and that axis, even when only Sectors was changed. The same face can also be
  chosen for both cuts.
- Reproduced: read only.
- Fix: add the existing axis as a choice when it is not one of the three ("As it is: (…) about
  (…)") and keep `origin` when unchanged; refuse `source == target` in `check`.
- Test: a component with `cyclic.origin=(0.1, 0, 0)`; run the form changing sectors; assert the
  origin is kept.

---

## Low

### PANEL-16: Optimisation plot: a design variable named c1, c2, … is read as a constraint

- File: `src/puzzle/gui/optimisation_panel.py:487` (`what.startswith("c") and what[1:].isdigit()`)
- What: "Objective against c1" has data `"c1"`, the same key as the second constraint's plot. It
  plots that constraint by point, or raises `IndexError` when there is none (from `_render`, so on
  every refresh while chosen). Read only.
- Fix: distinct keys (`("variable", name)`, `("constraint", i)`).
- Test: an optimisation with a variable `c1` and no constraints; choose that plot; no exception
  and x values are the variable's.

### PANEL-17: Forms show Python's own conversion messages

- Files: `weld_forms.py:158-171` (`_weld_args`, `_cycles`), `sweep_form.py:64-73`,
  `selection_rule.py:40-47`, `vdi2230_form.py:37-43`
- What: a blank or mistyped number blocks OK with `could not convert string to float: ''` (ran,
  `t1.py`/`t2.py`: clamp length `30mm`, a rule's distance left empty, a box corner `1 mm`).
  `frame_editor.py` already replaces this text; the others do not.
- Fix: one helper in `live_form` ("'30mm': a number, then a space and a unit") used by all.
- Also in `selection_rule.rule_of`: `min_area > max_area`, `nan` coordinates and a negative
  angle are accepted (ran).

### PANEL-18: Run lists show solver keys and UTC times

- Files: `bolt_results.py:132-133`, `weld_table.py:100-101`, `member_design.py:176-177`
- What: `f"{r.study_name} ({r.solver}, {when})"` prints `calculix`, `code_aster`, `pynite`, and
  `recorded_at.strftime` without `.astimezone()` prints UTC. The contact tool's run list
  (`contact_tool.py`) uses `solver_label` and local time. Read only.
- Fix: `solver_label(r.solver)` and `r.recorded_at.astimezone()` in all three (one shared helper).

### PANEL-19: Hard-coded units beside display-unit values (ADR 0024)

- `weld_table.py:136-137, 199` (kN, N/mm), `vdi2230_form.py:88-93, 102, 114` (kN, MPa, mm², µm,
  while lengths follow the display unit), `bolt_results.py:182-185, 215-225` (N·m; plot axes in
  mm and N), `field_wizard.py:350` (mm) and its Alignment page (metres), `beam_dialog.py:327`
  (cm⁴), `member_design.py:290-293` (N·m). All are labelled, so nothing is wrong numerically; they
  do not follow Settings, Units. Read only.

### PANEL-20: Edit sweep… drops a parameter whose list operator is no longer offered

- File: `src/puzzle/gui/sweep_form.py:141-149, 164-167`
- What: when a stored `SweepParameter.source` exists but `list_operators` is empty or lacks it
  (operator deleted, rewired, no longer a list), the default `"operator"` is not among the
  choices, the combo falls to "Not swept", and OK removes that parameter from the step without a
  word. Read only.
- Fix: keep the stored source as a choice marked "(missing)" and block OK with a message.

---

## To check (not verified)

- `selection_rule.py:250-276`: `highlight` redraws the main view (possibly switching it to the
  part's standalone preview) on every change; nothing restores the view on Cancel. The first
  preview also regenerates the geometry under `occ_lock` on the GUI thread.
- `weld_forms.py:431-434, 454-459`: `evaluate` reads the mesh and results on the GUI thread on the
  first preview; `show` catches `ValueError` and `PuzzleError` only, so a `KeyError` from
  `weld_fields.fatigue` on a run without weld fields would reach the error popup.
- `bolt_results.py:139-152` and `contact_tool.py` `ResultsTab._steps`: `read_results` of the whole
  run on the GUI thread, uncaught, on every `MainWindow.refresh` while the dock is shown.
- `window.show_tool` adds each new tool panel to the stack and never removes it; `MeasurePanel`
  stays connected to `picked_changed` unless closed with its own Close, so exact OCC measurements
  may keep running on every pick while another panel shows.
- `legend_panel.py:176-190`: a custom range is kept as display-unit numbers in `Viewport.ranges`;
  after Settings, Units changes the same numbers may be applied in the new unit.
- `compare.py:218-228`: `_follow` sets the other views' cameras and refreshes them; if
  `camera_moved` is emitted by `refresh`, the views would call each other.
- `optimisation_panel.py`: Run pressed twice overwrites `window.optimisation_stop`; whether
  `window.background` refuses the second run was not checked.
- `bolt_table.py:272-306`: editing Name with several rows selected gives every bolt the same name.
- `sections_panel.py:142-149`: renaming a cut to blank leaves a blank item until the next refresh.
- `frame_editor.py:252-263`: the DXF is copied into the project before the Create is submitted; a
  refused Create leaves the file.
- `material_builder.py:369-413`: `CurveBuilder.update_curve` catches `ValueError` only; a zero
  modulus may raise `ZeroDivisionError` from `materials.curves.hardening`.

---

## Coverage

Read fully: live_form.py, agent_prefill.py, weld_forms.py, vdi2230_form.py, sweep_form.py,
mesh_shootout.py, selection_rule.py, quick_form.py (as the forms' base), bolt_table.py,
bolt_results.py, weld_table.py, contact_tool.py, member_design.py, result_tools.py,
export_dialog.py, study_materials.py, sections_panel.py, symmetry_dialog.py, frame_editor.py,
element_choice_dialog.py, measure_panel.py, table_viewer.py, legend_panel.py, compare.py,
linearization_panel.py, field_wizard.py, errors.py.

Read in part: material_builder.py (Curve, TableEditor, CurveBuilder.update_curve, the fatigue
tab, load/material, tables and curves, MaterialBuilder actions; not the library tree, Duplicates,
MaterialPicker), optimisation_panel.py (from the New optimisation form on), construction.py
(the plane panel and helpers; not PathPanel or PointRow), beam_dialog.py (section dialog's
construction and `value`), versions_panel.py (list and actions), solution_panel.py (`Plot` only),
solvers_panel.py (threads and `_show`), settings_dialog.py (the key test thread only).

Grepped only (unit handling, hidden-field access, threads): axes_panel.py, bolt_types.py,
fastener_dialog.py, thread_dialog.py, contact_search.py, hosts_dialog.py, material_files.py,
jobs_panel.py, text_viewer.py, convergence_panel.py.

Not reached: derived_dialog.py, field_view.py, field_align_panel.py, advisor_panel.py,
help_viewer.py, start_page.py, paraview_open.py, visibility.py, theme.py, ribbon.py,
spacemouse/, the rest of settings_dialog.py, solution_panel.py, jobs_panel.py,
convergence_panel.py and text_viewer.py.

## Second pass (recovered)

The reviewer's second pass was not saved to this file; what follows is its summary as handed back on 2026-11-16, recovered from the session on 2026-11-17. The reproductions' scripts were scratch files and are not kept.

Second pass done: 14 new findings (PANEL-21 to PANEL-34), 6 medium and 8 low, none critical or high. Only PANEL-23 was reproduced, and only at the folder-creation step; the other 13 are from reading the code.

They are appended under "Second pass" in:
<scratch>\review\gui-panels-bugs.md

Nothing in the repository was changed.

### Medium
- **PANEL-21** `thread_dialog.py:44-49`: Thread… takes a bare number as millimetres, not the unit shown (metres by default), and its labels name no unit.
- **PANEL-22** `thread_dialog.py:35-38, 119-121`: opened from the menu bar, every default comes from the first contact, so choosing another contact and pressing OK overwrites that contact's thread and type.
- **PANEL-23** `paraview_open.py:60-66`, `paraview_link.py:84`: Open in ParaView with a derived result uses `derived:<uuid>` as a folder name. On Windows the `mkdir` raises `NotADirectoryError [WinError 267]` (ran here); the full action needs ParaView and was not run.
- **PANEL-24** `derived_dialog.py:182-190`: editing a combination whose case's study has no completed run silently moves that case to the first study in the list.
- **PANEL-25** `convergence_panel.py:44-47, 246-258`: a stored value the dropdown has no choice for is replaced by the first choice on OK (an `adapt.passes` of 10 becomes 3; same for ratio and levels).
- **PANEL-26** `derived_dialog.py:854-890`: the Fatigue dialog runs the whole fatigue evaluation on the GUI thread at every field change; the Error estimate form re-reads results per change the same way.

### Low
- **PANEL-27** Edit… panels for coordinate systems, paths and planes fill fields at six digits and rewrite every field on Apply, so a rename shifts values and changes the content hash.
- **PANEL-28** Find contacts…: the gap spin box has four decimals in the unit shown, so in metres anything under 0.1 mm rounds to zero and becomes the automatic tolerance.
- **PANEL-29** Add fastener… validates after it has closed, and blames the diameter whichever number was wrong.
- **PANEL-30** Convergence and optimisation plots are in SI with no unit, beside tables in the units shown.
- **PANEL-31** The log window resets its whole text every 0.7 s while the file grows, losing the cursor and selection.
- **PANEL-32** Settings saves preferences and agent settings before the "act" sign-off, so declining and then cancelling does not discard them.
- **PANEL-33** The Material builder drops unsaved edits on another click, New or Close without asking.
- **PANEL-34** Field alignment helpers raise on a non-number in the pairs table, and surface PANEL-4's errors as popups.

The report also lists 10 more unverified "to check" items. One is worth knowing: Solver output… with a derived result shown looked like it would raise, but did not on the two tree selections I tried.

### Coverage
- **Read fully this pass:** bolt_types, thread_dialog, material_files, contact_search, convergence_panel, text_viewer, field_align_panel, field_view, paraview_open; optimisation_panel is now whole.
- **Read in part:** fastener_dialog, axes_panel, construction, derived_dialog (combination and fatigue dialogs, not the equation dialog), jobs_panel, solution_panel, settings_dialog (save, autonomy, key test, project units; not the pages or provider rows), hosts_dialog, advisor_panel (action dispatch), help_viewer (F1 key lookup only), start_page, material_builder, versions_panel.
- **Still not reached:** visibility, which you asked for, plus theme, ribbon and spacemouse/ from the original scope.
