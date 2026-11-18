# 0083. A complete help and documentation system, with worked examples (supersedes 0075)

- Status: Accepted; steps 1-5 built (2026-11-15, 16): the Help window, search and F1,
  the generated reference and its tests, the guides, theory notes, verification pages,
  the static site and the PDF (linked, bookmarked, attached to each release), pictures;
  the tutorials (0075's list, on `main`) to come
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
   - Pictures (amended 2026-11-16, the maintainer: "include images in the manual where
     appropriate, especially in the Operators and anytime there is a reference to a
     location in a menu, the Advisor, and the agent window"): screenshots of the real
     GUI, drawn by a script, never by hand, so they follow the app. Every menu location
     a page names gets its picture, placed by itself; a test fails when a page names a
     menu location the menu bar doesn't have, or a picture is stale or missing.
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

**Built (2026-11-15), the verification pages** (decision 1): `help/pages/verification/`,
an overview (what a benchmark is, how to run one, what a skip means) and eight pages:
structural statics; dynamics, buckling and explicit; heat and transfers;
electromagnetics; contact, bolts, threads and welds; flow; frames and design codes;
meshing, convergence and studies. Each benchmark has a row (reference, solvers, the
test's tolerance, what was measured) and a section naming its test, with measured values
only where a README, an ADR, CLAUDE.md or the test records them ("passes" otherwise),
and the standards' worked examples among the unit tests. A test fails when an
integration test is on no verification page and not listed as plumbing
(`NOT_BENCHMARKS` in `tests/test_help.py`). Writing them found a wrong hand value (the
optimisation benchmark's 7.37 mm was for E = 200 GPa; the generic steel's 210 GPa gives
7.25 mm, now computed in the test and held to 1 % instead of 4 %) and stale docstrings
and records, fixed; the rest is on ADR 0072.

**Built (2026-11-15), step 5, the static site and the PDF:** `help/site.py`, run by
`scripts/build_help_site.py OUT [--pdf FILE]` (15 s for both). The site: a page per
help page with the contents beside it, `index.html`, search in the browser over every
heading (`search.js`), light and dark, phone width; equations drawn by mathtext as
inline SVG, as the Help window draws them; `help:` links to the page and heading (a
help key through `help.lookup`), `puzzle:` links as plain text, since a browser has no
tool to open. The PDF: every page in the contents' order after a cover and contents
list, equations as PNG at text size, printed by Qt (`QTextDocument`, `QPdfWriter`, A4)
in matplotlib's DejaVu fonts, since Qt's offscreen platform finds none of its own (306
pages). Markdown by markdown-it-py, which `mcp` brings in. `tests/test_help_site.py`:
every page written, every link and search entry landing on a file and an anchor that
exist, no TeX or `help:` left, the PDF whole. Where the site is published (an Artifact
page or GitHub Pages) is the maintainer's to choose.

**Built (2026-11-16), the PDF's links, its release job and pictures:**

- Links in the PDF: `help:` links and the contents list jump to their page and heading.
  Qt writes a named destination for each `<a name>` (`page` or `page~heading`) and a
  link for each `#` href. It writes only the first name in a run of text, so each help
  key's anchor holds a zero-width space of its own. Bookmarks (sections, pages, their
  second-level headings) are appended as an incremental update (`site.add_outline`:
  new objects, a catalog naming them, an xref section and a trailer), since Qt has no
  outline API. A link that resolves nowhere fails the build. The 516 links and 526
  bookmarks were checked with pypdf (strict) and PyMuPDF.
- `.github/workflows/manual.yml`: on a published release (and by hand), the PDF built on
  Linux and attached to the release as `Puzzle-manual-<tag>.pdf`.
- Pictures (decision 2, amended): `help/pictures.py` (no Qt) and
  `scripts/build_help_images.py`, which runs offscreen in matplotlib's DejaVu fonts and
  the light theme on a scratch copy of the cantilever, so every machine draws the same.
  - Menu locations: the pages' words are matched against the menu bar's own paths
    (`main_menu.paths`, across wrapped lines). Each one named gets the menu opened
    from the bar with the item highlighted (115 pictures, `images/index.json`), put
    after the paragraph, list or table that first names it on a page. A chain that
    starts at a menu bar title and is no real path is refused; one going on past an
    item that opens a dialog (Project → Settings… → Units) is a place in that dialog.
    Writing it found one wrong location (Geometry → Pick filter's items), fixed.
  - Every operator's node, as the schematic draws it, on the Operators reference page
    (86).
  - Screens: the schematic, a Result summary and Check against limit chain in it, the
    operator palette, the Advisor, and the agent window with a conversation and a
    proposal. A page names one as `![what it shows](image:screens/advisor.png)`.
  - The Help window, the site and the PDF all show them, no wider than the PDF's text.
  - `tests/test_help_images.py`: every picture named is drawn, none is left over, every
    operator has one, and the menu locations the pages name are real and drawn.

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

## Amended (2026-11-18, ADR 0090 package 16)

Built since (ADR-E-1, ADR-E-2):

- Links that open a tool: in the Help window each menu location a page names gets an
  "Open …" link under its picture (`pictures.with_pictures(links=True)`), `puzzle:menu/<slug>`,
  which runs that menu item (`help_viewer.open_tool`, `main_menu.action_for`; a submenu
  opens where the pointer is). The site and the PDF leave them out. A test resolves
  every named location and every `puzzle:` link written in a page.
- F1 on a menu item: while a menu is open Qt gives F1 to the menu, so an application
  event filter reads its highlighted item's path and opens the guide that names it, at
  the heading above the first mention (`help.menu_place`).
- F1 on the schematic: with one node selected, its entry opens (`operator:<type>`,
  `feature:<type>`, `kind:<kind>`) before the panel's page.

Tests: `tests/test_help_links.py`. Still as the Built text says, not the Decision's
stronger words (ADR-E-3, ADR-E-4, ADR-E-5): pictures are checked for being named and
present, not for matching what the GUI draws now; elements are held by the reference
page's staleness test, with no key per element; the knowledge pack indexes
`src/puzzle/help/`, not `docs/help/`.
