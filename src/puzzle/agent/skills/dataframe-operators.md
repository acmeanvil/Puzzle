---
name: dataframe-operators
description: tables of results (polars, a unit per column): a run's results per node, a sweep's or optimisation's points, files; filter, sort, top N, new columns, group and aggregate, units, CSV/Parquet/Excel
adr: 0064
---
A table operator holds a polars DataFrame. Every number in it is SI, and each column
has a quantity kind (length, pressure, force...). Units travel with the data, and
exports and views show the display units, or a column's converted unit. Tables are
operators (ADR 0062), so they are wired as any operator is (`operator_add`, or `submit`
with `create` kind `operator`) and read with `operator_value`. That returns the columns
with their units, `row_count`, and the first 20 rows, never a whole result table.

## Sources

- `result_table`, wired from a study's `result` (or a field port, or a Filter):
  - Its columns are `node` (or `element`), `x`, `y`, `z`, `part`, `step`, then `time`,
    `frequency` or `load factor` (per the step kind), then one column per quantity.
  - `quantities`: result keys (`"stress:von_mises"`, `"displacement:total"`), or `"*"`
    for every result of the field; none means the first. Columns are named as equations
    name them: `von_mises`, `deformation`, `stress_xx`, `temperature`.
  - `steps`: `one` (the `step` index, −1 the last) or `all`.
  - `selection`: limit it to one, "Part/Selection".
  - `layout`: `long` (a row per item per step, the default by setting), or `wide` (a
    column per step, "von_mises · step 2").
- `points_table`, wired from an optimisation's or swept study's `points` port: one row
  per point, with its variables, outputs, feasible, reason and run.
- `file`: `file` is a path inside the project, or a pattern (`tests/*.csv`), and several
  files stack with a `file` column. CSV headers are `name [unit]`, as a Table
  operator's; Parquet and Excel headers may carry units the same way.

## Shaping

Every one of these takes `table` and gives `out` (a table):

- `columns`: `pick` (["x", "von_mises as vm"], in order) and `drop`.
- `filter_rows`: `condition`. Puzzle's syntax (the default) checks units:
  `von_mises > 200 MPa and part == "Bracket"`. Or `syntax: "polars"`:
  `pl.col("von_mises") > 2e8`, SI and unchecked. Only `pl.col`, `pl.lit`, `pl.when`
  and expression methods are allowed.
- `sort`: `by` (columns), `descending`.
- `top_n`: `column`, the `count` input (default 10), `largest` (false for smallest),
  and `by` for the top N per group.
- `new_column`: `new_name` = `expression`. In Puzzle's syntax the unit is worked out
  (`margin = 250 MPa / von_mises - 1` has none). In polars syntax give `unit`.
- `convert_units`: `column` shown and exported in `unit` (MPa, kN). The values stay SI.
- `round`: `pick` (default every float column), `decimals`, `significant`; it rounds in
  the shown units.
- `group`: `by` (part, step, point, solver) and `aggregations`: `"max(von_mises)"`,
  `"count()"`, `"mean(stress_xx) as avg"`, `"quantile(von_mises, 0.95)"`. The
  functions are max, min, mean, median, sum, std, count, first, last and quantile.

## Out of a table

- `to_number`: `column` and `reduction` (max, min, mean, sum, median, first, last,
  count). With `by`, it takes the `column` value in the row where `by[0]` is largest.
  The result feeds Check, Panel, sweeps' Plots and optimisation objectives.
- `lookup`: `table`, `at` (a number or list), `by` (the x column), `column` (y), and
  `interpolation` (linear, step or nearest): a material curve read at a temperature.
- `list` with `operation: "column"` gives a column as a list. `list` statistics:
  mean, median, std, percentile (the `index` input is the percent), product,
  weighted_mean (the `weights` input).
- `export`: `file` ending `.csv`, `.parquet` or `.xlsx`, in display units with
  `name [unit]` headers. The CLI is `puzzle table PROJECT OPERATOR [--csv F | --parquet
  F | --xlsx F] [--rows N]`.

Related, from the same ADR:
- `measure`: `target` (a component, or a geometry), `selection` ("Part", or
  "Part/Selection"), and `measurement` (length, area, volume, mass with the part's
  material density, radius, centroid_x/y/z, size_x/y/z), from the exact geometry with
  no solve. Its value can't drive the geometry it measures.
- An Expression that compares (`stress <= 250 MPa`) also gives a verdict on its
  `passes` port, with a margin.

## Combining (phase 2)

- `join`: `table` and `other` on `by` (default the first shared column), `how` inner,
  left or full. A column in both gets the second table's name, or `suffix`
  ("_aster": then `vm_aster - vm` in a New column is the solvers' difference).
- `stack`: `table`, `other`, `third`, with a `from` column (`new_name`).
- `pivot`: `by` (the row keys), `column` (its values become columns), `pick` (the
  values). `unpivot` is the reverse.
- `distinct` (`by`), `fill_missing` (`fill`: forward, backward, mean, zero, value with
  the `value` input, or drop; `pick`), and `rolling` (`column`, `reduction`, `window` 0
  for running, `by` the order, `pick` the groups).

## More sources (phase 2)

- `mesh_table` (from a result): element, type, part, x, y, z, shape (1 regular, 0 flat)
  and aspect. "The 10 worst elements": sort by shape, top 10 smallest.
- `project_table` (`objects`: parts, bolts, materials, selections, studies, features,
  contacts). Parts carry their volume and mass.
- `run_history` (from a study), `history_table` (from a result tool), `mode_table`,
  `contact_table`, `bolt_table` (from a result; `step`).
- `nearest`: `result` and `points` (a table with x, y, z): the node, its distance and
  the `quantities` there.
- `section`: `result`, `target` (a construction plane), `quantities`. Its `out` is the
  cut as a table; `force` and `moment` are numbers.
- `divide`: `target` (a construction path, or a geometry with `selection` an edge), the
  `count` input; it gives x, y, z and s.
- `clearance`: `target` (a component), `selection` and `other` (parts, or
  "Part/Selection"), the least distance where they are placed.
- `text`: `template` with `{x}`, `{y}`, `{z}` (`{x:.1f}` for places).
- `preview`: a table with x, y, z, labelled by `column`, pinned in the 3D view.
- `plot`, from a table: `by[0]` against `column`, `chart` line, bar or scatter.

## Before a solve (phase 3)

- A table into a study: a table's x, y, z and value columns become an imported field.
  `submit` a `create` of kind `field` with `{"name": ..., "format": "table",
  "source": "<table operator id>", "columns": [{"name": "x", "role": "x"}, ...,
  {"name": "T", "role": "value", "unit": "K"}], "length_unit": "m"}`. Values are SI,
  so give each value column its SI unit. Then use the field as any imported field
  (skill `imported-fields`).
- `toggle` (the `value` input 1 or 0) is a parameter. A feature's `when` names it, and
  the feature drops out of the solve while it is 0.
- `choice`: `options` (quantities), `labels`, and the `index` input; its `options`
  port is a list for a sweep.
- `slider2d`: `low`/`high` and `low2`/`high2`, the inputs `value` and `value2`; the
  parameters are `<name>_x` and `<name>_y`.
- `merge_points` and `thin_points` (`spacing`) tidy a cloud before it becomes a field.
- The Expression's real(a, phase) and imag(a, phase) take the phase in degrees.
- Every object takes `tags` (a list of words). They never stale a study.

## Recipes and clusters (phase 4)

`recipes` lists the ready-wired chains Puzzle ships, and the user's saved clusters
(keys `mine:<name>`), with their input ports. `recipe_insert(key, bindings)` puts one
in, each port bound to a source (`{"result": {"source": "<study id>", "port":
"result"}}`), as one batch. Then adjust the copies' settings (the stress margin's
limit, the measure's part) with `submit`. The recipes are: `worst_nodes`,
`peak_per_part`, `solver_difference` (ports result and other), `stress_margin`,
`reactions_kn` and `mass_from_geometry`. An Export of a result with `format: "png"`
saves a picture of it.

## Recipes by hand

- **The 20 worst nodes**: `result_table` (`quantities: ["stress:von_mises"]`) →
  `top_n` (`column: "von_mises"`, count 20) → `export` `worst.xlsx`.
- **Peak stress per part**: `result_table` → `group` (`by: ["part"]`,
  `["max(von_mises)"]`) → `convert_units` (MPa) → Plot or export.
- **Margin table**: `result_table` → `new_column` (`margin = 250 MPa / von_mises - 1`)
  → `filter_rows` (`margin < 0.2`) → `sort` (by margin).
- **Two solvers' difference**: two `result_table`s (one per solver's run, or a
  shoot-out's) → `join` (by node, suffix "_2") → `new_column` (`diff = von_mises_2 -
  von_mises`) → `top_n`.
- **Test gauges against the model**: a `file` of gauge positions → `nearest` (a
  result, `quantities`) → `join` with the measured values → `new_column` (the ratio).

## Mistakes

- A column name with a space is used with an underscore in Puzzle's syntax (`load_factor`).
- Units: `von_mises > 200` compares with 200 Pa. Write the unit.
- Text needs quotes (`part == "Bracket"`). A bare `Bracket` is read as a name or a unit.
- `operator_value` shows 20 rows. Use `to_number` or `group` for an answer, not the rows.
- In the Expression operator, prefer parameter names that aren't pint constants:
  `sigma` is Stefan–Boltzmann's constant there.
