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

## Recipes

- **The 20 worst nodes**: `result_table` (`quantities: ["stress:von_mises"]`) →
  `top_n` (`column: "von_mises"`, count 20) → `export` `worst.xlsx`.
- **Peak stress per part**: `result_table` → `group` (`by: ["part"]`,
  `["max(von_mises)"]`) → `convert_units` (MPa) → Plot or export.
- **Margin table**: `result_table` → `new_column` (`margin = 250 MPa / von_mises - 1`)
  → `filter_rows` (`margin < 0.2`) → `sort` (by margin).
- **Two solvers' difference**: phase 2's Join. Until then, compare two `to_number`s.

## Mistakes

- A column name with a space is used with an underscore in Puzzle's syntax (`load_factor`).
- Units: `von_mises > 200` compares with 200 Pa. Write the unit.
- Text needs quotes (`part == "Bracket"`). A bare `Bracket` is read as a name or a unit.
- `operator_value` shows 20 rows. Use `to_number` or `group` for an answer, not the rows.
- In the Expression operator, prefer parameter names that aren't pint constants:
  `sigma` is Stefan–Boltzmann's constant there.
