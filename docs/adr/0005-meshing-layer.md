# 0005. A separate meshing layer, Gmsh first

- Status: Accepted (2026-10-25: built and in use)
- Date: 2026-10-17

## Context

Meshing sits between geometry and solver and drives much of commercial tools' usability. Structural FEM wants second-order tetrahedra and hexahedra; finite-volume CFD (Code_Saturne) wants polyhedral or hex-dominant meshes with boundary layers.

## Decision

- Meshing is its own layer (`puzzle.mesh`) with a mesher-neutral recipe: global size, per-selection size, element order, element type preference, boundary layers.
- Gmsh is the primary mesher (in process through its Python API; licence covered by ADR 0002). Netgen is the secondary. MMG handles remeshing and adaptation.
- CFD meshing for Code_Saturne uses Gmsh with boundary layers, exported as MED.
- Meshes keep named selections as physical groups: one per part (its bodies) and one per selection, named `Part/Selection`. MED is the target exchange format; the first implementation writes Gmsh `.msh` and hands adapters an in-memory, solver-neutral mesh (nodes, element blocks per entity, groups) with Gmsh node ordering, which each adapter reorders for its solver.
- All meshing happens in SI units: imported geometry is converted to metres.
- Assemblies: each bonded contact chooses conformal (the default) or nonconformal meshing.
  - Conformal: the touching parts are fragmented (OCCT boolean fragments) before meshing so they share faces and nodes. The solver sees one continuous mesh and needs no contact condition.
  - Nonconformal: the parts are meshed independently and the solver ties them (CalculiX `*TIE`, Elmer mortar tie contact). This is the fallback when fragmenting fails or produces poor elements, for example with sliver gaps or very different mesh sizes.
  - All other contact types are always meshed nonconformally.

## Consequences

- One recipe can produce meshes for different solvers.
- Two meshing paths (FEM and CFD) to maintain.

## Alternatives considered

- Salome SMESH: capable, but hard to use outside Salome.
- Leaving meshing to each solver's tools: inconsistent and breaks the single-model goal.
