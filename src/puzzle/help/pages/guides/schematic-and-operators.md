---
title: Schematic and operators
section: guide
order: 130
keys: panel:schematic, section:operators
summary: See the whole project as nodes and wires, and turn results into numbers, curves, checks, tables and reports kept in the project.
adrs: 0058, 0062, 0064, 0071
---

The schematic draws the project as nodes and wires: what each study is built from, what
feeds what, and what a change would put out of date. Operators are nodes you add to it
to compute from the model and its results: a peak stress, a check against yield, a curve
along a wall, a table, a report.

## How to read the schematic

1. Click the **Schematic** tab beside Model, Geometry and Mesh: the schematic takes the
   centre in place of the 3D view. Window → Schematic (Ctrl+9) docks one beside the view
   instead.
2. Nodes sit in five columns: Inputs, Resources, Models, Studies, Results. Each shows
   its state as the tree does (up to date, out of date, not run, failed, missing).
3. Thin grey **use wires** say what is built from what (a geometry into the components
   using it). **Data wires** carry values: a transfer, an imported field, runs into a
   derived result or an operator.
4. Select a node and turn on **What a change reaches** to highlight everything that
   would go out of date if it changed. Do this before editing a shared material,
   geometry or mesh recipe.
5. A click selects the object in the tree and Properties; a double-click opens it where
   it is edited; a right-click gives the tree's own menu.

![The cantilever example's schematic: its geometry file, resources, loads and component in columns, its three studies, and a Result summary and a Check against limit wired from Tip load's stress](image:screens/schematic.png)

## How to build in the schematic

1. **New ▾** above the canvas, or a right-click on the empty canvas, holds the tree's
   add menus: geometry, materials, components, studies, coordinate systems, result tools.
2. Draw a wire from one node onto another to use one in the other: a material onto a
   component, a coordinate system or amplitude onto a load, a construction plane onto a
   result tool. When the wire could mean several things, a form asks which.
3. Draw a wire from a study's output onto another study to make a transfer (a
   temperature into a structural study). Drop a geometry on a component to add it as a
   part.
4. Right-click a wire to remove what it stands for. A reference an object can't be
   without (a study's component) is refused: wire another in to replace it.
5. Dragging a node only moves it. Positions are yours, kept outside the project;
   **Tidy layout** forgets them.

## How to add an operator

1. Press **Tab** in the schematic, or double-click the empty canvas, and type a name
   ("maximum", "divide", "check"). Results → Operators → Find an operator… does the same.
   The palette lists what **Fits here** first, then recent ones.
2. Or use **Add operator ▾**, or Results → Operators → New operator, by group.
3. With a node selected, the new operator is wired from its output when it fits.
4. Set its inputs in Properties: each input is a dropdown of what can be wired in, or a
   value typed with its unit ("200 MPa").
5. Right-click an operator, Evaluate up to here: it runs the studies it reads that are
   not up to date, then shows its value on its node.

![The operator palette searching for "check": the matching operators, and what the chosen one does](image:screens/operator-palette.png)

Every operator's node, with its inputs and outputs, is drawn on the
[Operators](help:reference/operators) reference page.

### A check against yield

1. A **Result summary** (reduce) on the study's stress: maximum von Mises on a selection.
2. A **Model value** reading the material's Yield strength. The generic Structural
   steel carries a mild steel's 250 MPa; for another grade pick it in Materials (ASTM
   A992, A36, ...), give your material its yield, or type the limit.
3. A **Check against limit** with the summary as its value and the yield as its limit.
   Its verdict is pass or fail; its margin is limit ÷ value − 1 (0.25 is 25 % to spare).
   When the value is zero or negative, or the check is "at least", the margin is the
   distance to the limit as a share of it, so a passing check never shows a negative
   margin.

![Tip load's stress wired into a Result summary named Peak stress, and that into a Check against limit with a typed limit of 250 MPa; both wait for the study to run](image:screens/schematic-chain.png)

The limit can be typed, as here, instead of read from the material.

### Curves, tables and reports

- A path result tool gives a curve per step; any result tool gives its history. Use
  Utilisation, Safety factor, Normalise, At step, Compare with step, Envelope and Area
  under curve on them; a **Plot** draws curves of one x and one y quantity in the Plots
  tab.
- A **Result table** holds a run's results per node with a unit per column. Keep rows
  where, Sort, Largest N rows, Calculated column and Summarise by group shape it; its rows show
  in the Table tab. Export writes CSV, Parquet or Excel in the units shown.
- A **Report** gathers checks, numbers, curves and tables into one HTML file. Right-click
  it (or an Export), Export now.

## Which run an operator reads

A study wired into an operator reads one of its stored runs, and Properties says which
under the input (**Run read**):

- By default, the study's run for the model as it is now. When the model has changed
  since, there is none: the operator reads the newest run anyway, and says so: its node
  turns out of date and its value reads "(stale: from the run of Tip load · CalculiX ·
  2026-11-17 10:02)". A check on it says "Pass (stale: ...)", never a bare Pass. Nothing
  is solved by itself: run the study (Evaluate up to here) when you want it current.
- A **point**: a sweep point ("load = 300 N"), a mesh-convergence level or an adaptive
  pass. A swept study read with no point is refused, saying how many points it has;
  a Plot or Over the steps against the sweep reads every point, and a Points table
  lists them.
- A **solver**: one of a shoot-out's.
- One **stored run**, held whatever changes later (still marked stale when the model
  has moved on).

## Operators that drive the model

Number, Slider, Formula and the list operators read no results, so they act as
**parameters** by name. A geometry step's argument, a sweep or a load's value can follow
one: in Properties, type "= t" in a value. Dragging a slider previews the geometry it
drives and never solves; letting go writes one undoable change.

## Scripts and trusted projects

A Python script operator runs in its own environment, with a time limit, in an empty
folder that can't read the project. Scripts and custom equations run only in a
**trusted** project. A project from elsewhere waits until you choose Project → Trust
this project…; trust one only if you know where it came from.

## Options worth knowing

- **Use wires**: all, for the selected node, or none (Settings > Schematic, or the
  dropdown above the canvas).
- After-solving operators are worked out when asked and never stored, so they never
  put a study out of date. Before-solving ones do, as a parameter does, but only
  the studies whose model reads them by name (a history argument, a value wired to
  one, a sweep, a toggle); a Number nothing reads changes nothing.
- Values inside are SI; units travel with them and a mismatch is refused.
- An operator that can't be worked out (a formula too large, a text where a number goes)
  shows its error on its node; the operators reading it say their input failed. One
  before solving is a problem (`operator_failed`): the studies whose model reads it are
  refused at run, the others run.
- Preferences never change a value: Round rounds in the unit it names (Unit), else in
  the unit Convert units gave the column, else SI, never in the display units; a
  Result table keeps the layout it was made with (Settings > Results > Table layout
  sets a new one's).

## Mistakes to avoid

- A limit typed without its unit: a stress checked against "200" is refused. Type
  "200 MPa". The same goes for Utilisation, Safety factor and Margin of safety: they
  divide like by like.
- Reading a swept study without choosing its point is refused: choose one under the
  input, or plot over the points.
- Arithmetic on two curves with different x values is refused: put Match x values first.
- An operator that reads results can't drive the model (`inconsistent`): loops are an
  optimisation's job.
- A required input left unwired shows as missing input (`operator_input`).
- A peak stress at a sharp corner grows with every refinement: check a value away from
  it.

## For agents and scripts

The agent reads the schematic with [schematic](help:mcp:schematic), wires with
[link](help:mcp:link) and [unlink](help:mcp:unlink), finds types with
[operator_help](help:mcp:operator_help), adds them with
[operator_add](help:mcp:operator_add), reads them with
[operator_value](help:mcp:operator_value) and writes reports with
[operator_export](help:mcp:operator_export). In a terminal:
[puzzle schematic](help:cli:schematic), [puzzle operators](help:cli:operators),
[puzzle table](help:cli:table) and [puzzle trust](help:cli:trust).

## See also

- [Every operator](help:reference/operators)
- [Results](help:guides/results)
- [Optimisation and mesh convergence](help:guides/optimisation-and-convergence)
- [Studies and solvers](help:guides/studies-and-solvers)
