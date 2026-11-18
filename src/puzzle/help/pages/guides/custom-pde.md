---
title: Your own equation (custom PDE)
section: guide
order: 98
keys: physics:custom_pde
summary: Solve an equation Puzzle has no physics for, written in weak form, on FEniCSx: the unknown, the residual, coefficients, fixed values and fluxes, steady or in time.
adrs: 0012, 0062
---

When no physics fits (a reaction-diffusion, a conduction whose conductivity depends on
the answer, a check of a built-in solver by hand), write the equation yourself. FEniCSx
solves the residual F(u; v) = 0 you give, written in UFL, by Newton's method with the
exact Jacobian, so a nonlinear equation needs nothing more. One unknown, a continuous
(Lagrange) field, a number or a vector at each point, linear or quadratic elements;
steady, or in time with backward Euler written in the residual.

## How to set one up

1. Trust the project: Project → Trust this project…. A weak form is evaluated in the
   FEniCSx environment, so an untrusted project, one you were sent say, won't run it.
2. Model → Physics and loads → Add physics → Custom PDE asks, in one form, the unknown's
   name (`u`), whether it is a number or a vector, its element degree and the weak form,
   starting from a Poisson equation: `inner(grad(u), grad(v_u)) * dx`.
3. Add the coefficients the weak form names (a conductivity `k`, a rate `r`) in
   Properties under the physics' equation, each with its unit: they reach the solver as
   SI numbers.
4. Add the conditions with Model → Physics and loads → Add load or support, under
   Custom PDE: **Fixed value** holds the unknown on a selection (for a vector, x, y and z each
   on its own), **Flux into the body** adds a flux g on a face. Where you give nothing the
   flux is zero.
5. Make a study on FEniCSx, stationary or transient (a time step and an end time), and
   run it. The result is a field named after the unknown.

## What the weak form may use

- the unknown (`u`) and its test function (`v_u`, for an unknown called `T` it is `v_T`);
- in time: its value at the last step (`u_n`) and the time step `dt`, so
  `(u - u_n) / dt * v_u * dx` is backward Euler;
- the coefficients by name, `x` (the position, `x[0]` its x), and a material property by
  its key, element by element: `prop("thermal_conductivity")`;
- `dx` and `ds`, each taking a selection's name: `ds("Bar/Tip")` integrates over that
  face only;
- UFL's operators: grad, div, curl, inner, dot, outer, sym, tr, det, Identity,
  as_vector, sqrt, exp, ln, sin, cos, tanh, conditional, lt, gt, max_value, min_value,
  pi and the rest of its maths.

The physics' Initial value setting (a plain number in SI) is where Newton starts and the
first step's `u_n`.

## Examples

- Nonlinear conduction, `(1 + u**2) * inner(grad(u), grad(v_u)) * dx`, with u fixed at
  1 and 2 at the two ends: matches the Kirchhoff transformation's exact answer to 1e-5.
- Diffusion in time, `rho_cp * (T - T_n) / dt * v_T * dx + k * inner(grad(T),
  grad(v_T)) * dx`: matches Puzzle's own transient heat solver to 1e-9 of the rise.
- Elasticity written by hand matches FEniCSx's built-in elasticity to 1e-9.

## Mistakes to avoid

- A name the weak form uses that isn't one of the above stops the solve: the log says
  "weak form: NameError" and the name. Builtins and imports are not reachable.
- The test function is `v_` and the unknown's name.
- Write the residual in SI: the coefficients and the initial value are SI numbers when
  UFL sees them.
- Modal and frequency-domain steps are refused for a custom PDE.

## For agents and scripts

The agent's skill is `custom-pde`; below "act", an agent's change that writes a weak
form becomes a proposal for you to accept. It adds the physics with
[submit](help:mcp:submit) and reads the log of a failed solve with
[run_log](help:mcp:run_log). `puzzle trust PROJECT` trusts a project from a terminal.

## See also

- [Choosing an analysis](help:guides/analyses)
- [Loads and supports](help:guides/loads-and-supports)
- [Studies and solvers](help:guides/studies-and-solvers)
