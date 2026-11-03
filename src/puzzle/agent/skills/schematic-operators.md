---
name: schematic-operators
description: turning results into numbers and verdicts (a maximum stress on a selection, a pass/fail against an allowable, a value against time or a sweep), shown in the schematic; operators
adr: 0062
---
Operators are project objects that compute, wired together like Grasshopper's nodes.
These are post-solve operators: they read stored runs, are worked out when asked for,
and are never stored, so they never put a study out of date. Use them when the user
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
