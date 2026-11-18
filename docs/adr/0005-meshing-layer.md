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
- Bodies of one part (amended 2026-10-31): touching bodies of the same geometry are fragmented together before meshing, so they share faces and nodes (`Geometry.share_faces`, on by default). STEP stores each solid with its own copy of the face between them, so a multi-body STEP part would otherwise be meshed as loose bodies with nothing joining them. Converting the file to BREP doesn't help: the sharing is already gone when STEP is read. Overlapping bodies are refused rather than cut apart. A face the join splits is re-found by its pieces, so selections on it keep working. Turning the option off meshes each body apart.

## Consequences

- One recipe can produce meshes for different solvers.
- Two meshing paths (FEM and CFD) to maintain.

## Alternatives considered

- Salome SMESH: capable, but hard to use outside Salome.
- Leaving meshing to each solver's tools: inconsistent and breaks the single-model goal.

## Amended (2026-11-18, ADR 0090 package 16)

Two sentences no longer describe the code (ADR-E-21, ADR-E-22):

- "CFD meshing for Code_Saturne uses Gmsh with boundary layers": a boundary layer on
  Gmsh is refused ("boundary layers are meshed by Netgen"), and Netgen grows them. No
  CFD test or benchmark has meshed with one yet: the channels use uniform hexahedra,
  which is why their wall functions are benchmarked at Re 1e5. The CFD adapters read
  prisms, untested. Read it as "Netgen boundary layers, not yet benchmarked for CFD";
  a channel on a Netgen recipe with a layer, against the laminar closed form, is the
  check before it is claimed (ADR 0072's list).
- "the solver ties them (CalculiX `*TIE`, Elmer mortar tie contact)": Elmer's tie is not
  written; its adapter claims conformal bonding only and refuses the rest (since
  2026-11-09, ADR 0072).
