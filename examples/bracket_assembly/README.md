# Bracket assembly

A steel angle bracket bolted to a base plate: an assembly for exploring contacts,
fasteners, parts placed in one component, and the assembly side of the GUI.

```sh
python examples/bracket_assembly/build.py           # rebuild the project from scratch
puzzle gui examples/bracket_assembly
puzzle run examples/bracket_assembly --study Bonded
puzzle run examples/bracket_assembly --study Bolted
```

## The model

- **Base:** a 120 × 80 × 10 mm plate with two 8 mm holes, fixed on its bottom face.
- **Bracket:** an angle made of an 8 mm flange (120 × 80 mm) and a 60 mm upright at its
  back edge. It sits on the base (placed 10 mm up), its holes over the base's.
- **Load:** 1 kN on the top of the upright, pushing it forward (−y). The upright bends,
  and the flange's back edge tries to lift off the base.
- Both parts are geometry histories (ADR 0014): change the plate's size or the holes'
  places in the Geometry tab and the studies follow.

## Two components, the same parts

| Study | Component | Joint | Mesh |
| --- | --- | --- | --- |
| Bonded | Bonded bracket | the flange glued to the base: a conformal mesh, shared nodes | tets, 4 mm, quadratic |
| Bolted | Bolted bracket | frictional contact (μ = 0.2) on the seat, two 8 mm beam fasteners with 5 kN pretension each | tets, 8 mm, quadratic |

The fasteners have distributing ends (RBE3). The base's hole walls meet its fixed bottom
face, and a rigid end can't also be held there; Puzzle says so before writing the
input.

## Measured (CalculiX 2.23, 8 cores, Intel Mac)

| Study | Nodes | Time | Largest displacement | Largest von Mises |
| --- | --- | --- | --- | --- |
| Bonded | 53,367 | 15 s solve (22 s in all) | 0.080 mm | 50 MPa |
| Bolted | 20,724 | 15.6 min solve | 0.232 mm | 170 MPa (at the bolts) |

Every run prints its time per stage. For the bolted study: mesh 1.9 s, write 0.5 s,
solve 935 s, read 0.4 s, store 0.1 s. The solve is all of it.

The bolted joint is softer than the glued one: the flange's back edge lifts where the
glue held it, and the bolts carry the moment.

## What to look at

- **Contacts:** the component's Find contacts… finds the seat between the flange and
  the base; the contact's type, friction and meshing are in Properties.
- **Fasteners:** each bolt under the bolted component, its ends, diameter and
  pretension; the bolts' axial and shear forces are in the results (`fastener`).
- **Result tools:** a section force across the upright (construction geometry, ADR
  0046) gives the shear and bending moment there; the deformation, exaggerated, shows
  the flange's back edge lifting.

## Known limits

- **The bolted study is slow** (15.6 min on 8 cores), and contact is why: the same
  deck with the seat tied instead solves in under a minute. CalculiX's
  surface-to-surface contact makes about 108,000 contact elements for this seat, and
  friction makes the system unsymmetric, which its SPOOLES solver factors slowly (this
  build has no PARDISO). Each contact iteration costs about 20 s.
- **What was tried** (the same bolted deck):

  | Change | Time | Answer vs. the stiffest contact |
  | --- | --- | --- |
  | Penalty 50 E/h, load ramped from 10 % (the old default) | 37.6 min | reference |
  | Penalty 50 E/h, full load at once | 27 min | the same |
  | Penalty 10 E/h, full load (the "balanced" default now) | 13.6 min | within 0.05 % |
  | Penalty 1 E/h, full load ("fast") | 5.4 min | 0.7 % |
  | Node-to-surface contact | 9 min | 51 % off: wrong |

  Beam bolts in place of solid ones take about 5,000 nodes out of the mesh but barely
  change the time, since the contact iterations dominate.
