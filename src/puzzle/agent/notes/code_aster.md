---
link: https://code-aster.org/doc/
---

# Code_Aster: what Puzzle has learned

Curated from Puzzle's own development notes: behaviour measured or decided while building the adapters. Read with the skills and ADRs.

## Temperature tables per solver

CalculiX `*CONDUCTIVITY` value/temperature lines plus tight `*CONTROLS, PARAMETERS=FIELD` (its defaults stop 0.2 K short); Elmer `Variable Temperature` tables with nonlinear iterations; Code_Aster `THER_NL` (LAMBDA and enthalpy BETA functions) with `THER_NON_LINE`, steady via `ETAT_INIT=_F(STAT='OUI')`; FEniCSx a closed-form piecewise-linear UFL expression and Picard iteration. Code_Aster is written with `PARM_THETA` (1.0 by default, not its own 0.57). With temperature tables Code_Aster steps the enthalpy, the others rho cp(T); they differ by O(dt) (0.7 K at dt = 5 s in the shoot-out, 0.15 K at 1 s), Code_Aster being closer to the converged answer.

## Time schemes at t = 0

backward Euler agrees across all four solvers to 3 decimals and BDF2 between Elmer and FEniCSx. For theta < 1 the starting state matters: Code_Aster and FEniCSx start from the given initial field, while Elmer appears to apply the boundary values to the starting state first, and its Crank-Nicolson damps the initial jump (plain CN from a stepped start oscillates: 18.7 K in a FEniCSx experiment). So theta-scheme results differ between Elmer and the other two after a sudden load; prefer backward Euler or BDF2 there.

## Code_Aster 18

needs `INCREMENT` even for steady `THER_LINEAIRE`; table numbers need `FORMAT_R` (default keeps 5 digits). Containers run as your uid with `HOME=/tmp` (run_aster needs a home).

## Contacts

adapters must refuse contacts they don't write (`common.check_contacts`, code `unsupported`); conformal bonds need nothing (shared nodes). Nonconformal bonds: CalculiX `*TIE` (source = slave surface, target = master; works for heat too), Code_Aster `LIAISON_MAIL` (source face into the target part's volume; structural only). `tests/integration/test_contact.py` uses `conftest_geometry.split_bar_project`. Don't put SPCs on CalculiX tie slave nodes: the conflict floods the log with cascade warnings. Frictionless/frictional: CalculiX surface-to-surface `*CONTACT PAIR` with linear penalty K = 50 E/h (+ `*FRICTION`), static steps start at 0.1 of the load; Code_Aster frictionless `DEFI_CONTACT` (continuous) + `STAT_NON_LINE` ramped over 10 increments, contact faces reoriented with `ORIE_PEAU`. Code_Aster friction: classic continuous `DEFI_CONTACT(FROTTEMENT='COULOMB')` with `NEWTON=_F(REAC_ITER=1)` and up to 200 iterations (as its 3D validation ssnv128). It works on first-order meshes with the slave face inside the master and no displacement condition on slave nodes; the adapter refuses quadratic meshes and slave/Dirichlet clashes up front. Fastener faces (bolt holes) that share nodes with a slave face are left out of its zone (`SANS_GROUP_NO`, `comm._contact_exclusions`): tied to the bolt and in contact too, the matrix is singular (LAGS_F1). Tried and failed here: quadratic tets (singular LAGS_C/LAGS_F; Code_Aster validates quadratic 3D friction on HEXA27 only), mortar `DEFI_CONT` + `MECA_NON_LINE` (validated in 2D and shells only; no convergence in 3D), discrete penalty. CalculiX inverted an element when the master overhung the slave; its test keeps coincident faces. Static structural steps output reaction forces (`reaction_force`); `results.query.total` sums them over a selection.

## Solver threads

native and container runs get `OMP_NUM_THREADS` and CalculiX's `CCX_NPROC_EQUATION_SOLVER`/`_STIFFNESS`/`_RESULTS` (`jobs.runtime.thread_environment`; Settings > Solvers > Cores per run, the physical cores by default (`puzzle.machine`), and Parallel processes per run, which share those cores (ADR 0053); the user's own values win). Code_Aster's export takes them (`code_aster.comm.resources`: `ncpus`, `mpi_nbcpu`, memory from `Runtime.memory`, Docker's VM in a container); run_aster ignores OMP_NUM_THREADS. Its MPI runs need an MPI build (`code_aster.mpi_build`: the `puzzle-code-aster:18.1-mpi` image, label `puzzle.mpi=openmpi`, or conda-forge's openmpi variant). Without them CalculiX factors on one core: a frictional contact took 62 min instead of 38. conda-forge's CalculiX has no PARDISO (`SOLVER=PARDISO` errors), so ADR 0051 builds one (`calculix-pardiso` env, `scripts/build_calculix_pardiso.sh`, MKL), found first by `jobs.runtime`: the bolted bracket 935 s → 246 s, the same answers; CalculiX picks PARDISO itself when built in. Newer compilers need CalculiX's old C let through (`-Wno-error=return-mismatch` and the like).

## Face areas

(`common.surface_area`) integrate curved second-order faces as the solvers do: a force spread as F / A on flat-triangle areas came out 0.2 % low on a face with a hole (Code_Aster).

## Derived coordinate systems (ADR 0049 step 3)

a system from a face or edge, attached to a part, or relative to another is resolved by `puzzle.axes.absolute(project, cs, root)` (`run_study` hands adapters `resolved_project`); never read such a system's `centre()`/`orientation()` unresolved. Material directions: `MaterialAssignment.axes` (CalculiX `*ORIENTATION`; Code_Aster `MASSIF` ANGL_REP or ANGL_AXE, its cylindrical L T N being axial, tangential, radial, so `comm.CYLINDRICAL` reorders the constants; Elmer and FEniCSx refuse orthotropic and anisotropic models).

## Local axes (ADR 0037)

`common.globalize` turns Cartesian forces and full Cartesian displacements into global ones before writing; what's left (cylindrical, or some components only) each adapter writes itself, and `Capabilities.axes_features` (`"displacement:cylindrical"`, `":partial"`) says which. Code_Aster: when every constraint is a function, the plain `load` charge is left out (an empty `AFFE_CHAR_MECA` is a syntax error).

## Bolt pretension (ADR 0037, CalculiX)

the deck cuts the mesh at the section (twin nodes, the elements on one side moved onto them, `*EQUATION` u_twin − u = n u_ref), because `*PRE-TENSION SECTION` moves only elements with a face on the section and leaves tets joined at edges. Helper node ids, in order past the mesh's: rigid-body nodes, pretension reference nodes, twins, spring grounds. Spring grounds are held in each step (an `OP=NEW` would drop a model-level hold). `Feature.steps` limits a feature to some solve steps: CalculiX writes it per step (a later step resets each load card it doesn't write, since `OP=NEW` is per card); split studies get a project copy per step (`solve._in_step`). Code_Aster writes elastic supports and pretension through point elements added to the MED mesh (`comm._Points`: one group per node; `DIS_T`, `K_T_D_N` with `REPERE='LOCAL'`), the same cut as CalculiX (`common.section_cuts`), `LIAISON_DDL` to a reference node with a 1e-9 stiffness on its DX; helper nodes are dropped from results by `common.mark_mesh_nodes`/`drop_helper_nodes`, and value-less table rows ("-") are skipped. Compression-only supports: CalculiX `GAPUNI` to grounds in an `NLGEOM` step (gaps aren't switched in a linear one); Code_Aster `DIS_CHOC` on the point elements, local x into the face.

## CalculiX beams (ADR 0055)

B31/B32 expand into solids of RECT, CIRC, PIPE or BOX outlines; PIPE and BOX only on B32R (the adapter writes B32R for such parts and asks for second-order meshes). Its U1 general beam gives torsion G (I11 + I22) / L (`e_c3d_u1.f`), not G J, so open table sections (I, channels, tees, angles) are written as exact Timoshenko springs instead (`calculix/spring_beams.py`: per element, both orders, a second-order one as two segments; a rotation node per mesh node, linear rigid arms by `*EQUATION` to the segment's middle, six SPRING2 in the section's frame, `common.timoshenko_springs`; a zero spring N to its rotation node, since the .frd holds element nodes only; lumped MASS, no rotary inertia; `beam_forces` and the nodes' reactions read from the springs' end reactions, not from displacements, which the .frd prints to six digits), refusing heat, thermal loads, buckling, NLGEOM, prestress, plasticity, Rayleigh damping, centrifugal loads and shared nodes; Code_Aster takes every shape (`SECTION='GENERALE'`, IY strong, AY = A/A1).

## Beam fasteners (ADR 0027)

`Component.connectors` (`BeamFastener`) are beam bolts by default (`formulation`): CalculiX writes the exact Timoshenko equivalent, the ends' spiders carrying a middle node each and six `SPRING2` springs between them in the bolt's frame (`common.beam_bolt_stiffness`; its U1 beam is wrong for short beams and its B31 beams can't join rigid bodies); Code_Aster a `POU_D_T` beam (no point element on its end nodes: EFGE_NOEU averages over a node's elements). In a pretension cut the spider's node must be the dependent term: CalculiX passes no force through a spring whose node is the dependent term of an equation with an opening. `formulation="solid"` keeps the meshed bolt (`common.bolt_mesh`). Rigid ends: one rigid body with the hole (`*RIGID BODY`, `LIAISON_SOLIDE`); distributing: the bolt end rigid with two helper nodes, RBE3 to the hole (CalculiX `*EQUATION` solved for six face dofs, `_rbe3_equations`, so nothing cascades; Code_Aster `LIAISON_RBE3` to a `DIS_TR` master). Forces are the bolt end faces' reactions summed (`common.fastener_values`), as the global field `fastener`. CalculiX set names keep signs (`+z` → P, `-z` → M): box sides used to share a set.

## Material models

(`Material.models`: plasticity, orthotropic elasticity, hyperelasticity, anisotropic conductivity; `Material.solver_keywords`): an adapter writes those in `Capabilities.material_models` (`material_keywords` for keywords) and `Adapter.check` refuses the rest, only for studies that solve the model's physics. Plasticity is written by CalculiX (`*PLASTIC`, stepped static steps, PEEQ out) and Code_Aster (`VMIS_ISOT_TRAC` in `STAT_NON_LINE`), benchmarked in `tests/integration/test_plasticity.py`; orthotropic elasticity and anisotropic conductivity by CalculiX and Code_Aster (ADR 0049, `test_orthotropic.py`); hyperelasticity is refused everywhere so far. A study whose materials lack a needed property reports `incomplete_material` (`Project.problems`), so runs are refused before writing input.
