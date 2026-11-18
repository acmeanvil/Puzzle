---
name: cfd
description: incompressible flow on Code_Saturne and OpenFOAM: inlets, outlets, walls, laminar or RANS, temperature carried by the flow, iterations and convergence
adr: 0016
---
Read this before setting up or solving a flow: a channel, a duct, a pipe, cooling air
or water past a heated wall, or a shoot-out of the two CFD solvers.

Puzzle solves steady incompressible flow, laminar or RANS, on Code_Saturne and OpenFOAM,
and transient flow on Code_Saturne. A temperature can be carried by the flow (forced
convection: the temperature does not change the flow). Both run in their containers
(Docker or Podman); `solvers_list` says whether they can run here.

## Steps

1. The geometry is the fluid, not the pipe wall: one body for the fluid, with face
   selections for each inlet, outlet, wall and symmetry plane (skill `planar-models` for
   a 2D section; it is solved one cell thick).
2. The material: water or air from the library (`materials_library("water")`, then
   `{"command": "add_library_material", "key": "water"}`). Flow needs `density` and
   `dynamic_viscosity`; heat also `thermal_conductivity` and `specific_heat`
   (`material_check` says what is missing).
3. The component's physics, in one edit: `{"op": "append", "path": "physics", "value":
   {"type": "fluid_flow", "settings": {"turbulence_model": "laminar"}, "features":
   [...]}}`. Features (values in SI or with units):
   - `velocity_inlet` on an inlet face: `U` (m/s, the speed normal to the face, into the
     fluid). For turbulent flow also `intensity` (a fraction, default 0.05) and
     `hydraulic_diameter` (m, default 4A/P of the face).
   - `pressure_outlet`: `p` (Pa, default 0). Several outlets must share one pressure on
     Code_Saturne.
   - `wall` (no slip) and `symmetry`. Boundary faces in no selection are walls.
4. Turbulence: the physics setting `turbulence_model` is `laminar` (default),
   `k-epsilon` or `k-omega-SST`, with wall functions. They are benchmarked at Re of 1e5;
   at Re_tau near 400 wall functions under-predict friction by about 15 %.
5. Temperature (optional): a second physics `{"type": "heat_transfer", "features": [...]}`
   on the same component with `temperature` (`T`) on the inlet (required) and on walls,
   `heat_flux` (`q`, W/m² into the fluid) on walls, or `convection` (`h`, `T_inf`) on
   walls (Code_Saturne only). Other walls are adiabatic.
6. The study: `{"command": "create", "kind": "study", "data": {"name": "Flow",
   "component": "<component id>", "solver": "code_saturne", "steps": [{"type":
   "stationary", "physics": ["fluid_flow", "heat_transfer"], "settings": {"iterations":
   1000}}]}}`. `solver` is `code_saturne` or `openfoam`; `capabilities("openfoam")` lists
   what each takes.
7. `check_study`, then `submit(command={"command": "run_study", "study": "Flow"})`.
   `run_progress("Flow")` shows the residuals; `result_stats("Flow", "velocity")`,
   `result_value_at` with the study, the field and `x`, `y`, `z` in metres, and
   `result_stats("Flow", "pressure")` read the answer.
8. Both solvers on one mesh, compared: `solver_shootout("Flow", ["openfoam"])` (the
   study's own solver, then the others named). With no metrics on the study it compares
   the velocity cell by cell (relative L2 within 1 %); the laminar channel agrees to
   0.16 %. It is a run: each solve counts against the budget.

## Settings that matter

- `iterations` on the step: steady iterations (Code_Saturne default 500, OpenFOAM 2000).
  OpenFOAM stops earlier once each initial residual is below its target (p 1e-6, U 1e-7;
  the step's `tolerance` sets p and U). A run at its limit whose residuals all fell three
  decades from their peaks counts as converged on both solvers.
- `pseudo_time_step` (s, Code_Saturne): its default 0.1 s is slow for a temperature that
  conduction dominates; a larger step reaches steady state in fewer iterations.
- Transient (Code_Saturne only): a `transient` step with `time_step` and `end_time`,
  `output_every` k keeps every k-th time; the heat physics' `initial_temperature` is the
  starting temperature.
- `turbulent_prandtl` (OpenFOAM, fluid_flow setting, default 0.85).

## Results and units

Results are per cell (element values): `velocity` (m/s), `pressure` (Pa, OpenFOAM's
kinematic pressure is converted), `temperature` (K), and for RANS
`turbulent_kinetic_energy` and `turbulent_viscosity` (Pa·s). The outlet's `p` is the
pressure level: a 100 Pa outlet reads 100 Pa there.

## Mistakes

- Meshing the solid wall instead of the fluid: the fluid region is the body.
- A `heat_flux` sign: positive goes into the fluid (Puzzle flips it for Code_Saturne).
- Heat with no inlet temperature is refused (`check_inlet_temperatures`): give the inlet
  a `temperature`.
- `mass_flow_inlet` is in the catalog but neither solver writes it yet: use
  `velocity_inlet` with U = m_dot / (rho A).
- OpenFOAM has no `convection` wall and no transient step yet: use Code_Saturne.
- Selections whose names differ only in punctuation or case are refused on both
  solvers (their boundary names would meet).
- Imported or transferred fields are refused by both CFD adapters.
