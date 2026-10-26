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
| Bonded | 53,367 | 36 s | 0.080 mm | 50 MPa |
| Bolted | 25,541 | 38 min | 0.232 mm | 177 MPa (at the bolts) |

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

- **The bolted study is slow** (38 min on 8 cores). CalculiX's surface-to-surface
  contact makes about 108,000 contact elements for this seat, and friction makes the
  system unsymmetric, which its SPOOLES solver factors slowly (this build has no
  PARDISO). Its node-to-surface contact needs about 2,200 but doesn't converge with
  Puzzle's penalty stiffness yet; tuning it is a follow-up.
