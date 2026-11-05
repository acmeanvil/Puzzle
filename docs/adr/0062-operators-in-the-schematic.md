# 0062. Operators in the schematic: data, geometry, Python and result nodes, as Grasshopper's

- Status: Accepted (2026-11-02); built (phases 1 and 2 2026-11-03, 3 and 4 2026-11-05)
- Date: 2026-11-02

## Context

The schematic (ADR 0058) draws the project: files, resources, components, studies and
results, joined by data and use wires. The maintainer likes its Grasshopper look and
asks for Grasshopper's other half: nodes that **compute**, such as data manipulation,
geometry driven by data, Python snippets and result manipulation, wired into the model.

Puzzle already has pieces of this, each in its own place:

- **Parameters** (`ParameterSet`): named quantities. A history step's argument that is
  a string names a parameter (ADR 0014), so geometry is already parametric.
- **Sweeps** (ADR 0018): a study runs once per point of a parameter (or material)
  list.
- **Derived results** (ADR 0025): combinations and equations over runs, evaluated on
  demand and never stored. Its expressions are a small language walked from `ast`.
  Python scripts were rejected there as "unsafe to run from a shared project file".
- **Result tools** (ADR 0032, 0042): a value at a point, or a selection's max, mean,
  integral or reaction, with its history over steps.
- **Imported fields** (ADR 0044): tables of values at points, mapped onto the model.
- **Custom PDEs**: the user's UFL is already evaluated with `eval` and no builtins.
  CLAUDE.md says to treat projects with equations as code.

What Grasshopper adds is wiring these together freely. A number feeds a hole pattern,
a table of points becomes a beam frame, a script turns a test log into a load, and a
result's maximum becomes a pass/fail or a plot against a sweep.

The tension is ADR 0009's rule, kept by 0058, that **the schematic is never the source
of truth**: layout means nothing, and everything is a project object edited by
commands. Grasshopper's canvas *is* its program. This ADR keeps the rule: operators
are project objects like any other, and a wire drawn between two of them stores a
reference in the object downstream, as a transfer already does. The schematic draws
them; it still holds nothing.

## Decision

1. **Operators are project objects** (kind `operator`), each with typed input and
   output ports, so they are stored, hashed, versioned (ADR 0059), undone and logged
   like everything else. An input is a value typed in or a reference to another
   object's output (`{"from": <id>, "port": "out"}`). Drawing a wire in the schematic
   is an `Edit` setting that reference. Cycles are refused, as transfers' are.

2. **Port types**, each with its own colour and socket shape, so a wrong wire can't be
   drawn (a port takes only its type; a number is accepted where a list is wanted, as
   a one-item list):

   | Type | Holds |
   | --- | --- |
   | number | a quantity with its unit (SI inside, as everywhere) |
   | list | numbers, or texts (material names) |
   | table | named columns with units (a CSV, a sweep's results) |
   | points | positions, in metres |
   | geometry | a geometry object |
   | field | a result or imported field on a mesh (nodal or per element) |
   | result | a run, or a derived result |
   | verdict | pass or fail, with the margin |

3. **Pre-solve and post-solve.** Anything upstream of a study is **pre-solve**: its
   value goes into the study's content hash, so changing an operator stales what it
   feeds, with the reason ("Hole count changed"). It is evaluated when the model is
   regenerated, meshed or run. Anything downstream of a result is **post-solve**:
   evaluated on demand and never stored, as derived results are (ADR 0025). No wire
   goes from a result back into the model: a loop is a study of its own (item 9).

4. **Data operators** (first): Number and Slider (a parameter with a range and a
   step; dragging it previews geometry live, never solves), Series and Range (lists),
   Expression (the ADR 0025 language over numbers and lists, with units checked),
   Table (from a CSV in the project, or typed), List operations (pick, slice, sort,
   zip, cross: Grasshopper's longest-list and cross-reference), and Panel (shows any
   output's value on the canvas, Grasshopper's panel). A list wired into a sweep
   becomes its points (ADR 0018), so "run once per item" needs no new mechanism.

5. **Geometry operators.** A geometry's history stays one node (expandable to its
   steps), so the octopus doesn't return one step at a time. Operators feed its
   steps' arguments: a wire into a step argument sets it to a generated parameter,
   the mechanism history steps already use. New geometry from data: Points (from a
   table or an expression), Polyline through points (a beam frame, ADR 0027), Pattern
   by list (holes or bodies at listed positions), and Sketch values. Previews go through
   the existing pending history (`MainWindow.pending`), never writing the project.

6. **Result operators** (post-solve): Reduce (max, min, mean, integral of a quantity
   over a selection, as a number: the result tools of ADR 0032/0042 as nodes), Filter
   (a field masked by a region or a threshold), Combine (the ADR 0025 combinations and
   equations, whose nodes gain ports), Check (a number or field against an allowable:
   a verdict with its margin, ASME-style checks from ADR 0042 among them), Plot (a
   number against a sweep's parameter or time, into the Plots panel), and Export (any
   output to CSV or the ADR 0043 formats).

7. **Python snippets** (`operator` of type `script`): code with declared inputs and
   outputs (typed ports), the escape hatch for what the nodes don't do. It runs:
   - in a **separate process**, in a Python environment of its own (the pixi
     environment `scripts`, beside the solver environments, ADR 0013): numpy, scipy
     and polars always, plus any package the user installs there for the job
     (`puzzle scripts add PACKAGE`, Settings > Scripts lists what is installed). It
     belongs to the Puzzle installation, never to the GUI's own Python, so a package a
     script needs can't break Puzzle. It runs with a time limit, a working folder of
     its own, and no access to the project's files except the inputs it is handed. A
     project records the packages its scripts import, so another installation says
     which are missing before running them;
   - only in a **trusted project**. A project from elsewhere (opened from a folder not
     made here, an archive, ADR 0061) opens with scripts off, and the Advisor and the
     script nodes say so until the user trusts it, as VS Code's workspace trust does.
     The same holds for custom PDE equations, which today run without asking;
   - **deterministically for staleness**: the code's text and its inputs' values go
     into the hash, so a pre-solve script stales what it feeds when either changes;
   - **for agents at "act" only**: an agent may propose a script at "propose" (the
     user reads and accepts it, ADR 0015), never run new code of its own.
   This reverses ADR 0025's "no Python scripts" for this one place, because of the
   trust gate and the separate process.

8. **In the schematic**: operators sit in the column of what they feed (a Number
   before the geometry it drives, a Reduce after the results it reads), placed by the
   same layout. A palette adds them (Tab or a double-click on the empty canvas opens a
   search by name, as Blender's and Grasshopper's do). A node shows its value (a
   Slider its knob, a Panel its text, a Check green or red). Selecting one previews
   what it makes (points or geometry in the 3D view, a field on the mesh).
   The model tree lists them under Operators; the CLI (`puzzle operators`), MCP
   (`operator_value`, `operator_add`) and a skill are their twins. Positions are still
   per user and mean nothing.

9. **Loops are studies, not wires.** Optimisation (Grasshopper's Galapagos) is a study
   step whose design variables are Numbers or Sliders and whose objective and
   constraints are post-solve outputs; it reruns the model, each run stored as a sweep
   point. It needs its own ADR, named here only so that no wire from results back to
   the model is ever drawn.

10. **Phases** (both data and result manipulation are needed; result operators first,
    as derived results and result tools exist and mostly need ports): 1, the operator
    object, port types, Panel and Check, and the result operators (Reduce, Filter,
    Combine, Plot, Export); 2, data operators wired into parameters, sweeps and history
    arguments; 3, geometry from data (points, polylines, patterns); 4, Python scripts in
    their environment with the trust gate. Optimisation is ADR 0063.

## Consequences

- Parametric studies that now need a script outside Puzzle (hole count from a list,
  a frame from a spreadsheet of points, a verdict per sweep point) are built and seen
  in one place, and stay undoable and versioned.
- The schematic becomes an editor for operators while staying a picture of the model
  for everything else; the tree shows the same operators, so neither is the only way
  in.
- Content hashes reach operators: a typo in an expression stales studies until fixed.
  Every operator must say what it hashes, as `project/graph._own` does now.
- Projects can hold code. The trust gate is new work, and it covers custom PDE
  equations too, which closes a hole CLAUDE.md now only warns about.
- Pre-solve operators run at mesh and run time and must be fast and deterministic.
  Python's time limit and process cost (about 0.1 s to start) matter in sweeps, where
  a script is run per point.
- The tests: each operator against hand values; wiring and cycles refused; hashes and
  staleness through operators; scripts refused untrusted, killed past their limit,
  and kept away from the project's files.

## Alternatives considered

- **Grasshopper's model, the canvas as the program**: the most flexible, but layout
  and meaning tangle (ADR 0009's fourth cause), and the tree, the CLI and agents would
  have to read a canvas.
- **Every history step as its own node**: as Grasshopper draws geometry, but a part of
  thirty steps becomes thirty nodes and the octopus returns; the history stays one
  expandable node.
- **Python in the GUI's own process** (as Grasshopper's GhPython): fastest, but one
  bad snippet freezes or crashes the window and can read anything the user can.
- **No Python, only the expression language**: safe, but every case the nodes don't
  cover would be a feature request; the trust gate makes scripts acceptable.
- **Embedding Grasshopper or Rhino.Compute**: a closed, paid, Windows/macOS-only
  dependency for an open-source, cross-platform tool.
- **A Dynamo- or Node-RED-like separate graph file**: a second source of truth beside
  the project.

## Review (2026-11-02)

The maintainer's answers:

1. Both data and result manipulation are needed. Result operators come first because
   they build on what exists (item 10).
2. Python runs in a separate environment belonging to the Puzzle installation: any
   package the user installs there for the job, and numpy, scipy and polars for sure
   (item 7). Trust stays per project.
3. A geometry's history steps stay inside the geometry node (item 5).
4. Sliders preview geometry live while dragging, mesh on release or on Mesh, and never
   solve (item 4).
5. The optimisation ADR is written next (ADR 0063).

## Phase 1 built (2026-11-03)

- **The object**: `model.schema.Operator` (kind `operator`). Its `inputs` are keyed by
  port, each an `OperatorInput`: a wire `{"source": <id>, "port": <output>}` (named
  `source`, not `from`, which Python reserves) or a typed `value` (a quantity, a
  number, a text). Its own settings are plain fields (`quantity`, `selection`,
  `reduction`, `step`, `keep`, `compare`, `against`, `file`, `format`), and each type
  reads its own (`operators.TYPES[...].settings`). `puzzle.operators` holds the
  registry: types with their typed ports, the port types with their colours and
  socket shapes, `outputs_of`, `accepts`, `refuse`, `refuse_cycles`, `problems`
  (`operator_input`), `describe` and `inputs_from`.
- **Sources**: a study (`result`, or one field such as `stress`), a derived result
  (`result`; this is item 6's Combine, so there is no separate Combine operator), a
  result tool (its value), a parameter set's parameter (a number), and other
  operators' outputs.
- **Operators**: Reduce, Filter, Check, Panel, Plot and Export
  (`operators.evaluate`). Values are dataclasses per type and are never stored. A
  `Context` carries the step or sweep point, so a Plot repeats the same chain per step
  or per point. A check's margin is limit / value − 1 for at most and value / limit − 1
  for at least; a stress compared with a length is refused. An export writes only
  inside the project folder, as CSV or a result format.
- **Commands**: a wrong type, an unknown port, or a value typed where only a wire may
  go is refused. So is any reference loop, transfers' included: `refuse_cycles` now
  runs for every edit that adds a reference. The object is put back as it was. An
  operator is as fresh as what it reads (`status._operators`).
- **Schematic**: operator nodes sit in the Results column, to the right of what they
  read. Ports have a type, socket shape and colour (`port_colour`, `wire_colour`,
  `socket_shape`; the SVG draws the same). The node shows its value, and a check shows
  its verdict in green or red. Parameter sets gain a number port per value, and runs
  and derived-result nodes gain a `Result` port.
- **GUI**: wires can be drawn from any output onto an operator's input; one that fits
  no input is refused. Tab, or a double-click on the empty canvas, opens a search
  palette, wired from the selected node. Other ways in: an Operators section in the
  tree, New operator under Results (tree and menu bar), Find an operator….
  Properties shows each input as a dropdown of what may be wired in, a typed value
  with its unit, and the output. Plots draw in the Plots panel; an export writes from
  Export now.
- **Twins**: `puzzle operators PROJECT [NAME] [--json] [--export]`, MCP
  `operator_value` and `operator_add`, an Operator line per operator in the outline,
  and the skill `schematic-operators`. Tests: `tests/test_operators.py` (values checked
  by hand on two tets) and `tests/test_operators_gui.py` (the cantilever on CalculiX).

Not done in phase 1:
- Selecting a Filter does not yet preview its field in the 3D view.
- The ASME checks of a linearization (ADR 0042) are not offered as a Check.
- The Expression operator comes with the data operators in phase 2.

## Phase 2 built (2026-11-03)

- **Data operators**: Number, Slider (`low`, `high`, `increment`: snapped and held in
  range), Series and Range (lists), Expression, Table, List and Pairs.
  - Expression is the ADR 0025 language over pint quantities
    (`operators/expression.py`). "5 mm" after a number, or units as names; a sum or
    comparison of unlike units is refused. Its names are x, y and z (its inputs) and
    the parameter sets' parameters. Other operators come in by wire, never by name, so
    every dependency is a wire.
  - Table keeps its rows as CSV `text` ("Read a CSV…" copies a file in), so they are
    hashed and versioned with the project.
  - List does item, slice, sort, reverse, length, column, sum, min and max. Pairs zips
    (the shorter list repeats its last item) or crosses two lists into a table.
- **Pre-solve**: a data operator that reads nothing but parameter sets and other such
  operators (`operators.is_pre`) computes before a solve. Every study reads them, as it
  reads the parameter sets (`project/graph._inputs`), so changing a slider stales its
  studies; a post-solve operator never does. `operators.parameter_values(project,
  overrides)` returns every parameter by name: the parameter sets', then each
  pre-solve number operator's. It replaces the six copies of the parameter-set
  dictionary (geometry regeneration, naming and meshing, the workbench, the step
  dialog, the sketch check, derived equations).
- **Driving the model**:
  - A history argument names the operator, as it names a parameter: wiring writes that
    name. The geometry stays one node, with an input port per driven argument.
  - A sweep parameter may take its values from a list operator (`SweepParameter.source`
    and `port`). `operators.resolved(project)` fills the values in for counting and
    running (`run_hashes`, `run_study`, the schematic badge, the outline, Compare), and
    each point is an ordinary stored run.
  - A Number or Slider can be swept by name: `sweep.apply` sets its value.
  - An operator that reads results is refused when it feeds the model (code
    `inconsistent`): loops are ADR 0063's.
- **Schematic and GUI**:
  - Pre-solve operators sit in the Resources column, beside the parameter sets.
  - Wires from operators and parameters run into the geometry arguments and sweeps they
    drive.
  - A slider node has a knob. Dragging it, or the slider in Properties, previews the
    geometry it drives through `MainWindow.slider_overrides` (`operators.with_values`
    on the preview's copy) and never solves. Letting go writes one undoable Edit, in
    the unit shown.
  - Selecting a Number or Slider shows the geometry it drives.
  - A wire dropped on a geometry asks which argument it drives. A list dropped on a
    study asks which parameter it sweeps.
- **Twins**: `operator_add` takes every type; the outline says what each data operator
  drives and whether it runs before or after solving; the skill `schematic-operators`
  covers both. Tests: `tests/test_data_operators.py` (values by hand, units, hashes,
  sweeps, a slider driving an OCCT box's width) and `tests/test_data_operators_gui.py`.

Not done in phase 2:
- Renaming an operator doesn't rename the history arguments that name it (as with
  parameters today).
- A list's points don't yet feed a sketch's dimensions (phase 3, with geometry from
  data).

## Phase 3 additions from the Grasshopper review (2026-11-03)

ADR 0064A accepted these for phase 3 (geometry from data), with their row ids:

- Points from numbers or table columns, and back [D1], in a coordinate system [D2].
- Planes from data: a construction plane a Slider drives [D6].
- Grids of points on a plane, a face or in a box [D8].
- Profiles from data: polyline, circle, rectangle and polygon [E1].
- Selection by rule as a node with data inputs [F4, F5], and selection sets (union,
  intersection and difference of named selections) [C11].
- Patterns in two directions and along a path, and Pattern by list [H4].

## Phases 3 and 4 built, and the loose ends (2026-11-05)

**Loose ends of phases 1 and 2.**
- Selecting a Filter draws its kept values in the 3D view (`gui/operators.preview_field`); the values it leaves out are muted.
- A Check fed a stress linearization runs VIII-2 5.2.2.4's three checks (membrane, membrane + bending, primary + secondary) against the limit as the allowable S, or the tool's own S. It passes when all do; the verdict and margin are the worst ratio's (`evaluate._asme`).
- Renaming a pre-solve operator renames what names it, in the same command and the same undo (`operators.rename_references`). That covers history arguments (not a body's or a step's own name), a sketch's dimensions, sweeps' and optimisations' variables, and the name as a whole word in expressions and derived-result equations.
- A list's or points' entries feed a sketch's dimensions (`"P[2].x"`), with phase 3.

**Phase 3, geometry from data** (`operators/geometry_data.py`).
- Points [D1, D2]: from x, y, z or a table's columns, in a coordinate system. The output is a table of x, y, z too, so table operators take it and its columns are lists again.
- Grid [D8]: on a plane (an axis plane at an origin, or a construction plane) or in a box.
- Profile [E1]: polyline, circle, rectangle (centred or from a corner), regular polygon.
- `history.ops_of` puts their values into a geometry's arguments: a `profile`, a polyline's or spline's `points`, a pattern's `positions`, and `name[i]` or `name[i].x` anywhere. Pre-solve operators were already in the studies' hashes, so they stale what they feed.
- Patterns [H4] take a second direction (`count2`, `dx2`…), positions from data (offsets, the first the original), and `along` a polyline or spline (`count` copies at equal lengths).
- Selections [F4, F5, C11]:
  - rule values may name parameters (`naming.rule_inputs`);
  - new rule conditions `box`, `near`, `normal` (a planar face's outward normal within degrees), `min_area` and `max_area`;
  - sets `union`, `intersection` and `difference` of other selections, resolved after them (`naming.resolve`). Table sources read members through `naming.members_of`, so they see sets too.
- Construction planes [D6] take `drives` (value → parameter), resolved in `frame(project)`, so a slider moves a cut or a plane probe.
- The schematic wires a geometric operator into the geometry argument naming it, typed points or profile.
- 2D regions in one geometry: the history mode `separate` (ADR 0048).

**Phase 4, Python scripts** (`operators/scripts.py`, `puzzle/trust.py`).
- A `script` operator: inputs a to d, `out` as its `returns` says, `timeout_s`, its code edited in a box of its own (Edit code…).
- It runs in the pixi environment `scripts` (numpy, scipy, polars; `puzzle scripts list|add PACKAGE`) through `-I`, with the environment cleared, in an empty temporary folder. On macOS it runs under `sandbox-exec`, which denies reading or writing the project's folder and the network; elsewhere the folder is only not given.
- Its imports are the project's record of what it needs: Properties lists them and any the environment lacks.
- Trust:
  - A project built or saved by this installation is trusted: its folder goes into `<user data dir>/trusted.json`, and a save-as copy of a trusted project is too.
  - One loaded from an unregistered folder is not. Its scripts and its custom PDE runs are refused with code `untrusted`, `Project.problems` lists them, and the Advisor's Trust step and Project → Trust this project… (or `puzzle trust`, `--revoke`) let the user trust it.
  - Agents never trust a project. Below "act", an agent's command writing a script or an equation becomes a proposal.
- Its code and its inputs' operators are in the hashes.

Tests: `tests/test_geometry_data.py`, `tests/test_scripts.py`, `tests/test_planar_regions.py`, and the additions to `test_operators.py`, `test_data_operators.py` and `test_operators_gui.py`.

Not done: on Linux and Windows nothing stops a script from reading the project's folder: it is only not handed the path. A real sandbox there (bubblewrap, a job object) would be its own work.
