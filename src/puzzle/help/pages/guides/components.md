---
title: Components
section: guide
order: 50
keys: kind:component, section:components, tree:part, tree:physics, kind:axes, section:coordinate systems
summary: Put parts, materials, a mesh and physics together as a component; model it in 2D or by symmetry; give it local coordinate systems and element choices.
adrs: 0048, 0049, 0081, 0082
---

A component is what a study solves: one part or an assembly, with the material of each
body, a mesh recipe, and one or more physics holding its loads and supports. Contacts,
fasteners and beam or shell sections belong to it too.

## How to make a component

1. You need a geometry and a mesh recipe first ([Geometry](help:guides/geometry),
   [Meshing](help:guides/meshing)).
2. Model → Components → New component… (or right-click the Components heading). One form
   asks for the geometry, the mesh recipe and a name.
3. Give its bodies a material: Materials → Assign material…
   ([Materials](help:guides/materials)).
4. Add a physics: right-click the component, Add physics, or Model → Physics and loads →
   Add physics. Solid mechanics, heat transfer, electrostatics, magnetic fields, fluid
   flow and others are offered.
5. Add loads and supports to the physics
   ([Loads and supports](help:guides/loads-and-supports)).

Model → Components → Duplicate component… copies one, with its studies if you choose.

## How to build an assembly

1. Right-click the component's Geometry folder, Add part, and choose another geometry.
2. Select the part and place it in Properties: a translation in metres and rotation
   angles about X, Y and Z in degrees.
3. Join the parts with contacts ([Contact](help:guides/contact)) or fasteners
   ([Bolts and welds](help:guides/bolts-and-welds)).

A part's selections are named `<part>/<selection>`, such as `Plate/Top`. An imported
STEP assembly often arrives as one part with many bodies: Model → Components → One part
per body… splits it, and references follow.

## How to make a 2D model

1. Draw the part as faces at z = 0: the Geometry tab's 2D tools, or Section of a part
   to cut a 3D part.
2. Model → Symmetry → Model type (3D, 2D planar, axisymmetric)…: 3D, 2D planar: plane
   stress, 2D planar: plane strain, or 2D axisymmetric (Y the axis, X the radius).
3. Plane stress needs each part's thickness: Model → Beams and shells → Add shell
   thickness….
4. In 2D a region takes materials and body loads, an edge takes supports and pressure.

An axisymmetric model needs no condition on its axis, but must be held in Y somewhere.
A force on an edge is the whole revolution's (axisymmetric), per metre (plane strain)
or through the thickness (plane stress). Totals read back are the whole part's.

## How to model part of a symmetric whole

Model → Symmetry:

- **Symmetry plane…:** the model continues mirrored across a flat cut face. Nothing moves
  through it, and no heat or field crosses it. It goes on every physics that takes one.
- **Antisymmetry plane…:** the mirrored half carries the opposite load. Solid mechanics
  only.
- **Cyclic symmetry…:** one sector of a disc, wheel or flange, its two cut faces joined.
  Statics, transients and heat on CalculiX; statics and transients on Code_Aster.
- **Periodic symmetry…:** one slice of something that repeats along a line.
- **Remove cyclic or periodic symmetry** takes the repeat off.

Halve a load the plane cuts (a quarter for two planes). Results are drawn whole by
default (Settings → Results → Symmetric models); numbers read are the modelled part's.

## How to use a local coordinate system

1. Project → New coordinate system → Cartesian…, Cylindrical… or Spherical…, or
   right-click the Coordinate systems heading. Right-click a face selection for New
   cylindrical axes from this face.
2. The panel in Properties places it by directions, by rotation angles, by three picked
   points, or from a face or edge. It can be relative to another system, or attached to
   a part so it follows the part's placement.
3. Use it: a load's or support's Axes field (x, y, z then mean the system's, or r, θ, z
   for a cylindrical one); a material assignment's axes for orthotropic materials; the
   results bar's In axes, for radial and hoop components.

Project → Edit coordinate system…, or Edit… on its right-click menu, redefines one.

## How to choose an element formulation

Each solver writes its default element unless you choose another for a body. Model →
Components → Element formulation… (or the part's dropdown in Properties) offers:

- incompatible modes: linear bricks that bend without locking (CalculiX C3D8I);
- mixed u-p, for nearly incompressible material (Code_Aster, FEniCSx);
- Euler-Bernoulli beams for slender members (Code_Aster);
- lumped heat capacity, no undershoot after a thermal shock (Code_Aster);
- reduced integration on 20-node bricks (CalculiX C3D20R, Code_Aster 3D_SI);
- thick shells, thicker than about a twentieth of their span (Code_Aster DST).

Help → Element library lists every solver's elements and the ones Puzzle writes.

## Options worth knowing

| Field | Default | Note |
| --- | --- | --- |
| Model type | 3D | or 2D planar, 2D axisymmetric |
| Planar model | plane stress | or plane strain (per metre of depth) |
| Part translation | 0, 0, 0 m | where the part sits |
| Part rotation | 0, 0, 0 ° | Euler angles about X, Y, Z |

## Mistakes to avoid

- A 2D part off z = 0 (`not_planar`), or an axisymmetric region at x < 0
  (`negative_radius`): draw on the XY plane.
- A plane-stress part with no thickness: refused. Heat across parts of different
  thickness flows as k t dT/dx, so CalculiX and FEniCSx weigh it by each part's
  thickness and Elmer and Code_Aster, whose heat equation has none, refuse it.
- 2D models refuse beams, bolts, remote loads, pretension and contacts other than
  conformal bonds.
- A symmetry plane on a face that isn't on the plane of symmetry: it holds the part
  there. Curved faces are refused.
- Expecting a half model's modes to include the antisymmetric ones: the plane
  suppresses them.
- Cyclic symmetry with modes or buckling: refused, since a sector misses the other
  nodal diameters.
- Incompatible modes on tetrahedra: mesh with hexahedra at element order 1.
- A spherical system on a load or material: refused. Result tools give global
  components; use In axes for hoop or radial values.

## For agents and scripts

[symmetry_plane](help:mcp:symmetry_plane) and [symmetry_repeat](help:mcp:symmetry_repeat)
add planes and repeats; [element_choose](help:mcp:element_choose) and
[elements_list](help:mcp:elements_list) choose and list elements;
[parts_split](help:mcp:parts_split) makes one part per body. Components themselves are
made and edited with [submit](help:mcp:submit) and read with
[get_object](help:mcp:get_object). On the command line: `puzzle split-parts` and
`puzzle elements`.

## See also

- [Loads and supports](help:guides/loads-and-supports)
- [Contact](help:guides/contact)
- [Beams, shells and frames](help:guides/beams-shells-frames)
- [Elements](help:reference/elements)
