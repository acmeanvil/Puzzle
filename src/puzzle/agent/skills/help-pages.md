---
name: help-pages
description: explaining Puzzle to a user from its manual: the help tool's contents, pages, help keys and search, and pointing the user at the page they can read themselves
adr: 0083
---
Read this when a user asks how to do something in Puzzle, what an option or result
means, or why something was refused, and the answer should be one they can read again.
Puzzle's manual is the same set of pages the user opens with Help → Help or F1, so an
answer from it matches what they see.

## Steps

1. `help(topic)`:
   - no `topic`: the contents, by section (Getting started, Guides, Reference, Theory,
     Tutorials, Verification), each page's name, title and summary;
   - a page name (`guides/meshing`, `reference/settings`): that page;
   - a help key: the entry it names, from its heading to the next. Keys are
     `feature:<type>` (`feature:pressure`), `setting:<solver>:<key>` and
     `setting:shared:<key>`, `solver:<name>`, `operator:<kind>`, `property:<key>`,
     `model:<key>`, `result:<field>`, `field:<name>` (a Properties row),
     `problem:<code>`, `mcp:<tool>`, `cli:<command>`, `kind:<object kind>`,
     `panel:<panel>`, `theory:<name>` (the method behind a result: `theory:linearization`,
     `theory:mesh-convergence`, `theory:bolted-joints`, `theory:contact`...),
     `verification:<page>` (benchmarks and how close each solver came);
   - other words: the passages that answer them best (page, heading, an excerpt);
     then ask for the page you need.
2. Answer in your own words for this user's case, then name the page by its title so
   they can open it ("see the Meshing guide, under Mistakes to avoid"). Don't paste a
   whole page back.
3. Reference pages are generated from the code (every feature, setting, element,
   operator, property, result, problem code, agent tool and CLI command), so they are
   right for this build. Guides are written by people: when one disagrees with the
   capability matrix or a tool's answer, trust the tool and say the guide is out of
   date.

4. When a user asks why a number is what it is (how a GCI, a utilisation or a thread
   share is computed), read the theory note: it names the equations Puzzle uses, its
   limits and its sources, and cite those sources rather than your own memory.
5. When a user asks whether a result can be trusted, or how well a solver does on a
   problem, read the verification page for its area (`verification:index` lists them:
   `verification:structural`, `verification:contact-and-joints`, ...): each benchmark's
   reference answer, the solvers checked, the test's tolerance and what was measured.
   Quote the measured value only when the page gives one; "passes" means within the
   tolerance and nothing more.
6. Pages carry pictures the user sees and you get as a line of text:
   `![what it shows](image:...)`. Every menu location a page names comes with a picture
   of that menu opened, each operator on the Operators page with its node, and the
   guides with the schematic, the Advisor and the agent window. So "the Agent guide
   shows the window" is something to point a user at; describe the picture from its
   alt text, never from a guess at the screen.

## Writing or changing a page

- A menu location in words ("Model → Studies → Load combinations…") gets its picture
  by itself, but only when the path is the menu bar's exactly (its ellipses too).
  `tests/test_help_images.py` fails on a path the bar doesn't have.
- A screen goes in as `![what it shows, in a sentence](image:screens/<name>.png)`,
  drawn by `scripts/build_help_images.py` (add it there).
- After changing a menu, an operator or a page that names a menu location, run
  `pixi run -e dev python scripts/build_help_images.py`, then `scripts/build_help.py`.

## Mistakes to avoid

- Telling a user a menu path from memory: take it from the guide, which names the
  menus as the build has them.
- Explaining a refusal without its code: look the code up (`help("problem:<code>")`)
  and give the user what to change.
- Searching with the user's whole sentence when they named the thing: a help key
  (`feature:remote_force`) goes straight to it.
