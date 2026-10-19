# Puzzle

Open-source (GPL-3.0-or-later) FEM/CFD pre- and post-processor: one solver-neutral model run on CalculiX, Elmer, Code_Aster, FEniCSx and Code_Saturne, aiming for Ansys/COMSOL usability. Decisions live in `docs/adr/` (read the index before changing architecture); the shareable design doc is a Claude Doc: https://claude.ai/code/artifact/7e7a31c7-a7a6-49b3-97ed-05307493943f. Keep both in sync when a decision changes.

## Workflow

- Work on the `development` branch. Push to `main` only when the maintainer says so.
- Contributors are the maintainer and Claude; no CLA/DCO.
- Commit and push only when asked.
- New architectural decision → new ADR from `docs/adr/template.md`, plus a row in `docs/adr/README.md`.

## Commands

Environments come from pixi (`pixi.toml`, ADR 0013). Each native solver has its own environment.

```sh
pixi run -e dev check          # ruff lint + format check + all tests incl. native CalculiX runs
pixi run -e dev pytest -k name # one test
pixi install -e fenicsx        # other native solvers: fenicsx, code-aster (linux-64/win-64)
pixi run -e dev import-materials "<path>/Materials_Hunt_List.xlsx"
```

A plain `.venv` also works (`pip install -e ".[dev]"`); set `PUZZLE_SOLVER_PATH=$PWD/.pixi/envs/calculix/bin` so solver tests find `ccx`.

Transient steps take `time_step` and `end_time` settings, and `time_scheme` (default `backward_euler` on every solver, so shoot-outs compare like with like; also `crank_nicolson`, `bdf2`, `theta` with a `theta` setting, where the capability matrix's `time_schemes` allows); heat physics takes `initial_temperature`. Heat transfer uses a material's temperature tables (conductivity, specific heat, density) by default, which makes it nonlinear; the physics setting `temperature_dependent_materials = false` uses the single values instead (closed-form benchmarks do this). `tests/integration/test_nonlinear_conduction.py` checks the tables against the Kirchhoff solution (all four FE solvers within 0.002 K) and in a transient shoot-out. Results come back as one step per output time (`StepResult.value` = time). Whole-model values (e.g. stored electric energy) are fields with `location="global"`.

Solver tests in `tests/integration/` skip when the solver isn't available; `tests/solvers.py` `over("elmer", "fenicsx")` parametrizes a test over solvers. Rendering tests run only with `PUZZLE_RENDER_TESTS=1`; check renders by looking at the PNG (`puzzle show ... --screenshot`). Container runs: `PUZZLE_RUNTIME=docker pytest tests/integration` after building images in `containers/`.

## Layout

| Path | What |
| --- | --- |
| `src/puzzle/model` | Pydantic schema: resources, components (parts, contacts, physics), studies (sweep/solve steps, shoot-out), transfers |
| `src/puzzle/project` | One-TOML-file-per-object store; content hashes, staleness, run keys |
| `src/puzzle/materials` | `generic.toml`: the shipped generic materials (structural steel with EN 1993-1-2 tables, stainless, aluminium, titanium, copper, electrical steel, PTFE, air, water); user libraries load on top |
| `src/puzzle/geometry` | STEP import via Gmsh's OCC kernel, named entities with signatures |
| `src/puzzle/mesh` | Recipe → Gmsh mesh with physical groups `Part/Selection`; solver-neutral `Mesh` |
| `src/puzzle/adapters` | One adapter per solver: capability matrix, `write_input`, `command`, `read_results` |
| `src/puzzle/jobs` | Runs solvers natively or in Docker/Podman; runtime chosen per solver |
| `src/puzzle/commands` | Command layer (ADR 0015): `Session.submit/propose/accept/undo`, `history.jsonl`, autonomy checks, `outline` |
| `src/puzzle/agent` | `mcp.py`: MCP server over the command layer (`puzzle mcp PROJECT`); tools return summaries and coded errors |
| `src/puzzle/errors.py` | `PuzzleError(code, message, path, details)` and `Problem`; codes in `CODES` |
| `src/puzzle/results` | Solver-neutral `Results`; `ResultStore` = Delta Lake tables (runs, meshes, nodes, cells, groups, fields); `query` = summary, stats, value_at, integrate |
| `src/puzzle/solve.py` | `run_study`: validate → mesh → write → run → read |
| `src/puzzle/post` | VTK grids (tet10 reordered for VTK), `render` (window or PNG), `.vtu`/`.pvd` export, `sample` (results at a point) |
| `src/puzzle/cli.py` | `puzzle studies/run/runs/show/export/outline/history/undo/proposals/accept/reject/autonomy/schema/mcp` |
| `examples/cantilever` | Example project; regenerate with `python examples/cantilever/build.py` |
| `benchmarks/` | Benchmarks as projects (NAFEMS LE1, laminar channel flow); each has build.py, a README with measured results, and an integration test |
| `containers/`, `packaging/conda-forge/` | Solver images; conda-forge recipes (elmerfem) |

## Adapter status

| Solver | Implemented | Verified by (`tests/integration/test_benchmarks.py`, `test_nafems_le1.py`; transient heat vs the series solution, all four FE solvers within 0.34%) | Runs |
| --- | --- | --- | --- |
| CalculiX | static, modal, steady and transient heat; fixed, displacement, force, pressure, temperature, heat flux, convection | cantilever, modal, conduction, LE1 | native (pixi `calculix`) or Docker |
| Elmer | one solve step: static, modal (solid), steady or transient heat (BDF1), electrostatics (StatElecSolve, stored energy as a global field), magnetostatics (CoilSolver + WhitneyAV + CalcFields: one coil, B and J nodal, magnetic energy); own mesh files, ASCII .vtu output | same set plus `test_electrostatics.py` (capacitor) and `test_magnetostatics.py` (wire in air); agrees with CalculiX to 4 digits, LE1 within 0.13% | Docker image (not on conda-forge yet) |
| Code_Aster | one solve step: static, modal (solid), steady or transient heat (theta scheme); MED mesh via Gmsh with safe group names (G1, G2, ...); results as IMPR_TABLE CSVs | same set; LE1 +0.01% | native on linux-64/win-64 (pixi `code-aster`), Docker on macOS |
| FEniCSx | one solve step: static, modal (P2 elasticity, SLEPc shift-invert), steady or transient heat (implicit Euler), electrostatics, magnetostatics (coil conduction solve scaled to the total current, then Nedelec curl-curl with a small gauge mass term; same discrete answer as Elmer on the same mesh); fixed `study.py` + `case.json` + `mesh.msh`, results.npz mapped back by coordinates | same set plus the capacitor and wire EM benchmarks; LE1 −0.11% | native (pixi `fenicsx`, found via PUZZLE_SOLVER_PATH or PUZZLE_FENICSX_PYTHON) |
| Code_Saturne | steady laminar incompressible flow: velocity inlet, pressure outlet, wall, symmetry; `setup.xml` written in-container by Code_Saturne's own case model (`cs_setup.py`); EnSight Gold results read per cell | channel flow (`benchmarks/channel_flow`): peak velocity −0.23%, profile 0.21%, dp/dx −0.95% | Docker image built from source (v9.2.0) |

Capability matrices also list planned capabilities; `write_input` refuses what isn't implemented yet. Run Elmer tests locally with Docker on PATH (`/Applications/Docker.app/Contents/Resources/bin`) after building `containers/elmer`.

## Conventions

- Everything is SI internally (m, kg, s, N, Pa, K). Imported geometry is converted to metres; `Quantity.to_si()` converts user values. Never pass units implicitly to a solver.
- Adapters only claim a capability once a benchmark for it passes. Elmer's mechanical contact is bonding only (mortar tie); its slide/friction/stick are `experimental`.
- Tests compare against closed-form or published answers with stated tolerances, not snapshots.
- Match the surrounding code: dataclasses/Pydantic, type hints, short docstrings that say why.
- **Every project change goes through `puzzle.commands.Session`** (CLI, GUI, scripts, agents); no client writes project files itself. New user actions become commands in `commands/model.py`. Errors a client can act on are `PuzzleError` with a code from `puzzle.errors.CODES`.

## Gotchas

- **Gmsh is global state.** Always use `puzzle.geometry.gmsh_session()`; it holds a lock and sets OCC import units to metres. Don't nest sessions.
- **OCC pads bounding boxes** by ~1e-7 m. Plane tests need a tolerance of at least 1 µm.
- **Entity names** (`face-3`) follow STEP file order; signatures (kind, measure, centre) re-find them after placement or fragmenting.
- **Element node order:** Gmsh differs from CalculiX, Elmer and VTK for tet10 (last two edge nodes swap). Adapters own the reordering; shared face lookups live in `adapters/common.py`.
- **FEniCSx `study.py`** runs inside the FEniCSx env and must not import puzzle. dolfinx 0.11 `LinearProblem` needs `petsc_options_prefix`; MUMPS is used only if PETSc has it. Windows dolfinx has no PETSc, so `study.py` falls back to SciPy (SuperLU, ARPACK; CG for the stress projection); `PUZZLE_FENICSX_BACKEND=scipy` forces that path for testing (LE1 ~54 s vs ~10 s with PETSc).
- **Code_Saturne's Gmsh reader** names groups by physical tag number, not name: zones select `"2"`, not `"Inlet"` (`write_med` returns the tags). Results are per cell (`location="element"`), matched to Puzzle elements by node-average centres (`match_points`, float32-tolerant).
- **Temperature tables per solver:** CalculiX `*CONDUCTIVITY` value/temperature lines plus tight `*CONTROLS, PARAMETERS=FIELD` (its defaults stop 0.2 K short); Elmer `Variable Temperature` tables with nonlinear iterations; Code_Aster `THER_NL` (LAMBDA and enthalpy BETA functions) with `THER_NON_LINE`, steady via `ETAT_INIT=_F(STAT='OUI')`; FEniCSx a closed-form piecewise-linear UFL expression and Picard iteration. Code_Aster is written with `PARM_THETA` (1.0 by default, not its own 0.57). With temperature tables Code_Aster steps the enthalpy, the others rho cp(T); they differ by O(dt) (0.7 K at dt = 5 s in the shoot-out, 0.15 K at 1 s), Code_Aster being closer to the converged answer.
- **Time schemes at t = 0:** backward Euler agrees across all four solvers to 3 decimals and BDF2 between Elmer and FEniCSx. For theta < 1 the starting state matters: Code_Aster and FEniCSx start from the given initial field, while Elmer appears to apply the boundary values to the starting state first, and its Crank-Nicolson damps the initial jump (plain CN from a stepped start oscillates: 18.7 K in a FEniCSx experiment). So theta-scheme results differ between Elmer and the other two after a sudden load; prefer backward Euler or BDF2 there.
- **Code_Aster 18** needs `INCREMENT` even for steady `THER_LINEAIRE`; table numbers need `FORMAT_R` (default keeps 5 digits). Containers run as your uid with `HOME=/tmp` (run_aster needs a home).
- **Elmer output** lands in the mesh directory (`mesh/case_t0001.vtu`). Points come in Elmer node order (no point ids); `GeometryIds` there is cell data. Eigenvalues (omega^2) are parsed from the solver log.
- **CalculiX `.frd`** is fixed width (values can touch); match record tags on the first token. Modal block values are frequencies in Hz.
- **Native solver runs** must look activated: `NativeRuntime.environment()` puts the conda/pixi env on PATH and sets `CONDA_PREFIX` (Windows otherwise fails with 0xC0000135). Elmer from conda also needs `ELMER_HOME` = env prefix.
- **Elmer image** is built without MUMPS (Debian trixie lacks ParMETIS).
- **Elmer magnetics:** the A-V edge system stalls under GCR/BiCGStab here, so it is solved directly (Elmer then tree-gauges it). Unlisted SIF keywords such as `Use Magnetic Norm` need an explicit type (`Logical True`), or parsing fails. Lowest-order edge elements give B constant per element; nodal B is an average, so check it away from material interfaces and use the stored energy for the whole field.
- **Local mesh sizes** are Gmsh `Constant` fields over the selection's entities (min-combined background field); point sizes alone barely refine curved bodies.
- **Contacts:** adapters must refuse contacts they don't write (`common.check_contacts`, code `unsupported`); conformal bonds need nothing (shared nodes). Nonconformal bonds: CalculiX `*TIE` (source = slave surface, target = master; works for heat too), Code_Aster `LIAISON_MAIL` (source face into the target part's volume; structural only). `tests/integration/test_contact.py` uses `conftest_geometry.split_bar_project`. Don't put SPCs on CalculiX tie slave nodes: the conflict floods the log with cascade warnings. Frictionless/frictional: CalculiX surface-to-surface `*CONTACT PAIR` with linear penalty K = 50 E/h (+ `*FRICTION`), static steps start at 0.1 of the load; Code_Aster frictionless `DEFI_CONTACT` (continuous) + `STAT_NON_LINE` ramped over 10 increments, contact faces reoriented with `ORIE_PEAU`. Code_Aster frictional is refused: continuous friction gave singular multipliers (LAGS_C/LAGS_F) on quadratic tets and no Newton convergence on linear ones for slide-from-the-start loading; discrete penalty also failed. Likely next try: press then slide (separate load ramps) or the newer DEFI_CONT/MECA_NON_LINE. Static structural steps output reaction forces (`reaction_force`); `results.query.total` sums them over a selection.
- **Part selections are matched within the part:** `generate_mesh` finds each part's faces only among its own bodies' boundaries, since identical parts can have coincident faces (two halves end to end).
- **Solver logs** are capped at 256 MB (`jobs.runner.MAX_LOG_BYTES`) and read from the end on failure (`log_tail`).
- **Gmsh from threads:** OpenCASCADE keeps per-thread state from its first user; if that thread exits and another thread uses Gmsh, the process segfaults. `gmsh_session` first initialises Gmsh on a resident daemon thread. MCP runs sync tools on worker threads, so this matters.
- **MCP SDK 2.x:** `from mcp.server.mcpserver import MCPServer` (FastMCP was renamed); results are `structured_content`/`is_error`. Annotate tool returns as `dict[str, Any]`, not bare `dict`, or the output is unstructured text.
- **history.jsonl** is append-only and part of the project record: entries `command`, `undo`, `proposal`, `decision`, `confirmation_request`. Undo restores the logged before-snapshots and refuses if the objects changed since.
- **Material data:** only `generic.toml` ships. The maintainer's full library is generated from `Materials_Hunt_List.xlsx` by `scripts/import_materials.py` into the user library (`puzzle.materials.user_library_path()`, outside the repo) and must never be committed. Tests set `PUZZLE_MATERIALS=""` (generics only) in `tests/conftest.py`; anything tests or examples use must be generic.
