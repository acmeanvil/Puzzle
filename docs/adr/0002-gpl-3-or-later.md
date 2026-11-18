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
- Contributors are the maintainer and Claude for the foreseeable future, so there is no CLA or DCO process; revisit before accepting outside contributions.

## Alternatives considered

- MIT: forces Gmsh out of process and gives no copyleft protection.
- GPL-2.0-only: incompatible with Apache-2.0 dependencies.
- LGPL-3.0: possible, but Gmsh in process would make the distributed whole GPL anyway.

## Amended (2026-11-18, ADR 0090 package 16)

The Context's dependency table is the one of 2026-10-17 (ADR-E-25). Since then:
OpenFOAM (GPL-3.0, its own container), OpenCourant (AGPL-3.0, a separate program, ADR
0085), PyNite (MIT, its own environment), Netgen (LGPL-2.1, run in its own environment),
MMG (LGPL-3.0, its own environment), py-slvs (GPL-3.0, in process), manifold3d
(Apache-2.0, in process) and Intel MKL (proprietary: CalculiX with PARDISO is built on
the user's machine and never shipped, ADR 0051). The complete list, with each package's
own licence text, is gathered from the environments by `scripts/collect_licences.py`
and shown by Help → Licences (`puzzle.licences`); that list, not this table, is the one
a packager reads.
