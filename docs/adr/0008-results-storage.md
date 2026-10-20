# 0008. Results in Delta Lake, one array per field and step; VTKHDF for export

- Status: Accepted (format 2, after the benchmark below)
- Date: 2026-10-17, decided 2026-10-20

## Context

Solvers write results in many native formats (`.frd`, `.vtu`, `.rmed`, Code_Saturne EnSight or MED output, FEniCSx VTX/XDMF). Puzzle wants smaller results on disk, faster reads, safe writes, run history, and queries across runs (sweeps, design studies, surrogate training).

Delta Lake (Parquet plus a transaction log) gives ACID commits, time travel, schema evolution and fast columnar reads, and `delta-rs` needs no JVM. But ParaView cannot read Delta tables, mesh connectivity fits tables poorly, and HDF5-based formats such as VTKHDF compress about as well.

## Decision

- One Delta Lake store holds both the catalog and the fields. Its tables:
  - `runs`: run id, run key, study, solver, runtime, mesh id, status, timestamps.
  - `meshes`, `nodes`, `cells`, `groups`: stored once per mesh, not per run. `nodes` is one row per mesh (node ids and coordinates as arrays); `cells` is one row per element block (element ids and connectivity as arrays).
  - `fields`: one row per field per result step, partitioned by run and field. Entity ids and values (flattened entities x components) are Parquet list columns. A field's ids are stored again only when they change (`ids_from` names the row that holds them). Each row is its own Parquet row group, so a step's read touches only its pages.
- `store.json` records the format (2). A store in another format is refused with code `stale_store`; results are a cache keyed by run key, so the remedy is to delete it and rerun.
- VTKHDF and `.vtu`/`.pvd` are export formats (`puzzle export`), for ParaView. No view cache: the store reads as fast.
- Native solver files are kept until import is verified, then optionally deleted.

## Consequences

- Cross-run queries in DuckDB or Polars, and run history through Delta time travel.
- Results are keyed by input hash, so unchanged studies are cache hits (ADR 0009).
- Long transients create many small commits; schedule compaction (`OPTIMIZE`) and checkpointing.
- Long transients add one row per field per step, not one per node, so commits and files stay few.
- Arrays come back as read-only numpy views of Arrow buffers; copy before changing them in place.

## First implementation and measurement (2026-10-17)

`puzzle.results.ResultStore` implements the tables above (plus `meshes` and `groups`), zstd-compressed, with field components in wide columns `c0`…`c8`. A run's `runs` row is committed last, so a crash never lists a half-written run; `run_study` reuses a stored run whose run key matches.

One CalculiX static run, 146,292 nodes (second-order tets), on an Intel Mac:

| Format | Size | Notes |
| --- | --- | --- |
| CalculiX `.frd` (ASCII) | 56.3 MB | displacement, stress, strain + mesh |
| Delta store, Snappy | 32.2 MB | same content |
| Delta store, zstd level 3 | 26.2 MB | fields 18.5 MB, nodes 3.8, cells 3.2, groups 0.7 |

Reading every field back takes about 0.36 s.

## Benchmark and decision (2026-10-20)

`scripts/bench_results.py`: a structured hex mesh, smooth displacement (3) and stress (6) nodal fields per step. First frame = open the mesh and one field at the last step; full read = every field at every step. VTKHDF written by VTK 9.7 (deflate level 4) with the mesh stored once per file.

| Nodes, steps | Store | Size (MB) | Write (s) | First frame (s) | Full read (s) |
| --- | --- | --- | --- | --- | --- |
| 97,336, 100 | Delta, one row per entity (format 1) | 65.4 | 16.5 | 0.90 | 19.0 |
| 97,336, 100 | Delta, one array per field and step (format 2) | 49.0 | 2.3 | 0.09–0.18 | 0.71 |
| 97,336, 100 | VTKHDF | 55.9 | 4.4 | 0.03 | 1.02 |
| 1,000,000, 10 | Delta, format 1 | 65.0 | 13.0 | 1.40 | 6.43 |
| 1,000,000, 10 | Delta, format 2 | 68.4 | 4.8 | 0.45 | 1.58 |
| 1,000,000, 10 | VTKHDF | 77.9 | 4.9 | 0.36 | 1.21 |

Format 1's cost was the layout: one row per node per step, with repeated strings to decode, then sorting and slicing millions of rows. Reading only its numeric columns already cut the 100-step full read to 4 s, but opening stayed near 0.8 s. Format 2 brings opening to within 0.1 s of VTKHDF (the mesh read, 0.14 s at 1M nodes, is most of it), reads whole transients faster, and is smaller, while keeping cross-run queries, transactional commits and one mesh shared by many runs. Splitting fields into per-run VTKHDF files was therefore not needed.

Tried and not used: binary blobs per row (Parquet stored min/max statistics of each blob, tripling the size), float32 values (half the size, but results would lose digits), byte-stream-split encoding (the Delta writer ignored it for list columns; worth retrying when it applies it).

## Alternatives considered

- VTKHDF only: excellent for viewing, weak for cross-run analytics and transactional safety.
- Delta catalog plus a VTKHDF file per run for fields: the planned fallback; format 2 matched it on viewing without a second format, repeated meshes or VTK as a write dependency.
- Native files only: no compression gain, no common query path.
- HDF5 or ADIOS2 with a custom schema: capable, but no transaction log and a less familiar query story.
