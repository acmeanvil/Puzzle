# 0083. A complete help and documentation system, with worked examples (supersedes 0075)

- Status: Accepted; steps 1-3 built (2026-11-15): the Help window, search and F1, the
  generated reference and its tests, the guides; theory notes, the static site and PDF,
  and the tutorials (0075's list, on `main`) to come
- Date: 2026-11-11

## Context

Puzzle's help today is scattered:
- Help → Getting started (a page of steps);
- Keyboard shortcuts;
- Design decisions (the ADRs, written for developers);
- tooltips and Properties descriptions (`gui/form.py` `DESCRIPTIONS`);
- the operators' own help cards (ADR 0071);
- the agent's skills (ADR 0057), which are how-tos written for the agent, not people;
- two examples and the benchmarks.

A user has no manual: no page saying what a contact formulation is, how to read a
linearization, what each solver setting does, or which element suits a thick plate.

ADR 0075 planned 46 worked examples. Those belong in the same system, as its
tutorials, and are built from the same sources.

The maintainer asked (2026-11-11) for a complete help and documentation system, with
0075 merged into it.

## Decision

1. **One source, several readers.** Documentation lives in the repository as Markdown
   with front matter, in `src/puzzle/help/` (amended 2026-11-15: `docs/help/` would not
   reach an installed app, whose Help window and agent read the same pages; the package
   ships them as it ships the skills): `pages/` the written ones, `reference/` the
   generated ones. The parts are:
   - **Guides:** task by task, written for a user (make a mesh, set up contact, run a
     shoot-out, read fatigue results).
   - **Reference:** generated from the code's own data, so it can't drift from it. It
     covers:
     - every feature from `model/catalog.py`, with values, units and defaults;
     - every solver setting from each `settings_schema()` and `SHARED`;
     - every capability per solver, from the capability matrices;
     - every element, from ADR 0082's library;
     - every operator, from the operators' help (ADR 0071);
     - every material property, from `materials/registry.py`;
     - every result quantity, from `post/quantities.py`;
     - every MCP tool, from its description;
     - every CLI command, from argparse;
     - every problem code, from `errors.CODES`.
   - **Theory:** short notes on the methods behind the results, each citing its source.
     Examples: stress linearization (ASME VIII-2 5-A), GCI (Roache), the Zienkiewicz–Zhu
     estimate, VDI 2230, weld throat stress, the solvers' contact formulations.
   - **Tutorials:** the worked examples (point 4).
   - **Verification:** each benchmark, with its closed-form or published answer and what
     each solver measured, from the tests' own tolerances and the READMEs.
2. **Where people read it.**
   - In the GUI, a Help window (`gui/help_viewer.py`) with:
     - search;
     - a contents tree;
     - pages rendered with their equations (as the agent window renders TeX, ADR 0074);
     - links that open the tool they describe.
   - Context help: F1 on any panel, tree item, Properties row, menu item or operator
     opens its page. Each has a help key, and a test refuses one without a page.
   - Getting started becomes the first tutorial.
   - A static site built from the same files (an Artifact page or GitHub Pages) for
     reading outside the app, and a PDF of the whole manual for release.
   - The agent: the knowledge pack (ADR 0022) indexes `docs/help/`. MCP `help(topic)`
     returns a page, and the agent cites pages by name when it explains.
3. **Kept in step with the code.**
   - Reference pages are generated (`scripts/build_help.py`), and a test fails when a
     generated page is stale.
   - A test fails when a catalog feature, setting, operator, element, problem code or
     MCP tool has no reference entry.
   - Finishing an ADR includes its help: a guide or a reference section next to its agent
     skill (ADR 0057). `WITH_SKILLS` gains a twin list, `WITH_HELP`.
4. **Tutorials: ADR 0075's worked examples, kept as planned.**
   - What they cover:
     - every physics and solver;
     - a single component and a small assembly each;
     - the 23 rows of 0075's table, about 46 projects.
   - What each one is:
     - a project made by its own `build.py`, with stored results;
     - a test checking its README's numbers;
     - a README in one shape: the question, the model, the GUI steps, the answer and how
       close it is, and what to try next.
   - How they reach people: the README becomes the tutorial's page, with the GUI steps
     linked to the tools. They are packaged as the optional `puzzle-examples` installer
     component (ADR 0068), and Help → Examples opens a copy.
   - They are built once the schema is stable and on `main`, as 0075 said. The help
     system itself doesn't wait.
5. **Order of work.**
   1. The help window, search and F1 on what exists today (skills adapted for people,
      `DESCRIPTIONS`, operator help).
   2. The generated reference, and the tests that keep it whole.
   3. Guides for the main workflows: geometry to mesh, materials, loads and supports,
      contact, bolts and welds, studies and solvers, results and result tools,
      optimisation, the schematic, the agent.
   4. Theory notes.
   5. The static site and the PDF.
   6. Tutorials (0075), on `main`.
6. **Writing.** Plain words, for an engineer who isn't an expert in FE, with the expert's
   detail one click below. This is the overarching principle in CLAUDE.md: every feature
   explainable, by the help and by the agent alike.

**Built (2026-11-15), steps 1-3**:

- `puzzle/help/` (`pages()`, `parse`, `lookup`, `contents`, `search`, `answer`): pages
  with front matter (title, section, order, `keys`, summary, `adrs`); a line
  `<!-- key: ... -->` before a heading gives that heading its help keys, so F1 lands on
  the entry; a key falls back to a more general one (`setting:calculix:x` →
  `setting:calculix`); search is BM25 over every heading's passage.
- The reference (`help/reference.py`, `scripts/build_help.py`): eleven pages generated
  from the code: features (with the solvers that write each), solver settings,
  capabilities, elements, operators, material properties and models, result
  quantities, Properties fields (`DESCRIPTIONS`), problem codes, MCP tools, CLI
  commands. A test fails when one is stale; another when a feature, setting, solver,
  operator, property, model, result, code, MCP tool or CLI command has no entry.
- Guides: getting started and seventeen guides (the window, geometry, materials,
  meshing, components, loads and supports, contact, bolts and welds, beams shells and
  frames, analyses, studies and solvers, results, imported fields, schematic and
  operators, optimisation and convergence, the agent and Advisor, versions and
  projects); their `adrs` are `WITH_HELP` in `tests/test_help.py`.
- The Help window (`gui/help_viewer.py`: contents, search, Back, pages with their TeX as
  the agent window draws it, `help:` and `puzzle:` links); F1 anywhere
  (`help_viewer.help_key`: a widget's `help` property, a Properties row's field, a tree
  item's kind, feature or operator, its panel); Help → Help and Getting started open it.
  A test asks every panel, tab, tree section, object kind, tree item kind and described
  Properties field for a page, and resolves every `help:` link.
- The agent: MCP `help(topic)` (contents, a page, a key, or a search), the knowledge pack
  indexes the pages (`help <page>`), skill `help-pages`; `puzzle help [topic]`.
- Found on the way: 20 codes Puzzle raises were missing from `errors.CODES` (the schema's
  problems, the contact checks), so nothing could explain them; they are listed, and a
  test scans the source for codes used and not listed. The catalog said line loads and
  node springs act on faces, so adding one to a frame offered no member edges; they ask
  for edges and points. Two status messages and the coordinate-systems skill named
  menus that aren't there.

**Built (2026-11-15), step 4**: nineteen theory notes in `help/pages/theory/` (stress
results and combinations, linearization, mesh convergence, error estimates, bolted
joints, thread loads, weld strength, fatigue, structural dynamics, time integration,
explicit dynamics, buckling, contact, symmetry, electromagnetics, flow, frame design,
seismic design, optimisation), each from the code that computes it, with its limits and
sources; each guide's See also names its notes. Tests: every theory note ends in
Sources, carries only `theory:` keys (so F1 stays on the reference) and is linked from a
guide; every equation in the manual parses in mathtext. Writing them found three bugs
(2D mesh convergence, adaptive passes on 2D meshes and named fields) and stale text,
fixed, and the rest listed in ADR 0072.

## Consequences

- Users get a manual that matches the build they run, and the agent explains from the
  same pages a user reads.
- Every new feature carries a help page, as it carries a skill. That is more work per
  feature, and the tests hold it to that.
- Generated reference adds a build step, and its staleness test can fail a change that
  only renamed a label. That is deliberate.
- 0075 is superseded; its list and its "after the design is stable" timing stand here.

## Alternatives considered

- **A wiki or an outside documentation site only:** it drifts from the code, and neither
  the GUI nor the agent can read it offline.
- **Agent skills as the user manual:** they are written for the agent's tools and
  arguments, not a person's clicks; the two share facts, not text.
- **Sphinx with autodoc over the Python API:** it documents code, not use. The reference
  here comes from the model's data (catalog, settings, capabilities), which is what users
  meet.
