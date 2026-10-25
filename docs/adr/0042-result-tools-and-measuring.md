# 0042. Result tools beside the results, and exact measuring in the geometry

- Status: Accepted
- Date: 2026-10-25

## Context

The cantilever example's tip load is 100 N. Its reaction field, shown as colours, gives −32.7 to +53.5 N per node at the support and lights up the loaded tip too. That is right but not readable: a reaction is the sum over the support, and CalculiX's nodal force is the internal force at every node, so the loaded nodes carry the load. `results.query.total` already sums a field over a selection, but only agents and scripts can reach it.

The maintainer asked (2026-10-25) for a reaction probe, a wider set of result tools like Ansys's, and measuring tools in the Geometry tab. They also noted that tools such as probes are hard to find: today a probe is a double-click nobody is told about, and kept probes come from a button in the Probes list.

- **Ansys Mechanical:** the Solution branch holds result objects and probes:
  - Force Reaction and Moment Reaction, on a support or a selection, with a moment summation point;
  - scoped results with min, max and average over a selection;
  - Path results with a chart along the path;
  - beam, bolt and spring probes.
  Each updates with each solve and plots over time. Selection Information and the status bar give area, length, volume and distances; DesignModeler and SpaceClaim have a Measure tool.
- **PrePoMax:** Query reads node, element, surface or part values with min, max and sum.
- **Puzzle already has:**
  - kept point probes (`ResultProbe`, ADR 0032) and plots over steps (ADR 0038);
  - fastener forces (ADR 0027);
  - the pick bar's area, length and distance (ADR 0034). Those come from the display tessellation, so they are approximate, and they show only in the Model tab.

## Decision

1. **A result tool is a kept probe with a kind.** `ResultProbe` gains `type`:
   - `point`: today's probe;
   - `selection`: a result over a selection (body, face, edge or point), reduced by max, min, mean or integral;
   - `reaction`: the force and moment a support takes, as the sum of `reaction_force` over the nodes of a selection or of a support feature's selection. The moment is taken about the nodes' centroid, or about a given point. Only solvers that write reaction forces (CalculiX, Code_Aster) give it; the others are refused with the reason;
   - `path`: a result sampled at N points along a straight line between two points, as a series to plot.
   Values are never stored. Every type is evaluated by `post/probes.py` for its source's current run (or derived result), and every scalar type gives a history over steps for ADR 0038's plots: a reaction against time, for example.
2. **In the GUI**, result tools sit under Results in the tree, each labelled with its current value ("Force reaction on Bar/Root: 100 N"):
   - added from the Results menu (Add result tool) or from a support feature's menu (Reaction force);
   - selecting one shows its result with its selection highlighted, and Properties lists every component;
   - a path opens its chart in the Plots tab.
   The reaction's colour plot is left as it is, but Properties for it explains that a support's reaction is the tool's sum, not a node's value.
3. **Exact measuring, from the CAD geometry** (`geometry/measure.py`), for named entities of a part:
   - **one entity:**
     - point: coordinates;
     - edge: length, plus radius and centre for circles;
     - face: area and centroid, plus normal for planes, radius and axis for cylinders;
     - body: volume, surface area, centroid and bounding box;
   - **two entities:** the minimum distance between them and the points where it occurs, the distance between their centres with ΔX/ΔY/ΔZ, and the angle between two planes or two straight edges;
   - **more:** totals of length, area and volume.
   OCCT computes these exactly (`BRepGProp`, `BRepExtrema_DistShapeShape`, `BRepAdaptor`), not from the mesh.
4. **A Measure tool in the Geometry tab's ribbon.** Pick in the view; the Properties dock lists the measurements in display units, updated as the picks change. The Model tab's pick bar uses the same exact measurements where the geometry is available, falling back to the tessellation (faceted geometry).
5. **Every tool is visible where it applies.** Ansys shows a context toolbar for the Solution branch (Deformation, Strain, Stress, Probe, Tools); Puzzle does the same:
   - **a Results ribbon** shows with any result, beside the results bar: Probe (point; the status bar then says "double-click the part to read a value"), Reaction, Result on selection, Path, Section cut, Animate and Plots. Each button says in its tooltip what it does and what to pick;
   - **the Geometry ribbon** gains an Inspect group with Measure; the Model tab's pick bar has a Measure button too;
   - right-click menus keep the same actions, so both ways lead to them;
   - an empty Results branch in the tree says how to add a tool.
6. **Twins:** MCP `geometry_measure` and `probe_value` (any kind), and the CLI `puzzle measure PROJECT GEOMETRY NAME…`.

## Consequences

- One evaluation path serves the tree, the plots, MCP and the CLI, as ADR 0032 intended.
- The probe object grows fields that only some types use; Properties hides the rest (`APPLIES`).
- Measuring regenerates the part's history (cached by the preview). Large imported parts take as long as a regeneration.
- A reaction needs the solver's nodal reactions. Elmer and FEniCSx don't write them yet, so their reaction tools refuse.

## Alternatives considered

- Hide the reaction field on unsupported nodes: it removes the confusing tip, but the per-node values at the support still don't add up to anything readable. The sum is the answer.
- Measuring on the mesh: available everywhere, but it depends on the mesh size and differs from the CAD's numbers.
- A separate object kind per result tool: more classes and tree code for the same evaluate-and-show pattern.

## Built (2026-10-25)

- **Result tools** (`ResultProbe.type`; `post/probes.py` `at_step`, used by the tree, the plots, MCP and the CLI):
  - `reaction`: the selection's nodal reaction forces summed, and their moment about a given point or the selection's area (or volume) centroid. Refused with the reason where the results have no reaction forces;
  - `selection`: max or min (with where), or the area- or volume-weighted mean or integral over a face or body (shape-function weights, as `results.query.integrate`); points and edges give max, min and a plain mean;
  - `path`: N points on a straight line, interpolated in the elements; points outside the model are left out and counted;
  - every type has a history over steps for the Plots tab (a path gives its largest value per step).
- **Benchmarks** (`tests/integration/test_result_tools.py`, CalculiX, a 200 × 10 × 10 mm steel cantilever under 100 N):

  | Check | Result |
  | --- | --- |
  | Reaction force on the clamp | 100.0003 N (the .frd's printed digits) |
  | Reaction moment about the face's centroid, P L = 20 N·m | 20.000006 N·m |
  | Tip face mean deflection against P L³ / 3 E I | −0.20 % |
  | Deflection along the axis against the beam's curve, beyond L/4 | within 0.57 % |

- **GUI:**
  - **the Results ribbon** shows with any result: Probe (says how: double-click), Reaction…, On a selection…, Path…, Section cut, Animate, Plots;
  - each tool is one form (`gui/result_tools.py`): the results it reads (the shown study first), the selection (a support's selection first, named by its feature: "Bar/Root: fixed support"), the result, how to reduce it, the path's ends (default: through the model's middle along its longest side), and a name that follows the choices;
  - a support feature's menu has Reaction force…, and the Results section's menu has Add result tool;
  - the tree lists tools by name under Results; selecting one shows its source's result and its values in Properties (force and moment components in display units, moments as force·length); a path opens the Plots tab against the distance along it.
- **Measuring** (`geometry/measure.py`): exact values from OCCT for one pick (point position; edge length, circle radius and centre, line direction; face area, centroid, plane normal, cylinder radius and axis; body volume, surface area, centroid and size), two picks (minimum distance and where, centre distance with dX, dY, dZ, angle between planes or straight edges) and totals. `measure_placed` measures picks across a component's parts where they are placed.
  - GUI: Measure in the Geometry ribbon's Inspect group and in the pick bar opens a panel in the Properties dock, updated as you pick, with Copy;
  - twins: `puzzle measure PROJECT GEOMETRY face:B/+z …`, MCP `geometry_measure`; MCP `probes` reads every tool type and `probe_path` gives a path's series;
  - tests: `tests/test_measure.py` (a holed block against closed form; two placed parts), `tests/test_gui_geometry.py`, `tests/test_gui_result_tools.py`.
- **One dialog per new object** (asked on the way, 2026-10-25): new study, component, project, part, parameter and Assign material each asked two to five questions in a row of popups. They are now one form each (`gui/quick_form.py`: choices rebuilt from earlier fields, rows that don't apply hidden, a name that follows the choices until typed, the reason OK is blocked shown on top). `tests/test_quick_form.py`.
- **Not yet:** picking a path's ends or a moment point in the view; tools over several selections at once; beam and spring probes beside the fastener forces; the pick bar's own read-out still comes from the display mesh (Measure is exact).
- **The menu bar** (asked 2026-10-25): Project, Materials, Geometry, Mesh, Model, Results, Window and Help hold every ribbon tool and section menu item (`gui/main_menu.py`); an action that needs a part, component or study takes the selected one or asks in one form, and geometry and mesh tools open their tab. Help has Getting started, Keyboard shortcuts (from the menus), the ADRs and About. Window → Model and Properties bring closed panels back. `tests/test_main_menu.py` checks that nothing on a ribbon or a section menu is missing from the bar and that no shortcuts clash.
