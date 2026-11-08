---
name: derived-results
description: combining load cases (linear, SRSS, envelope, range and mean) and custom result equations over stored runs, and querying them like a study's results
adr: 0025
---
Read this when the user wants load cases added or scaled, a worst case over several
runs, a stress range for fatigue, or a result computed by a formula ("safety factor =
yield / von Mises").

A derived result is a project object of kind `result`. It is evaluated from stored
runs whenever it is read, and never stored, so it follows its runs: rerun a study and
it changes. No solve is needed. Its `method` is one of:

| method | needs | gives |
| --- | --- | --- |
| `linear` | `cases` | Σ coefficient × field, component-wise; the same field names as the runs, so von Mises etc. are taken after combining |
| `srss` | `cases` | √Σ (coefficient × field)², component-wise |
| `envelope` | `cases`, `quantity`, `extreme` (max or min) | fields `envelope` and `envelope_case` (1-based case number) |
| `range` | `cases` | `<field>_range` and `<field>_mean` per field: two cases give case 1 − case 2, more give max − min |
| `equation` | `source`, `expression`, `unit` (optional) | the field `equation` |

A case is `{"study": "<study id>", "point": null, "step": -1, "coefficient": 1.0}`:
the study's newest completed run (or a sweep point's, by its title, "Copper"), one of
its result steps (index from 0; −1 the last), scaled. Cases on different meshes are
mapped onto the first case's mesh. Fatigue (`fatigue`) and error estimates
(`error_estimate`) are derived results too; see skills `fatigue` and `mesh-convergence`.

## Steps

1. `outline(ids=true)`: the studies and their ids. `result_summary(study)`: each run's
   steps and fields, to choose `step`.
2. Create it (one `submit`). Ids, not names, in `study` and `source`.
   - Two load cases, the second at 1.5:
     `{"command": "create", "kind": "result", "data": {"name": "Dead + 1.5 Live",
     "method": "linear", "cases": [{"study": "<Dead id>"}, {"study": "<Live id>",
     "coefficient": 1.5}]}}`
   - The worst von Mises over three cases: `"method": "envelope", "quantity":
     "stress:von_mises", "extreme": "max", "cases": [...]`. `quantity` is a listed
     result's key (`displacement:total`, `stress:von_mises`, `temperature:value`).
   - A load and its reverse, from steps 0 and 1 of one study: `"method": "range",
     "cases": [{"study": "<id>", "step": 0}, {"study": "<id>", "step": 1}]`.
   - An equation: `{"command": "create", "kind": "result", "data": {"name": "Safety",
     "method": "equation", "source": "<study or derived result id>", "expression":
     "250e6 / von_mises"}}`. `source_point` names a sweep point and `source_step` a
     step (−1 the last). `unit` is the unit kind of the value (`pressure`, `length`),
     left out for a ratio.
   - An equation with a value from elsewhere: `"inputs": [{"name": "B", "source":
     "<operator or result tool id>", "port": "out"}]` makes `B` usable in the
     expression (`"von_mises / B"`): the one number that operator gives (a Model value's
     yield strength, a Reduce over another run, a check's margin), in SI. A table, list
     or field there is refused: reduce it to a number first.
3. Before writing an expression: `equation_names(study)` gives the names a study's
   newest run offers (`von_mises`, `deformation`, `deformation_y`, `stress_xx`,
   `stress_p1`, `temperature`), the project parameters, and the functions.
4. Check it: `derived_result(name)` gives `runs` read, `results` (each `field` and
   `key`), `warnings`, and for a combination the names an equation over it may use.
5. Query it: every `result_*` tool takes the derived result's name in place of a
   study: `result_stats(study="Dead + 1.5 Live", field="stress")`,
   `result_stats(study="Safety", field="equation")`,
   `result_value_at(study="Safety", field="equation", x=0.1, y=0, z=0)`.

## The equation language

Names from `equation_names`, numbers, `+ - * / ** %`, comparisons, `and`, `or`, `not`,
the constants `pi` and `e`, and the functions `abs`, `sqrt`, `exp`, `log`, `log10`,
`sin`, `cos`, `tan`, `min`, `max`, and `where` (condition, value if true, value if
false). `max` of one argument is the largest over the model; of several, the largest
at each node (`min` likewise). The expression where(von_mises > 200e6, 1, 0) is 1
where von Mises passes 200 MPa. Nothing else: no
attributes, no indexing, no text, no keyword arguments. A typo is refused with the
nearest name ("did you mean von_mises?").

## Units

Everything is SI: stresses in Pa, lengths in m, temperatures in K. Write `250e6`, not
`250` or "250 MPa", for 250 MPa in an expression. Parameters come in SI too.

## Mistakes to avoid

- A linear (or SRSS) combination, or an equation adding runs, of nonlinear runs is
  not valid superposition. Puzzle warns only for a `linear` combination of a plastic
  run (one with `plastic_strain_equivalent`). Runs with frictionless or frictional
  contact, large deflection (NLGEOM) or prestress get no warning: you must tell the
  user the sum is only an estimate, or solve the combined load case as its own step.
  An envelope or range over such runs is fine: it picks values, it adds nothing.
- Mixing per-node and per-element results in one equation: refused.
- Expecting a combination to keep global values (energies): they are left out.
- A case whose study has no completed run (or not that point): `not_found`. Run it.
- A field that only some cases have is left out of the combination (a warning says so).
- An equation referring to itself through another derived result: refused.

In the GUI: Results → New combination… or New equation…, or right-click a study.
