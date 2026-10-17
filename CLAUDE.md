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

Solver tests in `tests/integration/` skip when the solver isn't available. Rendering tests run only with `PUZZLE_RENDER_TESTS=1`; check renders by looking at the PNG (`puzzle show ... --screenshot`). Container runs: `PUZZLE_RUNTIME=docker pytest tests/integration` after building images in `containers/`.

## Layout

| Path | What |
| --- | --- |
| `src/puzzle/model` | Pydantic schema: resources, components (parts, contacts, physics), studies (sweep/solve steps, shoot-out), transfers |
| `src/puzzle/project` | One-TOML-file-per-object store; content hashes, staleness, run keys |
| `src/puzzle/materials` | Default library: `library.json` (generated, don't hand-edit) + `supplement.toml` (fluids) |
| `src/puzzle/geometry` | STEP import via Gmsh's OCC kernel, named entities with signatures |
| `src/puzzle/mesh` | Recipe → Gmsh mesh with physical groups `Part/Selection`; solver-neutral `Mesh` |
| `src/puzzle/adapters` | One adapter per solver: capability matrix, `write_input`, `command`, `read_results` |
| `src/puzzle/jobs` | Runs solvers natively or in Docker/Podman; runtime chosen per solver |
| `src/puzzle/results` | Solver-neutral `Results`; `ResultStore` = Delta Lake tables (runs, meshes, nodes, cells, groups, fields) |
| `src/puzzle/solve.py` | `run_study`: validate → mesh → write → run → read |
| `src/puzzle/post` | VTK grids (tet10 reordered for VTK), `render` (window or PNG), `.vtu`/`.pvd` export, `sample` (results at a point) |
| `src/puzzle/cli.py` | `puzzle studies/run/runs/show/export PROJECT` |
| `examples/cantilever` | Example project; regenerate with `python examples/cantilever/build.py` |
| `benchmarks/` | Published benchmarks as projects (NAFEMS LE1); each has build.py, a README with measured results, and an integration test |
| `containers/`, `packaging/conda-forge/` | Solver images; conda-forge recipes (elmerfem) |

## Adapter status

| Solver | Implemented | Verified by (`tests/integration/test_benchmarks.py`, `test_nafems_le1.py`) | Runs |
| --- | --- | --- | --- |
| CalculiX | static, modal, steady heat; fixed, displacement, force, pressure, temperature, heat flux, convection | cantilever, modal, conduction, LE1 | native (pixi `calculix`) or Docker |
| Elmer | one solve step: static, modal (solid) or steady heat; own mesh files, ASCII .vtu output | same set; agrees with CalculiX to 4 digits, LE1 within 0.13% | Docker image (not on conda-forge yet) |
| Code_Aster | one solve step: static, modal (solid) or steady heat; MED mesh via Gmsh with safe group names (G1, G2, ...); results as IMPR_TABLE CSVs | same set; LE1 +0.01% | native on linux-64/win-64 (pixi `code-aster`), Docker on macOS |
| FEniCSx, Code_Saturne | capability matrix and stubs only | — | — |

Capability matrices also list planned capabilities; `write_input` refuses what isn't implemented yet. Run Elmer tests locally with Docker on PATH (`/Applications/Docker.app/Contents/Resources/bin`) after building `containers/elmer`.

## Conventions

- Everything is SI internally (m, kg, s, N, Pa, K). Imported geometry is converted to metres; `Quantity.to_si()` converts user values. Never pass units implicitly to a solver.
- Adapters only claim a capability once a benchmark for it passes. Elmer's mechanical contact is bonding only (mortar tie); its slide/friction/stick are `experimental`.
- Tests compare against closed-form or published answers with stated tolerances, not snapshots.
- Match the surrounding code: dataclasses/Pydantic, type hints, short docstrings that say why.

## Gotchas

- **Gmsh is global state.** Always use `puzzle.geometry.gmsh_session()`; it holds a lock and sets OCC import units to metres. Don't nest sessions.
- **OCC pads bounding boxes** by ~1e-7 m. Plane tests need a tolerance of at least 1 µm.
- **Entity names** (`face-3`) follow STEP file order; signatures (kind, measure, centre) re-find them after placement or fragmenting.
- **Element node order:** Gmsh differs from CalculiX, Elmer and VTK for tet10 (last two edge nodes swap). Adapters own the reordering; shared face lookups live in `adapters/common.py`.
- **Code_Aster 18** needs `INCREMENT` even for steady `THER_LINEAIRE`; table numbers need `FORMAT_R` (default keeps 5 digits). Containers run as your uid with `HOME=/tmp` (run_aster needs a home).
- **Elmer output** lands in the mesh directory (`mesh/case_t0001.vtu`). Points come in Elmer node order (no point ids); `GeometryIds` there is cell data. Eigenvalues (omega^2) are parsed from the solver log.
- **CalculiX `.frd`** is fixed width (values can touch); match record tags on the first token. Modal block values are frequencies in Hz.
- **Native solver runs** must look activated: `NativeRuntime.environment()` puts the conda/pixi env on PATH and sets `CONDA_PREFIX` (Windows otherwise fails with 0xC0000135). Elmer from conda also needs `ELMER_HOME` = env prefix.
- **Elmer image** is built without MUMPS (Debian trixie lacks ParMETIS).
- **Material data** comes from the maintainer's `Materials_Hunt_List.xlsx` (shared with the Tension_Meter app). Regenerate with the import script; never edit `library.json` by hand.
