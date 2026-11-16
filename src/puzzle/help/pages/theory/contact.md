---
title: Contact
section: theory
order: 130
keys: theory:contact
summary: How Puzzle joins parts that touch, from shared nodes to sliding contact with friction, what it checks before a run, and why contact makes a solve nonlinear.
adrs: 0039, 0056, 0080
---

Where two parts touch, the model must say how they carry load to each other: glued,
pressed but free to slide, or held by friction. This note explains each contact type,
how CalculiX and Code_Aster are told about it, what Puzzle measures before a run, and
why a body held only by sliding contact needs help to start.

## The contact types

Every contact has a source face (the slave side) and a target face (the master side).
The source's nodes are kept from passing through the target's faces.

- **Bonded:** the faces are glued; nothing opens or slides.
- **No separation:** the faces stay together along the normal but slide freely.
- **Frictionless:** the faces may open, and press without friction when closed.
- **Frictional:** as frictionless, with Coulomb friction of coefficient $\mu$.
- **Rough:** friction with no slip while closed (Ansys's "rough"); it still opens.

Coulomb's law says a closed point sticks while its shear stress is below $\mu$ times
its contact pressure, and slides once it reaches it:

$$|\tau| \leq \mu p$$

## Bonded: shared nodes or a tie

A bonded contact is meshed conformally by default: the parts are fragmented so they
share nodes on the interface, and no constraint is needed at all. When that can't be
done, the contact is meshed apart (`meshing = nonconformal`) and the solver ties the
faces: CalculiX `*TIE` (the source nodes follow the target faces; it works for heat
too) and Code_Aster `LIAISON_MAIL` (the source face tied into the target part's
volume; structural only). Elmer takes conformal bonds only.

## Sliding contact on CalculiX: a penalty

CalculiX writes frictionless, frictional, no-separation and rough contact as a
surface-to-surface `*CONTACT PAIR` with a linear penalty. Where the faces overlap by
$g$, a pressure $p = K g$ pushes them apart, with

$$K = c \frac{E}{h}$$

where $E$ is the softer of the two materials' Young's moduli, $h$ the source face's
mean element size ($\sqrt{A/n}$ over its $n$ faces) and $c$ the contact's
**stiffness** setting: accurate 50, balanced 10 (the default), fast 1. A stiffer
penalty leaves less overlap but takes more iterations. The code's comment records the
measurements behind the choice: on a stack loaded through its interface, balanced was
within 0.8 % and fast 8 %; on the bolted bracket, balanced was within 0.05 %.
`extensions.calculix.penalty_factor` overrides $c$ per contact.

- Frictional contact adds `*FRICTION` with $\mu$ and a stick slope equal to $K$.
- No separation uses `PRESSURE-OVERCLOSURE=TIED`, so the penalty also acts in
  tension. TIED needs a stick slope, so Puzzle writes $\mu = 10^{-6}$ and a slope of
  $10^{-6} K$: too small to hold anything, so the faces slide.
- Rough is the linear pair with $\mu = 1000$: no real interface reaches that, so a
  closed point sticks.

## Sliding contact on Code_Aster: continuous contact

Code_Aster writes frictionless, frictional and rough contact as `DEFI_CONTACT` with
`FORMULATION='CONTINUE'` (an augmented Lagrangian method), solved by `STAT_NON_LINE`.
Unlike a penalty, it solves for the contact pressure as an unknown of its own, so the
faces don't overlap. Friction is `FROTTEMENT='COULOMB'`; rough is $\mu = 10$ there,
since 1000 stopped Newton converging once the interface opened. The contact faces are
turned outward with `ORIE_PEAU`, and Newton updates its tangent every iteration
(`REAC_ITER=1`, up to 200 iterations), as Code_Aster's 3D validation case ssnv128
does. No separation is `LIAISON_MAIL` with `DDL='DNOR'`, which is linear.

Code_Aster's friction is reliable here only on first-order meshes with no hold on the
source nodes, so the adapter refuses quadratic meshes and such clashes before writing.

## Why contact makes a solve nonlinear

Which points are closed, and which of those stick, is not known in advance: it depends
on the answer. The stiffness changes as points close, open, stick or slip, so the solver
iterates (Newton with an active set) until the contact status stops changing, and it
may need to cut the load into increments. A bonded contact is linear; every other type
is not. On CalculiX and Code_Aster a contact study starts at the full load and cuts
back only when an increment fails (plasticity, lift-off and large deflection are ramped
in ten instead, since their answer follows the loading path).

When a Code_Aster contact study is loaded only by imposed displacements, the forces
through the model can fall to nothing as the interface opens, and a relative
convergence test never settles. Puzzle then uses an absolute residual of
$10^{-6} E L u$ ($L$ the model's size, $u$ the largest imposed displacement).

## The initial state

Before a run, `contact_check.py` looks at every node of each contact's source face.
It finds the nearest point on the target face (exactly, through a KD-tree over the
target's triangles; on curved six-node faces it refines that point on the curved face
itself, since a flat triangle is off by the chord's sag). The gap is measured along
the target's outward normal $\mathbf{n}$ at that point:

$$g = (\mathbf{p} - \mathbf{q}) \cdot \mathbf{n}$$

positive for a gap, negative for a penetration. Each node is then

- **closed:** penetrating, or a gap within 2 % of the target's element size there;
- **near:** a gap within a tenth of the search distance;
- **open:** a gap within the search distance;
- **far:** farther than the search distance;
- **overhanging:** no target face under it (the nearest point lies off to the side by
  more than a tenth of the element size), where CalculiX can invert elements.

The search distance (Ansys's "pinball") is the contact's own, or 1 % of the model's
bounding-box diagonal. The checks that follow are warnings, never refusals:
[a far contact](help:problem:contact_far), [an overhang](help:problem:contact_overhang),
[source nodes also held](help:problem:contact_slave_held), and on Code_Aster
[a curved face on a face of another shape](help:problem:contact_nonconforming) (as in
Hertz contact, where its pairing fails, so it refuses them) and
[faces with the same outline](help:problem:contact_shared_outline) under friction.

## Adjust and offset

A contact's **interface** can move the source nodes before the mesh is written, for
every solver alike. **Adjust** puts each source node within the search distance onto
its nearest target point, closing a small gap or penetration. **Offset** puts it a
given distance from there along the target's normal: positive for a clearance,
negative for an interference such as a press fit. Puzzle moves nodes rather than
writing CalculiX's `*CLEARANCE`: on a press fit, the clearance gave contact pressures
three times Lamé's while the moved geometry gave Lamé's to 0.2 %.

## Bodies held only by sliding contact

A body whose only path to ground runs through frictionless or frictional contact is
free until the contact closes and is pressed, so the first stiffness matrix is
singular. `contact_check.held_by_contact_only` finds such bodies
([contact_only_hold](help:problem:contact_only_hold)), and `weak_springs.py` (ADR 0080)
gives each one springs to ground at three nodes: the one nearest its centre and the
two farthest out along its first two principal axes, off its contact, loaded and held
faces. Each spring, in x, y and z, has the stiffness

$$k = f E \frac{V}{L^2}$$

with $V$ and $L$ the volume and diagonal of the body's bounding box ($E A / L$ for a
bar, $E L$ for a cube) and $f$ the study's `weak_spring_factor`, $10^{-6}$ by default.
After the run Puzzle compares the springs' force with the supports' reactions; above
`weak_spring_limit` (0.1 % by default) the run warns that the body never seated
([weak_springs_carry](help:problem:weak_springs_carry)). The study's **weak springs**
is auto (these bodies), always (bodies held by nothing too) or never. They are left out
of modal and buckling studies, whose free modes are wanted.

## How Puzzle does it

- Contacts are set in the component, as the [Contact guide](help:guides/contact)
  shows; Find contacts proposes them.
- The Contact tool (Window → Contacts) shows what each solver is given, the initial
  state drawn on the faces, and the results: `contact_pressure`, `contact_shear`,
  `contact_gap`, `contact_sliding` and `contact_status` on the source nodes
  (`post/contact.py`). A frictional node counts as sticking while its shear is below
  99 % of $\mu p$.
- The agent reaches it through [contact_check](help:mcp:contact_check) and
  [contact_results](help:mcp:contact_results), the command line through
  [puzzle contacts](help:cli:contacts).

## Limits

- The penalty lets the faces overlap by $p/K$; a softer stiffness setting makes a
  stack loaded through its interface give way more.
- Results on the source side are only as fine as its mesh. Make the smaller, finer face
  the source.
- Contact pressure is checked against Hertz (peak within 5 %, half-width to an element)
  and Lamé's press fit (mean within 3 %) in the tests, not on every geometry.
- A body seated only by weak springs whose springs carry much of the load has an answer
  that is not to be trusted.

## Sources

- P. Wriggers, *Computational Contact Mechanics*, 2nd ed., Springer, 2006: penalty and
  augmented Lagrangian methods, Coulomb friction.
- K. L. Johnson, *Contact Mechanics*, Cambridge University Press, 1985: Hertz line
  contact, the benchmark in `tests/integration/test_hertz.py`.
- C. Ericson, *Real-Time Collision Detection*, Morgan Kaufmann, 2005, section 5.1.5:
  the closest point on a triangle, used by the initial-state check.
- G. Dhondt, *CalculiX CrunchiX User's Manual*: `*CONTACT PAIR`, `*SURFACE BEHAVIOR`,
  `*FRICTION`, `*TIE`.
- EDF, Code_Aster documentation: `DEFI_CONTACT`, `LIAISON_MAIL`, and validation case
  ssnv128.
