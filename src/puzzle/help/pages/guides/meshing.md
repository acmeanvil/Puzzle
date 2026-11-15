---
title: Meshing
section: guide
order: 40
keys: kind:mesh, panel:mesh, section:meshes
summary: Make a mesh recipe, set sizes where they matter, choose the element shape and order, and check the mesh's quality.
adrs: 0005, 0019
---

A mesh recipe says how a component is meshed: the element size, shape and order, and
local controls. A component points at one recipe; several components may share it, so
an edit changes all of them. Any change to a recipe means a new mesh and new runs.

## How to make a recipe and mesh

1. Mesh → New mesh recipe (or right-click the Meshes heading). It starts with a 5 mm
   global size and second-order elements.
2. Point the component at it: the New component… form asks for a recipe, and the
   component's Mesh field in Properties changes it.
3. Set the recipe's fields in Properties: global size, element order, element shape.
4. Mesh → Mesh (Ctrl+M, or the toolbar) meshes the selected component in the background.

The view then colours the elements by shape quality. The Mesh tab's quality box lists
the measures and the worst elements; double-click one to see where it is.

## How to refine where it matters

The **Mesh** tab shows one component's recipe and its controls in order. Double-click a
control to edit it. The tools are in the ribbon above the view, and in the Mesh menu:

- **Sizing → Local size:** one size on a selection's faces, edges or bodies.
- **Sizing → Refine near:** a small size at a selection, growing back to the global size
  between a near and a far distance.
- **Sizing → Box** and **Ball:** a size inside a region of space, not tied to geometry.
- **Method → Edge count:** a number of elements along edges, with a progression.
- **Inflation → Boundary layer:** thin layers along a wall, for flow (Netgen only).

Each control's options open in Properties; Apply adds it as one undoable edit.
Mesh → Show seeds shows where the edges' nodes will fall before you mesh.

## How to mesh with hexahedra

Set the recipe's element shape to hexahedra (quadrilaterals in a 2D model). Gmsh then
meshes, with structured bricks, the bodies that allow it:

- block-like bodies (six faces of four edges);
- bodies of revolution (shafts, pins, tubes, stepped bushes), cut into blocks, or their
  profile revolved when they have chamfers, fillets or a domed end;
- prisms (a plate with through holes, an extruded L), swept from one end in layers;
- bends (an elbow) and bodies that are blocks once cut on their re-entrant faces.

Any other body gets tetrahedra, and the warnings say which and why. Where it touches a
hexahedral body it is tied to it at the solve. CalculiX and Code_Aster take those ties;
Elmer and FEniCSx refuse them.

## How to choose the mesher

Mesh → Mesher → Gmsh (the default) or Netgen. Mesh → Gmsh options… sets any of Gmsh's own
options by name, searchable; unknown names are refused.

## Options worth knowing

| Field | Default | What it changes |
| --- | --- | --- |
| Global size | 5 mm in a new recipe | the largest element size |
| Element order | 2 | 1 or 2; first order when every study is CFD |
| Element shape | tetrahedra | or hexahedra (Gmsh only) |
| Hexahedron nodes | 20 | 20 (serendipity) or 27 (Lagrange) at second order |
| Curvature points | 12 | elements per full turn of a curved face; 0 ignores curvature |
| Quality floor | none | the smallest shape quality allowed (0 to 1); below it meshing fails, saying where |
| Quadrilaterals | all | a 2D model's: all, or mixed (some triangles left) |
| Bolt sectors | 24 | elements round a revolved modelled bolt or nut |

- Without a global size, Gmsh sizes from the geometry and Netgen uses a tenth of the
  model.
- CalculiX takes 20-node bricks only; FEniCSx takes 27-node ones only. Code_Aster and
  Elmer take both.
- Shape quality and scaled Jacobian run from 0 (flat) to 1 (ideal).

## What is refused

- A control naming a selection the component doesn't have.
- Two size controls giving one entity different sizes (both are named).
- An edge count on anything but edges.
- A boundary layer on Gmsh; refine near, edge count, box, ball and hexahedra on Netgen.
- Cyclic or periodic symmetry meshed by Netgen.

## Mistakes to avoid

- First-order tetrahedra for bending or stress: they lock and read low. Keep element
  order 2 for structures.
- Refining everything with the global size when one fillet or hole matters: add a local
  size or refine near there.
- Judging stress at a sharp corner: it never converges, however fine the mesh.
- Editing a recipe another component shares without meaning to change both.
- Meshing again after every small edit on a large model: each mesh takes time.

## How to tell whether the mesh is fine enough

Quality is not accuracy. To see whether a result has settled, run the study on finer
meshes: Results → Optimise and check the mesh → Check mesh convergence…
([Optimisation and convergence](help:guides/optimisation-and-convergence)).

A **mesh shoot-out** solves a study on a Gmsh and a Netgen mesh of the same recipe and
compares the peak values, by default displacement within 2 %. It takes two solves:
`puzzle mesh-shootout PROJECT STUDY`.

## For agents and scripts

[mesh_quality](help:mcp:mesh_quality) meshes with a recipe and reports its measures and
worst elements; [mesh_shootout](help:mcp:mesh_shootout) runs the shoot-out;
[mesh_convergence](help:mcp:mesh_convergence) and [mesh_adapt](help:mcp:mesh_adapt)
judge and refine for a result. Recipes and controls are edited with
[submit](help:mcp:submit). On the command line: `puzzle mesh-shootout`,
`puzzle convergence` and `puzzle adapt`.

## See also

- [Elements](help:reference/elements)
- [Components](help:guides/components)
- [Optimisation and convergence](help:guides/optimisation-and-convergence)
- [Geometry](help:guides/geometry)
