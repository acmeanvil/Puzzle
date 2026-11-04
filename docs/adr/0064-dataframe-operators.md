# 0064. Dataframe operators: results as polars tables to shape, reduce and export

- Status: Proposed; review answered (2026-11-03), operator list under review
- Date: 2026-11-03

## Context

ADR 0062's result operators turn a run into one number or one verdict (Reduce,
Check) or a masked field (Filter). Its `table` port type exists, but a table today is
only typed or read from a CSV (rows as text) and feeds Lists, Points and sweeps. What
engineers then do with results happens outside Puzzle, in a spreadsheet or a script:

- the 20 highest von Mises nodes with their positions and parts;
- the peak stress per part, per load step or per sweep point;
- two solvers' displacements joined by node and differenced (a shoot-out, ADR 0011);
- reactions summed per support and step, rounded and converted to kN for a report;
- a mode table (frequency, effective mass) or an optimisation's points (ADR 0063)
  sorted, filtered to the feasible ones and exported.

The store already holds results as Arrow (Delta Lake, ADR 0008), so a columnar
dataframe library reads them without copying. polars (MIT, Rust, on conda-forge for
all five platforms) is fast, lazy (a chain of steps is planned and run once) and
already named by ADR 0062 for the Python snippets' environment. It is not in Puzzle's
own environment yet.

The maintainer's ask (2026-11-03): polars dataframe operators to manipulate, convert,
organise, reduce, filter and sort the results data.

## Decision

1. **A table is a polars DataFrame with a unit per column.** The `table` port type
   (ADR 0062 item 2) carries a `polars.DataFrame` plus each column's unit and quantity
   kind (`Frame`: frame, units). Units travel through every operator: a filter keeps
   them, a new column's unit comes from its expression, an aggregate keeps its
   column's unit (a count is dimensionless), a join or concat needs matching units.
   Values are SI inside, as everywhere; the view and exports show the display units
   (ADR 0024). The existing Table operator (typed or CSV) produces a Frame too, so
   Lists, Points and sweeps read Frames. polars joins Puzzle's default environment
   (review answer 1), so the nodes work everywhere, and is imported lazily.

2. **Sources: results and other data as frames.**
   - **Result table**: a run's (or derived result's) field at nodes or elements. The
     columns are the id, x, y, z, the part, and the chosen quantities (any in the
     results bar: components, von Mises, principal stresses, magnitudes, in a
     coordinate system, ADR 0049). It has a step or time or frequency column. The
     settings are which quantities, steps (one, several, all), a selection to limit it
     to, and nodes or elements. It is long form by default (one row per item per
     step; review answer 3), or wide (a column per step) by the operator's `layout`,
     whose default comes from Settings > Results > Table layout.
   - **Points table**: a swept study's points (ADR 0018) or an optimisation's (ADR
     0063): one row per point, with the variables, outputs, feasible, run and solver.
   - **Probe history**: a result tool's values against steps (ADR 0032).
   - **Contact, bolt and mode tables**: the tables the Contact tool, the bolt table and
     modal runs already make (ADR 0056, 0050), as frames.
   - **Imported field** (ADR 0044) and a **file** in the project (CSV, Parquet).

3. **Frame operators**, each a node with a table input and a table output, named after
   what an engineer does:
   - **Columns**: pick, drop, rename and reorder columns.
   - **Filter rows**: a condition over the columns, in either syntax (review answer
     2): Puzzle's expression language, with units checked
     (`von_mises > 200 MPa and part == "Bracket"`), or polars' own expressions
     (`pl.col("von_mises") > 2e8`; columns in SI, no units checked). A polars
     expression is parsed with `ast` and allowed only polars' expression API on
     `pl.col`/`pl.lit` (never `eval`), so it needs no trust gate. Anything beyond that
     is a Python snippet (ADR 0062 item 7) behind the gate.
   - **Sort**: by one or more columns, each ascending or descending.
   - **Top / bottom N**: the N largest or smallest by a column, optionally per group.
   - **New column**: an expression in either syntax (`margin = allowable / von_mises -
     1`); a Puzzle expression's unit is derived and checked, a polars one's unit is
     given with it.
   - **Convert units**: a column to another unit of its kind (MPa, mm, kN).
   - **Round**: to decimals or significant figures.
   - **Group and aggregate**: group by columns (part, step, point, solver); min, max,
     mean, sum, count, standard deviation, a quantile, first or last; several at once.
   - **Join**: two tables on key columns (node id, point), inner, left or outer, with
     suffixes for clashing names. Two solvers' result tables joined by node, then a New
     column, give the difference.
   - **Stack**: several tables' rows together (concat), with a column saying which.
   - **Pivot and unpivot**: long to wide (a column per step or solver) and back.
   - **Distinct, slice, sample, fill missing**: the small tidy-ups.
   - **Cumulative and rolling**: running sums, maxima or means along an ordered column
     (time), for histories.

4. **Out of a table**:
   - **To number**: one value of a column (max, min, sum, or the row picked by another
     column). This feeds Check, Panel, sweeps' Plots and optimisation objectives and
     constraints (ADR 0063).
   - **To list**: a column as a list.
   - **Plot**: x and y columns, a series per group, into the Plots panel.
   - **Export**: CSV, Parquet or Excel (`.xlsx`, through polars' `write_excel`, with
     xlsxwriter, a unit row under the headers), inside the project (ADR 0062's Export;
     review answer 4).
   - **To field**: a column by node or element id back onto the mesh, drawn in the 3D
     view as a contour (a selection's ranks, a computed margin).

5. **Evaluation, pre- and post-solve** (review answer 5). A chain of frame operators
   builds one polars LazyFrame and collects it once, so a filter after a result table
   reads only what it keeps. Large results stay Arrow end to end (the store's lists to
   polars without a copy); the GUI shows the first rows and the counts.
   - **Post-solve** (reading a run): on demand, never stored, cached by the inputs'
     hashes and the run ids, as derived results are (ADR 0062 item 3).
   - **Pre-solve** (reading only imported fields, files and data operators): a table
     may feed the model. An imported field (ADR 0044) is reshaped (filtered, unit
     converted, a column computed) and then used as a load, temperature or initial
     state. **To field** is that use: a table with x, y, z and value columns (or ids on
     the target mesh) becomes the imported field's points. Its value goes into the
     study's content hash, as pre-solve operators' do, so editing a step stales the
     study with the reason.
   - **A run's table into another study**: this is the one place results feed a model.
     It is allowed as a transfer is (ADR 0009: the source study runs first,
     and the target's hash covers the source's run key), and cycles are refused, as
     ADR 0062 refuses them.

6. **GUI**:
   - The schematic's Add operator menu gains a Tables group.
   - A table node shows "1 234 rows × 6 columns" and its first column names.
   - Properties edits each operator with dropdowns of the upstream table's columns
     (readable names, never ids), and expressions are checked as typed.
   - A **Table viewer** (a dock beside Plots) shows the selected table's rows: a
     sortable grid, column types and units, and the count. Clicking a header to sort,
     or a cell to filter, writes that operator's settings as one Edit, so the view and
     the node never disagree.

7. **Twins**:
   - CLI `puzzle table PROJECT OPERATOR [--csv | --parquet FILE] [--rows N]`.
   - MCP `operator_value` returns a table's schema, row count and first rows (never the
     whole table), and `operator_add` takes the new types.
   - The outline shows each table operator's chain.
   - A skill `dataframe-operators`: when to use which, the units, the mistakes (joining
     different meshes by node id, aggregating across solvers).

8. **Phases**:
   1. Frame, the Result table and Points table sources, Columns, Filter rows, Sort,
      Top N, New column, Convert units, Group and aggregate, To number, the Table
      viewer, CSV, Parquet and Excel export, both expression syntaxes.
   2. Join, Stack, Pivot and unpivot, Round, Distinct, cumulative and rolling, Plot, the
      probe, contact, bolt and mode sources, the wide layout setting.
   3. Pre-solve tables: imported fields and files reshaped, To field into a study's
      loads, a run's table into another study as a transfer.
   4. The operators the Grasshopper review accepts (`0064-grasshopper-review.md`).

## Consequences

- Report tables, shoot-out comparisons and "worst N" lists become a few nodes in the
  project, recomputed when the runs change, instead of a spreadsheet that goes stale.
- Two more dependencies in the default environment: polars (about 35 MB) and
  xlsxwriter (small, pure Python) for Excel.
- Every frame operator must carry units, which costs care in each, but keeps "MPa or
  Pa?" out of exported reports.
- Tables can be large (a million nodes × steps). Laziness, Arrow without copies and
  the viewer's first rows keep that usable; an export writes everything.

## Alternatives considered

- **pandas**: more familiar, but eager, slower on large results, and its index adds
  rules users must learn; polars reads Arrow without a copy.
- **Only Python snippets** (ADR 0062 item 7): flexible, but needs the trust gate and
  code for everyday steps; nodes are readable, checked and hashed.
- **DuckDB SQL**: powerful, but SQL text in a node is less discoverable than named
  operations, and units have no place in it.

## Review questions

1. polars in Puzzle's default environment (about 35 MB), so the nodes work everywhere,
   or only in the Python snippets' environment (then tables run in a subprocess)?
2. Row filters and new columns: Puzzle's unit-checked expression language
   (`von_mises > 200 MPa`), polars' own expression syntax, or both (polars syntax
   behind the trust gate)?
3. Result tables in long form (one row per item per step, Pivot for wide), as
   proposed, or wide by default?
4. Excel export (`.xlsx`, through openpyxl or xlsxwriter) wanted for reports?
5. A table feeding the model (a table of nodal loads becoming an imported field, ADR
   0044) would make it pre-solve, with results upstream of another study, like a
   transfer. In scope, or keep tables post-solve only?
6. Anything to add to the operator list (item 3)?

## Review (2026-11-03)

The maintainer's answers:

1. polars in the default environment, so the nodes work across the board (item 1).
2. Both expression syntaxes, for completeness (items 3 and 4's filters and columns).
3. Long form by default, with a setting for wide (item 2).
4. Export to CSV, Parquet and Excel (item 4).
5. Both pre- and post-solve: an imported field can be reshaped by frame operators and
   then feed a study (item 5).
6. Review Grasshopper's nodes (https://grasshopperdocs.com/completeIndex.html) for
   those that apply to Puzzle, as a table to review: `0064-grasshopper-review.md`.
