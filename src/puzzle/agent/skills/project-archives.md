---
name: project-archives
description: one .puzzle file to send a project to someone else: what goes in (results, versions, history), the checks on the way in, and why a project with code arrives untrusted
adr: 0061
---
Read this when the user wants to send, share, back up or hand over a project, or has
received a `.puzzle` file.

An archive is a zip of the project as Save as copies it. Geometry files from outside
the folder are brought into `imported/`. There is no lock and no solver working
folders. `archive.json` alongside records the format, the Puzzle that wrote it, who and
when, a summary and each file's SHA-256. Materials need nothing extra: a project's
materials are its own copies.

## Steps

1. Choose what goes in. The model always goes in. `results` is `none` (smallest: the
   receiver solves again), `latest` (each study's newest run, the default) or `all`.
   `versions` and `history` are true or false. Results are a cache by run key, so leave
   them out for mail-sized files.
2. Write it with MCP `project_archive(path, results, versions, history)`. `path` must
   be inside the project folder, e.g. `exports/beam.puzzle`. It returns the size by
   part. The user can also use Project → Export project archive… or
   `puzzle archive PROJECT FILE [--results none|latest|all] [--no-versions]
   [--no-history]`.
3. Opening one is the user's step: Project → Open project archive…, a `.puzzle` file
   dropped on the window or opened from the system, or `puzzle unarchive FILE DEST`.
   It goes into a new or empty folder.

## What the receiver's Puzzle checks

- The format: a newer one is refused, naming the Puzzle that wrote it.
- Every file against its hash: a damaged archive is refused.
- Paths: nothing outside the destination, no absolute paths, no links.
- The unpacked size, against zip bombs.

All of these come before anything is written, as code `invalid_archive`. A results
store in an older format is dropped with a note (solve again). At export, file names
Windows can't make (`aux`, `:`, a trailing dot) are refused.

## Code in an archive

A project with script operators or custom PDE equations arrives untrusted (ADR 0062),
and its agent autonomy is set to "advise". Say so to the user. Only they can trust it
(Project → Trust this project…) and raise the autonomy again. Never suggest trusting a
project whose source the user doesn't know.

## Mistakes to avoid

- Don't put remote host names, keys or user settings in a project to share: they are
  user data and never go in an archive.
- Don't unpack an archive by hand with unzip: that skips every check.
