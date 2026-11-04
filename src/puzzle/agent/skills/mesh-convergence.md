---
name: mesh-convergence
description: is the mesh fine enough: a study on finer and finer meshes judged by Roache's GCI (order, extrapolated value, 2 % target), and one run's Zienkiewicz–Zhu error estimate
adr: 0066
---
Read this before saying a result is accurate, when the user asks whether the mesh is
fine enough, or when the Advisor's Mesh convergence step says "needed".

A convergence check runs the study on `levels` meshes (default 3). Each mesh divides
every size of the study's mesh recipe by `ratio` (default 1.5) once more: the global
size, each control's size, transfinite counts multiplied. A recipe with no global size
is refined through Gmsh's size factor; Netgen needs a global size. Each mesh is the
study's own stored run, labelled `mesh: level 2: 4 mm` and cached by its run key, so a
check that stopped resumes and a later check reuses the meshes it shares.

From the three finest meshes that ran, each quantity gets:

- `order`: the observed order of convergence. Near 2 for first-order elements and 3 to
  4 for second-order ones on smooth problems. Outside 0.5 to 6 the state is "not in the
  asymptotic range": the meshes are too coarse to judge, so refine.
- `extrapolated`: Richardson's estimate of the value on an infinitely fine mesh.
- `gci`: the grid convergence index as a share of the finest value (0.003 = 0.3 %),
  with Roache's safety factor 1.25. At or below `target` the state is "converged".
- "oscillating": the values go up and down between meshes. This is not converged,
  whatever the GCI says.

## Steps

1. Pick the quantities. By default it is the largest value of the study's first
   result (its total deformation, its temperature). For anything else, make a Reduce
   operator or a result tool first (as for an optimisation's outputs), then put it in
   the study's `convergence.quantities` as `{source: <its id>}`. A peak stress at a
   sharp corner never converges: judge a stress away from singularities, or a
   linearised or integrated one.
2. Set the check on the study with `submit` (one Edit):
   `{"command": "edit", "object": "<study>", "ops": [{"op": "set", "path":
   "convergence", "value": {"levels": 3, "ratio": 1.5, "target": 0.02}}]}`.
   For several checks of one study, make a `mesh_study` object instead (`study`,
   `levels`, `ratio`, `target`, `quantities`). `mesh_convergence` takes either name.
3. Run `mesh_convergence(study, solve=true)`. Solving needs autonomy "act". Without
   `solve` it only reads the meshes that already ran. Each finer mesh costs about
   ratio³ (3.4×) the solve time of the last at ratio 1.5, so start from a coarse
   recipe.
4. Report each verdict's words, its GCI and the extrapolated value. If it is not
   converged, suggest a finer recipe, or a size control where the quantity is read,
   and check again.

## One run's error estimate

Without solving again, `error_estimate(study)` says how far the newest run is from
the exact answer. Displacement gives the Zienkiewicz–Zhu energy-norm error, from the
stress recovered at the nodes; temperature does the same with the heat flux. The
result is a relative error ("error 4.2 % (energy norm), largest at B/Fillet: refine
there"), how many elements are too big for `target`, and the hot spots. Each hot spot
gives the element, its error, its centre in m and the selections it touches. Use it:

- before a convergence check, to see where to put a size control;
- when a check is too costly, as a single-run estimate (it is an estimate, not a
  bound: effectivity near 1 on smooth fields).

`field` names any other nodal field (velocity, electric potential), giving the jumps
of its gradient between elements. That is a relative indicator for where to refine,
not an error. The estimate's numbers are in the energy norm: compare them with
the target, not with a stress in Pa. A sharp re-entrant corner or a point load is
singular: its error stays high however fine the mesh, so judge quantities away from
it. A derived result of method `error_estimate` (`source` the study, `error_target`)
draws the error, refinement ratio and jump per element in the results view.

## Adaptive refinement

`mesh_adapt(study, target, passes, solve=true)` meshes again where the error is, pass
after pass, until the energy-norm error is at `target`. With the study's
`adapt.criterion` "quantity", it stops instead when its quantities change by at most
the target between passes. Use it when a convergence check says "not converged" and
the error sits in a few places: it reaches the target in far fewer elements than
uniform refinement.

- Settings live in the study's `adapt`: target, criterion, quantities, passes,
  max_elements, keep_passes.
- Each pass is a stored run, `adapt: pass k`. The last pass is the result.
- Earlier passes are kept unless `keep_passes` is false.
- Netgen refines through its size file, more slowly than Gmsh.
- A size control from data is the mesh control `size_field`, with `run` (a run's
  estimate, for `target`) or `table` (an operator giving x, y, z and size in m).

## Tools

- `mesh_convergence(study, levels, ratio, target, solve)`: the levels (label,
  elements, nodes, values, why one did not run) and the verdicts. `converged` is true
  only when every quantity converged.
- `error_estimate(study, field, target, point)`: one run's estimate, solving nothing.
- `mesh_adapt(study, target, passes, solve)`: the passes (elements, nodes, error,
  quantities) and why it ended. CLI `puzzle adapt PROJECT STUDY`.
- CLI: `puzzle error-estimate PROJECT STUDY [--field F] [--target T] [--json]`.
- CLI: `puzzle convergence PROJECT STUDY [--levels N] [--ratio R] [--target T]
  [--points] [--json]`. Its exit code is 0 only when converged.
- GUI: a study's Check mesh convergence…, and the Convergence panel (Window →
  Convergence).

## Units

Values are SI (m, Pa, K). The target is a share, 0.02 = 2 %, not 2.

## Mistakes

- Judging with two meshes: the order is unknown, so the state stays "too few levels".
- A target of 2 for 2 %: it is refused (the target is below 1).
- Refining only one part of the recipe by hand between runs: the check scales every
  size itself, from the recipe as it is.
- Trusting first-order tets in bending: they lock, the values move a lot between
  meshes, and the check says "not converged". Use second-order elements.
