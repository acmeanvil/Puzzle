# 0020. A material builder for the global library, complete for every solver

- Status: Accepted
- Date: 2026-10-22

## Context

Materials come from `generic.toml` (shipped) and the user's own library: the maintainer's is generated from a spreadsheet by `scripts/import_materials.py` into `puzzle.materials.user_library_path()`, outside any project. Adding one to a project copies it.

Adapters read properties by name. Today those are:

| Property | Used by |
| --- | --- |
| `density` | most physics |
| `youngs_modulus`, `poissons_ratio`, `thermal_expansion` | solid mechanics |
| `thermal_conductivity`, `specific_heat` | heat transfer |
| `dynamic_viscosity` | fluid flow |
| `relative_permittivity` | electrostatics |
| `relative_permeability`, `electrical_conductivity` | magnetics |

Temperature tables are given per property. Nothing tells a user which properties a study needs before it runs. There is no way to make a material in the GUI, and the solvers support more than Puzzle passes on: plasticity curves, orthotropic elasticity, hyperelastic models, creep, and anisotropic conductivity.

ADR 0018 (planned) makes materials sweepable choices in a study; this ADR is about making and completing the materials themselves.

## Decision (proposed)

1. **One property registry**, in `puzzle.materials.registry`, the single place that knows a property. Each entry has:
   - its key, a readable name, its dimension and default unit;
   - whether a temperature table is allowed;
   - the physics that use it.

   Adapters declare what they read through it (`Adapter.material_needs(physics, features) -> set[str]`), and the tests check that every key an adapter reads is registered.
2. **Completeness per solver.** For a material, the builder shows which studies it can serve on which solver, e.g. "CalculiX static: complete; Code_Saturne flow: needs dynamic viscosity". A study whose materials lack a needed property reports it as a problem (`incomplete_material`) before it runs.
3. **The builder**: a GUI dialog, also exposed as commands and MCP tools.
   - **Starting points:** a new material, or one copied from a library or generic material.
   - **Form:** fields are grouped by physics and generated from the registry, with units checked as typed.
   - **Temperature tables:** a two-column table, plotted beside it.
   - **Advanced models**, each a typed block that the adapters supporting it read, and that the others refuse rather than silently drop:
     - plasticity: an isotropic hardening curve;
     - orthotropic elasticity: nine constants and axes;
     - hyperelastic: Neo-Hookean or Mooney-Rivlin;
     - anisotropic conductivity.
   - **Provenance:** a material records its sources and the date it was made.
4. **Library writes are their own store.** The user library is not a project, so builder edits don't go through a project `Session`. A small `LibraryStore` writes the TOML atomically and keeps a history file beside it for undo, and agents may only propose library changes. Shipped generics are read-only; editing one saves a copy to the user library.
5. **Import and export.** CSV or XLSX in the importer's column format (the existing script becomes a library function), and export of a material to a project or to a file to share. Nothing from the maintainer's own data ever ships.

## Consequences

- "Will this material work for this study?" is answered before a run, in words.
- The registry is one more place to update when an adapter learns a property. The registry test makes forgetting fail loudly.
- Advanced models widen what each adapter must write or refuse; the capability matrices gain material-model entries (benchmarked before they are claimed, as with every capability).

## Decided (2026-10-22, maintainer)

- **Units.** SI by default. An application setting (ADR 0024) offers:
  - SI;
  - SI variants (mm-N-MPa-tonne, mm-N-MPa-s-kg/mm³ and similar);
  - the user's own choice per quantity;
  - Imperial.

  Values are stored in SI whatever is shown.
- **Variants.** The library organises materials into families with variants, such as tempers or suppliers. Aluminium 6061 has the variants O, T4 and T6, each a complete material inheriting what it doesn't override from the family. A project still copies one variant (ADR 0009).
- **Solver keywords.** Solver-specific material keywords get an expert pass-through per solver, as in ADR 0023: a Code_Aster `DEFI_MATERIAU` block, or CalculiX `*MATERIAL` cards. They are stored with the material, refused by adapters that can't place them, and recorded in each run.

## Alternatives considered

- Edit the TOML by hand: works for the maintainer, not for users, and gives no completeness check.
- A per-solver material form: mirrors the solvers but breaks the solver-neutral model (ADR 0006) and shoot-outs.
