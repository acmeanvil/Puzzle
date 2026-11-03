# 0058. A project schematic: the whole project as nodes and wires, generated from the model

- Status: Built (2026-11-02); accepted 2026-10-31
- Date: 2026-10-31

## Context

The maintainer wants a schematic of a project like Ansys Workbench's project schematic
or Blender's node editor: a picture of how studies feed each other, which geometry,
meshes and materials they share, and which outside data (CAD files, imported fields)
comes in. The model tree shows all of it, one object at a time; nothing shows it at a
glance.

Two ADRs already decided part of this, and neither is built:

- ADR 0009: the schematic is generated from the tree, laid out automatically, with
  transfers as its only edges; a free-form node editor as the source of truth was
  rejected. That was to avoid Workbench's "octopus", whose causes ADR 0009 lists:
  links per cell, one kind of wire for two meanings ("shares this geometry" and "takes
  this result"), sharing hidden, layout carrying meaning, and staleness without a
  reason.
- ADR 0044 item 8: a read-only Connections map (Window → Connections) of data links
  only (imported fields into features, transfers between studies), boxes coloured by
  state (ADR 0033), links labelled with what they carry; ordinary references
  (a component's geometry and mesh) kept out so the map stays small.

What the maintainer asks adds two things to those: **shared resources** (which
components use one geometry, mesh recipe or material; which studies run one
component) and **external inputs** (files) on the same picture, and a **node look**
(boxes with ports and wires, as Blender's) rather than a grid of cells.

What the code has already: every object's `dependencies()` (what it reads, used for
content hashes), `Transfer` objects, `project.graph.StudyGraph` (studies and transfers,
run order, downstream), every object's state with its reason (`project/status.py`,
ADR 0033), tree groups (`TreeGroup`), and the readable labels (`gui/choices.py`).

## Decision

1. **One schematic, generated from the model, never the source of truth.**
   `puzzle.schematic.build(project, runs, states) -> Schematic` (nodes, ports, wires,
   groups) is a pure function of the project, its runs and their states; the GUI, the
   CLI and the agent all draw or read that one structure. Nothing is stored in the
   project for it, so it can't drift from the model, and layout never means anything
   (ADR 0009's rule 1 holds).

2. **Nodes, in columns from left to right**, each column one kind of thing:

   | Column | Nodes | From |
   | --- | --- | --- |
   | Inputs | files: CAD (STEP, IGES, BREP, STL/OBJ/3MF), sketches' DXF, imported fields' files (ADR 0044) | `Geometry.source`, history steps' files, imported fields |
   | Resources | geometry, materials, mesh recipes, parameter sets | the tree's resources |
   | Models | components | `Component` |
   | Studies | studies, in transfer order (a study fed by another sits to its right) | `Study`, `StudyGraph.run_order` |
   | Results | runs (the latest per study, a sweep's or shoot-out's as one node), derived results (ADR 0025), fatigue | the result store, `ResultDefinition` |

   Smaller references (coordinate systems, amplitudes, bolt types, construction
   geometry, settings presets) are not nodes: they show as chips on the node that uses
   them ("CS: Flange", "Amplitude: Ramp"), and a chip's tooltip names everything else
   that uses the same one. A sweep shows its points as a badge ("× 12 points") and a
   shoot-out its solvers ("CalculiX · Code_Aster"); neither is expanded into nodes.

3. **Two kinds of wire, drawn differently**, so one wire never means two things (the
   octopus's second cause):
   - **Data wires** carry values: a transfer (study output → study input, labelled
     with the field, step and mapping: "temperature, step 3, interpolate"), an imported
     field into the feature it drives ("p ×1.2"), runs into a derived result. Solid,
     labelled, coloured by what flows (temperature, displacement, stress, pressure,
     velocity: one theme colour each, ADR 0045), as Blender colours sockets by type.
     Always shown.
   - **Use wires** say "is built from": file → geometry, geometry → component, material
     → component, mesh recipe → component, component → study, parameter set → sweep.
     Thin, grey, unlabelled. Shown by the setting `schematic.uses`: **all** (the
     default, so sharing is seen at a glance), **for the selected node** (selecting a
     geometry draws its users, selecting a study what it is built from), or **none**.
     With all shown, selecting a node still brings its own wires forward and dims the
     rest (item 5), and a resource used more than once carries a count ("used by 3"),
     so a busy picture stays readable (the octopus's third cause).

4. **Nodes have ports, as Blender's do.** A study node's inputs are its component and
   each transfer or imported field it takes; its outputs are the fields its runs make
   (displacement, stress, temperature, contact pressure, ...: what its physics
   produce, from the catalog, not from a run, so a study not yet run still shows them).
   A data wire joins an output port to an input port. A component node lists its parts
   and their materials inside, folded by default.

5. **State on every node** (ADR 0033): coloured and iconed as in the tree (up to date,
   out of date, missing input, not run, running, failed), the reason on hover ("the
   mesh recipe changed"). **Upstream and downstream on selection**: selecting a node
   highlights what it depends on and dims the rest; a toggle shows instead what would
   go out of date if it changed (its downstream through use and data wires), so a
   change's reach is seen before it is made (the octopus's fifth cause).

6. **Layout is automatic and stable, and dragged positions are remembered per user**:
   layered by column, then ordered within a column to shorten wires (barycentric
   ordering, a few sweeps), written in Python with no Graphviz dependency; the same
   project always lays out the same way, and adding an object moves its neighbours
   little. A node the user drags keeps its place: positions (and what is folded) are a
   per-user view preference, kept in the user data dir by project and object id
   (`schematic.json`), never in the project, so they never change a hash, a run or
   another user's picture. Nodes not dragged are still laid out automatically around
   the pinned ones; a node whose object is deleted drops its position; Tidy layout
   forgets them all. Tree groups (`TreeGroup`) are frames around their nodes and fold
   into one node. Where a node sits still never means anything (ADR 0009's rule 1).

7. **Interaction, all through what exists**:
   - a click selects the object in the tree and Properties; a double-click opens it
     where it is edited (a geometry in the Geometry tab, a study's runs in Results, a
     run's results in the view);
   - right-click gives the tree's own menu for that object (`gui/menus.py`), so every
     action there is here too;
   - search and filter (by name, kind or state), zoom, fit, and a minimap for large
     projects;
   - export the picture as SVG or PNG for reports.
   Phase 2 lets a wire be drawn: dragging from a study's output port to another's
   input opens the transfer dialog with both ends filled (one `Create` command, so it
   is undoable and logged like any edit, ADR 0015); dropping a geometry on a component
   adds a part. The schematic still never holds anything itself.

8. **Where it lives**: Window → Schematic (a dock, tabbable with the view, ADR 0045),
   and a Schematic tab beside Model, Geometry and Mesh. It replaces ADR 0044's
   Connections schematic (that ADR's other two ways, the tree and the 3D view, stay);
   `connections.schematic` becomes the switch for showing imported fields' wires.

9. **Twins**: `puzzle schematic PROJECT [--svg FILE] [--json]`; MCP `schematic` returns
   the nodes, wires and states as data, so the agent can explain how a project fits
   together and what a change reaches; the outline (ADR 0015) gains a short "flow"
   section from the same structure. A skill `project-schematic` (ADR 0057).

10. **Phases**: 1, the read-only view with use and data wires, state, selection
    highlighting, CLI, MCP and SVG export; 2, drawing wires to make transfers and parts;
    3, imported fields' nodes and wires once ADR 0044 is built.

A sketch (the bracket example with a thermal study feeding a structural one; `═`
data wires, `─` use wires shown because "Base plate" is selected):

```
 Inputs          Resources         Models               Studies                     Results
┌────────────┐  ┌─────────────┐   ┌────────────────┐   ┌─────────────────┐        ┌──────────────┐
│ base.step  ├──┤ Base plate ●├─┬─┤ Bolted bracket ├─┬─┤ Heat-up       ✓ ├────────┤ Heat-up, run │
└────────────┘  │ used by 2   │ │ │ Base · Bracket │ │ │ CalculiX  T ○═══╪══╗     └──────────────┘
                └─────────────┘ │ └────────────────┘ │ └─────────────────┘  ║
                                │ ┌────────────────┐ │ ┌─────────────────┐  ║     ┌──────────────┐
                                └─┤ Bonded bracket │ └─┤ Bolted load   ⚠ │  ║     │ Fatigue      │
                                  └────────────────┘   │ ○ T ════════════╪══╝     │ (not run)    │
                                                       │ out of date:    │        └──────────────┘
                                                       │ Heat-up changed │
                                                       └─────────────────┘
```

## Consequences

- A project reads at a glance: what comes in, what is shared, what feeds what, and
  what is out of date and why, without opening each object.
- ADR 0009's "transfers are the only schematic edges" becomes "transfers are the only
  edges between studies": use wires are a second, visibly different kind (thin, grey,
  unlabelled, brought forward for the selected node), which shows sharing without
  the octopus's tangle of look-alike wires.
- Remembered positions live outside the project, so two users can arrange the same
  project differently and neither changes it; the layout code must place new nodes
  around pinned ones without moving them.
- Every object kind needs a node or a chip, its ports and its readable label; a new
  kind of object must say which, or it won't appear (a test lists every kind against
  the schematic, as `test_gui_names.py` checks the tree).
- Output ports come from the physics' catalog, so the catalog must list the fields each
  physics produces (most do via `post/quantities.py`; contact results, ADR 0056, too).
- The layout code is ours to keep: large projects (hundreds of objects) must still lay
  out in well under a second; the build is pure, so it is tested without a GUI.

## Alternatives considered

- **A free-form node editor as the source of truth** (Blender's model): flexible, but
  layout and meaning tangle again; ADR 0009 rejected it, and Puzzle's tree and
  commands already are the source of truth.
- **Workbench's cell grid** (each system a column of cells: geometry, model, setup,
  solution, results): familiar to Ansys users, but links per cell are the octopus's
  first cause.
- **Data links only** (ADR 0044's map as decided): small and clear, but it can't answer
  "who else uses this geometry or mesh", which the maintainer asked for.
- **Graphviz for layout**: good layouts, but another native dependency on five
  platforms, and its output is hard to keep stable as a project grows.
- **A web view (a JavaScript graph library in a Qt WebEngine)**: rich, but a browser
  engine in the GUI for one panel; Qt's graphics scene draws nodes and wires well.

## Review (2026-10-31)

The maintainer's answers to the draft's questions:

1. Results are their own column (runs, derived results, fatigue), as item 2 has it.
2. Use wires are all shown by default (`schematic.uses = all`); for the selected node
   only and none stay as settings.
3. Dragged positions are remembered per user (item 6).
4. Drawing wires makes transfers and parts (phase 2), as item 7 has it.
5. Both: a Window → Schematic dock and a Schematic tab beside Model, Geometry and Mesh.

## Built (2026-11-02)

All three phases.

- **The structure** (`puzzle.schematic`): `build(project, runs, states, root, folded,
  fields)` gives nodes (column, state and reason, ports, chips with their other users,
  badge, a component's parts and materials, "used by N"), wires (data or use, labelled
  and coloured by what flows: temperature, displacement, stress, pressure, velocity,
  other) and tree groups. `SHOWN` says for every kind of object whether it is a node, a
  chip, a wire, a frame or not shown (presets, labels, units and section cuts: nothing
  refers to them); a test checks every kind against it. A study's output ports come from
  the catalog's new `OUTPUTS` per physics (plus contact results where a contact isn't
  bonded, a harmonic step's amplitude and phase), so a study not yet run shows them.
  Results: each study's runs as one node `runs:<study>` (the latest run's state; a
  sweep's or shoot-out's badge); derived results take data wires from them. Files not in
  the project folder are marked missing. A folded group becomes one node, its wires
  rerouted to it.
- **Layout** (`schematic.layout`): columns split into sub-columns where nodes of one
  column feed each other (a study fed by another to its right, a derived result fed by
  another), barycentric ordering (four sweeps), then placed near their neighbours
  without overlap; pinned (dragged) nodes stay and the rest are pushed clear of them.
  250 nodes lay out in well under a second. `rows()` measures a node, so the GUI and the
  SVG put ports where the wires end.
- **The user's view** (`schematic.view_state`): positions, unfolded components and
  folded groups in `<user data dir>/schematic.json` by project folder; gone nodes are
  forgotten; Tidy layout forgets the positions.
- **GUI** (`gui/schematic_view.py`): the Schematic tab beside Model, Geometry and Mesh
  puts the schematic in the centre in place of the 3D view (its page holds how to read
  it and the selected node's state, what it is built from and what a change reaches);
  Window → Schematic (⌘9) docks another beside the view, tabbed with the messages. A
  click selects the object in the tree and Properties (a file selects its reader, the
  Results node its latest run); a double-click opens a geometry in the Geometry tab, a
  mesh recipe in the Mesh tab, a run's results in the view, a folded group again;
  right-click gives the tree's menu. Selection lights the node, its upstream and its
  own wires' other ends ("What a change reaches" lights the downstream instead), the
  rest dimmed; search by name or kind and a state filter dim the same way; the use
  wires setting (`schematic.uses`: all, for the selected node, none) is in the bar;
  zoom with the wheel, pan by dragging, Fit, a minimap past 12 nodes; Export… as SVG
  (`schematic.svg`) or PNG.
- **Phase 2**: dragging from a study's output port onto another study opens New
  transfer… (`menus.new_transfer`, also under Transfers in the tree and Model → New
  transfer…: the GUI had no transfer form before) with both ends and the field filled;
  dropping a geometry on a component asks the part's name and appends it
  (`menus.add_part`). Both are ordinary commands, undoable.
- **Phase 3**: imported fields are resource nodes with an output per value column, fed
  by their files, wired into the features, material assignments and shell sections they
  drive (an input port each, "p ×1.2"); `connections.schematic` switches them off.
- **Twins**: `puzzle schematic PROJECT [--svg FILE] [--json] [--no-uses]`; MCP
  `schematic(node)` with upstream and downstream; the outline's `Flow:` section; the skill
  `project-schematic`. Tests: `tests/test_schematic.py`, `tests/test_schematic_gui.py`.

Not done: wires are not routed around nodes (cubic curves only), and a chip is not
clickable on its own (its object is reached from the tree).

