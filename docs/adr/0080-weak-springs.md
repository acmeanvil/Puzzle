# 0080. Hold bodies that only contact holds with weak springs, added automatically and reported

- Status: Accepted; built on CalculiX and Code_Aster (2026-11-11)
- Date: 2026-11-11

## Context

A body held only through frictionless or frictional contact is free until that contact
closes and is pressed. Its first solve is singular. Examples are a cover resting on a
flange, a pin in a bore, and a bolt's head before its pretension acts. CalculiX then stops
with a zero pivot, or returns rigid-body drift. Code_Aster stops on its singularity check
(`NPREC`), or Newton never starts.

`contact_check.held_by_contact_only` already finds these bodies. It reports
`contact_only_hold` (held through sliding contact) and `free_body` (held through nothing),
logs them when a solve starts, and shows them in Problems and the Advisor's Contacts step.
The message tells the user to add a weak support, a bond or a bolt. The user then has to
work out where to put the support and how stiff to make it, by hand.

Neither solver has an automatic answer (checked 2026-11-11):

- **CalculiX**
  - Its linear pressure-overclosure law takes a small tension across open gaps, but only
    for node-to-surface contact. Puzzle writes surface-to-surface contact.
  - `*CONTACT DAMPING` acts in dynamic steps only.
  - It has no static stabilisation like Abaqus's `*STATIC, STABILIZE`.
- **Code_Aster**
  - It detects a singular matrix and stops; it does not prevent one.
  - `CONTACT_INIT` starts a contact closed, but only where the gap is already zero.
  - `PILOTAGE` and quasi-static `DYNA_NON_LINE` answer instability, not a free body.

Ansys's answer is weak springs. In program-controlled mode it adds springs to ground on
bodies that would otherwise be free, at a stiffness small against the model's, and lists
the force they carry.

Puzzle already writes springs to ground on both solvers. CalculiX uses `SPRING2` to
ground nodes held in each step (helper ids past the mesh's). Code_Aster uses `DIS_T`
point elements with `K_T_D_N` on one-node groups. Both serve `elastic_support` and
`compression_only_support`.

## Decision

1. **A setting on the study.** `Study.weak_springs` takes `auto` (the default), `always`
   or `never`. `Study.weak_spring_factor` sets the stiffness and defaults to 1e-6.
   - `auto` adds springs only to bodies that `held_by_contact_only` reports as
     `contact_only_hold`, and only in studies with static or transient solid-mechanics
     steps.
   - `always` also adds them to a component's `free_body` bodies.
   - Neither mode lifts the `unconstrained` refusal: a component held by nothing at all is
     a modelling mistake, and springs would hide it.
   - A modal-only study gets none, since free modes are wanted there.
2. **Where the springs go.** Each body gets three nodes, chosen in `puzzle/weak_springs.py`
   (pure, from the mesh):
   - the node nearest the body's centroid;
   - the nodes farthest out along the body's first two principal axes.

   These nodes are taken from outside its contact, held and loaded faces where the body
   has any, so that no spring sits on a slave node (Code_Aster refuses Dirichlet-like
   holds there) or under a load.

   Each node gets x, y and z springs to ground: nine springs in all, which hold all six
   rigid-body motions.
3. **How stiff.** Each spring's stiffness is the factor × E × V / L², where E is the
   body's Young's modulus, V its bounding box's volume and L that box's diagonal. That is
   E A / L for a bar and E L for a cube: the body's own stiffness scale, so a seated body
   passes about the factor's share of its load through them however slender it is
   (E × L alone put 4.5e-4 of the load through a 10 mm × 10 mm × 100 mm bar's springs). A 2D model writes the springs per its basis
   (thickness, 2πr) through `common.load_area`'s rules, so totals stay whole.
4. **Writing them.** The springs go to the adapters as a resolved list of
   `(node, direction, k)` built in `run_study` after meshing, next to `bolts.with_joints`.
   - CalculiX and Code_Aster write them with the elastic supports' code. The
     `Capabilities.weak_springs` flag says so.
   - Elmer, FEniCSx and PyNite have no sliding contact, so `auto` never adds springs
     there. `always` is refused on them until a benchmark passes.
   - The helper nodes and elements are dropped from results as elastic supports' are.
5. **Reporting.** After each solve, `post/weak_spring_fields.py` writes:
   - a global field `weak_springs`: force per body and its largest share;
   - a nodal `weak_spring_force` on the chosen nodes.

   When the springs carry more than `Study.weak_spring_limit` (default 0.1 %) of the
   body's applied load or reaction, the run reports the problem `weak_springs_carry`.
   That means the body never seated, and the result is not to be trusted. The problem
   names the body and the share, and appears in Problems, the Advisor's Results step and
   the run's summary.
6. **Run keys.** The setting is part of the solved model, so it is in the study's hash.
   It is dumped only when it differs from its defaults. A study whose `auto` adds no
   springs keeps its key, so existing runs stay up to date.
7. **What the user sees.**
   - `contact_only_hold` becomes a note, not a warning, when `auto` will add springs:
     "weak springs added to 'Cover' until its contact closes".
   - The springs are drawn as the elastic supports' glyphs, muted.
   - Properties show Weak springs (Automatic, Always, Never), Stiffness factor and Limit.
   - MCP `weak_springs` lists, per study, the bodies, nodes and stiffnesses before a
     solve, and the forces after it.
   - The `contact-tool` skill gains a section on reading `weak_springs_carry`.
8. **Benchmarks** (`tests/integration/test_weak_springs.py`, CalculiX and Code_Aster):
   - **Cover on a flange:** a block pressed onto a held block through frictional contact.
     Without springs the solve fails. With springs, the contact force matches the applied
     load within 0.1 %, and the springs carry less than 1e-4 of it.
   - **Sliding:** a tangential load under the friction limit. The springs carry less than
     the limit, and the friction holds the rest.
   - **Lift-off:** a pull that opens the contact. `weak_springs_carry` is reported.
   - **Factor:** the result changes by less than 1e-5 relative between factors 1e-6
     and 1e-7.

## Built (2026-11-11)

- **The module.** `puzzle/weak_springs.py`: `applied` runs in `run_study` after the
  symmetry planes. It finds the bodies through `contact_check.held_by_contact_only`, and
  `nodes_for` picks three nodes off the body's contact, loaded and held faces.
- **Stiffness.** Each spring is the factor × E × V / L² (see decision 3, changed while
  building).
- **Writing.** The springs go in as an `elastic_support` on a node group
  `<part>/__weak_springs`, which `common.support_nodes` reads per node (1 m² each, along
  x). So CalculiX's SPRINGA and Code_Aster's `DIS_T` write them unchanged.
- **Reporting.** `finish` adds `weak_spring_force` to each step. A share of the load path
  (springs against the supports' own reactions) above `Study.weak_spring_limit` logs the
  warning `weak_springs_carry` on the run. `contact_only_hold`'s message says the solve
  adds them.
- **Settings.** `Study.weak_springs`, `weak_spring_factor` and `weak_spring_limit` are
  left out of hashes at their defaults.
- **Agent.** MCP `weak_springs` gives a study's mode, bodies and the latest run's spring
  force.
- **Benchmarks.**
  - `tests/integration/test_weak_springs.py`: the split bar's right half pressed onto the
    held left half through frictional contact (CalculiX, Code_Aster) and frictionless
    contact (CalculiX). The reaction is 99.9995 N of 100 and the springs carry 4.4e-4 N
    (4.4e-6).
  - `tests/test_weak_springs.py`: the nodes chosen, and the warning when the springs carry
    the load.
- **Not done.**
  - A lift-off solved end to end: frictional contact searching while the body floats on
    weak springs diverged on CalculiX, so the warning is checked on results alone.
  - Drawing the springs in the view.

## Consequences

- Models built from contact alone run the first time, which matters most to users who
  rely on the agent: the agent no longer has to place supports by hand.
- A spring is a small error in every such result. The report and the limit make it
  visible, so a body that never seats fails loudly instead of resting on its springs.
- Run keys gain a field. Defaults are kept out of dumps, so no existing run goes out of
  date.
- The springs stay in every step. Releasing them once the contact closes needs element
  removal per step (CalculiX `*MODEL CHANGE`, Code_Aster a windowed multiplier). That is
  left for later, once a benchmark shows the residual matters.
- Inertia relief (balancing a free body's loads by its own inertia) is a different tool,
  for whole free models such as aircraft or satellites. It stays a separate decision.

## Alternatives considered

- **Springs on every model, as Ansys's Program Controlled can:** this hides an
  unconstrained model, which Puzzle deliberately refuses (`unconstrained`).
- **Weak elastic support spread over a contact face:** it distorts the contact pressure
  exactly where results are read, and needs a face the user might not have.
- **Contact stabilisation (damping across open gaps):** CalculiX has it only in dynamic
  steps, and Code_Aster has none.
- **Quasi-static dynamics:** this adds inertia and damping to every step and costs many
  increments for one free body.
- **Starting contacts closed (`CONTACT_INIT`, CalculiX's adjust):** this helps only where
  the gap is already zero or within the adjust distance, and does nothing for a body that
  slides off.
- **Leaving it to the user:** this is today's state. The checks already know which bodies
  need holding, so not acting on it leaves work the program can do.
