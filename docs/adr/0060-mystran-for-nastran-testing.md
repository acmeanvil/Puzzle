# 0060. Run MYSTRAN to test the Nastran export, and perhaps as a solver

- Status: Proposed; further research (after the first release)
- Date: 2026-11-01

## Context

ADR 0043 writes Nastran bulk data (`.bdf`, large field) and punch results (`.pch`).
Neither was checked against a Nastran: the mesh, properties and sets were read back by
our own reader only, and the punch stress records follow the OES1 layout from the
manuals, unverified. A user taking the deck into MSC or Simcenter Nastran would be the
first to find a mistake.

MYSTRAN is an open-source finite element solver that reads Nastran input decks
(executive control, case control and bulk data) and writes Nastran-style output (F06,
and per its current README OP2). It was released by its author as open source and is
now kept at github.com/MystranSolver; its licence (MIT is reported) and its OP2 support
are to be confirmed. It is not on conda-forge as far as we found, and it is Fortran,
built with CMake.

## Decision

Research first; build after the first release:

1. **A test harness:** wrap a written `.bdf` in executive and case control (SOL 101
   static, SOL 103 modes; a load and SPCs written for the test), run MYSTRAN, and compare
   its displacements, frequencies and stresses with the same model solved by CalculiX
   and with the punch Puzzle wrote. This is what verifies the export: the cards, the
   element node order, the sets, and the punch layout (or, where MYSTRAN writes no
   punch, the F06/OP2 values the punch should hold).
2. **Packaging:** a pixi environment or a container image (`containers/mystran`), as
   for the other solvers; a conda-forge recipe in `packaging/conda-forge/` if the build
   is clean on linux-64, osx and win-64.
3. **Then decide** whether MYSTRAN becomes an adapter (static, modal, buckling on
   solids, shells and beams; a capability matrix that claims only what benchmarks pass,
   e.g. NAFEMS LE1 and the cantilever) or stays a test tool. An adapter would make the
   Nastran deck a first-class input, not only an export.

The export's open item ("Nastran stresses unverified", ADR 0043) stays open until step
1 runs.

## Consequences

- The Nastran export gets an independent check; a mistake in a card shows up as a wrong
  answer or a MYSTRAN error, not at a user's site.
- One more solver to build and keep running; its F06/OP2 must be read (our own reader,
  or an open OP2 reader whose licence fits GPL-3.0-or-later).
- MYSTRAN implements a subset of Nastran: a pass there is evidence, not proof, for MSC
  or Simcenter Nastran.

## Alternatives considered

- **A commercial Nastran:** the reference, but not available to the project or its CI.
- **Checking the cards by hand against the manuals:** done already; it is what left the
  stresses unverified.
- **A mesh reader (Gmsh reads BDF):** it checks the grids and elements only, not
  properties, sets, loads or results.
