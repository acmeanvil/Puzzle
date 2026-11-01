# 0061. Archive a project in one file to share it and open it in another Puzzle

- Status: Proposed
- Date: 2026-11-01

## Context

A project is a folder (ADR 0005, 0059): one TOML file per object, geometry sources,
imported fields, a results store, meshes under `.puzzle/runs`, versions and history.
Save as New Project (ADR 0059) copies it whole on the same machine and brings outside
geometry files in. Sharing it with someone else, by mail, a file share or a ticket, still
means zipping the folder by hand, and the receiver gets whatever the sender's folder held:
lock files, solver working folders, absolute paths to files on the sender's disk,
materials from the sender's user library that the receiver doesn't have, and a results
store that may be large or from another version of Puzzle.

Wanted: one file that carries a project to another Puzzle instance (another user,
machine or operating system) and opens there as it was, with the sender choosing how much
goes in.

## Decision (proposed)

1. **An archive is one file, `<name>.puzzle`**: a zip (deflate; large binaries stored)
   holding the project folder as `save_as` would copy it (outside files brought into
   `imported/`, no lock, no solver working folders), plus `archive.json`: the format
   version, the Puzzle version that wrote it, when and by whom, the project's name, a
   summary as a version's (ADR 0059: counts, studies and their states, problems), what
   was included, and every member's SHA-256.
2. **What goes in is chosen** in one form (`ask_form`), each with its size shown:
   - always: the model (objects, geometry sources, imported fields, sketches, notes);
   - **materials**: every material the project uses, copied from the user library into
     the project (ADR 0020) so the receiver needs nothing else; on by default;
   - **results**: none, the latest run per study, or all (the store's runs and the
     meshes they use); meshes alone without results;
   - **versions** and **history**: all, or none (the archive then starts the receiver's
     project with one version, "imported from <archive>").
3. **Export:** `puzzle.project.archive.write(root, path, include)` from a clean state
   (the session's lock taken, a version made first: "archived"); GUI Project → Export
   project archive…, CLI `puzzle archive PROJECT FILE [--results latest|all|none]
   [--no-versions] [--no-history]`, MCP `project_archive` (inside the project folder
   only, as `export`).
4. **Import:** `archive.read(path, dest)` checks before it writes: the format version
   (newer formats refused with a message naming the Puzzle version needed), every
   member's hash, no member path leaving the destination (no `..`, no absolute paths, no
   links), a size limit against zip bombs. Then it unpacks into a new or empty folder,
   rewrites nothing but the project's name if asked, and opens it. Materials carried in
   the project are used as they are; where the receiver's library has one of the same
   name that differs, the project's copy wins and Problems says so. A results store in
   an older format (`stale_store`) is dropped with a note, since results are a cache by
   run key and can be solved again. GUI Project → Open project archive… (and a
   `.puzzle` file dropped on the window or opened from the OS), CLI `puzzle unarchive
   FILE DEST`.
5. **Portability:** paths in the archive are relative and use `/`; file names that
   Windows refuses (`:`, `aux`, trailing dots) are refused at export; text files keep
   their line endings. Solver settings that name local executables or hosts (ADR 0021)
   are left to the receiver's own settings, which are user data, never project data.
6. **Trust:** an archive can carry custom PDE equations and expert solver settings,
   which are code (CLAUDE.md, custom PDEs). Opening an archive with them says so and
   opens the project at the "advise" autonomy level for agents until the user accepts.

## Consequences

- One file to send, which opens the same on another machine; the sender decides whether
  it is a light model-only file or a full record with results.
- A new file format to keep readable: `archive.json`'s version and a reader for every
  older version, tested by archives kept in `tests/data/archives`.
- Archives with results can be large; the form shows the sizes before writing.
- An archive is a snapshot: it has no link back to the original (as Save as).

## Alternatives considered

- **A plain zip of the folder by hand:** what users do now; it carries lock files and
  absolute paths, misses outside files and library materials, and nothing checks it on
  the way in.
- **Git bundles or a repository:** history for free, but a dependency for every user and
  binary STEP and result files in it (as ADR 0059 rejected for versions).
- **tar.gz:** fine on Linux and macOS, less familiar on Windows, and no random access to
  read `archive.json` without unpacking.
- **Results always in:** too large to mail for most projects; they can be solved again.
