---
name: versions
description: project versions to roll back to: listing and filtering them, saving one before a risky batch of changes, restoring one or the last known good, and what a restore does to undo and results
adr: 0059
---
A **version** is a snapshot of the project's own files (its objects, geometry files,
imported fields), numbered from 1, in the project's `versions/` folder. Results are
not in it: they are a cache by run key, so a restored state finds its runs again while
the store still has them. There is no Save in Puzzle: every change is written when it
is made, so versions are the way back over many changes.

Puzzle makes versions itself, only when something changed since the latest:

- `first` / `opened`: before the first change of a session, when the project has none
  yet or was changed outside Puzzle;
- `before_run`: the state a study is solved from; a successful run marks it **known
  good** ("solved Static");
- `edits`: after 20 changes or 15 minutes (Settings > Versions);
- `before_restore`: the state a restore replaces;
- `copied`: the first version of a project made by Save as New Project.

Users (and agents) save their own: `user`.

## Look at them

`versions_list(limit=20, by=None, types=None, since=None, until=None, good=None,
text="")`: newest first, each with `number`, `time` (UTC), `by` (You, Puzzle, Agent),
`what` (its label, or why it was made), `good` and `good_reason`, `note`, and a
`summary`: `counts` (geometry, components, materials, meshes, studies), `studies`
(each one's solver and state then: up to date, out of date, not run), `problems`,
`last_command`, and `changes` since the version before (`added`, `changed`, `removed`,
by object name or file). Filters: `by` = `you`, `puzzle` or `agent`; `types` = a list of
the triggers above; `since`/`until` = ISO dates or times; `good=True`; `text` = words
in the label, note, studies or changes. `matching` and `count` say how many passed.

## Save one

`version_save(label="Before swapping the contact to frictional", note="...",
good=False)` before a batch of changes you might want to take back as a whole. A label
says what the state is, not what you are about to do to it.

## Restore one

`version_restore(version="12")`, or `version="good"` for the latest known good. It:

1. keeps the project as it is as a `before_restore` version;
2. writes the version's files back (files added since are removed; results stay);
3. logs one history entry, so `undo` takes the restore back (files too).

Agents restore only when the autonomy level is "act" or "act in budget"; otherwise the
call is refused (`not_allowed`): tell the user which version and why, and let them.

## Mistakes

- Restoring to undo one small change: use `undo`; a restore replaces everything.
- Expecting results from a restored state that were deleted since: solve again.
- Saving a version after every command: Puzzle already keeps them; save one at a
  point worth naming.
- Reading `time` as local: it is UTC.
