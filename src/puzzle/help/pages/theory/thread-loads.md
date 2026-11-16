---
title: Thread loads
section: theory
order: 60
keys: theory:thread-loads
summary: How the load of a screw shares itself between the engaged threads, and how Puzzle gets that share, the nut's wedge and the thread's stiffness without meshing the helix.
adrs: 0076
---

A screw in a nut or tapped hole does not load its threads evenly. The first engaged
thread, at the mouth where the load comes in, carries far more than its share, and the
threads further in carry less. This note explains why, and how Puzzle reproduces the
share, the radial wedge that opens the nut and the joint's stiffness from two smooth
cylinders and springs along the thread's flanks.

## Why the first thread carries most

Picture a bolt pulled out of a nut. Along the engagement the bolt's core is in tension,
most at the mouth and falling to zero at the far end; the nut is in compression the
other way round. So the bolt stretches most where the nut squeezes most: at the mouth
the two move apart fastest. The teeth between them are springs, and the springs at the
mouth are bent furthest, so they carry most. How uneven the share is depends on how
stiff the teeth are against the core and the nut body: soft teeth even the load out,
stiff teeth push it toward the first thread. A standard nut carries about a third of the
load on its first thread (ADR 0076's context: about 33, 27, 20, 12, 8 %).

## Teeth as springs

Puzzle takes the teeth's give from Yamamoto's analysis, as Liu et al. (2024) restate it:
each tooth is a short cantilever of root width $a$, width $b$ at the pitch line where it
is loaded and depth $c$ from root to load. Under an axial load $w$ per unit length of
thread, its axial give is

$$\delta = \frac{K}{E}\,w$$

$$K = K_1 + K_2 + K_3 + K_4$$

from the tooth's bending ($K_1$), its shear ($K_2$), the root's inclination ($K_3$) and
the root's shear ($K_4$). For a flank angle α (30° for ISO and UN threads), with
$t = \tan\alpha$:

$$K_2 = 1.2(1 + \nu)\cot\alpha \ln\frac{a}{b}$$

$$K_3 = (1 - \nu^2)\frac{12 c}{\pi a^2}\left(c - \frac{b}{2}t\right)$$

and $K_1$, $K_4$ as Liu et al.'s Eqs. 5-12 give them (`threads.yamamoto`). For the 60°
forms the tooth is $a = 0.75P$, $b = 0.5P$, $c = 0.2165P$ on the screw and
$a = 0.875P$, $c = 0.3248P$ on the nut; other forms take the same proportions of their
fundamental triangle. These reproduce Liu et al.'s coefficients (screw 0.015, 0.84,
0.106, 1.246; nut 0.067, 1.164, 0.293, 1.142), which `tests/test_threads.py` checks.

The screw's and the nut's teeth act in series. A patch of the cylinder of area $A_c$,
one pitch high, then has an axial stiffness $A_c / (P\,\Sigma K/E)$. Puzzle's springs act
along the flank's normal, which is tilted by α, so along the normal the stiffness is

$$k = \frac{A_{flank}}{P \cos\alpha\,\left(K_s/E_s + K_n/E_n\right) f_T}$$

with $A_{flank} = A_c/\cos\alpha$ and $f_T$ is `TEETH_FACTOR` = 0.25. Yamamoto's fifth term, the nut's expansion and the
screw's contraction ($K_5$), is left out: the smooth cylinders' own continuum gives that
radial give, and counting it twice would soften the thread.

The factor 0.25 is a tuning, stated as such. It was fitted once against a meshed M16×2
thread in a steel boss, its bearing flanks glued (converged: 50.0 % on the first thread
at 0.25 mm and 50.1 % at 0.12 mm elements, 1.167 kN/µm). Yamamoto's teeth as published
gave 36.8 % and −5 % stiffness; × 0.25 gives 47.6 % and +2.6 %, threads 2 to 8 within
4.7 points. Cantilever teeth are softer than teeth that are part of a solid.

## The flank normal

The screw's outward normal on the flank facing +z, at radius $r$, with lead $L$ and
$s = +1$ for a right hand and −1 for a left, is

$$\mathbf{n} \propto \mathbf{e}_z - \frac{s L}{2\pi r}\mathbf{e}_\theta + \tan\alpha\,\mathbf{e}_r$$

The $\mathbf{e}_\theta$ term is the helix; the $\mathbf{e}_r$ term is the flank's tilt,
which turns an axial load into the radial push that opens the nut, $F\tan\alpha$ in
total (the wedge). In a 2D axisymmetric model the helix drops out. A square thread has
no wedge.

## How Puzzle does it

A threaded contact (Make it a thread… on a contact, or [contact_thread](help:mcp:contact_thread))
joins two smooth coaxial cylinders, the screw the inner one. Model both at one radius;
the screw's minor diameter $d_3$ measured best. Their radii may differ by at most 2 % of
the diameter ([thread_radii](help:problem:thread_radii)).

- `adapters/thread_pairs.py` fits the axis to the faces' normals, pairs each screw node
  with the nut node at the same angle and height, gives each pair its share of the face
  area (on six-node faces the corners 1/12 and mid-edge nodes 1/4, so no corner floats)
  and the stiffness above, along the flank normal.
- The bearing flank is the one facing the way the screw goes on past the engagement,
  as a tightened bolt is pulled; the thread's `bearing` says which of the form's angles
  is on it (a buttress's 7° or 45° flank). A bonded thread ties the bearing flank only;
  no-separation, frictionless and frictional threads act on both.
- In 3D a "turn" spring per pair, circumferential and as stiff as the flank, stands in
  for the friction that holds a thread from unscrewing; it carries no flank load.
- Compliance `teeth` is the default; `rigid` is a penalty of 50 E* A / h; a number is a
  stiffness per area (N/m³). Frictionless threads are gaps along the normal, with an
  optional clearance.
- After every solve `post/thread_fields.py` reads each pair's force from the
  displacements, $k\,\mathbf{n}\cdot(\mathbf{u}_{screw} - \mathbf{u}_{nut})$, and stores
  the flank force and pressure, each thread's axial load binned by pitch from the mouth,
  and the wedge ([thread_results](help:mcp:thread_results)).
- A beam bolt's tapped end can be a thread too: its core moves rigidly with the end's
  spider but for an axial slide per level, so the screw's own radial contraction ($K_5$
  of the screw, untuned) goes into the springs. Its first thread then came within 1.4
  points of a solid screw's per thread.

CalculiX, Code_Aster and FEniCSx (2D axisymmetric) write these springs and agree to
four digits. Elmer has no node-pair springs and refuses threads.

## Limits

- The share depends on `TEETH_FACTOR`, tuned on one M16×2 steel joint. Other sizes,
  pitches and materials follow Yamamoto's proportions but were not each measured.
- The threads are not meshed, so the stress at a thread root is not in the results. The
  basic stresses peak at the first engaged thread; a notch factor there is the way to
  read its fatigue (a modelled bolt's `thread_kf`, see
  [fatigue](help:theory/fatigue)).
- Read thread shares from the springs' forces, not from nodal stresses near the bore:
  two solvers' nodal stresses at a bore's corners differed by 30 % where their spring
  forces agreed.
- VDI 2230's whole-engagement resilience
  $\delta_G + \delta_M = 0.5d/(E_S A_{d3}) + l_M/(E_M A_N)$ (`threads.vdi_compliance`;
  see [bolted joints](help:theory/bolted-joints)) is a check of the total, not of the
  share; it was not yet compared with the springs' stiffness.
- Plastic yielding of the first thread, which evens the share out in a real joint, is
  not modelled: the springs are linear.

## Sources

- A. Yamamoto, The theory and computation of threads connection, Yokendo, Tokyo, 1980.
- L. Liu, X. Jin, J. Liu, M. Liu, P. Wang, H. Gao and H. Cui, Load-carrying capacity of
  hole threads in TOBs bolted curved T-stubs to circular steel tube connections,
  Engineering Structures 315 (2024) 118486, Eqs. 5-12 (Yamamoto's tooth compliance
  restated: δ1 bending, δ2 shear, δ3 root inclination, δ4 root shear, δ5 radial).
- VDI 2230 Part 1:2015, p. 40, Eqs. 20-27 (the engaged thread's and nut's resilience).
- ISO 68-1 and ISO 724, ASME B1.1 (the 60° basic profiles); ASME B1.5, ISO 2904, ANSI
  B1.9, DIN 513, ASME B1.20.1, ISO 7-1 (the other forms in `threads.FORMS`).
- The benchmark: `tests/integration/test_thread_benchmark.py` (axisymmetric M16×2, 2D
  and 3D, forms and wedges, a beam bolt's tapped end).
