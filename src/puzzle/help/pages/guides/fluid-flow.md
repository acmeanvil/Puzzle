---
title: Flow and convection
section: guide
order: 97
keys: physics:fluid_flow
summary: Set up an incompressible flow on Code_Saturne or OpenFOAM, laminar or turbulent, with a temperature carried by the flow, run it, read it and compare the two solvers.
adrs: 0012, 0016
---

Puzzle solves steady incompressible flow, laminar or turbulent (RANS), on Code_Saturne
and OpenFOAM, and flow that changes in time on Code_Saturne. A temperature can ride on
the flow: forced convection from heated walls into a fluid whose properties don't
change with it. Both solvers run in their containers, so Docker or Podman must be
installed; Settings > Solvers says whether they can run here.

## How to set up a flow

1. Make the geometry of the fluid, not of its pipe: one body that the fluid fills.
   Name a face selection for each inlet, outlet, wall and symmetry plane. A 2D section
   works too (Model → Symmetry → Model type (3D, 2D planar, axisymmetric)…): Puzzle
   solves it one cell thick.
2. Give the body a fluid from the library: Materials → Add material… (Water, Air and
   six gases at 20 °C). A flow needs density and dynamic viscosity; a temperature also
   thermal conductivity and specific heat.
3. Model → Physics and loads → Add physics → Fluid flow.
4. Add the boundaries with the component's right-click → Add load or support → Fluid
   flow (or Model → Physics and loads → Add load or support once the physics is there):
   - **Velocity inlet**: the speed U into the fluid, normal to the face. For turbulent
     flow also its turbulence intensity (a fraction, 0.05 by default) and hydraulic
     diameter (by default 4A/P of the face).
   - **Pressure outlet**: the pressure p there (0 Pa by default). On Code_Saturne every
     outlet must have the same pressure.
   - **Wall (no slip)** and **Symmetry**. A boundary face in no selection is a wall.
5. Choose laminar or turbulent on the physics in Properties: Turbulence model is
   Laminar, k-epsilon or k-omega SST (with wall functions).
6. For a temperature, add Heat transfer to the same component: a Temperature on the
   inlet (needed) and on walls, a Heat flux into the fluid on a wall, or Convection
   (Code_Saturne only). Other walls are adiabatic.
7. Model → Studies → New study…: a stationary study on Code_Saturne or OpenFOAM, then
   run it with Model → Run → Run study.

## How to read the result

Flow results are per cell: velocity, pressure and temperature, and for turbulent flow
the turbulent kinetic energy and turbulent viscosity. The outlet's pressure is the
level: an outlet at 100 Pa reads 100 Pa there. Window → Solution shows the residuals
as the solve goes; a steady run whose residuals have all fallen three decades from
their peak counts as converged.

## How to compare the two solvers

Model → Run → Run shoot-out… (or the study's right-click) solves the study on its own
solver and on the other on one mesh and compares the velocity cell by cell: the laminar
channel benchmark agrees to 0.16 %. See [Studies and solvers](help:guides/studies-and-solvers).

## Options worth knowing

- Iterations: the step's `iterations` setting, 500 by default on Code_Saturne and 2000
  on OpenFOAM. OpenFOAM stops early once its residuals are below their targets; the
  step's `tolerance` sets the pressure's and velocity's.
- Pseudo time step (Code_Saturne): its default 0.1 s is slow for a temperature that
  conduction dominates; a larger one reaches steady state in fewer iterations.
- A flow in time (Code_Saturne): a transient step with a time step and end time;
  Output every k keeps every k-th time. The heat physics' initial temperature starts it.

## Mistakes to avoid

- Wall functions under-predict friction by about 15 % at a friction Reynolds number
  near 400; the turbulence models are benchmarked at a Reynolds number of 1e5.
- Heat with no inlet temperature is refused: give the inlet a Temperature.
- Mass flow inlet is in the list but neither solver writes it yet: use a velocity inlet
  with U = ṁ / (ρ A).
- OpenFOAM has no convection wall and no transient flow yet: use Code_Saturne.
- Two selections whose names differ only in case or punctuation are refused: the
  solvers' boundary names would meet.

## For agents and scripts

The agent's skill is `cfd`. It builds the physics with [submit](help:mcp:submit),
checks a study with [check_study](help:mcp:check_study), follows the residuals with
[run_progress](help:mcp:run_progress) and compares the solvers with
[solver_shootout](help:mcp:solver_shootout). From a terminal, `puzzle run PROJECT
--study NAME --shootout openfoam`.

## See also

- Theory: [Flow (CFD)](help:theory/cfd).
- Verification: [Flow](help:verification/cfd).
- [Choosing an analysis](help:guides/analyses)
- [Studies and solvers](help:guides/studies-and-solvers)
