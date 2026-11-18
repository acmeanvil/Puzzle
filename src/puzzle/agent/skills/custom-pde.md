---
name: custom-pde
description: a user's own equation in weak form, solved by FEniCSx: the unknown, the UFL residual, coefficients, fixed values and fluxes, steady or transient, the trust gate
adr: 0012
---
Read this when the user wants an equation Puzzle has no physics for (a reaction-
diffusion, a nonlinear conduction, a hand-written elasticity to check one), or asks
about `custom_pde`, a weak form or UFL.

FEniCSx solves a residual F(u; v) = 0 written in UFL by Newton's method with the exact
Jacobian. One unknown, a Lagrange field, scalar or vector, degree 1 or 2. Steady or
transient (backward Euler written by hand with `<u>_n` and `dt`). No other solver takes
it.

## Steps

1. The project must be trusted: a weak form is evaluated as Python in the FEniCSx
   environment (no builtins, only UFL's names). The user trusts it with Project → Trust
   this project… or `puzzle trust PROJECT`. Below "act" an agent's change that writes a
   weak form is a proposal, whatever the tool.
2. The physics, on the component, in one edit: `{"op": "append", "path": "physics",
   "value": {"type": "custom_pde", "equation": {"unknowns": [{"name": "u", "degree":
   2}], "weak_form": "(1 + u**2) * inner(grad(u), grad(v_u)) * dx", "coefficients":
   {"k": {"value": 50, "unit": "W/(m*K)"}}}, "features": [...], "settings":
   {"initial_value": 0}}}`.
   - `unknowns`: one, with `name` (a Python word), `degree` 1 or 2, `shape` "scalar" or
     "vector" (3 components).
   - `weak_form`: the residual. Names it may use: the unknown `u`, its test function
     `v_u`, its last value `u_n` and the time step `dt` (transient), the coefficients
     (converted to SI numbers), `x` (the coordinates), `dx` and `ds`, which take a
     selection name (ds('Bar/Tip'), dx('Bar/Body')), `prop` with a property key
     (prop("youngs_modulus"): a material property, element by element), and UFL's
     `grad`, `div`, `curl`, `inner`, `dot`, `outer`, `sym`, `tr`, `det`, `Identity`,
     `as_vector`, `sqrt`, `exp`, `ln`, `sin`, `cos`, `conditional`, `lt`, `gt`,
     `max_value`, `min_value`, `pi`, ...
   - `settings.initial_value`: the starting value (a plain number, in SI), for Newton
     and for a transient's first step.
3. Features (catalog `custom_pde`):
   - `dirichlet` (Fixed value) on a selection: for a scalar unknown the value keyed by
     the unknown's name (`{"u": {"value": 1, "unit": "dimensionless"}}`, or `value`); for
     a vector one `x`, `y`, `z`, each held on its own.
   - `neumann` (Flux into the body): g on a face, added as −g v ds; a vector unknown
     takes `x`, `y`, `z` (a traction).
   A natural boundary (nothing written) is zero flux.
4. The study: `"solver": "fenicsx"`, a `stationary` step, or a `transient` one with
   `time_step` and `end_time`. `check_study`, then run it as any study.
5. The result is a field named after the unknown (`result_stats(study, "u")`).

## Examples that are benchmarked (tests/integration/test_custom_pde.py)

- Nonlinear conduction −div((1 + u²) grad u) = 0, u = 1 and 2 at the ends: the
  Kirchhoff solution to 1e-5.
- Linear elasticity by hand, the weak form inner(lam*tr(sym(grad(u)))*Identity(3) +
  2*mu*sym(grad(u)), sym(grad(v_u)))*dx minus a traction: FEniCSx's own elasticity to
  1e-9.
- Transient diffusion `rho_cp*(T - T_n)/dt*v_T*dx + k*inner(grad(T), grad(v_T))*dx`:
  the built-in transient heat solver to 1e-9 of the rise.

## Mistakes

- A name the weak form uses that isn't one of the above fails the solve with
  "weak form: NameError ..." in the log (`run_log`); a weak form can't reach Python's
  builtins or imports.
- The test function is `v_<name>`: `v_u` for `u`, `v_T` for `T`.
- Coefficients and `initial_value` are SI numbers by the time UFL sees them; write the
  weak form in SI.
- Modal and harmonic steps are refused (no eigen or complex solve for a custom PDE).
- An untrusted project refuses the run: ask the user to trust it; never trust it for
  them.
