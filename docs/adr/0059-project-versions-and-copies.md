# 0059. Keep project versions to roll back to, and copy projects whole

- Status: Accepted; built 2026-11-01
- Date: 2026-11-01

## Context

A project has no Save: every change is written when it is made, through the session
(ADR 0015), and `history.jsonl` keeps each command with the objects' state before and
after, which is what linear undo works from. That serves small steps back. It does not
serve "take me back to where this worked", over many commands, other clients' changes
and replaced geometry files, and undo stops at the first object changed since.

The maintainer asked for:

- versions made both by Puzzle (implicitly) and by the user (explicitly);
- a version browser that shows for each one a brief description of the project's
  condition at that point, the date and time, a version number and so on;
- rolling back to a "last known good" state, alongside linear undo;
- Save as New Project: a full copy of the open project, with imported fields, meshes
  and geometry, and no connection to the original afterwards.

## Decision

### Versions

A **version** is a snapshot of the project's own files, numbered from 1, kept in the
project's `versions/` folder (so it travels with the project, a copy and git):

- `versions/<n>.json`: the manifest: number, time (UTC), who (you, an agent, or Puzzle),
  how (`user` or `auto`) and why (the trigger), a label and a note, whether it is
  **known good**, the last history entry it follows, every file's hash, and a summary
  of the project then (below);
- `versions/blobs/<hash>`: each file's content, zlib-compressed, stored once however
  many versions hold it.

Snapshotted: `project.toml`, every object's TOML, and every other file in the folder
(geometry sources, imported fields, `imported/`, notes). Left out: `results/` (a cache
by run key, so a restored state finds its runs again while they are kept), `.puzzle/`
(working files, the lock), `versions/` itself and `history.jsonl` (append-only; it
records the restore instead). Hashes are cached by size and modification time in
`versions/cache.json`, so a large STEP file is read once until it changes.

**Implicit versions** (`auto`), made by the session for every client (GUI, CLI, MCP,
the built-in agent), and only when something changed since the latest version:

- **opened**: when a session opens a project that changed outside Puzzle (or has no
  version yet);
- **before a run**: the state a study is solved from; when the run succeeds it is
  marked **known good** ("solved Static"); a failed run notes that it failed;
- **edits**: after a number of commands (default 20) or minutes (default 15) since the
  latest version;
- **before restoring**: the state a restore replaces, so a restore is always reversible;
- **copied**: the first version of a project made by Save as New Project.

**Explicit versions** (`user`): Save version… (Project menu, ⌘/Ctrl+S, since there is
no Save to take that key), with a label and a note, optionally marked known good; any
version can be marked or unmarked as known good later.

**The summary** stored with each version, for the browser and the agent: the counts of
geometry, components, materials, meshes and studies; each study's solver and state
(ADR 0033: up to date, out of date, not run, failed); the number of problems; the last
command before it; and what changed since the previous version, by object name
(added, changed, removed) and by file.

**Restoring** a version is a session command (`restore_version`): it first makes a
"before restoring" version, then writes the version's files back (objects and their
files; files added since are removed, results are kept), reloads the project and logs
one history entry with the objects' before and after state. So:

- linear undo undoes a restore (the files too, from the "before restoring" version);
- the undos before it refuse, as for any change since (their objects changed);
- other clients see the history change and reload.

**Roll back to last known good** restores the latest known-good version.

**Retention:** user and known-good versions are kept until deleted; automatic ones keep
the latest 30 (Settings > Versions: on or off, after how many changes or minutes, how
many kept), then one a day; unreferenced blobs are removed. With automatic versions
off, a restore still keeps the state it replaces.

**Agents** list versions and save them (MCP `versions_list`, `version_save`) and
restore one (`version_restore`) under the project's autonomy level: at "propose" and
"advise" a restore is refused with a message to ask the user, since it replaces much
at once. The skill `versions` says when to save one (before a risky batch).

**Filters** (`versions.select`, the same in every client): who made it (you, Puzzle,
an agent), its type (saved by you, first, changed outside Puzzle, before a run, after
changes, before a restore, copied), when (a range; the GUI offers today, the last 7,
30 or 365 days), known good only, and words found in the label, note, studies or
changes.

**Clients:** GUI Versions panel (Window → Versions, Ctrl+8; Project → Versions…), tabbed
with Messages: the filters across the top with a count ("3 of 40"); a table of
number, date and time, by, what (label or trigger), known good, the studies' states;
the selected version's summary and changes beside it; Save version…, Restore (or a
double-click), Roll back to last known good, Mark known good, Save as new project…
(from that version), Rename…, Delete. Project menu: Save version… (⌘/Ctrl+S),
Versions…, Roll back to last known good…. CLI `puzzle versions` (`--by`, `--type`,
`--since`, `--until`, `--good`, `--search`, `--show N`), `version-save`,
`version-restore N|good`. MCP `versions_list` takes the same filters.

### Save as New Project

`puzzle.project.copy.save_as(root, dest, name, version=None)` copies the whole project
folder to a new or empty folder outside it: objects, geometry files, imported fields,
results and meshes (the results store, and the meshes kept under `.puzzle/runs`),
versions and history. Files a geometry names outside the project (an absolute path, or
`../parts/x.step`) are copied into the new project's `imported/` folder and the geometry
points at the copy; a missing one is named and the copy refused. The original's lock and
solver working folders stay behind. Object ids are kept (results stay valid); nothing
refers to the original afterwards. With `version`, the copy starts from that version's
files instead of the current ones. The copy's next version is "copied from <name>".
GUI Project → Save as new project… (⇧⌘S), CLI `puzzle save-as`.

## Built (2026-11-01)

- `puzzle/project/versions.py` (manifests, blobs, hashes cached by size and time,
  `save`, `select`, `mark`, `edit`, `delete`, `prune`, `collect`, `restore_files`,
  `materialize`, `summarize`), `puzzle/project/copy.py` (`save_as`), the session's
  `save_version`, `restore_version`, `mark_version` and its implicit versions (on the
  first change, before runs, after edits), undo of a restore putting its files back;
  error code `exists`.
- GUI `gui/versions_panel.py`, the window's `save_version_dialog`, `restore_version`,
  `roll_back_good`, `save_as` and `_save_as_dialog(version)`; CLI `versions`,
  `version-save`, `version-restore`, `save-as`; MCP `versions_list`, `version_save`,
  `version_restore`; skill `versions`.
- Tests: `tests/test_versions.py` (the first change, edits and outside edits, save,
  restore and its undo with files, known good, agents refused below "act", thinning
  and blob collection, a copy from a version, filters, the CLI, a CalculiX run marking
  its start known good), `tests/test_save_as.py` (a full copy, outside files brought in
  and repointed, refusals), `tests/test_gui_versions.py` (save, roll back, undo,
  filters, a new project from a version), `tests/test_gui.py` (Save as opens the copy).
- The first save after opening a hand-written project rewrites every object file in
  its canonical form, so that version's "changed" list names objects that only
  changed format.

## Consequences

- "Where it last worked" is one click, whatever happened since, including replaced
  geometry files; undo keeps its small steps and can step back over a restore.
- Projects grow by their versions; deduplication and compression keep that small for
  TOML (a few kB a version), and a STEP file is stored once per content. Large
  imported fields count fully once each time they change.
- Results are not versioned: a restored state shows its runs only while the store still
  has them (deleted runs must be solved again).
- Every session open hashes the project's files (cached by size and time).
- A copy shares no files, so two projects that should share a CAD file each keep their
  own; that is the point of "no connection".

## Alternatives considered

- **Git underneath:** a full history for free, but a dependency for every user, binary
  STEP files in a repository, and merge semantics nobody asked for; the version folder
  works with git when the user wants it.
- **History replay to any point:** replays need every command to be deterministic and
  every input file unchanged; snapshots don't.
- **Snapshots of results too:** they are a cache by run key already, and large.
- **Versions under `.puzzle/`:** that folder is working space (disposable); versions are
  part of the project's record.
- **A copy that links the original's files:** fragile when either moves; asked against.
