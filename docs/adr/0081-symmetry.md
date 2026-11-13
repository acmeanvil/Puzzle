# 0081. Model symmetry: planes first, made supports by Puzzle; cyclic and periodic per solver after

- Status: Accepted; built (2026-11-11): planes on all four FE solvers (any orientation on CalculiX and Code_Aster, one oblique plane per edge on Elmer), cyclic and linear periodic symmetry on CalculiX (solid, heat) and Code_Aster (solid), drawn whole
- Date: 2026-11-11

## Context

The maintainer asked whether every solver supports the usual symmetry types (symmetry and
antisymmetry planes, cyclic, periodic, planar, axisymmetric) and for them in a Model
submenu.

Puzzle had some of these already:
- 2D planar and axisymmetric models, as `Component.dimension` (ADR 0048, all four FE
  solvers and the CFD two by extrusion);
- fluid symmetry (Code_Saturne, OpenFOAM);
- magnetic symmetry (Elmer, FEniCSx).

It had no structural or thermal symmetry plane: users held a cut face's normal with a
`displacement` support, which only works when they know which component that is. It had
nothing cyclic or periodic.

What the solvers offer (checked 2026-11-11):

| Type | CalculiX | Code_Aster | Elmer | FEniCSx | Code_Saturne | OpenFOAM | PyNite |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Symmetry plane | a component held (`*BOUNDARY`), any plane by `*TRANSFORM` | `DDL_IMPO`, any plane by `FACE_IMPO DNOR` | `Displacement i`, any plane by Normal-Tangential | a Dirichlet component (constant normal) | `symmetry` boundary | `symmetry`/`symmetryPlane` patch | releases and supports by hand |
| Antisymmetry plane | the same, other components | the same, `DTAN` | the same | the same | – | – | – |
| Cyclic symmetry (a sector) | `*CYCLIC SYMMETRY MODEL` (static, modal with nodal diameters, heat) | `LIAISON_CYCL`-type links; cyclic modes | `Periodic BC` with rotation | needs `dolfinx_mpc` (not in Puzzle's environment) | periodicity by rotation | `cyclic`/`cyclicAMI` patches | – |
| Linear periodic | `*EQUATION` (written by Puzzle) | `LIAISON_GROUP` | `Periodic BC` with translation | `dolfinx_mpc` | periodicity by translation | `cyclic`/`cyclicAMI` | – |
| 2D planar, axisymmetric | built (ADR 0048) | built | built | built | extruded one cell (built) | extruded, wedge (built) | – |

So symmetry and antisymmetry planes are universal among the FE solvers. Cyclic and
periodic symmetry exist on most of them, each written its own way, and need a benchmark
per solver.

## Decision

1. **Step 1, built: symmetry and antisymmetry planes.**
   - The features are `symmetry` and `antisymmetry` in solid mechanics, and `symmetry`
     in heat transfer and electrostatics (catalog).
   - `puzzle/symmetry.py` `applied`, called in `run_study` after meshing, fits a plane to
     the face's nodes (a straight edge's line in 2D) and refuses a curved one.
   - It turns a solid plane into a `displacement` support. Symmetry holds the normal
     component; antisymmetry holds the in-plane ones.
   - A plane across x, y or z is held in global components, which every FE solver
     writes. Any other plane is held in a Cartesian system made along its normal, which
     only adapters with `displacement:cartesian:partial` (CalculiX) write. The others
     refuse it by name.
   - Heat and electrostatic symmetry is the natural condition, so it is dropped, and
     fluid and magnetic symmetry pass through as they were.
   - The planes count as supports for `unconstrained`, the Advisor and the contact
     check.
2. **Model → Symmetry**, a submenu (`gui/symmetry_dialog.py`):
   - Symmetry plane…: one form, the face. It goes on every physics of the component that
     takes one, as one Edit.
   - Antisymmetry plane…;
   - Model type (3D, 2D planar, axisymmetric)…, setting `Component.dimension` and
     `planar_model`.
   - MCP `symmetry_plane` does the same as the first two, and the skill is `symmetry`.
3. **Step 2: planes across no axis on Code_Aster and Elmer** (`FACE_IMPO DNOR`, and
   Normal-Tangential displacement), benchmarked on a turned bar.
4. **Step 3: cyclic symmetry, on CalculiX first.**
   - A `CyclicSymmetry` on the component holds the axis, the sector count and the two
     cut faces.
   - CalculiX writes `*CYCLIC SYMMETRY MODEL` with a tie for statics and for modes by
     nodal diameter. Code_Aster and Elmer follow, each with its own benchmark: a disc
     spinning, and a bladed disc's modes against the full model.
   - Results draw expanded to the whole model, as axisymmetric ones are revolved
     (`post/revolve.py`).
5. **Step 4: linear periodic symmetry** (node pairs by translation), on CalculiX
   (`*EQUATION`), Code_Aster (`LIAISON_GROUP`) and Elmer (`Periodic BC`). CFD
   periodicity on Code_Saturne and OpenFOAM follows, benchmarked on a periodic channel.
6. **Mirrored display**, results shown across the planes, comes with step 3's expansion.
7. Cyclic and periodic items join the submenu as each step is built, never before.

## Built (2026-11-11)

- **Step 2, planes across no axis.**
  - **CalculiX:** `*EQUATION` n · u = 0 per node (`inp._plane_equations`). Each node's
    planes are taken together, the components plain supports hold are dropped, and the
    rows are reduced so each dependent dof appears once. It meets other planes and
    supports at shared edges, where a `*TRANSFORM` couldn't.
  - **Code_Aster:** `LIAISON_OBLIQUE` with the plane's nautical angles, as the general
    `displacement:cartesian:partial`.
  - **Elmer:** Normal-Tangential Displacement. Two oblique planes meeting at an edge are
    refused there: Elmer averages their normals, 0.07 % off on a turned bar.
  - **FEniCSx:** planes across an axis only.
- **Step 3, cyclic symmetry.**
  - `Component.cyclic` (`CyclicSymmetry`: source, target, sectors, axis, origin).
  - The target face is meshed as the source turned (Gmsh `setPeriodic`,
    `generate._periodic`), and `puzzle/cyclic.py` pairs the nodes.
  - Each pair gives u_t = R u_s (and T_t = T_s), with held components and prescribed
    temperatures kept off the dependent side.
  - CalculiX writes them as `*EQUATION` (solid and heat), Code_Aster as `LIAISON_DDL` on
    one-node groups (solid).
  - Statics and transients only: a modal or buckling step would miss the other nodal
    diameters, so it is refused. Netgen meshes are refused, since the faces must be
    meshed together.
- **Step 4, linear periodic symmetry.** `Component.periodic` (`LinearPeriodicity`:
  source, target, translation) works the same way, with R the identity.
- **Step 6, the whole drawn.** `post/expand.py` adds copies to the view under Settings >
  Results > Symmetric models (default: whole):
  - every combination of up to three symmetry planes;
  - antisymmetric copies reversed;
  - every cyclic sector;
  - four periodic slices.

  Vectors and tensors are turned or mirrored. Probes read the part modelled.
- **GUI and agent.** Model → Symmetry gains Cyclic symmetry…, Periodic symmetry… and
  Remove cyclic or periodic symmetry. MCP `symmetry_repeat`. The skill `symmetry` covers
  all of it.
- **Left for later:** cyclic modes by nodal diameter (CalculiX `*CYCLIC SYMMETRY MODEL`);
  cyclic heat on Code_Aster; cyclic and periodic symmetry on Elmer (`Periodic BC`), the
  CFD solvers and FEniCSx (`dolfinx_mpc`).

## Consequences

- A half or quarter model takes one menu item per cut face, whatever the face's
  orientation (on CalculiX, and on the others for faces across an axis).
- Users and the agent say "symmetry", not which displacement component to hold.
- The conversion lives in one place, so a new FE adapter gets symmetry planes by holding
  components.
- FEniCSx cannot do cyclic or periodic symmetry without `dolfinx_mpc`. Adding it to the
  environment is step 3's or 4's decision, not this one's.
- A plane's normal comes from the mesh's nodes. A cut face that isn't flat is refused
  rather than held approximately.

## Benchmarks

All in `tests/integration/test_symmetry.py`:

- **An eighth of a bar in tension**, held only by three symmetry planes. On CalculiX,
  Code_Aster, Elmer and FEniCSx, the end moves σL/E and the sides draw in νσb/E
  (9.5238095e-6 m and −1.4285714e-7 m against the same closed form).
- **The same bar turned 30° about z**: exact on Code_Aster; CalculiX to its six printed
  digits; Elmer refuses it (two oblique planes meet).
- **A 30° sector of a thick ring** under internal pressure, on symmetry planes at 0° and
  30° (oblique). Lamé's outer displacement within 1.3e-5 on CalculiX, Code_Aster and
  Elmer.
- **Antisymmetry:** half a round shaft in torsion on an antisymmetry plane twists
  TL/(GJ) within +0.077 %, the same on all four FE solvers. A symmetry plane would hold
  it.
- **Cyclic:** the ring as one sector of twelve within 1.3e-5 of Lamé on CalculiX and
  Code_Aster. Each target node moves as its source turned.
- **Cyclic with kinked cut faces**, which no plane fits: 1.2e-5, and the same ring's heat
  (log profile) within 0.007 K on CalculiX.
- **Periodic:** a slice of an endless slab under pressure, exact (−p h (1 − ν²)/E,
  ν (1 + ν) p b / E) on both solvers.
- `tests/test_symmetry.py`: the plane fit, which components each plane holds, local axes
  and their refusal, natural heat symmetry, fluid symmetry left alone, a plane on every
  physics, and the MCP tool.
- `tests/test_expand.py`: copies for planes, antisymmetry and sectors, and tensors
  turned.

## Alternatives considered

- **Each adapter writes symmetry itself:** six writers for what is a held component in
  every FE solver. The conversion is one function, and adapters keep only what differs,
  such as planes across no axis.
- **Leave symmetry as a displacement the user sets:** needs the user to know which
  component is normal, and says nothing of the intent in the tree, the schematic or to
  the agent.
- **Cyclic symmetry through Puzzle's own constraint equations on every solver:** each
  solver's own cyclic support handles phase per nodal diameter in modal analysis, which
  plain equations don't.
