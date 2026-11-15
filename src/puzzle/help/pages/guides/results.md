---
title: Results
section: guide
order: 110
keys: tree:run, kind:result, kind:probe, kind:cut, panel:results, panel:sections
summary: Look at a solved study, read numbers from it, combine load cases, estimate fatigue life and export results to other software.
adrs: 0025, 0026, 0032, 0038, 0043
---

Every solve is stored as a run under its study in the model tree. Select a run, or one
of the results listed under it, to draw it in the view. Everything here reads stored
runs, so nothing needs solving again.

## How to look at a result

The results bar above the view sets what is drawn:

- **Result**: every quantity the run can show, from its stored fields: total
  deformation and its components, von Mises, principal and shear stresses, strains,
  temperatures, fluxes. [Result quantities](help:reference/results) lists them.
- **In axes**: Global, or a coordinate system's axes. In a cylindrical system the normal
  stress along θ is the hoop stress.
- **Step**: the load step, time, mode or frequency.
- **Deformed**: draws the shape displaced (exaggerated to be seen).
- **▶ Animate** plays the deformation, or the steps when there are several; **Save
  GIF…** keeps it.

Values show in the units of Settings → Units. A result made before the model changed
says so, with Run again beside it.

### The legend

Results → View → Legend… (or the Results ribbon) sets the colour bar: Auto or a custom
range for each result, the colour map, smooth or banded, colours above and below a
custom range, its place and size. The look is kept under Settings > Results; ranges
last for the session.

## How to read a number

- **Probe**: the Results ribbon's Probe, then double-click the part; Shift adds more,
  Esc clears. Keep (under the view) adds a probe to the tree.
- **Result tools** stay in the project under Results and follow every new solve. From
  Results → Result tools, or right-click Results in the tree, Add result tool:
  - Reaction…: the force and moment a support takes;
  - Result on a selection…: maximum, minimum, average or integral over a selection;
  - Path…: a result along a straight line, plotted against distance;
  - Linearized stress…: membrane, bending and peak through a wall (ASME VIII-2 Annex
    5-A), checked against an allowable;
  - Result on a plane… and Section force…: a result over a plane's cut, or the force
    and moment carried across it.
- Results → View → Plots draws result tools against time, steps or distance.

Paths and planes can be construction geometry: Project → New construction geometry →
Path… or Plane…, picked on an edge or face. Every tool reading one follows when it moves.

## How to see inside: section cuts

1. Results → View → Section cut (or the Results ribbon).
2. Cut across X, Y or Z, on a picked face or a face selection, or on a construction
   plane, with an offset.
3. Window → Sections (Ctrl+4): Save current cut keeps it; click a saved cut to cut the
   shown result again. Rename with F2.

## Combinations and equations

A derived result is computed from stored runs whenever it is read, never stored. From
Results → Combinations and fatigue, or right-click a study or a run:

- **New combination…**: load cases added and scaled (linear), SRSS, an envelope (the
  worst of several, and which case gives it), or a range and mean (for fatigue).
- **New equation…**: a field from a formula, such as `250e6 / von_mises` for a safety
  factor. Names are the run's results (`von_mises`, `deformation_y`, `stress_xx`),
  parameters, and `abs`, `sqrt`, `min`, `max`, `where` and the like. Values are SI.

Derived results draw and read like a study's results.

## Fatigue

Results → Combinations and fatigue → New fatigue tool… gives fatigue life, damage at
the design life and a safety factor at every node, from a duty of load cycles:

- each block fully reversed, zero-based, or a range combination's range, with its count;
- the stress: signed von Mises, maximum principal, or a component;
- a mean-stress correction (Goodman, Gerber, ASME elliptic, Soderberg, Smith-Dolan);
- the life model: Shigley's S-N line with Marin factors, the material's S-N curve, or
  its strain-life model (the Material builder's Fatigue tab);
- a notch factor Kf for a stress raiser the mesh doesn't resolve.

It is high-cycle and proportional loading only. A safety factor of 15 means 15 or more,
and a life of 1e12 means infinite.

## Exports and ParaView

- Results → Export → Open in ParaView opens the result as shown: colours, range,
  deformation, cut and camera.
- Results → Export → Export… (or right-click a run) writes a run for other software:
  ParaView, VTKHDF, Exodus II, EnSight, CGNS, Tecplot, UNV (modes for test
  correlation), Nastran, Abaqus/CalculiX decks, point and wall clouds, glTF, and the
  deformed shape as STL or STEP. Each export writes a `.txt` beside it naming its
  source and units.

Right-click a run, Delete results…, to remove it and free its space.

## Mistakes to avoid

- A peak stress at a sharp corner or a point load grows with every refinement. Read it
  away from the singularity, or linearize through the wall.
- A reaction read off one node's value: sum it with a Reaction tool. Elmer, FEniCSx and
  the CFD solvers write no reaction forces.
- A reaction selection touching a loaded face: the shared nodes carry the load too.
- A path or linearization that leaves the material: points outside are left out, and a
  linearization is refused.
- A linear combination of nonlinear runs (contact, plasticity, large deflection) is not
  valid superposition, only an estimate. Solve the combined case as a step instead. An
  envelope or range is fine.
- A notch factor where the mesh already shows the notch counts it twice.
- A morphed STEP across contact or a plastic hinge is refused: use the tessellated STEP.

## For agents and scripts

The agent reads results with [result_summary](help:mcp:result_summary),
[result_stats](help:mcp:result_stats), [result_value_at](help:mcp:result_value_at),
[result_total](help:mcp:result_total) and [result_history](help:mcp:result_history),
reads result tools with [probes](help:mcp:probes), [probe_path](help:mcp:probe_path) and
[probe_history](help:mcp:probe_history), checks derived results with
[derived_result](help:mcp:derived_result) and [equation_names](help:mcp:equation_names),
pictures with [render](help:mcp:render), and writes files with
[export](help:mcp:export). In a terminal: `puzzle show` ([cli:show](help:cli:show)),
`puzzle export` ([cli:export](help:cli:export)) and `puzzle paraview`.

## See also

- [Studies and solvers](help:guides/studies-and-solvers)
- [Schematic and operators](help:guides/schematic-and-operators)
- [Optimisation and convergence](help:guides/optimisation-and-convergence)
- [Result quantities](help:reference/results)
