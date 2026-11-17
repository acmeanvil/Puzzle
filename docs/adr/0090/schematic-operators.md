# Review: the schematic view and its operators

Reviewer brief: "Review the schematic view and its operators and suggest ways to improve its
functionality and the user experience." Branch `development` at `da26cde`, Windows 11, pixi `dev`
environment, Qt offscreen. Nothing in the repository was changed.

## How this was done

- Read: CLAUDE.md rows, ADRs 0058, 0062 (start), 0071, the three skills, the guide, and the code
  (`schematic/__init__.py`, `layout.py`, `gui/schematic_view.py` in full; `gui/operators.py`,
  `operators/__init__.py`, `evaluate.py`, `expression.py` mostly; `frames.py`, `reshape.py`,
  `series.py`, `scripts.py`, `report.py` by targeted reads).
- Ran: `MainWindow` on scratch copies of `examples/cantilever` (its three studies solved on CalculiX,
  13 190 nodes) and `examples/bracket_assembly`, operators added through `session.submit`, about
  150 operators in all, a real 3-point sweep, pictures of the canvas at 15, 36 and 55 nodes.
- Scripts and pictures: `<scratch>\review\schematic\`
  (`harness.py`, `run1.py` ... `run8.py`, `pics\*.png`). Each bug below names the script that
  reproduces it. Run one with
  `PYTHONUTF8=1 .pixi/envs/dev/python.exe <script>` from the repo root.

"Reproduced" means I saw it happen. "Read" means it follows from the code and I did not run it.

---

## Part 1. Bugs

### Operators (OP-n)

**OP-1. A before-solving Formula that raises anything but `PuzzleError` makes the project unusable.**
Severity: critical. Reproduced (`run3.py`, section 11).
- Where: `src/puzzle/operators/__init__.py:1767-1771` (`parameter_values` catches `PuzzleError`
  only), `src/puzzle/operators/evaluate.py:1377-1396` (`shown`, the same), and
  `src/puzzle/operators/expression.py:134-138` (only pint's `DimensionalityError` is turned into a
  `PuzzleError`).
- What: a Formula with the text `10.0**400` is accepted by `create`. From then on
  `OverflowError: (34, 'Result too large')` comes out of `parameter_values`, which is called
  wherever parameters are read. Measured: `MainWindow(root)` raises on opening the project;
  `window.refresh()` raises; `puzzle run PROJECT --study "Tip load"`, `puzzle outline`,
  `puzzle operators`, `puzzle schematic` all exit 1 with a traceback. Only `puzzle studies` and
  `puzzle advise` still work. The user cannot open the project to delete the operator.
- Other texts that do the same: `'a' * 3` (numpy `UFuncTypeError`), `20 degC + 5 K` (pint
  `OffsetUnitCalculusError`), `min()` (`IndexError`), `2**2**2**2**2` (`OverflowError`).
- Fix: (1) in `expression.evaluate`, wrap `walk(tree)` in `except (ArithmeticError, TypeError,
  ValueError, IndexError, pint.errors.PintError)` and raise `PuzzleError(code="invalid")` with the
  operator's words ("the result is too large", "a text can't be multiplied"). (2) In
  `evaluate.outputs`, catch every `Exception` that is not a `PuzzleError` and re-raise as
  `PuzzleError(f"'{op.name}': {type(exc).__name__}: {exc}", code="invalid", path=op.name)`, so no
  caller ever sees a raw exception from an operator. (3) Validate a Formula at `create`/`edit`
  time by evaluating it once when it reads no results, and refuse the command on error.
- Test: for each text above, `create` then `parameter_values(project)`, `sch.build(project)`,
  `Project.problems()` and `run_hashes(project)` must not raise; the operator has a problem
  `operator_input`.

**OP-2. A value that follows a parameter leaves its study "out of date" for ever, right after a successful run.**
Severity: high. Reproduced (`run6e.py bound-only`; `run6d.py oplist`).
- Where: `src/puzzle/solve.py:118` applies `operators.with_parameters(project)` before
  `keyed = project` at `:162`; the run key at `:178` is hashed from the substituted project.
  `src/puzzle/project/status.py:150-160` hashes the stored project.
- What: a load `{"value": 100, "unit": "N", "parameter": "load"}` with the Number `load` = 150 N.
  Run the study: `status()` says `out_of_date`, "the model changed since the last run". It is up
  to date only while the stored `value` happens to equal the parameter's value. After any slider
  drag or parameter edit the stored value is the old one (the skill says "`value` keeps the
  last"), so this is the normal state of every wired load.
- Fix: hash a bound quantity as its parameter name, not its stored value (in `graph._own`, drop
  `value` where `parameter` is set; the study already hashes every parameter), or have
  `status`/`run_hashes` hash `with_parameters(project)`. The first is cheaper.
- Test: the reproduction above must give `up_to_date`; then editing the Number must give
  `out_of_date`; then a run must give `up_to_date` again.

**OP-3. Adding any before-solving operator puts every study out of date, even one that feeds only a check.**
Severity: high. Reproduced (`run2.py`, section 1).
- Where: `src/puzzle/project/graph.py:22-27`: every study depends on every `pre_solve` operator.
- What: with all three studies up to date, `create` a Number `Sy` = 235 MPa wired to nothing: all
  three become `out_of_date`. Undo restores them. Model value is a before-solving operator too, so
  the guide's own recipe (Result summary → Check, with a Model value's yield as the limit)
  invalidates every stored run the moment the Model value is added. The user is then told to
  re-solve to see a check whose inputs did not change.
- Fix: a study depends on a before-solving operator only if the operator's name is used by what
  the study reads: a history argument, a `Quantity.parameter`, a `Feature.when`, a sweep parameter,
  a selection rule, a construction plane's `drives`, an equation, or another such operator
  upstream of one. `operators.model_uses(project, op)` already lists most of these. Parameter sets
  could get the same treatment later, but they are out of this review's scope.
- Test: the reproduction; and the reverse (a Number named in a history argument still puts the
  study out of date when edited).

**OP-4. An operator on a swept study reads whichever point ran last, and does not say so.**
Severity: high. Reproduced (`run5.py`).
- Where: `src/puzzle/post/derived.py:374-391` (`run_for`: the newest completed run), called from
  `src/puzzle/operators/evaluate.py:328-336`.
- What: Tip load swept over 100, 200, 300 N; the slider `load` on the canvas shows 100 N; a Result
  summary on the study shows 361.5 MPa (the 300 N point) and the Check on it shows "Fail, margin
  −35 %". Nothing on the node, in Properties or in `operator_value` says which point was read.
  The same function serves shoot-outs (the last solver to finish) and, by reading the code, mesh
  convergence and adapt runs, which are stored runs of the same study (`mesh: level k`,
  `adapt: pass k`): after a convergence study a check may silently read a coarse or the finest
  level. Not reproduced for those.
- Fix: (1) show the run read: add `ResultValue.point` and put it in the node's subtitle
  ("at load = 300 N", "Code_Aster", "mesh level 3") and in `summary()`. (2) Give Result summary,
  Keep result where, Result table and the result tools a `point` setting: "last run" (today),
  "every point: worst", or a named point; Check on a swept study should default to the worst
  point. (3) `run_for` should skip runs labelled as convergence or adapt passes unless asked.
- Test: the sweep above; a Check with the worst-point default fails at 300 N and its node names
  the point.

**OP-5. A raw exception in one after-solving operator stops the schematic drawing and `puzzle operators`.**
Severity: high. Reproduced (`run2.py` section 4 for the list, `run3.py` section 9 for the effect).
- Where: `src/puzzle/operators/evaluate.py:1395` (`except PuzzleError`), reached from
  `src/puzzle/schematic/__init__.py:598`.
- What: with one Pick one value (`to_number`) set to `column="part"`, `reduction="mean"`,
  `SchematicPanel.refresh()` raises `TypeError: float() argument must be a string or a real
  number, not 'NoneType'` and the canvas is not redrawn; `puzzle operators` exits 1.
- Reproduced raw exceptions (each from a plausible setting):

  | Operator | Setting | Exception |
  | --- | --- | --- |
  | Pick one value | mean of a text column | `TypeError` |
  | Keep rows where | `part > 3` | `TypeError` ('>' between str and float) |
  | Keep rows where (polars) | `pl.col("deformation") * 2` | polars `InvalidOperationError` |
  | Calculated column (polars) | `pl.col("deformation") * 7**7**7` | polars `InvalidOperationError` |
  | Read off a table | `by` a text column | polars `InvalidOperationError` |
  | List item or statistic | `max` of an empty list | `ValueError` (`evaluate.py:1306` runs before the length test at `:1307`) |
  | List item or statistic | `replace` at index 7 of 3 | `IndexError` (`evaluate.py:1213`) |
  | List item or statistic | `percentile` 150 | `ValueError` (`evaluate.py:1300`) |

- Fix: the catch-all in `evaluate.outputs` proposed in OP-1 (2) covers all of these. Then fix each
  at its source with a message in words: test the column's dtype in `frames.to_number` and
  `_lookup`; in `frames` expressions refuse comparing a text column with a number; require a
  Boolean result from a polars filter; bound the index and the percent in `_list`/`_mode`.
- Test: a parametrised test over the table above: `ev.shown(ctx, op)` returns a message and
  `sch.build(project, store=store)` returns.

**OP-6. Check against limit accepts a limit with no unit; the guide says it is refused.**
Severity: medium. Reproduced (`run2.py`, section 2).
- Where: `src/puzzle/operators/evaluate.py:583-589` (`_same_kind` raises only when both kinds are
  set) used at `:608`.
- What: peak stress 120.5 MPa against the typed limit `200`: "Fail, margin −100 %"; against `2e9`:
  "Pass, margin +1560 %". The guide ("a stress checked against '200' is refused") and the skill say
  otherwise. A pass is possible with a bare number that the user meant in MPa only by typing a
  huge one, but for `at_least` checks a bare `200` passes any stress above 200 Pa.
- The same gap in the intent-level curve operators (`run7.py`, section B): Utilisation of a stress
  curve by the bare number `235` gives "Utilisation: 4486 Pa to 1.3e+05 Pa"; by `2 mm` gives
  "Utilisation: 5.271e+08 Pa/m ...". A utilisation with a unit is always a mistake.
- Fix: in `_check`, refuse when exactly one side has a kind ("Peak is a stress; give the limit a
  unit, such as 200 MPa"). In `series` for `utilisation`, `safety_factor` and `margin` refuse a
  result that is not dimensionless ("a utilisation divides like by like: A is a stress, B a
  length"). Divide (A ÷ B) stays free.
- Test: both refusals; kind-less against kind-less still allowed.

**OP-7. A passing check on a negative value reports a negative margin.**
Severity: medium. Reproduced (`run2.py`, section 3).
- Where: `src/puzzle/operators/evaluate.py:203-210`.
- What: minimum σxx = −144.3 MPa, `at_most` 250 MPa: "Pass, margin −273 %". The skill says "a
  negative margin fails". Zero against 250 MPa gives "margin inf". Reports and optimisation
  constraints read `margin`.
- Fix: define the margin on distance to the limit: `(limit − value) / |limit|` for at most,
  `(value − limit) / |limit|` for at least (and say so in the help), or keep the ratio only when
  value and limit have the same sign and otherwise show "n/a". For "is the magnitude within the
  limit" add `compare: within` (|value| ≤ limit), which is what users checking a compressive
  stress want.
- Test: sign cases (−, 0, +) for both comparisons: `passed` and `margin >= 0` always agree.

**OP-8. A wire from a study port that does not exist silently reads another quantity.**
Severity: medium. Reproduced (`run2.py`, section 6, "unknown port").
- Where: `src/puzzle/operators/__init__.py:1395-1396` (any port of a study is a `result`) and
  `src/puzzle/operators/evaluate.py:459-460` (`... or items`: an unknown field falls back to every
  item).
- What: Result summary wired from `{"source": "Tip load", "port": "banana"}` is accepted and shows
  0.001522 m, the maximum total deformation. A typo by an agent (`stres`), or a physics removed
  from the study later, gives a number of a different quantity with no error.
- Fix: in `_field`, when `given.field` is set and no item has it, raise `not_found` naming the
  fields the run has. In `refuse`, accept a study port only if it is `result`, `points` or one of
  `schematic.outputs(project, study)` plus the fields of its newest run.
- Test: the reproduction raises; a real field still works; a run holding a field the catalog
  lacks still works.

**OP-9. `evaluate.summary` returns a tuple for a Report.**
Severity: medium. Reproduced (`run3.py`, section 10).
- Where: `src/puzzle/operators/evaluate.py:1517-1520` (`return f"to {path_of(op)}", ""`, copied
  from `shown`).
- What: `puzzle operators PROJECT Rep --json` prints `[["to exports/Rep.html", ""]]` where every
  other operator gives `{"name", "type", "outputs", "shown"}`. MCP `operator_value` gets the same.
- Fix: `out["shown"] = f"to {path_of(op)}"; return out`. Also list its items' verdicts so an agent
  can read "2 of 3 checks pass" without writing the file.
- Test: `summary` returns a dict with `name` for every type in `TYPES`.

**OP-10. Rows to columns with its defaults makes a table of tens of thousands of columns.**
Severity: medium. Reproduced (`run2.py`: "13190 rows × 52345").
- Where: `src/puzzle/operators/reshape.py:84-93`: `on` defaults to the last column, a float one.
- Fix: require `column`; refuse an `on` column with more than, say, 200 distinct values, or a
  float column, with the count in the message. Same guard in `pairs` cross (`evaluate.py:1321`:
  1500 × 1500 took 3.6 s; 100 000 × 100 000 is allowed by `_count`).
- Test: default pivot of a result table is refused in under 0.1 s.

**OP-11. Pair up lists loses a column when both lists have the same label.**
Severity: low. Reproduced (`run7.py`: "2250000 rows × 1: R1").
- Where: `src/puzzle/operators/evaluate.py:1328`: `Table([a.label or "a", b.label or "b"], ...)`;
  equal names collapse in the dict at `:71-75`.
- Fix: suffix the second name (`R1_2`) when equal. Make `Table.__init__` refuse duplicate names.

**OP-12. A step index past the end is clamped without a word.**
Severity: low. Reproduced (`run2.py`, section 5: `step=9` and `step=-9` on a one-step run).
- Where: `src/puzzle/operators/evaluate.py:446`.
- Fix: raise `not_found` ("Tip load has 1 step; step 10 does not exist"). A stored step index is
  fragile anyway: see OP-NEW-6 (pick a step by its value).

**OP-13. Slider and table settings that are accepted but meaningless.**
Severity: low. Reproduced (`run2.py`, section 4).
- Slider with `low` 10 mm above `high` 1 mm shows 0.001 m (`evaluate.py:774-777`); an `increment`
  of 1 N on a length slider snaps 5 mm to 0 m (`:771-773`, no kind check). Largest N rows with
  count −3 returns 13 187 rows. Calculated column accepts an empty `new_name` and silently
  replaces an existing column (`x`). `1/0` in a Formula shows `inf`, and Divide by zero shows
  "nan to nan", both without a warning.
- Fix: validate in the `Operator` model or in `operators.refuse`: low < high, increment's kind,
  count ≥ 1, a non-empty new name; show "division by zero" as an error state.

**OP-14. Units a structural engineer needs are refused or misnamed.**
Severity: medium (usability). Reproduced (`run1.py`, `run2.py`).
- `(10 mm)**4 / 12` (second moment of area): "the result's unit (m⁴) is not one Puzzle shows".
  `50 MPa * sqrt(1 m)` (fracture toughness): refused. `5 N * 2 m` shows "10 J" (a moment shown as
  energy). `5 %` is a syntax error. The integral of a stress is documented as losing its unit.
- Where: `src/puzzle/operators/expression.py:142-146`, `model/units.py` `DIMENSIONS`.
- Fix: add kinds for second moment of area (m⁴), section modulus (m³, shown as such when it comes
  from length³), moment (N·m, preferred over energy when the formula multiplies a force by a
  length), stress intensity factor, and fall back to showing any other result in its SI base unit
  as text instead of refusing. Give `_reduce`'s integral the kind `kind × length^dim`.

**OP-15. Error texts that do not help.**
Severity: low. Reproduced.
- `x.bit_length()` and `(lambda: 1)()` in a Formula: "no function 'that'". `decimals=-2` on Round,
  or an unknown input key: "[invalid] invalid operator" (pydantic's reason is dropped; an agent
  cannot correct itself). Add (A + B) with a bare number: pint's raw sentence. A missing-input
  message starts with the operator's own type and name, so on the node it reads
  "check against limit 'Missing':" and the reason is cut off (`pics\32-dark.png`).
- Fix: pass pydantic's first error (field and message) through in `code="invalid"` details and
  text; drop the "'<name>':" prefix from `shown()` (the node already has the name).

**OP-16. Constant folding in the polars syntax is unbounded.**
Severity: low to medium (a project from elsewhere). Partly reproduced: `7**7**7` (695 975 digits)
is computed by Python before polars refuses it. Not tried with a larger tower, which would hang.
- Where: `src/puzzle/operators/frames.py:628-629`: `operator.pow` on Python ints; also `"ab" * n`.
- Why it matters: expressions are evaluated when a project opens (the schematic's refresh, on the
  GUI thread), before any trust question.
- Fix: refuse `**` unless the exponent is a constant ≤ 64 and refuse `str * int`; or build
  `pl.lit` for constants first so polars does the arithmetic.

**OP-17. The guide's first recipe cannot be followed on the shipped material.**
Severity: low. Reproduced (`run1.py`, first attempt).
- Model value with `property="Yield strength"` on generic Structural steel: "Structural steel has
  no value 'Yield strength'". `materials/generic.toml:13-27` has no `yield_strength` for it (the
  ASTM grades have). The guide's "A check against yield" names exactly this.
- Fix: give the generic structural steel a yield (S235: 235 MPa, with the source), or make the
  message name the materials that have one and offer to type the limit.

### Schematic view (SCH-n)

**SCH-1. Every refresh builds the schematic twice and hashes the whole project once per study, on the GUI thread.**
Severity: high (responsiveness). Reproduced (`run4.py` profile; `run7.py` section C).
- Where: `src/puzzle/gui/schematic_view.py:658` calls `_node_ids` (`:681-686`), which runs
  `sch.build(project, runs, states={})` only to learn the node ids; then `:665` builds again.
  Before-solving operators are evaluated in both (`schematic/__init__.py:590-601`: "pre-solve ones
  always show their values"). `schematic/__init__.py:496` calls `run_state` per study, each of
  which calls `run_hashes(project)` afresh (`project/status.py:157`).
- Measured: 55 nodes, 40 operators: 0.31 to 0.49 s per `panel.refresh()`, of which 0.23 s is six
  `content_hashes` calls and 0.125 s the extra build; one edit through `window.submit` 0.29 s. With
  one slow before-solving operator (a 1500 × 1500 Pair up lists, 3.6 s) the refresh took 7.65 s,
  the operator being evaluated twice. A 13 190-node result table costs 0.1 to 0.3 s each time it
  is re-read; a real model is 100 times that.
- Fix, in order of value: (1) delete the second build: compute the possible node ids from the
  project's object ids, feature paths and run study ids directly. (2) Compute `run_hashes` once
  per build and pass it to `run_state`. (3) Cache operator outputs across refreshes keyed by
  (operator content hash, upstream hashes, run ids read); a refresh after an unrelated edit then
  evaluates nothing. (4) Evaluate after-solving operators off the GUI thread
  (`MainWindow.background`), drawing nodes at once with a "working" mark and filling values as
  they arrive. (5) Stop `_draw` clearing and rebuilding the whole scene on every change; diff
  nodes by id.
- Test: a counter on `sch.build` and on `_EVALUATE` calls per refresh (1 build; 0 evaluations
  when nothing an operator reads has changed); a time budget for 200 nodes.

**SCH-2. Wires that carry data cannot be removed on the canvas.**
Severity: medium. Reproduced (`run4.py`: the context menu handles `kind == "use"` only).
- Where: `src/puzzle/gui/schematic_view.py:357-363`.
- What: the guide says "Right-click a wire to remove what it stands for". For an operator's input
  wire, a transfer, a sweep wire, a value following a parameter and an imported field's wire,
  right-click does nothing. The only way to unwire an operator is the Properties dropdown's "Not
  wired". Also a use wire is 1 px wide and has no wider hit shape, so it is hard to hit.
- Fix: `WireItem.contextMenuEvent` for data wires: "Remove" (an operator input: one `Edit`
  deleting `inputs.<key>`; a transfer: delete the transfer; a sweep parameter: remove it; a bound
  quantity: clear `parameter`), "Go to source", "Go to target", "Insert an operator here…".
  Override `WireItem.shape()` with a `QPainterPathStroker` of 10 px. Select a wire on click and
  delete it with Delete.
- Test: each kind of data wire removed through the menu is one undoable command.

**SCH-3. Out-of-date values are drawn as current ones.**
Severity: medium. Reproduced (`run3.py` section 8, `pics\07-stale.png`).
- What: after the load is doubled, the Check still shows a bold green "Pass, margin +66 %" from
  the old run; only the small dot and the 4 px side bar turn amber. With the new load it would
  fail. A report exported now would say "pass".
- Where: `src/puzzle/gui/schematic_view.py:170-175` draws `value` without reading `node.state`.
- Fix: when `node.state` is `out_of_date`, draw the value muted and struck through or prefixed
  "was", and the verdict in the muted colour with "(out of date)"; `report.write` should print a
  banner "N items are from runs that are out of date" (and `summary()` a `stale: true`).
- Test: build with a stale study: the node's `value_state` is "stale"; the report has the banner.

**SCH-4. A wire let go on a node, but not on the row of an input that takes it, replaces another input's wire without asking.**
Severity: medium. Reproduced (`run4.py`: a Number dropped on Check 1 replaced its wired Value).
- Where: `src/puzzle/gui/schematic_view.py:984-991`: if the row under the cursor does not accept
  the type, the first input that does is taken, wired or not.
- Fix: prefer the first *unwired* accepting input; if every accepting input is wired, ask
  (`ask_form`: "Replace Value (from Peak) / Replace Limit (typed 235 MPa)") or refuse with the
  status message. While dragging, light the sockets that accept the wire and dim the rest
  (see SCH-UX-3), so the user aims.
- Test: the reproduction leaves Value as it was and sets Limit.

**SCH-5. Text on a node is clipped mid-letter, and the important part is what is lost.**
Severity: low. Reproduced (`pics\03-chains-closeup.png`, `pics\21-sweep-after-run.png`).
- Where: `src/puzzle/gui/schematic_view.py:159` takes `metrics` from the plain font, then elides
  bold text with it (`:168`, `:174`), so bold text overruns and is cut by the clip with no "…".
  Port labels are not elided at all (`:229`).
- Seen: "the result's unit (m⁴) is not or", "Use field: 13190 of 13190 nod", "Maximum equivalent
  (von Mis", "study 'Tip load' has no run ye".
- Fix: `QFontMetricsF(bold)` for bold rows; elide port labels; let a value row wrap to two lines
  for errors; shorten the texts at the source: a field shows its range ("0.0045 to 0.51"), a table
  "13 190 × 7", a curve its y range first, an error a short reason with the full text in the
  tooltip and Properties.

**SCH-6. The search box searches only names and kinds, and only inside the selection's chain.**
Severity: low. Reproduced (`run4.py`: with a node selected, "check", "pass", "MPa" and "Result
summary" each lit one node).
- Where: `src/puzzle/gui/schematic_view.py:787-794`: matches `label` and `KIND_WORDS` only, and
  intersects with the selection's upstream. Enter goes to the first match only (`:841-846`).
- Fix: also match the subtitle (the operator's type), the value text, the state and tags; do not
  intersect with the selection; show "3 of 12" and step through with Enter / Shift+Enter; Ctrl+F
  focuses the box.

**SCH-7. Properties shows an operator's type under its old name, as an editable dropdown.**
Severity: low. Reproduced (`run7.py` section A, `pics\35-properties-check-light.png`).
- What: the node says "Check against limit", "Result summary", "Formula", "Stepped list";
  Properties → Type says "Check", "Reduce", "Expression", "Series" (86 choices from the Literal).
  ADR 0071 decision 7 renamed these. Changing the type there keeps inputs that the new type does
  not have.
- Fix: label the choices from `operators.TYPES[key].label` in `gui/choices.py`; make Type read-only
  for operators, or offer only types whose ports are a superset ("Change to… Safety factor").

**SCH-8. The palette's list cannot be reached from the keyboard while typing.**
Severity: low. Reproduced (`run4.py`: Down in the search box does not move the choice).
- Where: `src/puzzle/gui/operators.py:598-689`: no key forwarding from `self.search`.
- Fix: an event filter sending Up, Down, PageUp, PageDown to the list. Match by word starts and
  initials ("rs" → Result summary) and rank name matches above help-text matches.

**SCH-9. An output's label contradicts its type.**
Severity: low. Reproduced (`pics\10-mid-light.png`: Safety factor nodes with a number's diamond
labelled "Curve").
- Where: `src/puzzle/schematic/__init__.py:565-568` takes the type from `first_type` but the label
  from the spec.
- Fix: label from `TYPE_WORDS[first_type]` when the type follows the inputs ("Value", "Field").

**SCH-10. Contacts and bolts are not on the canvas; loads on two components share a title.**
Severity: low. Reproduced (`run8.py bracket`, `pics\30-bracket.png`).
- What: the bolted bracket's contacts and two fasteners appear nowhere (13 nodes: features,
  geometries, meshes, components, studies, a material). ADR 0071 decision 8 lists "contacts and
  bolts". Two nodes are both titled "Fixed on Base/Bottom" (one per component).
- Fix: a "3 contacts · 2 bolts" row on the component node that unfolds as its parts do, each
  line selecting the item in the tree; subtitle a feature node with its component when more than
  one component has that title.

**SCH-11. Selection is single and has no keyboard.**
Severity: low as a bug, high as a gap (see SCH-UX-1). Reproduced (`run4.py`): Delete does nothing,
Ctrl+A selects nothing more, Ctrl+click on a second node deselects the first
(`schematic_view.py:1238-1246` forces one selected item), the canvas has no `keyPressEvent`
besides Tab, and the drag mode is `ScrollHandDrag`, so there is no rubber band.

---

## Part 2. Improvements

Value: what the user gains. Effort: S under a day, M a few days, L a week or more.

### Canvas (SCH-UX-n)

**SCH-UX-1. Multi-select, rubber band, and the standard keys.** Value high, effort M.
Left-drag on empty canvas draws a rubber band (`RubberBandDrag`); pan moves to middle-drag and
Space+drag (and stays on left-drag when a setting says so). Ctrl/Shift+click add to the selection;
the tree follows the last one clicked. Keys: Delete (the tree's delete, as one batch through
`gui/deleting.py`), F2 rename in place, Ctrl+A, Ctrl+C/V and Ctrl+D (copies of the selected
operators with the wires between them, placed at the cursor, one batch: `clusters.commands`
already builds such a batch), Ctrl+Z/Y, F or Home fit all, Shift+F fit the selection, arrows walk
along wires (right: first downstream node), Ctrl+F search, Ctrl+G group the selection, M mute
(SCH-UX-9). Dragging moves every selected node. Precedent: Blender, Houdini, Grasshopper, n8n.

**SCH-UX-2. Edit on the node.** Value high, effort M.
A typed input shows its value on its row ("Limit: 235 MPa") but cannot be edited there. A
double-click on that row opens a line edit in place (a `QGraphicsProxyWidget`, parsed by
`gui.operators.parse_typed`, one `Edit`). The settings that define the node get the same: Result
summary's reduction and quantity, Check's ≤ / ≥, a Formula's text, a Number's value, each as a
small dropdown or field in the node body. Properties stays the full form. Precedent: Blender and
ComfyUI inline widgets, Grasshopper's number slider and panel.

**SCH-UX-3. Feedback while a wire is dragged, and a search when it is let go on nothing.** Value
high, effort M.
While dragging: sockets that accept the wire stay bright and grow, the others dim; hovering a
socket that refuses shows why ("takes a table; this is a curve": `operators.refuse` has the words;
ADR 0071 decision 1 promised this and only the status bar after the drop has it). Let go on empty
canvas: the palette opens there with "Fits here" filtered to the dragged type and the new node is
wired (today only a component does something on empty canvas, `schematic_view.py:933`). Also allow
dragging backwards from an input socket to pick a source, and dragging an existing wire's end off
a socket to move or remove it. Precedent: Unreal Blueprints and Blender's node search on wire
drop; Grasshopper's wire rewiring.

**SCH-UX-4. A layout that stays readable past 30 nodes.** Value high, effort L.
Seen at 55 nodes (`pics\10-mid-light.png`): 3300 × 4000 px, a 900 px empty band in the last
layers, the Number `Sy` in the Resources column wired across three columns to eight checks, wires
drawn straight through study and results nodes, and a fit zoom near 0.1 where nothing can be
read. Causes in `schematic/layout.py`: wires spanning several layers get no waypoints (`:163-212`),
so nothing keeps them out of nodes; vertical placement pulls toward the mean of all neighbours
with no compaction (`_spread`, `:219-229`); ordering ignores which port a wire lands on.
- Put before-solving operators that feed only after-solving ones (a limit, an allowable) in the
  Results area next to their first reader, not in Resources. `is_pre` plus `model_uses` decides.
- Insert dummy nodes for long wires (standard Sugiyama), route wires through them, and order by
  port position; compact each layer after placement.
- Give the operator area its own headings ("Read", "Shape", "Check", "Report") instead of one
  "Results" heading over four unnamed sub-columns.
- "Tidy selection" lays out only the selected nodes; today Tidy forgets every dragged position.
Precedent: KNIME and n8n auto-layout, ELK layered.

**SCH-UX-5. Fewer sockets and rows on a node.** Value medium, effort S.
A Plot shows Curve 2 to 5 empty, a Report Items A to H, a Field formula g, a, b, c, a Formula x, y,
z. Show the wired optional inputs plus one empty "+" socket (the `more` mechanism Plot already
uses past Curve 4, `operators/__init__.py:92-105`), and apply it to Report and Formula. Hide
"Use in…" on operators. A results node repeats its study's output list: drop the outputs from the
study node once a results node exists, or fold the two into one node with a "1 run" row.

**SCH-UX-6. Reroute dots and wire bundling.** Value medium, effort M.
Double-click a wire to add a dot that can be dragged; store dots in `view_state` (per user, as
positions are). Wires from one output to many inputs leave as one trunk and split near the
targets. Draw wire labels ("stress") only on hover or for transfers; today they sit on top of
other wires. Precedent: Unreal and Blender reroute nodes, Houdini dots.

**SCH-UX-7. A viewer on every wire and a pinned preview.** Value high, effort M.
Hover a data wire or an output socket: a tooltip with the value as the node would show it (a
number, a curve's sparkline, a table's first five rows, a field's range). Click a socket: the
Table or Plots tab shows that port. "Pin preview" on a node keeps its plot or table drawn under
the node at a readable size (today the sketch is 44 px with no axes or numbers,
`layout.py:26`). Precedent: KNIME's port views, ComfyUI preview nodes, Houdini's display flag,
Grasshopper's panel on any wire.

**SCH-UX-8. State colours that say what will happen.** Value high, effort S to M.
One language for a node's state, drawn on the title bar and not only a 4 px bar: up to date
(plain), out of date (amber hatch, value muted: SCH-3), needs a run (blue "not run"), error (red,
with the reason on the node), working (animated while evaluated in the background), muted (grey).
A toolbar button "Bring up to date" runs what the selection needs (`evaluate_here` exists per
operator; make it work for a multi-selection and for "everything"). A failing Check tints its
node's title red, a passing one green, so a report's worth of checks reads at a glance when
zoomed out. Precedent: Workbench's cell state icons with "Update Project", Houdini's cook
colours, KNIME's traffic lights.

**SCH-UX-9. Mute and bypass.** Value medium, effort M.
"Mute" on an operator passes its first input through unchanged where the types allow (a Keep rows
where, a Convert units) or disables it (a Check left out of a report). Stored as `Operator.muted`
(out of hashes for after-solving ones). Precedent: Blender's mute (M), Houdini's bypass flag,
ComfyUI's bypass.

**SCH-UX-10. Notes and coloured frames.** Value medium, effort S.
A sticky note object (`kind = "note"`, text and a size, shown only here and in the SVG) and a
colour for a group's frame. Frames should be made from the canvas: select nodes, Ctrl+G, type a
name (today a group needs `section` and is made in the tree; the frame only shows it,
`schematic_view.py:378-415`). A folded group should show its worst verdict and count ("3 checks:
1 fails"); today a folded group of checks shows nothing (`pics\24-folded.png`). Precedent:
Grasshopper groups and scribbles, Blender frames, Node-RED comments.

**SCH-UX-11. Zoom levels that keep meaning.** Value medium, effort M.
Below about 0.5 zoom, draw each node as a coloured block with its title only, enlarged to stay
legible, checks as red or green blocks; hide ports and thin wires. Show the minimap by node area
against the view rather than by a count of 12, let it be dragged, and draw the state colours in
it. Add zoom-to-selection and a zoom readout. Precedent: Houdini's and Unreal's level of detail,
ComfyUI's low-quality zoom.

**SCH-UX-12. Sliders that move the canvas, not only the 3D view.** Value medium, effort S.
Dragging a slider updates its own node only (`schematic_view.py:1005-1013`). Re-evaluate the
before-solving operators downstream of it with the same `overrides` and repaint their values
while dragging (they are cheap), and mark the after-solving ones "will be out of date". Precedent:
Grasshopper's live recompute.

**SCH-UX-13. What a change reaches, as a preview of cost.** Value medium, effort S.
The toggle dims the rest; add a line in the side panel: "A change here puts 2 studies out of date
(last solve 4 s and 38 s) and 14 operators". Solve times are in the run records.

**SCH-UX-14. First-use help on the canvas.** Value medium, effort S.
An empty or operator-free schematic shows three ghost cards on the canvas: "Read a result"
(Result summary on the selected study), "Check it against a limit", "Make a report", each a
click that inserts the shipped recipe (`clusters.templates()` has `stress_margin`,
`worst_nodes`...). The Recipes submenu is three levels deep in "Add operator ▾" and was not
visible in any picture I took. A recipe gallery with pictures (the 86 operator pictures already
exist in `help/images/operator/`) in the palette's right half would also explain each operator
before it is added. Precedent: ComfyUI and n8n template galleries, Grasshopper's component
help.

**SCH-UX-15. Palette ranking.** Value medium, effort S.
"Fits here" lists 29 types for a results node in registry order, including Text, Add and Export
(`gui/operators.py:573-583`: any input that accepts). Rank by: the first required input accepts
the type, then how often this user followed this type with that one (kept in the user folder),
then the rest; cap at eight with "more…". Keep "recent" across sessions.

**SCH-UX-16. The schematic's other half: Properties for a selection of several.** Value low,
effort M. With several operators selected, show the settings they share (a limit, a selection)
and edit them as one batch.

**SCH-UX-17. Plots panel basics.** Value medium, effort S. (`pics\22-plots-panel.png`.) The y axis
has no label or unit and two ticks (0 and 3.615e+08); the "Against" dropdown says "Step" for a
plot against a sweep. Label both axes with quantity and display unit, use round ticks, and set
the dropdown from the plot.

**SCH-UX-18. Readable numbers.** Value medium, effort S. With SI display units a yield shows as
"2.35e+08 Pa" and a deflection as "0.001522 m". Operators' shown values should pick an
engineering prefix (235 MPa, 1.522 mm) when the display system is SI, as `Verdict` texts and
nodes are read far more often than they are post-processed. One function: `quantity_text`
(`evaluate.py:226-236`).

### Operators (OP-NEW-n)

**OP-NEW-1. Merge the four arithmetic operators and the three ratio checks.** Value medium,
effort M. Divide, Subtract, Multiply, Add share ports and code (`series.py:349`); make one
"Arithmetic" with an operation dropdown shown in the title ("A ÷ B"), keeping the four old types
as aliases that load into it. Likewise Utilisation, Safety factor, Margin of safety → one "Ratio
check" with a mode. File them under a "Maths" group, not "Curves": they take numbers and fields
too, and a user looking for number maths does not open Curves. Precedent: Blender's Math node,
Houdini's VOP math.

**OP-NEW-2. Merge the look-alike table sources.** Value medium, effort M. Mesh table, Run history,
Probe history, Mode table, Contact table, Bolt table and Model contents are one "Table of…" with
a `what` dropdown (they differ only in the reader). Slice and Plane cut values overlap (a field on
a plane as points); Curve from table duplicates the implicit table-to-curve wiring; Curve formula
and Field formula and Formula are one language with different names for the inputs. Target: about
60 operators with modes instead of 86. Old types stay as aliases (ADR 0071 decision 7's rule).

**OP-NEW-3. Conversions that are missing.** Value high, effort S. Reproduced refusals
(`run2.py` section 6): a curve into any table operator ("takes a table, not a curve"), a list
into Plot, Check or Area under curve. Accept `series → table` (its `table()` exists), `list →
series` (index against value), and `list → number` where one item. Where a conversion is not
safe, insert a visible converter node on the wire rather than refuse. Precedent: Blender's and
Unreal's automatic conversion nodes.

**OP-NEW-4. Load-case envelope and combination as operators.** Value high, effort M. Fields from
several studies or steps in, the maximum, minimum, absolute maximum or a factored sum out, with
"which case governs" as a second field. Derived results (ADR 0025) compute these; an operator
makes them wireable into a Check and visible on the canvas. Precedent: Abaqus "Create field
output from fields/frames", ANSYS solution combination.

**OP-NEW-5. Field calculus and invariants.** Value medium, effort M. Gradient, magnitude,
principal values, a component in a coordinate system (wire a CS node in), time derivative and
time integral over steps, node-to-element averaging. Field formula has only pointwise algebra.
Precedent: ParaView's Gradient and Calculator, COMSOL's derived values, Abaqus field operators.

**OP-NEW-6. Step picker by value.** Value medium, effort S. "At time 0.5 s", "at 120 Hz", "mode
3", "the last", "where a value peaks" instead of a stored index (OP-12). One small operator or a
shared setting. Precedent: COMSOL's "solution at", ParaView's time picker.

**OP-NEW-7. Statistics over a sweep or optimisation.** Value medium, effort S. Minimum, maximum,
mean, standard deviation, percentile and "the point where" of a number across points, giving
numbers and the governing point. Today: Points table → Pick one value, which hides the intent.
Precedent: DesignXplorer's parameter set charts, HyperStudy post-processing.

**OP-NEW-8. A Parameter set table view.** Value high, effort M. One table of every design point:
inputs (Numbers, Sliders, parameters) as columns on the left, outputs (chosen operator values) on
the right, a row per point, "Update all", add a row to add a point. It is the Points table with
editing. Precedent: ANSYS Workbench's Parameter Set, COMSOL's parametric table.

**OP-NEW-9. Logic.** Value medium, effort S. If/else switch on a verdict or toggle (choose input
A or B), And/Or/All of verdicts with the worst margin (Report counts them, nothing combines
them), Count where. Formula has `where` but no node takes a verdict (`run2.py`: verdict into
Formula refused). Precedent: Grasshopper's Stream Filter and Gate, KNIME's switches.

**OP-NEW-10. A general Check.** Value medium, effort S. `compare`: at most, at least, between two
limits, within a tolerance of a reference (the benchmark case: "within 2 % of 1.53 mm"),
magnitude at most (OP-7). Report the governing location and step, which Result summary already
knows (`Number.point`).

**OP-NEW-11. Histogram and sample along / over.** Value low, effort S. A field's histogram as a
curve; a field sampled on a grid of points as a table (Values at points does one cloud; Grid
exists). Precedent: ParaView's Histogram, Plot Over Line, Probe Location.

**OP-NEW-12. Report templates.** Value medium, effort M. Report takes eight items and writes
HTML. Add: any number of items (the `more` mechanism), sections with text, a picture of the
model with the result (Export's PNG snapshot exists), a title block, and PDF through the manual's
existing Qt path (`help/site.py`). Add the out-of-date banner of SCH-3.

**OP-NEW-13. Operator aliases worth adding.** Value low, effort S. Searches that found nothing
or the wrong thing (`run1.py`): "vm" (nothing), "average" (three items, none of them Result
summary's mean first), "integrate" (Area under curve only; not Result summary's integral),
"abs" (nothing), "if" (noise). Add aliases: von mises, vm, average, mean, integrate, absolute,
if, switch, threshold (Keep result where), probe, peak, hot spot, envelope, combine.

**OP-NEW-14. Defaults.** Value medium, effort S. Result summary with no quantity on a `result`
port reads total deformation; on a study with several physics that is a guess. Show the chosen
quantity in the node's subtitle ("max von Mises on Bar/Root") always, built from the settings,
so two summaries are told apart without opening Properties (in `pics\10-mid-light.png` eight
Result summary nodes differ only by the names I gave them).

---

## Part 3. Against the best node editors

| Convention | Who does it well | Here today | Item |
| --- | --- | --- | --- |
| Inline parameter widgets | Blender, ComfyUI, Grasshopper | slider only | SCH-UX-2 |
| Typed coloured sockets | Blender, Unreal | yes, with shapes; good | keep |
| Auto-convert on connect | Blender, Unreal | a few implicit, most refused | OP-NEW-3 |
| Node search on wire drop | Unreal, Blender | no | SCH-UX-3 |
| Compatible sockets lit while dragging | Unreal, ComfyUI | no | SCH-UX-3 |
| Reroute dots | Unreal, Blender, Houdini | no | SCH-UX-6 |
| Subgraphs | Houdini, Grasshopper clusters | folded groups and clusters; good start | SCH-UX-10 |
| Live preview nodes, data on wires | KNIME, ComfyUI, Houdini | 44 px sketch | SCH-UX-7 |
| Dirty-state colouring, "update all" | Workbench, KNIME, Houdini | dot and thin bar; stale values look current | SCH-3, SCH-UX-8 |
| Bypass / mute | Houdini, Blender, ComfyUI | no | SCH-UX-9 |
| Templates gallery | ComfyUI, n8n | six recipes in a submenu | SCH-UX-14 |
| Multi-select, copy/paste, align | all | no | SCH-UX-1 |
| Comments | Node-RED, Grasshopper | no | SCH-UX-10 |
| Parameter set table | Workbench, HyperStudy | Points table, read-only | OP-NEW-8 |
| Background evaluation | Houdini, KNIME | GUI thread, twice | SCH-1 |

What is already better here than in most of them: units travel with every value and mismatches
are refused in words; the graph can never drift from the model because it is generated; every
action is an undoable command an agent can also issue; cycles are refused with the loop named
("that would make a loop: B → A → B"); deleting a node that is read is refused with the reader's
name.

---

## Part 4. The top ten

1. **OP-1** A Formula that overflows or errs makes the project impossible to open or run.
2. **OP-5** One operator's raw exception stops the schematic drawing. (Same catch-all as OP-1.)
3. **OP-2** A load wired to a parameter leaves its study out of date for ever.
4. **OP-3** Any new Number, Slider or Model value puts every study out of date.
5. **OP-4** Operators on swept, shoot-out and convergence studies read "the newest run" unseen.
6. **SCH-1** Two builds, per-study rehashing and all evaluation on the GUI thread per refresh.
7. **SCH-3 + SCH-UX-8** Stale values and verdicts look current; one clear state language and
   "Bring up to date".
8. **OP-6 + OP-7** Checks: unit-less limits accepted against the guide; negative margins on a pass.
9. **SCH-UX-1 + SCH-2** Multi-select, Delete, copy/paste, and removing data wires on the canvas.
10. **SCH-UX-3 + SCH-UX-2 + SCH-UX-4** Wire-drop search with lit sockets, editing on the node,
    and a layout that survives 50 nodes.

## Part 5. What the schematic should be for

The tree is already the complete, ordered place to build a model, and decision 8's parity makes
the schematic a second one; in the pictures that parity costs the canvas its clarity (every load
a node, every material a "Use in…" socket) without making model building faster than the tree.
The schematic earns its place where the tree is weak: showing **what depends on what and what is
now out of date**, and holding the **chain from results to answers**: the numbers read from a
run, the limits they are checked against, the curves and tables made from them, and the report,
all recomputed when the model changes. Its promise to the engineer should be: "after any change,
this canvas tells me truthfully which of my conclusions still hold, and one click brings the rest
up to date." Everything in the top ten serves that sentence: values must never crash the canvas,
never look current when they are not, never read a run the user did not choose, and never be
invalidated by adding a node that changes nothing. Model building from the canvas should stay
possible (it is what agents and wiring gestures use) but be drawn quietly: fold loads, materials
and meshes into their component by default, and give the screen to studies, results and
operators.

## Part 6. Not covered

- The real mouse path (drag gestures, hover tooltips, the context menus' contents) was driven
  through panel methods and `QTest` on the offscreen platform, not by hand on a screen; the
  whole-window picture under offscreen had a distorted dock layout, so zoom and minimap behaviour
  at real window sizes is unverified.
- Script operators: only the untrusted refusal was run. The sandboxes (`scripts.py`,
  `sandbox_win.py`) were not tested; `scripts.kind()` can return "none" and I did not check what
  runs then.
- Clusters and block files (import checks), `geometry_data.py` (Points, Grid, Outline driving
  history), the VTK filters (Slice, Clip, Contour, Stream tracer, Glyph), `table_sources.py`
  beyond Result, Mode and Points tables, optimisation and mesh-study nodes, imported-field wires,
  transfers drawn between studies: read lightly or not at all, none run.
- ADRs 0063 and 0064 and the 1137-line operator reference were not read in full; the Grasshopper
  review (0064A) only at its head, so some OP-NEW items may already be listed there as "later".
- Large models: all timings are on 13 190 nodes. Costs for 1e6 nodes are extrapolated.
- The existing tests (`tests/test_operators.py`, `test_tables.py`, `test_series.py`,
  `test_schematic*.py`) were not read or run, so I cannot say which bugs a test already pins as
  intended behaviour. OP-3 in particular follows a documented rule ("changing one puts the
  studies out of date, as a parameter does"); I rate it a defect for operators nothing in the
  model uses.
- MCP tools (`operator_add`, `link`, `schematic`) were not called; agent-side findings come from
  the shared functions underneath.
