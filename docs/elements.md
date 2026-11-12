# Element library

Every solver's elements, grouped by what they model. ★ marks the ones Puzzle writes today (ADR 0082); the rest are listed so you can see what each solver offers and ask for it.

## 3D solids

| Formulation | Order | CalculiX | Code_Aster | Elmer | FEniCSx |
| --- | --- | --- | --- | --- | --- |
| full integration | linear | C3D4 ★, C3D8 ★, C3D6 ★ | – | – | – |
| full integration | quadratic | C3D10 ★, C3D20 ★, C3D15 ★ | – | – | – |
| reduced integration | linear | C3D8R | – | – | – |
| incompatible modes (bending without locking) | linear | C3D8I ★ | – | – | – |
| reduced integration | quadratic | C3D20R ★ | – | – | – |
| full integration | linear and quadratic | – | 3D ★ | – | – |
| reduced integration | linear and quadratic | – | 3D_SI ★ | – | – |
| mixed displacement-pressure (nearly incompressible) | quadratic | – | 3D_INCO_UPG ★, 3D_INCO_UP | – | Taylor-Hood P2-P1 |
| Lagrange (nodal) unknowns | linear and quadratic | – | – | 504/510 ★, 808/820/827 ★, 706/715 ★ | – |
| Lagrange (nodal) unknowns | linear | – | – | p-elements | Lagrange P1 ★, Lagrange P3+ |
| Lagrange (nodal) unknowns | quadratic | – | – | – | Lagrange P2 ★, Lagrange Q2 (27-node bricks) ★ |

- **CalculiX C3D4**: The linear tetrahedron: quick meshes, heat transfer. Not for: stress in bending: it locks, so use C3D10. Studies: static, modal, buckling, structural dynamics, harmonic response, contact, plasticity, large deflection, heat transfer. (CalculiX User's Manual, 6.2 Element Types)
- **CalculiX C3D10**: The quadratic tetrahedron, Puzzle's default: any geometry, accurate stresses. Studies: static, modal, buckling, structural dynamics, harmonic response, contact, plasticity, large deflection, heat transfer. (CalculiX User's Manual, 6.2 Element Types)
- **CalculiX C3D8**: The linear brick, for hexahedral meshes in heat or stocky solids. Not for: bending of thin parts: it shear-locks; C3D8I or C3D20 instead. Studies: static, modal, buckling, structural dynamics, harmonic response, contact, plasticity, large deflection, heat transfer. (CalculiX User's Manual, 6.2 Element Types)
- **CalculiX C3D8R**: The linear brick with one integration point: cheap explicit-style meshes. Not for: coarse meshes: hourglassing. Studies: static, modal, buckling, structural dynamics, harmonic response, contact, plasticity, large deflection. (CalculiX User's Manual, 6.2 Element Types)
- **CalculiX C3D8I**: The linear brick with incompatible modes: bending of thin parts in one layer. Studies: static, modal, buckling, structural dynamics, harmonic response, contact, plasticity, large deflection. (CalculiX User's Manual, 6.2 Element Types)
- **CalculiX C3D20**: The quadratic (20-node) brick: the most accurate solid on hexahedral meshes; CalculiX has no 27-node brick, so those are refused. Studies: static, modal, buckling, structural dynamics, harmonic response, contact, plasticity, large deflection, heat transfer. (CalculiX User's Manual, 6.2 Element Types)
- **CalculiX C3D20R**: The quadratic brick with reduced integration: bending without locking, cheaper. Not for: one element through a thickness under contact. Studies: static, modal, buckling, structural dynamics, harmonic response, contact, plasticity, large deflection. (CalculiX User's Manual, 6.2 Element Types)
- **CalculiX C3D6**: The linear wedge, to fill swept meshes. Studies: static, modal, buckling, structural dynamics, harmonic response, contact, plasticity, large deflection, heat transfer. (CalculiX User's Manual, 6.2 Element Types)
- **CalculiX C3D15**: The quadratic wedge, on revolved meshes' axes. Studies: static, modal, buckling, structural dynamics, harmonic response, contact, plasticity, large deflection, heat transfer. (CalculiX User's Manual, 6.2 Element Types)
- **Code_Aster 3D**: Continuum solids of every cell, Puzzle's mechanics modelisation. Not for: nearly incompressible material (rubber, plastic flow): 3D_INCO_UPG. Studies: static, modal, buckling, structural dynamics, harmonic response, contact, plasticity, large deflection. (Code_Aster U4.41.01 (AFFE_MODELE))
- **Code_Aster 3D_SI**: 3D with under-integration: quadratic hexahedra and prisms without locking. Studies: static, modal, buckling, structural dynamics, harmonic response, contact, plasticity, large deflection. (Code_Aster U3 (modélisations))
- **Code_Aster 3D_INCO_UPG**: Mixed displacement-pressure-swelling: nearly incompressible materials and large plastic strains. Studies: static, modal, buckling, structural dynamics, harmonic response, contact, plasticity, large deflection. (Code_Aster U3 (modélisations))
- **Code_Aster 3D_INCO_UP**: Mixed displacement-pressure: incompressible elasticity. Studies: static, modal, buckling, structural dynamics, harmonic response, contact, plasticity, large deflection. (Code_Aster U3 (modélisations))
- **Elmer 504/510**: Tetrahedra for StressSolver, HeatSolver and StatElecSolver. Studies: static, modal, structural dynamics, harmonic response, heat transfer, electrostatics. (Elmer Solver Manual, Appendix D)
- **Elmer 808/820/827**: Hexahedra. Studies: static, modal, structural dynamics, harmonic response, heat transfer. (Elmer Solver Manual, Appendix D)
- **Elmer 706/715**: Wedges. Studies: static, modal, structural dynamics, harmonic response, heat transfer. (Elmer Solver Manual, Appendix D)
- **Elmer p-elements**: Hierarchic higher-order bases (Element = "p:2"...). Studies: static, modal, structural dynamics, harmonic response. (Elmer Solver Manual, p-elements)
- **FEniCSx Lagrange P2**: Quadratic Lagrange on tetrahedra: Puzzle's elasticity. Studies: static, modal, structural dynamics, harmonic response, heat transfer, electrostatics. (DOLFINx, basix.ufl.element)
- **FEniCSx Lagrange P1**: Linear Lagrange on tetrahedra. Not for: bending: it locks. Studies: static, heat transfer, electrostatics. (DOLFINx, basix.ufl.element)
- **FEniCSx Lagrange Q2 (27-node bricks)**: Quadratic Lagrange on 27-node hexahedra (the mesh recipe's hexahedron nodes 27). Not for: 20-node bricks: DOLFINx has no serendipity cells, so they are refused. Studies: static, modal, structural dynamics, harmonic response, heat transfer. (DOLFINx, basix.ufl.element)
- **FEniCSx Lagrange P3+**: Higher degrees on the same cells. Studies: static, heat transfer. (DOLFINx, basix.ufl.element)
- **FEniCSx Taylor-Hood P2-P1**: Mixed displacement-pressure for incompressible materials (or Stokes flow). Studies: static. (DOLFINx, basix.ufl.element)

## 2D solids (plane stress, plane strain, axisymmetric)

| Formulation | Order | CalculiX | Code_Aster | Elmer | FEniCSx |
| --- | --- | --- | --- | --- | --- |
| full integration | linear and quadratic | CPS3/CPS6/CPS4/CPS8 ★, CPE3/CPE6/CPE4/CPE8 ★, CAX3/CAX6/CAX4/CAX8 ★ | C_PLAN ★, D_PLAN ★, AXIS ★ | – | – |
| mixed displacement-pressure (nearly incompressible) | quadratic | – | AXIS_INCO_UPG | – | – |
| Lagrange (nodal) unknowns | linear and quadratic | – | – | 303/306/404/408 ★ | Lagrange P1/P2 (2D) ★ |

- **CalculiX CPS3/CPS6/CPS4/CPS8**: Plane stress (thin plates loaded in their plane); also 2D heat. Studies: static, modal, structural dynamics, harmonic response, contact, plasticity, heat transfer. (CalculiX User's Manual, 6.2 Element Types)
- **CalculiX CPE3/CPE6/CPE4/CPE8**: Plane strain (long bodies of constant section). Studies: static, modal, structural dynamics, harmonic response, contact, plasticity. (CalculiX User's Manual, 6.2 Element Types)
- **CalculiX CAX3/CAX6/CAX4/CAX8**: Axisymmetric solids (Y the axis). Studies: static, modal, structural dynamics, harmonic response, contact, plasticity. (CalculiX User's Manual, 6.2 Element Types)
- **Code_Aster C_PLAN**: Plane stress. Studies: static, modal, buckling, structural dynamics, harmonic response, contact, plasticity. (Code_Aster U3 (modélisations))
- **Code_Aster D_PLAN**: Plane strain. Studies: static, modal, buckling, structural dynamics, harmonic response, contact, plasticity. (Code_Aster U3 (modélisations))
- **Code_Aster AXIS**: Axisymmetric (Y the axis). Studies: static, modal, buckling, structural dynamics, harmonic response, contact, plasticity. (Code_Aster U3 (modélisations))
- **Code_Aster AXIS_INCO_UPG**: Axisymmetric, nearly incompressible. Studies: static, modal, buckling, structural dynamics, harmonic response, contact, plasticity. (Code_Aster U3 (modélisations))
- **Elmer 303/306/404/408**: 2D cells: plane stress, plane strain (Cartesian 2D) and axisymmetric (Axi Symmetric); A_z and A_phi magnetics. Studies: static, modal, structural dynamics, harmonic response, heat transfer, electrostatics, magnetic fields. (Elmer Solver Manual, Appendix D)
- **FEniCSx Lagrange P1/P2 (2D)**: 2D plane stress, plane strain and axisymmetric (2πr weights), A_z magnetics. Studies: static, modal, structural dynamics, harmonic response, heat transfer, electrostatics, magnetic fields. (DOLFINx, basix.ufl.element)

## Shells

| Formulation | Order | CalculiX | Code_Aster | Elmer | PyNite |
| --- | --- | --- | --- | --- | --- |
| thick shell (Mindlin-Reissner) | quadratic | S6 ★, S8 ★, S8R | COQUE_3D | – | – |
| thin shell (Kirchhoff) | linear | S3 | DKT ★ | – | – |
| thick shell (Mindlin-Reissner) | linear | S4/S4R | DST, Q4G | – | Plate / Quad (MITC4) |
| thick shell (Mindlin-Reissner) | linear and quadratic | – | – | ShellSolver | – |

- **CalculiX S6**: The quadratic triangular shell, expanded to C3D15 internally. Studies: static, modal, buckling, structural dynamics, contact, plasticity. (CalculiX User's Manual, 6.2 Element Types)
- **CalculiX S8**: The quadratic quadrilateral shell, expanded to C3D20R. Studies: static, modal, buckling, structural dynamics, contact, plasticity. (CalculiX User's Manual, 6.2 Element Types)
- **CalculiX S8R**: The quadratic quadrilateral shell with reduced integration. Studies: static, modal, buckling, structural dynamics, contact, plasticity. (CalculiX User's Manual, 6.2 Element Types)
- **CalculiX S3**: The linear triangular shell. Not for: anything: it locks, so Puzzle refuses it. Studies: static. (CalculiX User's Manual, 6.2 Element Types)
- **CalculiX S4/S4R**: The linear quadrilateral shells. Studies: static, modal, buckling. (CalculiX User's Manual, 6.2 Element Types)
- **Code_Aster DKT**: Thin plates and shells (Kirchhoff), linear cells: Puzzle's shells. Not for: thick plates (span/thickness below ~20): DST. Studies: static, modal, buckling, structural dynamics, plasticity, large deflection. (Code_Aster U3 (modélisations))
- **Code_Aster DST**: Thick plates (Mindlin-Reissner), linear cells. Studies: static, modal, buckling, structural dynamics. (Code_Aster U3 (modélisations))
- **Code_Aster Q4G**: Thick quadrilateral plates. Studies: static, modal. (Code_Aster U3 (modélisations))
- **Code_Aster COQUE_3D**: Curved shells on quadratic cells, large rotations. Studies: static, modal, buckling, structural dynamics, large deflection. (Code_Aster U3 (modélisations))
- **Elmer ShellSolver**: Elmer's Reissner-Mindlin shells. Studies: static, modal. (Elmer Models Manual, ShellSolver)
- **PyNite Plate / Quad (MITC4)**: Rectangular plates and MITC4 quadrilaterals for walls and slabs. Studies: static. (PyNite, FEModel3D.add_plate, add_quad)

## Beams

| Formulation | Order | CalculiX | Code_Aster | Elmer | PyNite |
| --- | --- | --- | --- | --- | --- |
| Timoshenko beam (shear deformation) | linear | B31 ★, U1 (general beam) | POU_D_T ★ | BeamSolver3D | – |
| Timoshenko beam (shear deformation) | quadratic | B32 ★, B32R ★ | – | – | – |
| Euler-Bernoulli beam | linear | – | POU_D_E ★ | – | Member ★ |
| beam with warping (open sections in torsion) | linear | – | POU_D_TG | – | – |
| large rotations | linear | – | POU_D_TGM | – | – |

- **CalculiX B31**: The linear beam, expanded to bricks of its section (rectangle, circle). Not for: short or many-element members: B32 is more accurate. Studies: static, modal, structural dynamics. (CalculiX User's Manual, 6.2 Element Types)
- **CalculiX B32**: The quadratic beam, expanded to bricks of its section. Studies: static, modal, buckling, structural dynamics. (CalculiX User's Manual, 6.2 Element Types)
- **CalculiX B32R**: The quadratic beam with reduced integration: the one taking box and pipe sections. Puzzle writes it for those. Studies: static, modal, buckling, structural dynamics. (CalculiX User's Manual, 6.2 Element Types)
- **CalculiX U1 (general beam)**: CalculiX's general-section beam. Not for: torsion: its stiffness is G(I11 + I22)/L, not G J, so Puzzle writes open sections as exact springs instead. Studies: static. (CalculiX User's Manual, 6.2 Element Types)
- **Code_Aster POU_D_T**: The straight Timoshenko beam: Puzzle's beams and beam bolts. Not for: large rotations: POU_D_TGM. Studies: static, modal, buckling, structural dynamics, harmonic response. (Code_Aster U3 (modélisations))
- **Code_Aster POU_D_E**: The slender Euler-Bernoulli beam. Not for: stocky members (shear deflection). Studies: static, modal, buckling, structural dynamics. (Code_Aster U3 (modélisations))
- **Code_Aster POU_D_TG**: The Timoshenko beam with warping: open sections in torsion. Studies: static, modal, buckling. (Code_Aster U3 (modélisations))
- **Code_Aster POU_D_TGM**: The multi-fibre beam with warping: large rotations, plasticity in the section. Studies: static, structural dynamics, plasticity, large deflection. (Code_Aster U3 (modélisations))
- **Elmer BeamSolver3D**: Elmer's 3D beams. Studies: static. (Elmer Models Manual, BeamSolver3D)
- **PyNite Member**: The 12-dof frame member with end releases: Puzzle's frames (ADR 0079), P-Delta and the Direct Analysis Method. Not for: stocky members: no shear deflection. Studies: static, large deflection. (PyNite, FEModel3D.add_member)

## Trusses and cables

| Formulation | Order | CalculiX | Code_Aster |
| --- | --- | --- | --- |
| full integration | linear and quadratic | T3D2/T3D3 | – |
| full integration | linear | – | BARRE |
| large rotations | linear | – | CABLE |

- **CalculiX T3D2/T3D3**: Trusses (axial force only). Studies: static, modal. (CalculiX User's Manual, 6.2 Element Types)
- **Code_Aster BARRE**: Bars (axial force only). Studies: static, modal, large deflection. (Code_Aster U3 (modélisations))
- **Code_Aster CABLE**: Cables (tension only, large displacements). Studies: static, large deflection. (Code_Aster U3 (modélisations))

## Springs, gaps, masses and other point elements

| Formulation | Order | CalculiX | Code_Aster | PyNite |
| --- | --- | --- | --- | --- |
| point element | linear | SPRINGA/SPRING1/SPRING2 ★, GAPUNI ★, DASHPOTA | DIS_T ★, DIS_TR ★ | Spring |
| point element | – | MASS ★ | – | Spring support ★ |

- **CalculiX SPRINGA/SPRING1/SPRING2**: Springs: elastic supports, bolt and weld springs, thread flanks, beam springs. Studies: static, modal, structural dynamics. (CalculiX User's Manual, 6.2 Element Types)
- **CalculiX GAPUNI**: A one-way gap: compression-only supports, frictionless thread flanks. Studies: static, contact. (CalculiX User's Manual, 6.2 Element Types)
- **CalculiX MASS**: A point mass (lumped at a spring beam's nodes). Studies: modal, structural dynamics. (CalculiX User's Manual, 6.2 Element Types)
- **CalculiX DASHPOTA**: A dashpot (viscous damper between two nodes). Studies: structural dynamics, harmonic response. (CalculiX User's Manual, 6.2 Element Types)
- **Code_Aster DIS_T**: Translational springs and gaps (DIS_CHOC): elastic supports, pretension, thread helpers. Studies: static, modal, structural dynamics, contact. (U4.42.01 (AFFE_CARA_ELEM))
- **Code_Aster DIS_TR**: Springs with rotations: RBE3 masters, beam-bolt ends. Studies: static, modal, structural dynamics. (U4.42.01 (AFFE_CARA_ELEM))
- **PyNite Spring support**: A node held by a spring in a dof (node_spring). Studies: static. (PyNite, FEModel3D.def_support_spring)
- **PyNite Spring**: A two-node axial spring, tension or compression only. Studies: static. (PyNite, FEModel3D.add_spring)

## Heat transfer

| Formulation | Order | CalculiX | Code_Aster | FEniCSx |
| --- | --- | --- | --- | --- |
| full integration | linear and quadratic | DCAX3/DCAX6/DCAX4/DCAX8 ★ | 3D (THERMIQUE) ★, PLAN/AXIS (THERMIQUE) ★ | – |
| lumped (diagonal) capacity or mass | linear and quadratic | – | 3D_DIAG ★ | – |
| Lagrange (nodal) unknowns | linear | – | – | Raviart-Thomas, DG |

- **CalculiX DCAX3/DCAX6/DCAX4/DCAX8**: Axisymmetric heat transfer. Studies: heat transfer. (CalculiX User's Manual, 6.2 Element Types)
- **Code_Aster 3D (THERMIQUE)**: Heat conduction in solids, Puzzle's heat modelisation in 3D. Studies: heat transfer. (Code_Aster U3 (modélisations))
- **Code_Aster 3D_DIAG**: Heat conduction with lumped capacity: no oscillation after a thermal shock. Studies: heat transfer. (Code_Aster U3 (modélisations))
- **Code_Aster PLAN/AXIS (THERMIQUE)**: 2D and axisymmetric heat conduction. Studies: heat transfer. (Code_Aster U3 (modélisations))
- **FEniCSx Raviart-Thomas, DG**: Flux-conforming and discontinuous spaces (mixed Poisson, transport). Studies: heat transfer. (DOLFINx, basix.ufl.element)

## Electric and magnetic fields

| Formulation | Order | Elmer | FEniCSx |
| --- | --- | --- | --- |
| edge (Nédélec, Whitney) unknowns | linear | WhitneyAV edge elements ★ | – |
| edge (Nédélec, Whitney) unknowns | linear and quadratic | – | N1curl (Nédélec, first kind) ★ |

- **Elmer WhitneyAV edge elements**: Lowest-order edge elements for the magnetic vector potential (magnetostatics, eddy currents). Studies: magnetic fields. (Elmer Models Manual, WhitneyAVSolver)
- **FEniCSx N1curl (Nédélec, first kind)**: Edge elements for the magnetic vector potential. Studies: magnetic fields. (DOLFINx, basix.ufl.element)

## Fluid cells (finite volume)

| Formulation | Order | Code_Saturne | OpenFOAM |
| --- | --- | --- | --- |
| finite-volume cell | linear | Polyhedral cells ★ | polyMesh cells ★ |

- **Code_Saturne Polyhedral cells**: Any first-order cell, joined by faces: laminar and RANS flow, conjugate heat. Not for: quadratic cells: meshes are first order. Studies: fluid flow, heat transfer. (Code_Saturne theory guide, finite-volume discretisation)
- **OpenFOAM polyMesh cells**: Any first-order cell: simpleFoam's steady laminar and RANS flow, a passive temperature. Not for: quadratic cells. Studies: fluid flow, heat transfer. (OpenFOAM User Guide, 4.1 Mesh description)
