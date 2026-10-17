# Puzzle

An open-source FEM/CFD pre- and post-processor that runs one model on many open-source solvers (CalculiX, Elmer, Code_Aster, FEniCSx, Code_Saturne), with the usability of commercial tools.

Status: early design. See the [architecture decision records](docs/adr/README.md).

## Layout

| Path | Purpose |
| --- | --- |
| `src/puzzle/model` | Solver-neutral model IR: resources, components, studies, transfers |
| `src/puzzle/project` | Project files (one TOML per object), content hashing, staleness, study graph |
| `src/puzzle/adapters` | Solver adapters and their capability matrices |
| `src/puzzle/geometry` | OCCT geometry kernel wrapper (planned) |
| `src/puzzle/mesh` | Meshing layer (planned) |
| `src/puzzle/results` | Delta Lake / Parquet results store (planned) |
| `src/puzzle/post` | Post-processing adapters (planned) |
| `src/puzzle/gui` | PySide6 desktop GUI (planned) |
| `docs/adr` | Architecture decision records |

## Development

```sh
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

Optional extras: `geometry`, `mesh`, `results`, `gui`.

## License

Copyright (C) 2026 acmeanvil

Puzzle is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version. See [LICENSE](LICENSE) for the full text.

SPDX-License-Identifier: GPL-3.0-or-later
