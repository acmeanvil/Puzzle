# 0083. A complete help and documentation system, with worked examples (supersedes 0075)

- Status: Proposed
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
   with front matter, in `docs/help/`. The parts are:
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
