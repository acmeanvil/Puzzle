---
name: schematic-operators
description: operators: results as numbers and verdicts; curves (paths, histories) as utilisation, normalised or against a step; parameters from sliders and formulas; geometry from data; scripts
adr: 0062
---
Operators are project objects that compute, wired together like Grasshopper's nodes.
There are two sorts:
- **After solving** (reduce, filter, check, panel, plot, export): they read stored runs,
  are worked out when asked for, and are never stored, so they never put a study out of
  date.
- **Before solving** (number, slider, series, range, expression, table, list, pairs):
  data that reads no results. They drive the model (see "Data that drives the model"
  below), and changing one puts the studies out of date, as a parameter does. Use them when the user
wants a figure or a check kept in the project, such as "max von Mises on the bracket
≤ 200 MPa", or "tip deflection against thickness".

## Finding the right operator (ADR 0071)

Call `operator_help` before using a type you are unsure of: with no arguments it lists
every type with its name and purpose; `search="maximum"` (or "divide", "pivot") finds
the types whose name, purpose or other names match; `type="check"` gives the full card
(purpose, an example, each input and output). The user sees an operator's *name*, and
`operator_add` takes its *type*; speak to the user in names. Some names differ from the
type (the type never changes, so old projects keep working):

| Name shown | Type |
| --- | --- |
| Result summary | reduce |
| Keep result where / Keep rows where | filter / filter_rows |
| Check against limit | check |
| Formula | expression |
| Stepped list | series |
| Points along | divide (points along an edge; not arithmetic) |
| Plane cut values | section |
| Values at points | nearest |
| Read off a table | lookup |
| Pick one value | to_number |
| Calculated column, Choose columns | new_column, columns |
| Rows to columns, Columns to rows | pivot, unpivot |
| Append tables, Match tables | stack, join |
| Remove duplicates, Largest N rows | distinct, top_n |
| Summarise by group, Running totals and averages | group, rolling |
| Show value, Mark points in view | panel, preview |
| Model contents, Read file, Random samples | project_table, file, random |
| On/off switch, Outline, Pair up lists, List item or statistic | toggle, profile, pairs, list |
| Divide (A ÷ B), Subtract (A − B), Multiply (A × B), Add (A + B) | divide_by, subtract, multiply, add |
| Utilisation, Safety factor, Margin of safety | utilisation, safety_factor, margin |
| Curve formula, Curve from table, Over the steps, At step, Compare with step | curve_formula, curve_from_table, over_steps, at_step, compare_steps |
| Envelope, Match x values, Area under curve, Slope, Limit line, Annotate | envelope, match_x, area_under, slope, limit_line, annotate |

## Model value: the model's own values (ADR 0071)

`model_value` reads a value the model holds, as it is now, with no solve: a material's
yield or ultimate strength, Young's modulus, density (or the property against
temperature, as a table), a load's or support's value, a section's thickness, a mesh
size, a step's setting, a parameter. Call `model_values` to list the objects that hold
values, and `model_values(object="Steel")` for what one holds (label, path, kind). Add
one with `operator_add(type="model_value", settings={"target": "Steel", "property":
"Yield strength"})` (a label or a path). Wire its `out` into `check`'s limit, a
`expression`, or a plot. It is a before-solving operator: it can also drive the model
as a parameter by its name, and it follows the model when the material or load changes.
For a utilisation check against yield: reduce (max von Mises) → check's value, and
model_value (Yield strength) → check's limit.

## Curves: paths, histories and what to make of them (ADR 0071)

A curve (port type `series`) is x and y with their units: one curve, or one per step
(a path in a transient run). Where curves come from:
- a result tool along a path (or a linearization), port `along`: one curve per step,
  distance against the value;
- any result tool, port `history`: its value against the steps (time, frequency,
  increment);
- `over_steps` (Over the steps): any number (a Result summary) at every step, or with
  `against="sweep"` at every sweep point;
- `curve_from_table`, or a table wired straight into a curve input: its first two
  number columns (or `by` = [x column], `column` = y column); a Model value against
  temperature is such a table.

What to do with them (units follow; a mismatch is refused with the reason):
- `utilisation` (value ÷ allowable), `safety_factor` (allowable ÷ value), `margin`
  (allowable ÷ value − 1), and `divide_by`, `subtract`, `multiply`, `add`: inputs `a`
  and `b`, each a curve or a number (typed "250 MPa" or wired). Two numbers give a
  number (so a safety factor can feed a Check); anything with a curve gives a curve.
  Two curves must share their x values: put `match_x` (inputs `series`, `onto`) first.
- `normalise` (input `series`, optional `by`: a number or curve; none: each curve by its
  largest size), `curve_formula` (`expression` over x, y and wired a, b, c: "y / 250 MPa").
- `at_step` (`step`, -1 = last) picks one step's curve; `compare_steps` (`step` = the
  reference, `comparison` difference or ratio) gives every other step against it, or,
  for a history, each step's value against the reference step's.
- `envelope` (`reduction` max, min or mean over the curves wired in), `area_under`
  (a number; one curve only), `slope` (dy/dx).
- `limit_line` (a number, drawn dashed across) and `annotate` (marks the largest or
  smallest point; `note` labels it) go into `plot`, whose inputs `value`, `b`, `c`, `d`
  take up to four curves of one x and one y quantity. Its `curves` output is what the
  Plots panel draws.

`operator_value` gives a curve as a summary, never every point: its axes and units,
`curve_count`, and per curve its `max`, `x_at_max`, `min`, `x_at_min`, a 5-point
`sample` and its `step`.

Example: von Mises along a wall against yield, plotted with the limit.
1. `operator_add("at_step", "Wall at the end", {"series": {"source": "Wall path", "port": "along"}})`
2. `operator_add("model_value", "Sy", settings={"target": "Steel", "property": "Yield strength"})`
3. `operator_add("utilisation", "Wall use", {"a": {"source": "Wall at the end"}, "b": {"source": "Sy"}})`
4. `operator_add("limit_line", "Yield", {"value": {"source": "Sy"}})`, then
   `operator_add("plot", "Wall stress", {"value": {"source": "Wall at the end"}, "b": {"source": "Yield"}})`

## Fields: results worked on at every node (ADR 0071)

A Filter gives a field: one result (`quantity`, e.g. "stress:von_mises") at one step
(`step`, -1 the last, 0 the first), on a selection or the whole model. Wire fields into
the arithmetic (Divide, Subtract, Multiply, Add, Utilisation, Safety factor, Margin of
safety), Normalise or Field formula (`expression` over `f`, `g`, numbers `a`, `b`, `c`
and parameters: "f - g", "f / 250 MPa", "abs(f)") and the output is a field too
(`first_type` says "field"); selecting it in the GUI draws it on the model. Use them for:
the change between two steps (two Filters at different steps, Subtract), utilisation
on the model (a Filter, a Model value's yield, Utilisation), a shape (Normalise). Two
fields must be on the same mesh (the same run's, or runs on one mesh); units follow
(stress ÷ stress is a pure number, stress + temperature is refused). Reduce turns a
field into a number (its maximum, where it is).

ParaView's filters, run on VTK in Puzzle (no ParaView needed): Slice (a field on a
construction plane, `target`, as a table of points x, y, z and the value), Clip (a field
kept on one side of the plane, `keep` above = the side the normal faces; still a field),
Contour (where a field takes a wired `level`, as a table of points). By ParaView's other
names: Threshold is Filter, Calculator is Field formula, Integrate Variables is Reduce's
integral, Plot Over Line is a Path result tool.

## Types and their ports

| Type | Inputs | Outputs | Its own settings |
| --- | --- | --- | --- |
| reduce | result (a result or field) | out (number) | quantity, selection, reduction (max, min, mean, integral), step |
| filter | result, limit (number, optional) | out (field) | quantity, selection, keep (all, above, below), step |
| check | value (number or field), limit (number) | out (verdict), margin (number) | compare (at_most, at_least) |
| panel | value (anything) | none | none |
| plot | value (number, table or curve), b, c, d (curves, optional) | out (table), curves (curve) | against (step, sweep), chart |
| export | value (anything) | none | file (inside the project), format (csv, or a result export format) |

What can be wired in:
- A study, by its port `result` (the newest run), or by a field such as `stress`
  (that field's results only).
- A derived result, by its port `result` (combinations and equations, ADR 0025).
- A result tool, by its port `out` (its value), `along` (a path's curve per step) or
  `history` (its value over the steps).
- A parameter set, by a parameter's name (a number with its unit).
- Another operator, by one of its output ports.

`quantity` is a listed result's key, such as `stress:von_mises`, `displacement:total`
or `temperature:value`. Left empty, the first listed result is used: the total
deformation for a whole result, or von Mises for `stress`. `selection` is
`Part/Selection` or a part. Values are SI inside: the outputs report metres and pascals.

Data operators:

| Type | Inputs | Outputs | Its own settings |
| --- | --- | --- | --- |
| number | value (typed) | out (number) | none |
| slider | value (typed, optional: its low) | out (number) | low, high, increment |
| series | start, step, count | out (list) | none |
| range | start, end, count (both ends included) | out (list) | none |
| expression | x, y, z (numbers or lists, optional) | out (number, or a list when a list goes in) | expression |
| table | none | out (table) | text: CSV with a header such as `t [mm], load [kN]` |
| list | list (a list or table), index, end | out (list), first (number) | operation (item, slice, sort, reverse, length, column, sum, min, max), column |
| pairs | a, b (lists) | out (table) | combination (zip: item by item, the shorter repeating its last; cross: every combination) |

## Make one

`operator_add(type, name, inputs, settings)` makes one operator as one undoable
command and returns its value. Inputs go by port name:
- a wire: `{"source": "<name or id>", "port": "<output>"}`;
- a typed value: `{"value": "200 MPa"}`.

Example: the maximum von Mises stress on a face, checked against 200 MPa.
1. `operator_add("reduce", "Peak stress", {"result": {"source": "Static", "port": "stress"}}, {"selection": "Bracket/Bolt holes"})`
2. `operator_add("check", "Strength", {"value": {"source": "Peak stress"}, "limit": {"value": "200 MPa"}})`

The Check's `out` is pass or fail. Its `margin` is limit / value − 1: 0.25 means 25 %
to spare, and a negative margin fails. For at_least it is value / limit − 1.

Inputs can also be set with `submit`: `{"command": "edit", "object": "Strength", "ops":
[{"op": "set", "path": "inputs.limit", "value": {"source": "<parameter set>", "port": "allowable"}}]}`.

## Data that drives the model

- A Number, Slider or number Expression that reads no results is a **parameter by its
  name**. A geometry history argument that names it takes its value, as one that names
  a parameter set's parameter does: `{"command": "edit", "object": "Plate", "ops":
  [{"op": "set", "path": "history.0.args.dx", "value": "width"}]}`. Sweeps can sweep it
  by name, and equations can use it.
- A list (Series, Range, List) gives a sweep's points:
  `{"name": "t", "source": "<operator id>", "port": "out"}` as a sweep parameter, in
  place of `values`. One run is made per item, and each run is stored with its point,
  as listed values are.
- An expression's names are its inputs x, y and z, the parameter sets' parameters, pi
  and e, and units. Write a unit after a number ("5 mm", "200 MPa") or as a name
  ("x / mm"). Units are checked: adding a stress to a length is refused. A result must
  have a unit Puzzle shows (divide by a unit otherwise). To use another operator, wire
  it into x, y or z; operators aren't read by name.
- An operator that reads results can't feed the model, and is refused with code
  `inconsistent`. Loops are an optimisation's job (ADR 0063).
- In the GUI, a slider dragged previews the geometry it drives and never solves.
  Letting go writes one undoable command.

## Geometry from data

- **Points** (x, y, z numbers or lists, or a table's x, y, z columns; `axes` a
  coordinate system), **Grid** (`region` plane: counts nu, nv and sizes along an axis
  `plane` at an origin or a construction plane `target`; box: corners x0..z1 and nu,
  nv, nw) and **Profile** (`shape` polyline through Points, circle, rectangle
  `centred`, polygon with `sides` and `angle`) compute before a solve.
- A history argument names them as it names a Number: `profile: "Hex"` on a face,
  extrude or revolve; `points: "P"` on a polyline or spline; a pattern's
  `positions: "Spots"` (offsets from the original, the first the original itself);
  `"sizes[2]"` or `"P[0].x"` for one entry, in a sketch's dimensions too.
- A pattern also takes a second direction (`count2`, `dx2`, `dy2`, `dz2`) or `along`
  an earlier polyline or spline (`count` copies at equal lengths).
- Points are a table of x, y, z: wire them into any table operator.
- A selection's rule may name parameters (`{"box": {"low": [...], "high": ["reach",
  0.02, 0.01]}}`), and takes `box`, `near` (point, distance), `normal` (direction,
  within degrees), `min_area`/`max_area`, or is a set of other selections:
  `{"union": [...]}`, `{"intersection": [...]}`, `{"difference": [first, rest...]}`.
- A construction plane's `drives` maps a value to a parameter
  (`{"origin.0": "cut_at"}`, `{"offset": "h"}`): a slider moves the plane.
- A Check fed a stress linearization runs ASME VIII-2's three checks against the
  limit as the allowable S; the verdict is the worst ratio's.
- Renaming a Number or Slider renames what names it (history, sweeps, expressions).

## Python scripts

A `script` operator's `code` sees inputs a, b, c, d (a number in SI, a list, a table as
{column: [values]}, points as [[x, y, z], ...], a text) and sets `out`, as its `returns`
says (number, list, table, points, text). It runs in the pixi `scripts` environment
(numpy, scipy, polars; `puzzle scripts add PACKAGE`), with `timeout_s`, in an empty
folder, never reading the project (macOS denies it). It runs only in a **trusted**
project: one from elsewhere waits until the user trusts it (Project → Trust this
project…, `puzzle trust`); the problem `untrusted` says so. Never ask to trust a
project yourself. Below autonomy "act", writing a script (or a custom PDE equation) is
proposed, and the user accepts it.

## Read them

`operator_value(name)` gives each operator's `outputs` (numbers with `unit`, and `at`
where a maximum or minimum is; a verdict's `passed` and `margin`; a table's `columns`
and `rows`), plus `shown` (what the schematic node shows) and `wiring`. With no name it
covers them all. The outline has one `Operator` line each. An operator whose study
hasn't run says so in `error`.

## Plots and exports

- A plot against `step` repeats the chain once per result step (time, frequency or
  increment).
- A plot against `sweep` repeats it once per point of the swept study it reads, using
  each point's newest run. The x column is the swept parameter's value when one
  parameter is swept, else the point's name.
- An export writes its file when asked: in the GUI, Export now; on the command line,
  `puzzle operators PROJECT NAME --export`. A result goes out in a result format (`vtu`,
  `exodus`, ...); everything else as CSV.

## Mistakes

- A wire that doesn't fit is refused, with code `invalid`: a result into a check's
  limit, or a value typed where only a wire may go (`result`, `value`).
- Loops are refused, with code `inconsistent`. Nothing goes from results back into the
  model: optimisation is a study of its own (ADR 0063).
- Leaving a required input unwired gives the problem `operator_input`, and the
  operator shows as missing input.
- A check compares like with like. A stress against a length is an error, so type the
  limit with its unit ("200 MPa", not "200").
- Arithmetic on two curves with different x values is refused: Match x values first.
  Area under curve takes one curve: At step or Envelope first.
- At step on a history (one value per step) is refused: it already is one value per
  step; read one step with Result summary's `step`.
- An integral needs values per node and a face or body. An integral of a stress over a
  body is in Pa·m³, and its unit isn't tracked.
