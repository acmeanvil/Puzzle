---
title: Versions and projects
section: guide
order: 160
keys: panel:versions
summary: Go back to an earlier state of the project, copy it as a new one, send it as one file, and undo single changes.
adrs: 0059, 0061
---

There is no Save in Puzzle: every change is written as you make it. Undo takes back one
change; a version takes the whole project back to an earlier state. A project archive
puts the project in one `.puzzle` file to send to someone.

## How to save a version

1. Project → Save version… (Ctrl+S, ⌘S on a Mac).
2. Give it a **Label** that says what the state is ("Bonded, before friction"), a
   **Note** if you want, and tick **Known good** if it is a state you trust.
3. Save one at a point worth naming. Puzzle already keeps versions of its own:
   - before the first change of a session;
   - before each run (a run that succeeds marks it **known good**: "solved Static");
   - after 20 changes or 15 minutes, whichever comes first;
   - before every restore.

## How to restore a version

1. Window → Versions (Ctrl+8), or Project → Versions….
2. Filter the list by who made them, by type, by dates, known good only, or by words.
   Select one to see what it held: counts, each study's state, problems, and what
   changed since the version before.
3. **Restore** (or double-click it). Puzzle first checks the version's files are all
   there and undamaged, keeps the project as it is now as a version, then writes the
   old files back, all or none. Files added since are removed.
4. Undo takes a restore back, files too.

To go back to the last solved state in one step: Project → Roll back to last known
good…. **Mark known good** marks or unmarks the selected version.

Results aren't in versions: they are kept by run key, so a restored state finds its runs
again while the results store still holds them. Nor are exports (the `exports` folder:
ParaView links, operator exports, reports): a restore leaves them in place.

## How to copy a project

Project → Save as new project… (Ctrl+Shift+S) copies the whole project into a new or
empty folder: geometry and its files, meshes, results, imported fields, versions and
history. Geometry files from outside the folder are copied in. The two projects are
independent afterwards. The Versions panel's **Save as new project…** starts the copy
from the selected version.

## How to send a project

1. Project → Export project archive….
2. Choose what goes in. The model always does. **Results**: none (the smallest file:
   the receiver solves again), the latest of each study (the default), or all.
   **Versions** and **History**: all or none. The form shows each one's size.
3. To open one: Project → Open project archive…, or drop the `.puzzle` file on the
   window. It goes into a new or empty folder.

Before anything is written, Puzzle checks the archive's format, every file against its
hash, that no path leads outside the folder, and its unpacked size. A failure refuses
it as `invalid_archive`.

## Trusting a project

A project from elsewhere arrives with the agent's autonomy at "only advise". One with
Python script operators or custom equations also arrives **untrusted**: its code
doesn't run until you choose Project → Trust this project…, which first says what code
it holds. Trust it only if you know where it came from. Then raise the agent's
autonomy again if you want to.

## Undo and history

- Project → Undo (Ctrl+Z) takes back the last change, yours or an agent's. A batch of
  edits made at once, or an agent's proposal you accepted, undoes as one. Undo refuses
  when the objects have changed since, or when something made later uses what it would
  remove (undo the later change first).
- The History tab under the view lists every change: who made it, what it was, and the
  agent's proposals and your decisions. The record is never rewritten.

## When Puzzle stops part-way

Every change is written as one transaction: the files that changed and its history line
together. If Puzzle (or the computer) stops part-way, the project opens as it was
before the change or as it was after it, never a mix; the next open finishes a change
that was recorded. A history line cut short is set aside in `.puzzle/history.torn`.
Two Puzzles (the window, the terminal, an agent) can work on one project: each change
waits for the other's, and a client that held the project too long finds out
(`lock_lost`) before it writes anything.

New project refuses a folder that holds a project already: open it, or choose another
folder. A project saved by a newer Puzzle says so (`newer_schema`) rather than opening
wrongly; one saved by an older Puzzle is brought up to date, and its old files are kept
as a version first.

## Options worth knowing

Settings > Versions: **Automatic versions** on or off (versions you save are always
kept), a version after 5 to 100 changes (default 20) or 5 minutes to an hour (default
15), and how many automatic versions are kept (default 30; older ones thin to one a
day). Known good versions and yours stay until you delete them.

## Mistakes to avoid

- Restoring to take back one small change: use Undo. A restore replaces everything.
- Expecting results that were deleted since the version was made: solve again.
- Reading a version's time as local: it is UTC.
- Putting remote host names or keys in a project to share: they are yours, kept outside
  the project, and never go in an archive.
- Unpacking an archive with a zip tool: that skips every check.

## For agents and scripts

Agents use [versions_list](help:mcp:versions_list), [version_save](help:mcp:version_save),
[version_restore](help:mcp:version_restore) (only at "act within the budget" or "act
freely"), [project_archive](help:mcp:project_archive), [undo](help:mcp:undo) and
[history](help:mcp:history). In a terminal: [puzzle versions](help:cli:versions),
[puzzle version-save](help:cli:version-save),
[puzzle version-restore](help:cli:version-restore), [puzzle save-as](help:cli:save-as),
[puzzle archive](help:cli:archive), [puzzle unarchive](help:cli:unarchive),
[puzzle trust](help:cli:trust), [puzzle history](help:cli:history) and
[puzzle undo](help:cli:undo).

## See also

- [The agent and the Advisor](help:guides/agent-and-advisor)
- [The interface](help:guides/interface)
- [Schematic and operators](help:guides/schematic-and-operators)
- [Command line](help:reference/cli)
