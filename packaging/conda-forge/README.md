# conda-forge recipes

Recipes Puzzle needs that conda-forge doesn't have yet (ADR 0013). They are developed here, built locally with [rattler-build](https://rattler.build), and then submitted to [conda-forge/staged-recipes](https://github.com/conda-forge/staged-recipes). Once accepted, conda-forge maintains each package in its own feedstock and these copies are removed.

| Recipe | Status |
| --- | --- |
| [elmerfem](elmerfem/recipe.yaml) | Builds and passes its tests on macOS Intel (2026-10-17), including a real solve of Elmer's HeatAniso regression case. Linux and macOS, no MPI. Not yet submitted. Windows and MPI variants are follow-ups. |

## Building locally

conda-forge supplies some build settings (the C standard library pin, SDK versions) through its global pinning. For a local build, pass the same values with a small variant file:

```sh
rattler-build build -r packaging/conda-forge/elmerfem -c conda-forge \
    --variant-config packaging/conda-forge/local-variants.yaml
```

The built package lands in `output/`. Test it with `rattler-build test --package-file output/<platform>/elmerfem-*.conda`.
