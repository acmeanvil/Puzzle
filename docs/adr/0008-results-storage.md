# 0008. Delta Lake catalog, Parquet fields, VTKHDF view cache

- Status: Proposed (format locked after the Phase 1 benchmark)
- Date: 2026-10-17

## Context

Solvers write results in many native formats (`.frd`, `.vtu`, `.rmed`, Code_Saturne EnSight or MED output, FEniCSx VTX/XDMF). Puzzle wants smaller results on disk, faster reads, safe writes, run history, and queries across runs (sweeps, design studies, surrogate training).

Delta Lake (Parquet plus a transaction log) gives ACID commits, time travel, schema evolution and fast columnar reads, and `delta-rs` needs no JVM. But ParaView cannot read Delta tables, mesh connectivity fits tables poorly, and HDF5-based formats such as VTKHDF compress about as well.

## Decision

- The results store is a Delta Lake catalog with these tables:
  - `runs`: run id, study id, input hash, solver, version, image digest, status, timestamps.
  - `meshes`, `nodes`, `cells`: stored once per mesh, not per run; cells hold type, connectivity and region.
  - `fields`: wide layout, one row per node or cell, one column per component, a `location` tag (node, cell, integration point), partitioned by run and field, one Parquet row group per time step.
- Viewing uses a VTKHDF cache built on first open, or a ParaView reader plugin that maps Arrow buffers to VTK arrays without copying.
- Native solver files are kept until import is verified, then optionally deleted.

## Consequences

- Cross-run queries in DuckDB or Polars, and run history through Delta time travel.
- Results are keyed by input hash, so unchanged studies are cache hits (ADR 0009).
- Long transients create many small commits; schedule compaction (`OPTIMIZE`) and checkpointing.
- A Phase 1 benchmark compares Parquet+zstd, VTKHDF+zstd and native files on size, write time and time to first frame. If Delta loses badly on viewing, VTKHDF becomes the primary field store and Delta keeps only the catalog.

## Alternatives considered

- VTKHDF only: excellent for viewing, weak for cross-run analytics and transactional safety.
- Native files only: no compression gain, no common query path.
- HDF5 or ADIOS2 with a custom schema: capable, but no transaction log and a less familiar query story.
