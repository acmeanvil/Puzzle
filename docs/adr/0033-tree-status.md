# 0033. The tree says what is missing, stale or done

- Status: Accepted (2026-10-23)
- Date: 2026-10-23

## Context

Problems are listed today in the Problems tab, and objects with a problem get a "⚠" after their name. A run's results don't say if the model changed since they were made. In PrePoMax, invalid items are red in the tree and geometry problems show a warning triangle (review item 5). In Ansys, every tree item carries a state icon: complete, missing input, needs update, solved, failed. Puzzle already knows these states:

- `Project.problems()` and material completeness give what is missing.
- Run keys and content hashes (`project.graph`) tell whether a stored run still matches the model.

## Decision

1. **Every tree item has a state**, shown as a small icon before its name and explained in its tooltip:
   - complete;
   - missing input (with what is missing, from the problem's message);
   - not used (e.g. a suppressed step, an unassigned material);
   - and for studies and results: up to date, out of date (the model changed since the run), running, or failed.
2. **States roll up.** A part or component shows the worst state below it, so a collapsed tree still points to the problem.
3. **The Problems tab links both ways.** Double-clicking a problem selects its item; selecting an item filters Problems to it.
4. **Out-of-date results stay viewable, marked.** The results bar shows "Out of date: the model changed since this run" with a Run button.
5. **The state comes from one function,** `project.status(project, store)` → {object or path: state, reason}. The tree, the outline (for agents) and the CLI (`puzzle outline`) all use it.

## Consequences

- Users see at a glance why a study won't run, without opening Problems.
- Staleness needs a run key per study at refresh time. `run_hashes` is cheap but walks the project, so it is computed once per refresh and cached by the project's content hash.
- Icons come from the same set as ADR 0031.

## Alternatives considered

- Red text for invalid items (PrePoMax): it tells less than a state icon and is harder to read for colour-blind users.
- Only the Problems tab: problems stay one click away from where the user is working.
