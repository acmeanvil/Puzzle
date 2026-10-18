# Puzzle

An open-source FEM/CFD pre- and post-processor that runs one model on many open-source solvers (CalculiX, Elmer, Code_Aster, FEniCSx, Code_Saturne), with the usability of commercial tools.

Status: early design. See the [architecture decision records](docs/adr/README.md).

## Try it

The cantilever example is a 200 x 10 x 10 mm steel bar fixed at one end, with three studies:

```sh
pixi shell -e dev
puzzle studies examples/cantilever
puzzle run examples/cantilever --study "Tip load"      # about 1.53 mm tip deflection
puzzle run examples/cantilever --study Modes           # first bending about 209 Hz
puzzle show examples/cantilever --study "Tip load" --field stress
puzzle show examples/cantilever --study Modes --mode 3 --screenshot mode3.png
puzzle export examples/cantilever --study Modes modes.pvd   # open in ParaView
puzzle runs examples/cantilever
```

Running an unchanged study again reuses the stored result. Results go to `examples/cantilever/results` (Delta Lake), solver files to `examples/cantilever/.puzzle/runs`.

## Working with an AI agent

Every change to a project, whether from the CLI, a script or an AI agent, goes through one command layer and is recorded in the project's `history.jsonl`, with undo (ADR 0015). Agents connect over MCP:

```sh
claude mcp add puzzle -- puzzle mcp /path/to/project --name claude-code   # Claude Code
puzzle autonomy examples/cantilever --level propose    # advise | propose | act_in_budget | act
puzzle proposals examples/cantilever                   # review what the agent proposed
puzzle accept examples/cantilever <id>                 # or: puzzle reject ...
puzzle history examples/cantilever
puzzle undo examples/cantilever
```

At the default level, `propose`, an agent's changes wait for you to accept them. `act_in_budget` lets it apply changes and run solvers within a run and time budget (`--runs`, `--solver-seconds`). Unbudgeted `act` needs two confirmations. `--mcp off` closes the project to agents.

## Layout

| Path | Purpose |
| --- | --- |
| `src/puzzle/model` | Solver-neutral model IR: resources, components, studies, transfers |
| `src/puzzle/project` | Project files (one TOML per object), content hashing, staleness, study graph |
| `src/puzzle/adapters` | Solver adapters and their capability matrices |
| `src/puzzle/geometry` | STEP import and named entities (Gmsh's OpenCASCADE kernel) |
| `src/puzzle/mesh` | Meshing recipes run by Gmsh |
| `src/puzzle/commands` | The command layer: every change, its history and undo; autonomy levels |
| `src/puzzle/agent` | The MCP server that offers a project to AI agents |
| `src/puzzle/results` | Results model, the Delta Lake / Parquet store, and summary queries |
| `src/puzzle/post` | VTK grids, viewer (`puzzle show`), ParaView export |
| `src/puzzle/gui` | PySide6 desktop GUI (planned) |
| `docs/adr` | Architecture decision records |

## Development

Puzzle uses [pixi](https://pixi.sh) for its environments on Linux, macOS and Windows (ADR 0013):

```sh
pixi run -e dev check      # lint, format check, tests incl. a native CalculiX run
pixi shell -e dev          # a shell with Puzzle installed and the solver envs on PUZZLE_SOLVER_PATH
pixi install -e fenicsx    # add another native solver (also: code-aster on Linux/Windows)
```

Solvers without conda-forge packages run in containers; see [containers/](containers/README.md).

## License

Copyright (C) 2026 acmeanvil

Puzzle is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version. See [LICENSE](LICENSE) for the full text.

SPDX-License-Identifier: GPL-3.0-or-later
