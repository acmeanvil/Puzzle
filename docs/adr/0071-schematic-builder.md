# 0071. The schematic as the primary builder: model values, series and plots, help

- Status: Accepted (2026-11-07)
- Date: 2026-11-07

## Context

The schematic (ADR 0058) and its operators (ADR 0062, dataframe operators ADR 0064) let a
user wire inputs, studies and results together. Used for real (the maintainer's Windows
tests, 2026-11-07), three gaps stood out:

1. **What an operator does isn't at hand.** The palette and the nodes show names; what
   each takes, gives and is for has to be known or looked up.
2. **The model's own values aren't operators.** A material's yield strength or a load's
   pressure can't be wired into a check or a plot; parameters can, but only those made
   as parameters.
3. **Plots can't be worked on.** Path, probe histories and sweep points end in a Plot
   node or a table. Turning them into utilisation against yield, displacements
   normalised by their maximum, or every step against one step means dataframe
   operators, which expose rows and columns and aren't what an engineer thinks in.

The aim is the schematic as the place an analysis is built and its results turned into
answers: setup values in, results out, checks and plots made from them, with readable
names, units and no raw data on view.

## Decision

1. **Every operator explains itself.** Each operator kind carries a one-line purpose, its
   inputs and outputs (name, type, unit, what it accepts) and a short example, in one
   place (`operators.DOCS`, beside the registry). The same text is shown:
   - as a node's tooltip, and in a help card from the node's "?" and the palette entry;
   - on a socket's hover, and while a wire is dragged ("needs a series; this is a
     number");
   - in Properties when the node is selected;
   - to the agent (the knowledge pack, ADR 0057) and in generated reference docs.
   A test refuses an operator kind without its documentation.
2. **Model value**, an operator reading the model: picked by dropdowns (object, then its
   property: a material's yield or ultimate strength, Young's modulus, density; a
   feature's value; a section's thickness; a measured length; a parameter), giving a
   number with its unit or, for a temperature-dependent property, a series. It is a
   pre-solve operator (it reads no results), so it can also feed model inputs, and it
   is recomputed when the model changes.
3. **A series type**: x and y with their quantities and units, a label, and where it came
   from (a study, step, path, probe). It is what Path, probe histories, Plot over line
   and sweep points give; nodes show it as a small plot and never as rows. Operators on
   series (and a series with a number):
   - *Divide (A ÷ B)*, *Subtract (A − B)*, *Multiply*, *Add*, and the intent-level
     *Utilisation* (value ÷ allowable), *Safety factor* and *Margin*; *Normalise* (by a
     number, its own maximum or another series); *Formula* (over `x`, `y` and wired
     numbers by name, in Puzzle's unit-aware expression language);
   - *At step*, *Compare with step* (each step against a reference step, as a
     difference or ratio), *Over the steps* (one value's history);
   - *Envelope*, *Match x values* (onto another series' x), *Area under curve*, *Slope*;
   - *Plot* takes any number of series, plus *Limit line* (a number drawn across) and
     *Annotate*; axes, units and the legend come from the series.
   Units follow through each operator (stress ÷ stress is a pure number), and a wire
   between compatible units converts; incompatible ones are refused with a reason.
4. **Later steps of this ADR**, in this order:
   - **Field operators**: the same algebra on result fields (*field ÷ number*, *field −
     field* across steps or studies, *Normalise*, *Expression*), giving a result drawn in
     the view with the legend and probes (generalising derived results, ADR 0025);
   - **ParaView operators** (ADR 0070 level 2): one node per filter (Slice, Clip, Contour,
     Threshold, Plot over line, Integrate variables, Stream tracer, Calculator), its form
     generated from ParaView's own property descriptions, giving a field, a series or a
     number;
   - **User operators**: a selection of nodes made into a block with exposed ports
     ("Stress utilisation check": a study and a path in, a plot and a verdict out),
     kept in the user's library (ADR 0064's clusters) and shareable;
   - **Building the model from the schematic**: a material dropped on a part, a study
     dragged from a component, numbers wired into a feature's value socket;
   - **Live nodes**: a value, a mini plot or a verdict's colour on the node, stale marks,
     "evaluate up to here"; a **Report** node gathering checks, plots and values.
5. **Everything stays a command.** New operators, wires and edits go through the
   Session (undo, history, the agent through MCP), operators are evaluated on demand and
   never stored, and the GUI shows names and display units, never ids or raw columns.
6. **The agent sees and does everything the schematic does** (the maintainer,
   2026-11-07). Nothing here is GUI-only:
   - the operator documentation of decision 1 is served by MCP (the operator types with
     their purpose, ports, types, units and examples) and searchable in the knowledge
     pack, so the agent knows what an operator does before using it;
   - every operator kind, including Model value, the series operators and the later
     field, ParaView and user operators, can be added, wired, edited and evaluated
     through MCP tools, with the same refusals and reasons the GUI gives (a wire of the
     wrong type, incompatible units, a cycle);
   - values, series and verdicts are readable by the agent as summaries (a series as
     its range, extremes and a few points, never a dump), and plots as their series;
   - user-made blocks are listed and inserted by name through MCP, as clusters are;
   - the `schematic-operators` skill is updated with each step, and tests drive each new
     operator through MCP as well as the GUI.

7. **Names a user looks for first** (the maintainer, 2026-11-07). An operator's name says
   what you get, or the action, in an engineer's words rather than a programmer's or a
   data scientist's; maths operators show their symbol (Divide (A ÷ B)); no word means
   two different operators; intent-level operators (Utilisation, Safety factor, Margin)
   sit beside the generic ones. Renaming changes labels only: projects store the type,
   which never changes, and the old name stays an alias so search finds it. The existing
   operators were renamed so (Reduce → Result summary, Divide → Points along, Series →
   Stepped list, Expression → Formula, Pivot → Rows to columns, and 25 more:
   `operators.LABELS`), and ADR 0071's new ones follow the same rules (Divide and
   Subtract rather than Ratio and Difference, Compare with step, Over the steps, At step,
   Match x values, Area under curve, Slope).

8. **The tree and the schematic are equal ways to build a project** (the maintainer,
   2026-11-07). The user builds in either, as they prefer:
   - **Everything the tree can add, the schematic can add**: geometry, materials, mesh
     recipes, components, physics and their features (loads, supports), contacts and
     bolts, studies and their steps, transfers, imported fields, coordinate systems,
     construction geometry, amplitudes, result tools, derived results, optimisations,
     mesh studies, parameters and operators, from the same one-form dialogs
     (`window.ask_form`), placed where asked.
   - **Every reference between objects is a wire that can be drawn**: a material onto a
     part, a mesh recipe onto a component, a component into a study, a selection onto a
     feature, a coordinate system or amplitude onto what uses it, a study into a
     transfer, a run into a derived result or a result tool; drawing one is the same
     Edit command the tree's Properties dropdown makes, and a wire the target can't take
     is refused with the reason. Deleting a wire clears the reference.
   - A test lists every kind the tree's menus can create and every reference field in
     the schema, and refuses one the schematic can't create or wire. The agent does the
     same through MCP (decision 6).

## Plan

1. `DOCS` for every existing operator kind; tooltips, the help card, socket and wire
   messages, Properties; the test.
2. Parity with the tree (decision 8): every creatable kind added from the schematic,
   every reference a drawable wire, and the test that keeps it so.
3. Model value.
4. The series type and its operators (decision 3), with the utilisation, normalised
   displacement and steps-against-a-step plots as tests against hand-computed answers.
5. Then decision 4's steps, each its own entry in Built so far.
6. Each step is driven through MCP too (decision 6): its tools, its documentation
   served, its tests, and the `schematic-operators` skill.

## Consequences

- Answers an engineer asks for (utilisation, margins, normalised curves, step
  comparisons) become a few nodes, without seeing tables.
- One more port type (series) and its conversions to keep consistent with tables and
  fields.
- Operators grow in number, so their documentation and the palette's search matter more;
  decision 1 is the first step for that reason.

## Alternatives considered

- **Do it with the dataframe operators.** Possible today, but rows and columns are on
  view and each plot takes many generic steps.
- **A scripting node only.** Flexible, but needs trust (ADR 0062), hides intent from the
  schematic and the agent, and isn't what the maintainer asked for.
- **A separate plot editor outside the schematic.** Another place to look, and its plots
  wouldn't feed checks or reports.

## Built so far

**Step 1 (2026-11-07): operators explain themselves, by names a user looks for first.**

- `operators.LABELS`: 30 operators renamed (decision 7), the old name kept as an alias;
  keys unchanged, so projects are untouched. Python script lost the alias "formula",
  now Formula's name.
- `operators.operator_doc(key)` / `doc_text` / `port_doc`: purpose, an example
  (`EXAMPLES`, for the common ones), each input (what it takes, needed or optional,
  whether a value can be typed) and output.
- The schematic: an operator node's tooltip is its card (`Node.help`), and hovering
  along a port's row says what that port takes or gives (`Port.help`); Properties shows
  "What it does" (purpose, example).
- MCP `operator_help`: every type, a search over names, purposes and other names, or one
  type's card by type or shown name; `operator_add` points to it; the
  `schematic-operators` skill maps shown names to types.
- `tests/test_operator_docs.py`: every operator documented, names unique and none
  another's search word, no jargon names, old names still found, the schematic's help,
  and the same help over MCP.

**Step 2 (2026-11-07): the tree and the schematic build the same project (decision 8).**

- **New** (the schematic's toolbar, and a right-click on the empty canvas) holds the
  tree's own add menus, one per section (`menus.build_menu` for each heading), so
  whatever the tree adds the schematic adds; the new node is placed where asked.
- Coordinate systems, amplitudes, bolt types and construction geometry are nodes in the
  Resources column, result tools in Results (they were chips), and every reference
  between nodes is a use wire (operators, derived results, optimisations and mesh
  studies keep their data wires).
- `schematic/links.py`: `LINKS` (every reference the schematic wires: where, what feeds
  it, its name, whether it's required), `choices` (what a wire from one object to
  another can mean, named by part or load), `commands` (the tree's Edit), `existing` and
  `clear_commands` (removing a wire: an optional reference cleared, a required one in a
  list removes that item, a required single one refused).
- Every wireable node has a "Use in…" socket to draw from; a wire or a node dropped on
  another sets the reference (asked which when it could mean several; a geometry on a
  component adds a part, as before); a use wire's right-click removes it.
- MCP `link` and `unlink` do the same for agents, with the same choices and refusals.
- `tests/test_schematic_parity.py`: the references set, refused and removed; every
  reference field in the schema is wireable or listed with how it is wired instead
  (`WIRED_ELSEWHERE`: an operator's sockets, a study-to-study transfer, a field's
  sockets ...); the New menu holds every section's tree menu; the agent's link/unlink.
- **Loads, supports and every other feature are nodes** (`feature:<component>:<path>`,
  kind `feature`, beside their component and wired into it): a feature's coordinate
  system, amplitude and imported fields wire into its node, not the component's; a
  geometry wired onto a feature offers that geometry's selections ("Acts on
  Bracket/Outer") to set where it acts; a click selects the feature in the tree (and
  its faces in the view); its wire's right-click removes the feature. `links.choices`
  and `existing` take `within` (the feature's path); MCP `link`/`unlink` take `feature`
  (its name as the schematic shows it, or its path).

**Step 3 (2026-11-08): Model value.**

- `operators/model_values.py`: `readable(project, obj)` walks any object for its
  quantities and temperature tables, each with a dotted path and a label in the tree's
  words (a material's "Yield strength", "Young's modulus against temperature"; a
  component's "Pressure on Bracket/Inner: p"; a mesh's "Global size"; a parameter's
  "t"), so a quantity added to the schema later is readable without a change;
  `read` gives a Number (SI, its kind) or a two-column Table for a table.
- The `model_value` operator (Inputs; `target` and the new `property`): before solving,
  so a parameter by its name too; its output a number or, for a table, a table
  (`first_type`); it follows the model (a material changed, every check moves).
- Properties: the object (materials first, by name and kind) and its values by label
  (`gui.operators.model_value_choices`); in the schematic, a wire from a material,
  component, mesh recipe, parameter set, study and the like onto a Model value sets
  what it reads (`Link.types` keeps Measure to geometry).
- MCP: `model_values` (the objects holding values; one object's values with label, path,
  kind and table); `operator_add` takes a target by name and a property by label.
- `tests/test_model_value.py`, and the skill's Model value section (a utilisation check:
  Result summary into Check's value, Model value's yield into its limit).

**Step 4 (2026-11-08): curves (series) and their operators.**

- A port type `series` (shown as Curve): x and y with their quantity kinds, one curve or
  one per step (`operators/series.py`: `Series`, `Curve`; a limit line is a curve with
  one value and no x). Nodes show a small plot (`Node.sketch`, drawn by the schematic
  view and the SVG), agents a summary (`series.summary`: axes, units, curve count, each
  curve's extremes, where they are and five points), never every row.
- Where curves come from: a result tool's `along` (a path or linearization, a curve per
  step) and `history` (its value over the steps) ports, now sockets on its own node;
  Over the steps (any number at every step or sweep point); Curve from table; and any
  table wired where a curve is wanted (its first two number columns), so a Model value
  against temperature is a curve.
- Operators: Divide (A ÷ B), Subtract (A − B), Multiply (A × B), Add (A + B),
  Utilisation, Safety factor and Margin of safety (curves or numbers: two numbers give a
  number, `first_type(op, project)`, so they also feed a Check or act as parameters);
  Normalise (by a number, a curve, or each curve's largest size); Curve formula (over x,
  y and wired a, b, c); At step; Compare with step (difference or ratio, or a history
  against one step's value); Envelope (max, min or mean); Match x values; Area under
  curve; Slope; Limit line; Annotate. Plot takes up to four curves (`value`, `b`, `c`,
  `d`) of one x and one y quantity and gives them as `curves`; the Plots panel draws
  limit lines dashed across and annotations labelled.
- Units follow through Puzzle's expression language: stress ÷ stress is a pure number,
  a stress plus a temperature is refused with the reason, curves with different x
  values are refused with "Match x values first". New quantity kinds for what curves
  give: force per length, stress gradient, temperature gradient, rate of temperature
  change, impulse.
- `tests/test_series.py` against hand-computed answers on test_operators' two tets
  (σxx = 100·t·x MPa at t = 1, 2, 3 s): utilisation along a path against Model value's
  yield (1.2 d), a safety factor and margin at the peak, normalised shapes, every step
  against step 1 (ratios 2 and 3), a history against its first step, envelope, slope
  (300 MPa/m), area (96 MPa·m), matching onto gauge positions, refusals, a parameter
  from arithmetic on model values, the agent's summary and MCP; `tests/test_series_gui.py`
  (the Plots panel's limit line and note in display units, the node's small plot,
  settings in words). The skill's Curves section.
