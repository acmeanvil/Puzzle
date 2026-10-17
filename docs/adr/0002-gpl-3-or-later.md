# 0002. License Puzzle under GPL-3.0-or-later

- Status: Accepted
- Date: 2026-10-17

## Context

Puzzle links in-process with libraries under several licences and drives GPL solvers:

| Dependency | Licence | Use |
| --- | --- | --- |
| deltalake (delta-rs), pyarrow | Apache-2.0 | In process |
| Gmsh | GPL-2.0-or-later | In process (Python API) |
| OCCT / OCP | LGPL-2.1 with exception / Apache-2.0 | In process |
| PySide6 | LGPL-3.0 | In process |
| VTK, ParaView, pyvista | BSD-3-Clause / MIT | In process |
| CalculiX | GPL-2.0 | Separate process |
| Elmer | LGPL-2.1 solver library and some modules; GPL for ElmerGrid and most modules | Separate process |
| Code_Aster, Code_Saturne | GPL | Separate process |
| FEniCSx (dolfinx) | LGPL-3.0 | Generated script, separate process |

The repository started under MIT, which cannot be distributed together with in-process Gmsh without the combination falling under the GPL.

## Decision

License Puzzle under GPL-3.0-or-later. Solvers are always invoked as separate processes (ADR 0007).

## Consequences

- Gmsh can be linked through its Python API.
- Apache-2.0 libraries remain compatible (they are incompatible with GPL-2.0-only, which is why version 3 is required).
- Anyone distributing Puzzle or a derivative must provide source under the same licence.
- A contribution policy (DCO or CLA) is still to be decided.

## Alternatives considered

- MIT: forces Gmsh out of process and gives no copyleft protection.
- GPL-2.0-only: incompatible with Apache-2.0 dependencies.
- LGPL-3.0: possible, but Gmsh in process would make the distributed whole GPL anyway.
