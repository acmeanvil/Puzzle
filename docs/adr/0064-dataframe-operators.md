# 0064. Dataframe operators: results as polars tables to shape, reduce and export

- Status: Accepted (2026-11-03): review answered, the operator list from 0064A; phase 1 built (2026-11-04)
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

6. **GUI: presenting the operators** (0064A's "Presenting them", adopted with its
   review):
   - **Few node types, each with modes**: about 45 types in all (one List with a Mode
     dropdown, one Expression, one Filter rows). The title shows the mode ("List ·
     sort"), and changing it keeps the wires that still fit.
   - **Seven groups in the order work flows**, the same in the toolbar's Add operator
     menu, the palette, the outline, MCP's lists and the skills: Inputs, Geometry,
     Logic, Results, Tables, Checks, Outputs, coloured by output type.
   - **What fits first**: with a node selected, or a wire dropped on empty canvas, a
     "Fits here" section lists the nodes whose input takes that output; recently used
     nodes follow.
   - **Aliases**: Grasshopper's, Excel's and SQL's names find the node ("Cull Pattern →
     Filter rows (every nth)").
   - **Recipes**: small wired chains for common jobs (worst N nodes labelled, peak per
     part as a bar chart, two solvers' difference, mass from geometry, reactions in kN,
     a stress margin). Users' own clusters (item 9) save the same way.
   - **Help**: one line and an example per node in the palette; readable dropdowns;
     advanced options folded.
   - **Values on the canvas**: a table node shows "1 234 rows × 6"; hovering a wire
     shows what flows.
   - The **Table viewer** (a dock beside Plots) shows the selected table's rows: a
     sortable grid, column types and units, and the count. Sorting or filtering there
     writes that operator's settings as one Edit. Properties offers the upstream
     table's columns as dropdowns.

7. **Twins**:
   - CLI `puzzle table PROJECT OPERATOR [--csv | --parquet FILE] [--rows N]`.
   - MCP `operator_value` returns a table's schema, row count and first rows (never the
     whole table), and `operator_add` takes the new types.
   - The outline shows each table operator's chain.
   - A skill `dataframe-operators`: when to use which, the units, the mistakes (joining
     different meshes by node id, aggregating across solvers).

8. **Phases** (0064A's row ids in brackets):
   1. **Tables and the presentation.**
      - Frame; the Result table, Points table and File sources (CSV, Parquet, xlsx, a
        folder) [A7, J1].
      - Columns, Filter rows with its condition builder [J2], Sort, Top N, New column,
        Convert units [J4], Group and aggregate [J3], Round [B7], To number.
      - CSV, Parquet and Excel export [A8]; both expression syntaxes.
      - The Expression gains comparisons as verdicts [B2] and more functions [B3]; List
        gains statistics [B5]; Lookup [A5]; Measure [E4, F3].
      - The Table viewer [A13]; item 6's groups, modes, aliases, help and "Fits here".
   2. **Combining and showing.**
      - Join, Stack [C2], Pivot and unpivot, Distinct, Fill missing [C7], cumulative
        and rolling [B6].
      - List modes [B8, C4, C5, C11 lists]; Text [C12, J7]; vector expressions [D3].
      - Nearest [D4, G2], Divide [E5], Section [F10], Clearance [F11].
      - The Mesh table [G1], Project table [J5], Run history [A12], and the probe,
        contact, bolt and mode sources.
      - Preview in the 3D view [I1]; bar and scatter plots [I3]; the wide layout setting.
   3. **Pre-solve tables and model inputs.**
      - Imported fields and files reshaped, To field into a study's loads, a run's
        table into another study as a transfer.
      - Toggle [A3], Choice [A4], the 2D slider [A2].
      - Points tidy and point-cloud thinning [D5, D7]; complex columns for harmonic
        tables [B9]; date and time columns [B11]; tags on objects [J6].
   4. **Reuse and reports**: clusters with their own ports, saved across projects
      [A11]; recipes; Snapshot (a result view saved as a PNG) [J8].
   - **Later**: reroute dots [A14], Freeze [A15].
   - **Elsewhere** (0064A's outcome):
     - ADR 0062 phase 3: geometry from data [D1, D2, D6, D8, E1, F4, F5, C11 sets of
       selections, H4].
     - ADR 0063: [A16, C9].
     - A geometry ADR (0065): [E2, F2, F9, F12–F16, H2, H3, H6, E7's Measure
       additions].
     - A meshing ADR (0066): adaptive refinement [G3].

9. **Clusters** [A11]: a group (ADR 0058) given input and output ports becomes one
   node. It can be saved to the user's library and dropped into another project,
   where it is copied in (not linked), so a project stays self-contained.

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
   those that apply to Puzzle, as a table to review: `0064A-grasshopper-review.md`.

## Phase 1 built (2026-11-04)

- **One table type**:
  - `operators.evaluate.Table` is a polars DataFrame with a quantity kind per column
    (values SI), plus `units`, each column's shown unit set by Convert units.
  - Built from columns and rows (a typed table, a plot's points) or from a frame. It
    keeps `columns`, `rows`, `csv()` and `text()` ("3 rows × 2: t, load"), so Lists,
    Points and sweeps read it as before.
  - polars and xlsxwriter are in the default environment.
- **Sources** (`operators/frames.py`):
  - `result_table`: node or element, x, y, z (an element's centre), part, step, the
    step's time, frequency or load factor, and the quantities by key (`*` for all of a
    field), named as equations name them. It takes `steps` one or all, a `selection`,
    and `layout` long or wide (Settings > Results > Table layout by default).
  - `points_table`: the `points` port that studies with sweeps and optimisations now
    offer.
  - `file`: CSV with `name [unit]` headers, Parquet or Excel, inside the project, with
    patterns stacked and a `file` column.
- **Table operators**:
  - `columns` (pick with `as`, drop), `filter_rows`, `sort`, `top_n` (per group),
    `new_column`, `convert_units`, `round` (places or significant figures, in shown
    units), `group` (max, min, mean, median, sum, std, count, first, last, quantile),
    `to_number`.
  - They are data operators: pre-solve when their table is (a file, a typed table),
    post-solve when it comes from a run.
- **Both expression syntaxes**:
  - Puzzle's, units checked: the Expression evaluator now takes a table's columns
    (text columns compared as text, `part == "Bracket"`).
  - polars': built by walking the `ast` against an allow-list (`pl.col`, `pl.lit`,
    `pl.when` and expression methods), never `eval`'d. `__import__` and the like are
    refused.
- **Exports**: an Export of a table writes CSV, Parquet or Excel by its file's suffix,
  in shown units with `name [unit]` headers.
- **From 0064A for phase 1**:
  - The Expression gains comparisons as verdicts (a `passes` port: one comparison keeps
    its margin) [B2]. It also gains asin, acos, atan, atan2, degrees, radians, clamp,
    round, floor and ceil [B3]; rounding works in base units.
  - List gains mean, median, std, percentile, product and weighted mean [B5].
  - `lookup` (linear, step, nearest) [A5].
  - `measure` (length, area, volume, mass with the part's density, radius, centroid,
    size, from the exact geometry) [E4, F3]. It is kept out of the parameters the
    geometry reads (`reads_geometry`), so it can't loop.
  - The File source with patterns [A7, J1]; the condition in Filter rows [J2]; Convert
    units [J4]; Group [J3].
- **Presentation** (item 6):
  - The seven groups (`operators.GROUPS`, `GROUP_OF`) everywhere: the schematic's Add
    operator menu, the menu bar's Results → New operator, and the palette.
  - Aliases per type (`ALIASES`: Grasshopper's, Excel's and SQL's names). "cull
    pattern" finds Filter rows and says so: "(as cull pattern)".
  - The palette and menu list what fits the selected node first ("Fits here", "Fits
    the selection"), then the recently used (this session), then every type by group.
  - Properties shows each type's own settings (now derived from the schema), lists of
    names as one text row ("x, y, von_mises as vm"), and dropdowns of the upstream
    table's columns, of Measure's components, geometries, parts and selections.
  - The **Table viewer** is a Table tab beside Plots: the selected table's first 500
    rows in shown units, with the count. A header click on a Sort sets its order (one
    Edit); elsewhere it sorts the view.
- **Twins**:
  - CLI `puzzle table PROJECT OPERATOR [--csv|--parquet|--xlsx FILE] [--rows N]`.
  - MCP `operator_value` gives a table's columns, `row_count` and first 20 rows.
  - The outline describes each table operator.
  - The skill `dataframe-operators`.
- **Tests**:
  - `tests/test_tables.py`, on the hand-made two-tet results (T = t·x, σxx = 100·t·x
    MPa): long and wide tables, selections, filters in both syntaxes with units and
    refusals, top N per step, new column, group's values and kinds, to number, units
    and rounding, the three export formats read back, files stacked, lookup, list
    statistics, verdicts with margins, new functions, measure of the beam (volume,
    mass, area, centroid, size), the CLI.
  - `tests/test_operators_gui.py`: the toolbar's groups and "Fits the selection",
    aliases in the palette, the recently used, the Table viewer on the cantilever's
    run, column dropdowns, Sort by header.

Not done in phase 1:
- Clicking a cell to filter (the viewer sorts, and sets a Sort's order).
- Recently used types last only for the session.
- A typed polars expression can't use `pl.Expr.map_elements`, `pl.Expr.cast` or I/O:
  by design.
