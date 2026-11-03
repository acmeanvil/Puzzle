---
name: schematic-operators
description: operators: results as numbers and verdicts (max stress, pass/fail, a plot over steps or a sweep); sliders and expressions as parameters; a list as a sweep's points
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

## Types and their ports

| Type | Inputs | Outputs | Its own settings |
| --- | --- | --- | --- |
| reduce | result (a result or field) | out (number) | quantity, selection, reduction (max, min, mean, integral), step |
| filter | result, limit (number, optional) | out (field) | quantity, selection, keep (all, above, below), step |
| check | value (number or field), limit (number) | out (verdict), margin (number) | compare (at_most, at_least) |
| panel | value (anything) | none | none |
| plot | value (number) | out (table) | against (step, sweep) |
| export | value (anything) | none | file (inside the project), format (csv, or a result export format) |

What can be wired in:
- A study, by its port `result` (the newest run), or by a field such as `stress`
  (that field's results only).
- A derived result, by its port `result` (combinations and equations, ADR 0025).
- A result tool, by its port `out` (its value).
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
- An integral needs values per node and a face or body. An integral of a stress over a
  body is in Pa·m³, and its unit isn't tracked.
