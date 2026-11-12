---
name: weak-springs
description: bodies held only by frictionless or frictional contact: the weak springs a solve adds, their warning, and the study's settings
adr: 0080
---
Read this when a body rests only on sliding contact (Problems says contact_only_hold), a
run warns weak_springs_carry, or the user asks how a free body is held while its contact
closes.

## Steps

1. `weak_springs(study)`: the mode, the bodies that get springs, and the force they
   carried in the latest run.
2. After a run with weak_springs_carry, the body never seated. Check the contact's gap
   and search distance (`contact_check`), or add a real support, a bond or a bolt.
3. Change the mode with an edit of the study: `weak_springs` "auto" (default), "always"
   or "never"; `weak_spring_factor` (1e-6); `weak_spring_limit` (0.001).

## What they are

A body held only by frictionless or frictional contact (`contact_only_hold`) gets weak
springs for the solve: three nodes, x, y and z, each 1e-6 × E × V / L² of the body. They
hold it until the contact closes. `weak_springs(study)` lists the bodies and, after a run,
the force the springs carried. A run warning `weak_springs_carry` means they carried more
than the study's `weak_spring_limit` (0.1 %) of the load: the body never seated, so its
results are not to be trusted. Check the gap and the contact's search distance, or add a
real support.

Set the study's `weak_springs` to "never" to solve without them (the solve may then fail
or drift), or to "always" to hold bodies that nothing touches too. A model held by
nothing at all is still refused (`unconstrained`).

## Units

The factor and the limit are plain numbers; the springs' forces are newtons.

## Mistakes to avoid

- Turning them off to make a warning go away: the body then has nothing holding it.
- Reading a body's results after weak_springs_carry: they rest on the springs.
