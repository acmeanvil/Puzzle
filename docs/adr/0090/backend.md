# Puzzle design review: backend (speed and data integrity)

Reviewer scope: `src/puzzle/project/`, `commands/`, `results/`, `solve.py`, `sweep.py`, `jobs/`, `runner/`, `trust.py`, `operators/scripts.py`, and `model/schema.py` as it affects hashing.
Method: reading, plus small scripts run with `pixi run -e dev python` on copies of `examples/cantilever` and `examples/bracket_assembly` under the scratch folder (scripts kept in `scratchpad/review/be/t1.py` … `t7.py`). No repository file was edited. Machine: Windows 11, Docker Desktop running.

Counts: 1 critical, 5 high, 12 medium, 4 low; 6 items "to check".

---

## Measurements (cantilever copy unless noted)

| What | Measured |
| --- | --- |
| `Session(root)` open, 6 objects | 0.28 s |
| First edit of a session (first version, imports) | 1.45 s |
| Later edits, 6 objects | 21–38 ms |
| `run_hashes` / `status` / `problems`, 6 objects | 3.6 ms / 2.5 ms / 0.5 ms |
| One `create` with ~10 objects in the project | 26 ms |
| One `create` with ~300 objects in the project | 270 ms profiled (420 ms average including the periodic auto-version); 300 creates took 65 s in total |
| Where the 270 ms goes | `save_project` 127 ms (308 files rewritten), `load_project` 64 ms (308 files parsed), two `_snapshot`s 27 ms, `versions.due`/`latest` 34 ms |
| `status` with 300 objects | 12 ms |
| History growth, bracket example | 2.9 kB per edit of a 2.8 kB component (the whole object is stored as the undo snapshot) |
| Result store, 120 runs of a 20k-node mesh, 5 fields, 1 step | 993 files in 734 folders, 483 MB; 604 Parquet files in `fields` (one per run and field, never merged) |
| `write_run` (20k nodes, 5 fields) | 0.3–0.4 s; 1.5 s when it compacts |
| `store.runs()` | 76 ms |
| `find_run` | 58 ms each (120 calls: 6.95 s) |
| `read_field` (one 20k × 6 field) | 0.75 s |
| `field_catalog` / `read_results` / `read_mesh` | 0.37 s / 0.44 s / 0.06 s |
| `write_run`, 200k nodes × 30 steps × 5 fields (912 MB of values) | 46.8 s (about 20 MB/s); peak extra memory during the write 920 MB on top of the 912 MB held |
| `transfer.map_field` interpolate, 60k nodes / 400k tets | 3.2 s (linear: about 53 µs per target node) |
| `transfer.map_field` nearest, 60k nodes | 2.1 s |
| `FaceLookup.build`, 400k tets | 2.0 s |

---

## Findings

### BE-1 (critical) Geometry files' contents are not in any hash: a replaced CAD file leaves results "up to date" and reused

- Where: `src/puzzle/project/graph.py:36-135` (`_own`), `:138-153` (`content_hashes`), `:156-208` (`run_hashes`); `src/puzzle/model/schema.py:195` (`Geometry.source`), history `import` steps' `file` argument; `src/puzzle/solve.py:178-188` (cache hit before meshing).
- What: an object's hash covers its Pydantic dump only. `Geometry.source = "bar.step"` is a path; the file's bytes are never hashed. Imported fields do this correctly (`ImportedField.file_hash`, `schema.py:947`), geometry does not.
- Failure: the user (or a CAD export script, or a `git pull`) overwrites `bar.step` with a thicker bar. The tree still shows the study "Up to date", `run_study(reuse=True)` returns the old run from the store without meshing, derived results, reports and exports are of the old shape. The same holds for `import` history steps, DXF files of `frame` steps and STL/OBJ/3MF sources. ADR 0072 line 451 lists only "a File operator's files in the content hash" as open; the geometry gap is not listed.
- Confidence: high. Verified by running: replaced one byte-run in `bar.step` of a cantilever copy, reopened the session, `run_hashes` before and after were equal (`t1.py`: "key unchanged after geometry file replaced: True"). The cache path was read, not run.
- Fix: give every file a model object reads a content hash that enters `_own`: either store `source_hash` on `Geometry`/`HistoryStep` (set by the import command, refreshed by a "file changed" check on open and before a run, as `fields.file_hash` does), or have `content_hashes(project, root)` mix in a size+mtime-cached SHA-256 of each referenced file (the cache `versions.hashes` already keeps). The second needs `root` passed to `run_hashes`/`status`, which the tree and solve both have.
- Test: build the cantilever, store a fake run under its current key, overwrite `bar.step`, assert `status(...)[study]` is `out_of_date` and `run_hashes` changed; the same for an `import` history step and a `frame` DXF.

### BE-2 (high) The tree's run keys and the solve's run keys are computed from different projects: studies with wired parameters, suppressed geometry or toggles never read "up to date"

- Where: `src/puzzle/solve.py:112-124, 162, 178` (key from the project after `bolts.resolved`, `operators.resolved`, `operators.with_parameters`, `suppress.applied`, `axes.resolved_project`); `src/puzzle/project/graph.py:163-169` (`run_hashes` applies only `bolts.resolved` and `operators.resolved`); callers that pass the raw project: `project/status.py:71, 157`, `commands/session.py:651`, `gui/window.py:820`, `agent/mcp.py:2067`, `commands/outline.py:122`, `schematic/__init__.py:380`, `optimise.py:1128`.
- What: `_run_study` stores the run under `run_hashes(keyed)` where `keyed` has parameter-bound quantities replaced by values, suppressed parts removed, `when`-toggled features dropped and derived coordinate systems resolved. `status` hashes the project as saved. The two keys differ whenever one of those transforms changes any object.
- Failure: a project with a load wired to a parameter (`Quantity.parameter`, ADR 0071), or one suppressed body (ADR's "as Ansys"), is solved; the tree, the outline, the schematic and MCP `next_steps` keep saying "Out of date: the model changed since the last run" for the study, its derived results and its operators. Versions are never marked from a matching state, `Session._fresh_points` counts every sweep point as new against an agent's run budget, and users learn to ignore the stale mark, which is the one signal BE-1 depends on.
- Confidence: high. Verified by running (`t2.py` cases A and B): with a force bound to parameter `F`, and separately with a part on a suppressed geometry, the key computed with solve's transforms differed from `run_hashes(project)` for every study. `resolved_project` (derived axes) was not exercised but is the same pattern.
- Fix: one function, e.g. `graph.keyed(project, root)`, that applies the full chain, called by `run_hashes` itself (so no caller can forget), with `_run_study` passing a flag or the already-transformed project. Alternatively key from the untransformed project in `_run_study` (take `keyed` before line 118) and make sure parameter values reach the hash through the `ParameterSet` inputs they already do; suppressed/hidden flags are in `_own` already (`suppressed` is not popped), so the raw project's hash is sufficient and cheaper.
- Test: for each of (bound quantity, suppressed geometry, `Feature.when` off, a face-derived coordinate system): run the study with a stub adapter or a stored fake run under `_run_study`'s key, then assert `status` gives `up_to_date`. A property test: `run_hashes(p) == key used by _run_study(p)` over the example projects.

### BE-3 (high) Stop and timeouts do not stop container solves on Windows (nor child processes of native ones)

- Where: `src/puzzle/jobs/runner.py:120-129, 284-291` (`_stop`: `proc.terminate()` on the `docker run` client); `src/puzzle/jobs/runtime.py:119-121, 344-388` (`ContainerRuntime` has no `cancel`; `docker run --rm` without `--name`/`--cidfile`); `jobs/service.py:27-33` (the job runner, which does own process groups, is POSIX-only).
- What: on Windows `terminate()` is `TerminateProcess` on the Docker CLI only. The CLI forwards no signal, so the container keeps running. The run is reported `stopped`/timed out while the solver goes on using the cores and writing into the run folder. `WarmRuntime.cancel` (`warm.py:181-190`) handles this for batches; the plain runtime does not. Native runs started through a wrapper (`run_aster.bat`, `mpiexec`) have the same gap: only the direct child is killed, and if a grandchild keeps the pipe open `_pump` never delivers `None` and `run_job` does not return.
- Failure: the user presses Stop on a 40-minute Code_Aster or FEniCSx container solve (both container-first on Windows), reruns with a fix: two solves now compete for cores and memory. An agent at "act within the budget" whose `timeout_s` is its remaining solver seconds has the budget enforced on paper only. Closing Puzzle leaves containers running with no record of them (the runner's `pending` is POSIX-only).
- Confidence: high. Verified by running (`t3.py`): `docker run --rm … sleep 40` started as `run_job` starts it, `_stop(proc)` after 4 s; the client returned code 1 and `docker ps` still listed the container "Up 5 seconds". (During the review `docker ps` also showed a `puzzle-fenicsx:0.11` container not started by this reviewer; it may be another reviewer's run or such an orphan.)
- Fix: give each container run a name (`--name puzzle-<uuid>` or `--cidfile` in the run folder) and implement `ContainerRuntime.cancel` as `docker kill <name>` (then `docker rm -f`), called before `_stop`. For native runs on Windows start the process in a job object (`CREATE_NEW_PROCESS_GROUP` plus `AssignProcessToJobObject` with `KILL_ON_JOB_CLOSE`, as `operators/sandbox_win.py` already does) and on POSIX `start_new_session=True` + `killpg`. Give the pump loop a deadline after a stop so a held pipe cannot hang the caller.
- Test: integration test (skipped without an engine): start a container solve that sleeps, set `cancel`, assert `docker ps --filter name=…` is empty within 10 s. Unit test with a fake engine script that spawns a child and ignores the parent's death.

### BE-4 (high) Project saves are neither atomic nor durable, and every command rewrites every object file

- Where: `src/puzzle/project/store.py:30-56` (`save_project`: `Path.write_text` in place for `project.toml` and each object, then unlinks); `src/puzzle/commands/session.py:693, 705` (`_record`: save, then history append); no `os.fsync` anywhere in `src/puzzle`.
- What: (a) each file is truncated and rewritten in place; a crash, power loss or full disk mid-save leaves a zero-length or half TOML. (b) All N object files are rewritten for a one-field edit (308 writes at 300 objects), which multiplies the window and the wear on network drives and sync clients (OneDrive/Dropbox see every file change on every edit). (c) The project is saved before the history line is appended: a crash between leaves a change with no history entry and no undo; a later undo of an earlier entry may then refuse ("changed since") or restore over it. (d) Deleted objects' files are unlinked after the rest is written, so a crash can also leave a deleted object present with references to it already removed, or the reverse order problem for creates.
- Failure: laptop sleeps or loses power during an agent's batch of 40 edits; on reopening `Session(root)` raises a raw pydantic `ValidationError` ("3 validation errors for Component", verified with an emptied file, `t5.py`), not the `invalid` `PuzzleError` that advises restoring a version (`store._toml` only catches TOML syntax errors; an empty file is valid TOML). The latest automatic version may be up to `versions.every_edits` (20) changes old.
- Confidence: high for the mechanism (reading); the unreadable-project symptom verified by running.
- Fix: write each changed file to `<name>.tmp` in the same folder, `flush` + `os.fsync`, `os.replace`; write only objects whose snapshot hash changed (the session already computes `created/changed/deleted`); append the history entry first as an intent record (or write a small `.puzzle/journal.json` naming the files of the command, removed when done) so a reopen can finish or roll back. Wrap `cls.model_validate` in `load_project` so any unreadable object file gives the `invalid` error naming the file and the version to restore.
- Test: monkeypatch `Path.write_text`/`os.replace` to raise after k files during `submit_batch`; reopen and assert the project loads as either the before or the after state, never a mix; assert an emptied object file raises `PuzzleError(code="invalid")` naming it.

### BE-5 (high) One torn line in `history.jsonl` stops every later command

- Where: `src/puzzle/commands/session.py:528-536` (`history_entries`: `json.loads` on every line, no guard), used by `_log` (`:865-872`), `_last_seq`, `undo`, `proposals`, `budget_used`, `_double_sign_off`. `versions._last_command` and `versions.due` (`versions.py:527-533, 588-592`) already tolerate bad lines; the session does not.
- What: the history is appended with a plain buffered write and no fsync. A crash mid-append leaves a partial last line. Every later `submit` then raises `json.JSONDecodeError` from `_check_opened → _auto_version → _last_seq`, before anything is applied. If a later append did get through, it would be glued onto the torn line (no leading newline), making both unreadable.
- Failure: Puzzle is killed during a save; the project opens (the tree shows) but no edit, undo, run or proposal works from GUI, CLI or MCP until someone repairs the file by hand. The error is an uncoded traceback.
- Confidence: high. Verified by running (`t2.py` case C): after appending `{"seq": 2, "type": "comm` the next `Session.submit` raised `JSONDecodeError: Unterminated string…`.
- Fix: in `history_entries` skip a last line that does not parse (and report it once as a problem); in `_log` open with `"rb+"`, check the file ends in `\n` (truncate a torn tail or write a newline first), write the entry in one `os.write`, fsync. Keep the last seq in a small sidecar or read only the file's tail (see BE-11) instead of parsing it all.
- Test: the script above as a unit test: torn tail, then `submit` succeeds, `history()` lists the earlier entries, the new entry has the next seq.

### BE-6 (high) The project lock can be stolen from a live holder, and the loser overwrites the winner's changes

- Where: `src/puzzle/commands/session.py:53` (`STALE_LOCK_S = 120`), `:874-902` (`_locked`): staleness is the lock file's mtime only; the pid written into it is never read; `finally: lock.unlink(missing_ok=True)` removes whatever lock is there; the stale check and unlink are not atomic.
- What: (a) A holder that takes longer than 120 s inside the lock loses it silently. Work done under the lock includes `versions.save` (hash and zlib-compress every new tracked file, whole file in memory; see BE-13), `restore_files`, and `load_project`/`save_project` on slow or synced drives; a suspended laptop or a debugger pause also qualifies. (b) When the first holder resumes it writes its in-memory project over the other client's change (`save_project` rewrites all files) and then deletes the second client's lock. (c) Two waiters that both see a stale lock can both unlink and both create (the second unlinks the first's fresh lock). (d) After a crash while holding the lock, other clients get `busy` after 10 s for up to two minutes.
- Failure: GUI and an MCP agent on one project. The GUI's "before run" version compresses a 3 GB ParaView export for 150 s; the agent's edit steals the lock and renames a material; the GUI then saves its stale copy: the agent's edit is gone from the files but present in `history.jsonl`, so undo data and the model disagree.
- Confidence: high for the logic. Verified by simulation (`t5.py` case 2): session A inside `_locked()`, lock mtime set back 200 s, session B's edit applied, A's save then lost B's change ("B's change survives: False"). The 120 s being exceeded in practice was not timed (40 MB of exports added 2 s to one edit; it scales linearly).
- Fix: use an OS lock held on an open handle (`msvcrt.locking` / `fcntl.flock`, or the `filelock` package): it dies with the process, needs no staleness rule and cannot be stolen. If a file-existence lock is kept: write pid+hostname+token, treat it as stale only when that pid is dead on this host (`jobs.pending.alive` exists), refresh mtime from a heartbeat during long work, and on exit unlink only if the token is still ours. Move version compression out of the lock (hash under the lock, copy blobs after).
- Test: two `Session`s; hold A's lock past the stale limit with the pid alive; assert B gets `busy` rather than the lock. Kill-holder test: lock file with a dead pid is taken at once.

### BE-7 (medium) Accepting a proposal applies its commands one by one; a failure leaves it half applied and still open, and accepting again duplicates the first part

- Where: `src/puzzle/commands/session.py:308-334` (`accept`): one `_locked()`/`_apply` per command, the `decision` entry logged only after all succeed; `_open_proposal` read outside the lock.
- What: no all-or-none, unlike `submit_batch`. The proposal's preview was computed against the project at proposal time; nothing checks it still applies.
- Failure: an agent proposes [create material, delete "Tmp"]; the user deletes "Tmp" by hand, then clicks Accept: the material is created, the delete fails with `not_found`, no decision is logged. The proposal still shows as open; a second Accept creates a second material of the same name (names are not unique keys, so later references by name become `ambiguous`). Two clients accepting at once also both apply.
- Confidence: high. Verified by running (`t2.py` case D): after two failed accepts the project held two "NewMat" materials and one open proposal.
- Fix: apply the picked model edits through the batch path (trial copy, one history entry, one undo) inside one lock that also re-reads the proposal's state and logs the decision; run commands in the proposal are submitted after the batch. On failure log nothing and change nothing.
- Test: the scenario above; assert zero objects created, the proposal still open, and that a successful accept is one undoable entry.

### BE-8 (medium) Undo of an earlier entry removes objects that later changes depend on

- Where: `src/puzzle/commands/session.py:353-408` (`undo`): the staleness check compares only the hashes of the objects the entry itself created or changed; no check of dependents, no `validate()` of the result. Reachable with `seq` from MCP (`agent/mcp.py:1084`) and the CLI, and without `seq` by an agent, whose "own latest" entry need not be the project's latest.
- Failure: the agent creates a material (seq 5); the user assigns it to a part (seq 6); the agent calls `undo()`: seq 5 is its latest, the material's hash is unchanged, so it is deleted and the component now names a missing material (`missing_reference`). `Delete` refuses exactly this (`used_by`), undo does not.
- Confidence: high. Verified by running (`t2.py` case E): the undo was allowed and the reopened project reported `missing_reference`.
- Fix: before saving an undo, refuse when any object not in the entry depends on an object the undo removes (reuse `Delete`'s check), and when a restored object's dependencies are gone; message "undo the later changes first" with the names.
- Test: the scenario above expects `not_undoable` with `details.objects`.

### BE-9 (medium) The "agents propose code" gate is bypassed by setting a parent path

- Where: `src/puzzle/commands/session.py:587-608` (`_writes_code`): for `Edit` it looks for "equation"/"code" in the op's path, not in the value.
- What: at "act within the budget" an agent's new code is meant to become a proposal (ADR 0062). An `Edit` with `{"op": "append", "path": "physics", "value": {"type": "custom_pde", "equation": {...}}}` (or `set` on `physics.0`) carries the weak form in the value, so it is applied directly. A weak form is `eval`'d in `study.py` (CLAUDE.md: "limits mistakes, not a determined attacker").
- Failure: in a trusted project at `act_in_budget`, a prompt-injected agent adds a custom PDE physics and runs the study within its budget: code of its choosing runs in the FEniCSx environment without the user having seen it.
- Confidence: high. Verified by running (`t2.py` case F): the edit by `Actor("agent")` returned `applied`.
- Fix: decide on the result, not the request: run the command on a trial copy and compare `trust.holds_code`-style fingerprints (script operators' code, PDE equations' text and coefficients) before and after; any difference means propose. That also covers `CopyComponent`, undo and future fields.
- Test: parametrised over paths `physics`, `physics.0`, `physics.0.equation`, and a script operator's settings: all give `status == "proposed"` below "act".

### BE-10 (medium) `write_mesh` is not idempotent: an interrupted or concurrent write doubles the mesh's blocks

- Where: `src/puzzle/results/store.py:249-275` (`write_mesh`: appends `nodes`, `cells`, `groups`, then the `meshes` row that marks it present); `:357-385` (`read_mesh` reads every `cells` row of the mesh id).
- What: existence is tested on `meshes` only. If the process dies (or the disk fills) after `cells` is appended and before `meshes`, the next run of the same mesh appends everything again; two processes storing the same new mesh at once (GUI and CLI, or a sweep and a shoot-out sharing a mesh) race the same way. `read_mesh` then returns each block twice.
- Failure: results drawn on a mesh with duplicated elements; integrals over elements, element counts, exports (Exodus, Nastran) and error estimates count every element twice. Nothing reports it.
- Confidence: high. Verified by running (`t4.py`): forcing an exception at the `meshes` append and calling `write_mesh` again gave `read_mesh(...).blocks` of length 4 for a 2-block mesh.
- Fix: make the write one unit: either write `meshes` first with a `complete=false` flag and flip it, or before appending delete any `nodes/cells/groups` rows of that mesh id; and make `read_mesh` defensive (first row per `block`). Take a store-level lock (see design notes) around `write_run`.
- Test: the monkeypatched interruption above, asserting 2 blocks; a two-thread `write_mesh` of the same mesh.

### BE-11 (medium, speed) Every command costs O(objects + history): whole-project reload, two full snapshots, a full rewrite and several full parses of the history

- Where: `session.py:899` (`load_project` on every lock), `:673, 691` (`_snapshot` before and after: dump + JSON + SHA-256 of every object), `:693` (`save_project` rewrites all), `:867` (`_log` parses the whole history to find the next seq), `:460` (`_last_seq`, again), `versions.py:582-604` (`due`: parses it again), `versions.py:124-131` (`latest` re-reads every version manifest), `:713` (`project.problems()` on every outcome).
- Measured: 26 ms per create at ~10 objects, 270 ms at ~300 objects (profile above); 300 creates took 65 s. History grows by the full dump of every changed object per edit (2.9 kB per edit on the bracket's 2.8 kB component); a component with hundreds of bolts, contacts and features is 100 kB to 1 MB, so a long agent session makes a history of hundreds of MB that is parsed two to three times per command.
- Failure: a bolted assembly with operators (1 000–2 000 objects) takes seconds per property edit; slider releases and agent batches feel stuck; the cost grows through the session.
- Confidence: high (measured).
- Fix: keep an in-memory project plus a cheap change token (history size + mtime, or a counter file) and reload only when another client wrote; snapshot only the objects a command touches (`_execute` knows them; a copy-on-write `objects` dict gives the rest for free); write only changed files (BE-4); keep `seq` in a sidecar or read the last line by seeking; store undo data as a reference to a version blob or a JSON patch when the object is large; cache `versions.latest`.
- Test: a benchmark test with 1 000 small objects asserting an edit is under, say, 50 ms and does not write unchanged files (count `os.replace` calls).

### BE-12 (medium, speed) Result store layout and lookups scale poorly with the number of runs

- Where: `store.py:125` (`fields` partitioned by `run_id` and `field`), `:286-289` (`find_run` loads and sorts the whole `runs` table per call), `:291-295` (`get_run` likewise), `:334-355` (`read_field`: three dataset opens), `:425-432` (compaction cannot merge across partitions), `:471-476` (`_rows` builds a `DeltaTable` and re-reads its log every call); callers in loops: `session._fresh_points:658`, `optimise.py:332, 429, 1131`, `convergence.py:158, 195`, `adapt.py:118-203`.
- Measured (120 runs): 604 Parquet files in 600+ partition folders that compaction leaves as they are; `find_run` 58 ms each, so a 120-point sweep spends 7 s on cache look-ups and an optimisation replay is O(points²); `read_field` 0.75 s for a 20k-node field, which is the per-frame read the viewer makes.
- Failure: an optimisation with 500 points and 10 fields makes 5 000 partition folders; the Windows file system and Delta's log listing dominate every read; animation frames take a second each.
- Confidence: high (measured); the growth beyond 120 runs is extrapolated.
- Fix: partition `fields` by `run_id` only (the `field` filter then prunes by row-group statistics, which the one-row-per-group layout already supports), or not at all with Z-ordering/sorting by `run_id`; cache the `runs` table in the `ResultStore` keyed by the Delta version (`DeltaTable.version()` is cheap) and index it by `run_key` and `run_id`; let `read_field` take the ordinal index from one metadata read cached per run. Give `find_run` a filter pushed down (`run_key = …`).
- Test: a benchmark with 300 small runs asserting `find_run` under 5 ms after the first call and the file count under a bound after `compact()`.

### BE-13 (medium) Versions track everything in the project folder, including exports, and do it under the project lock

- Where: `src/puzzle/project/versions.py:28` (`LEFT_OUT = results, .puzzle, versions, history.jsonl`), `:224-241` (`tracked`), `:300-306` (blobs: `read_bytes()` of the whole file, `zlib.compress(…, 6)`), called inside `Session._locked()`.
- What: `exports/paraview/<run>/shown.vtu`, `series.pvd` data, exported STEP/STL/CGNS, reports and snapshots written by Export operators all live in the project folder and are therefore copied into `versions/blobs` on the next automatic version. Files are read whole into memory.
- Failure: a 2 GB ParaView export doubles the project's size at the next edit, holds the project lock for the time it takes to compress (leading to BE-6), and needs 2 GB+ of RAM in the GUI process. Restoring an older version then deletes exports made since (`restore_files` removes tracked files the version lacks).
- Confidence: high. Verified by running (`t5.py` case 1): a 40 MB file under `exports/paraview/run1/` appeared in the next version's manifest and added 40 MB of blobs; the edit took 2.15 s instead of ~0.03 s.
- Fix: add `exports` (and any other derived-output folder) to `LEFT_OUT`, or track by allow-list (object kinds' folders, `fields/`, `imported/`, referenced geometry files); stream blobs in chunks; hash under the lock and copy blobs after releasing it.
- Test: a project with a file under `exports/` gets a version whose `files` does not list it; a restore leaves it in place.

### BE-14 (medium) No schema evolution path: `SCHEMA_VERSION` has never moved, unknown fields are fatal, and hashes depend on hand-maintained default pops

- Where: `src/puzzle/model/schema.py:32` (`SCHEMA_VERSION = 1`), `ConfigDict(extra="forbid")` on every model; `project/store.py:78-96` (no migration step; only "newer than this Puzzle" is checked, which can never trigger); `project/graph.py:36-135` (thirty-odd special cases that drop new fields at their defaults so old hashes hold); `commands/session.py:1060-1071` (undo hashes over the full dump).
- What: (a) a project saved by a newer Puzzle with one new field fails to open in an older one with a raw `ValidationError` ("1 validation error for Study", verified in `t5.py` case 3), because the version number that should have explained it was not bumped. (b) Renaming or removing a field has no place to convert old files; old `versions/` blobs and old `history.jsonl` undo snapshots hold the old shape too, so restore and undo break across such a change. (c) A new field with a default that someone forgets to pop in `_own` silently stales every stored run of every user on upgrade; a default whose value is later changed while being popped changes solves without changing the key. (d) Undo's "changed since" hashes include defaults, so every pre-upgrade history entry becomes not undoable after any field is added.
- Confidence: high for (a) (run), reading for the rest.
- Fix: bump `SCHEMA_VERSION` with each release that changes the schema and add `project/migrations.py` (ordered functions on the raw TOML dict, applied in `load_project`, also to version blobs on restore and undo snapshots on use); catch `ValidationError` in `load_project` and say which file, which field, and "made by a newer Puzzle?" when unknown keys are the cause. For hashes, replace the hand list with a rule: hash `model_dump(exclude_defaults=True)` plus a table of `{field: default at the time it was added}` checked by a test that fails when a model's default changes; or keep the list but add a golden-hash test (pinned run keys for the example projects) so a forgotten pop fails CI.
- Test: golden run keys for `examples/cantilever` and `examples/bracket_assembly`; a fixture project saved by the previous release that must open, undo and restore.

### BE-15 (medium) Version restore is not atomic and does not check its blobs first

- Where: `src/puzzle/project/versions.py:400-436` (`content`, `materialize`, `restore_files`: files written in place one by one; blobs are decompressed as they are reached, never checked against their digest); `commands/session.py:425-449` (`restore_version`; `_auto_version` returns `None` on `OSError`, and the restore proceeds without the "before restore" version).
- Failure: one blob missing or corrupt (a sync client, a pruned blob from an imported archive, disk error): `restore_files` has already overwritten some files when it raises; the project is a mix of two versions, no history entry is written, and the session's in-memory project no longer matches the disk. On a full disk the safety version is skipped and the restore still overwrites; undo then has no `files_from`.
- Confidence: medium-high (reading only).
- Fix: first verify every needed blob exists and hashes to its name; write the new files to a staging folder and swap (or write `*.tmp` then replace all); refuse to restore when the "before restore" version could not be saved; validate the restored project loads before committing, else roll back.
- Test: delete one blob, call `restore_version`, assert `PuzzleError` and that no tracked file changed.

### BE-16 (medium, speed and integrity) Archiving copies the whole project, results included, before pruning; Save as copies a live store without the lock

- Where: `src/puzzle/project/archive.py:141-158` (`write`: `save_as(root, stage)` into the system temp folder, then `_prune`); `project/copy.py:40-76` (`shutil.copytree` of everything, no project lock, the Delta tables copied file by file while a run may be committing).
- Failure: exporting a model-only archive of a project with 60 GB of results first copies 60 GB to `%TEMP%` (possibly another, smaller drive), then deletes it; with "latest" it copies all and runs `delete_run` + vacuum per dropped run. A Save as taken during a sweep can copy a `_delta_log` entry whose Parquet file was listed before it existed (or the reverse), giving a store that fails to read in the copy.
- Confidence: high for the copy-then-prune cost (reading); the torn store copy is plausible, not reproduced.
- Fix: pass `Include` into `save_as` as an ignore rule so excluded parts are never copied; for "latest", write a fresh store in the stage from the chosen runs (read/write through `ResultStore`) rather than copy-and-delete; take the project lock for the model files and a store lock (or copy the store at a pinned Delta version) for results.
- Test: archive with `results="none"` while monkeypatching `shutil.copytree`'s ignore to record: no path under `results/` is visited.

### BE-17 (medium, speed) Field transfers and face look-ups loop in Python per node and per element

- Where: `src/puzzle/transfer.py:92-120` (`_nearest`: a dict of buckets and a Python loop per query), `:123-172` (`_interpolate`: a triple Python loop over every tetrahedron's grid cells, then a loop per target node); `src/puzzle/adapters/common.py:286-307` (`FaceLookup.build`/`of`: one `frozenset` per element face in a dict).
- Measured: interpolate 3.2 s and nearest 2.1 s at 60k nodes (linear); `FaceLookup.build` 2.0 s at 400k tets. Extrapolated to 1 M nodes / 6 M tets: about 55 s, 35 s and 30 s, and tens of millions of Python objects (several GB) for the look-up.
- Fix: `scipy.spatial.cKDTree` for nearest (already used in `contact_check`); for interpolation, a KD-tree on tet centroids with k candidates or `matplotlib`/VTK cell locators (VTK is a dependency: `vtkStaticCellLocator`, or `scipy.spatial.Delaunay.find_simplex`-style vectorised barycentric tests over candidate sets); for faces, sort each face's node ids into an (n, 3) array and match with `np.unique(..., axis=0)`/a structured view and `searchsorted`.
- Test: benchmarks at 200k nodes with time bounds, and equality of results against the current implementations on the existing transfer tests.

### BE-18 (medium) Results are whole-run, in-memory objects end to end; sweeps hold every point

- Where: `src/puzzle/solve.py:179-188` (a cache hit reads the full mesh and every field of every step), `:506-545` (`_sweep_points` keeps each point's `StudyRun` with mesh and results in `runs`), `:193-208` (cached upstream studies read whole to pick one field); `results/store.py:532-578` (`_fields_table`/`_lists` concatenate every step of every field into one array, then Arrow copies again).
- Measured: storing 912 MB of values took 46.8 s (about 20 MB/s) and 920 MB of extra memory. A 1 M-node, 100-step transient with stress and strain is roughly 15 GB of values: it cannot be stored on a 16 GB machine, and a cached re-run of it loads all of it only to return.
- Failure: this is where large models fall over first: after the solver has finished, in `write_run`, losing the solve. Second: a 50-point sweep of a 300k-node model holding 50 result sets.
- Fix: write fields step by step (append per step or per field with a streaming writer; the one-row-per-step layout already suits it); let adapters yield steps lazily; make a cache hit return a lazy handle (`run_id`, `mesh_id`, summary) and load fields on demand; in sweeps keep only `(words, run_id, cached, job)` per point. Check the write throughput: 20 MB/s suggests `max_row_group_size=1` with zstd level 3 on one thread or an extra cast; try `table.cast` avoidance and larger data pages.
- Test: a memory-bounded test (tracemalloc) writing a synthetic 30-step run asserting peak extra memory below one step's size times a small factor.

### BE-19 (low) `.puzzle/jobs.json` and `trusted.json` are read-modify-written without a cross-process lock

- Where: `src/puzzle/jobs/pending.py:22, 59-70, 77-83, 121-126` (a `threading.Lock` only); `src/puzzle/trust.py:43-56` (plain `write_text`, not atomic).
- Failure: GUI and CLI each submit a job to the runner within the same moment: one entry overwrites the other, and that job is never offered for resume (its results stay on the host). A crash during `trust()` leaves an empty `trusted.json`, which reads as "nothing trusted" (safe, but every project loses its trust).
- Fix: the same OS file lock as BE-6 around both; atomic replace in `trust.py`.

### BE-20 (low) Run folders are named to the second

- Where: `src/puzzle/commands/session.py:809-811` (`<slug>-%Y%m%d-%H%M%S`).
- Failure: the GUI and an agent start the same study within one second (or two studies whose names slug alike): both write input and logs into one folder and read each other's results.
- Fix: add a short random suffix or `mkdir(exist_ok=False)` with retry.

### BE-21 (low) The job runner stores each local job's whole environment on disk

- Where: `src/puzzle/jobs/service.py:113-118` (`env = … or dict(os.environ)`), `src/puzzle/runner/jobs.py:83-88` (`asdict(job)` including `spec["env"]`).
- What: provider keys set as environment variables (ADR 0084 reads them from there) are written to `<user data dir>/runner/p1/jobs/<id>.json` (owner-only, removed on `forget`, left behind when Puzzle dies or a job fails to be forgotten).
- Fix: send only the variables the runtime sets plus an allow-list (PATH, HOME, TMP, locale, the thread variables), or strip names matching `*_KEY`, `*_TOKEN`, `*SECRET*`.

### BE-22 (low) The script sandbox on macOS allows writes everywhere but the project; on Linux without bubblewrap there is none

- Where: `src/puzzle/operators/scripts.py:150-156` (`(allow default) (deny network*)` plus a deny on the project folder), `:139-147` (`kind()` returns "none" without `bwrap`).
- What: a trusted project's script can write `~/.zshrc`, `<user data dir>/trusted.json`, `agent.toml` or `hosts.toml`. Trust is the stated gate (ADR 0062), so this is defence in depth, but the profile is one line from being tight.
- Fix: `(deny file-write*)` then allow the work folder and `/dev/null`; deny reads of the user data dir and `~/.ssh`; on Linux without bwrap say so in the trust dialog rather than only in `kind()`.

---

## To check (not verified, or a judgement for the maintainer)

1. **Run keys carry no Puzzle, adapter or solver version** (`graph.run_hashes`; `solver_version` is stored on the run, `store.py:67-70`, but not compared). After an adapter fix or a solver upgrade, earlier runs stay "up to date" and are reused. Suggest an `ADAPTER_EPOCH` per adapter in the key (bumped when its input writing changes results) and a tree note "solved with CalculiX 2.21, now 2.23".
2. **`delete_run` vacuums every table with zero retention** (`store.py:406-410`). delta-rs's default vacuum removes tombstoned files only, so another writer's uncommitted files should be safe, but a reader holding an older snapshot of `runs`/`groups`/`meshes` (the GUI on a worker thread) can hit "file not found". Also: field rows appended before a crash (no `runs` row) and untracked Parquet files are never cleaned. Worth one concurrency test and a "sweep orphans" step in `compact()`.
3. **The stored mesh is `meshed.mesh`, the solved one is `written`** (`solve.py:280-303, 466`): contact-adjusted node positions, helper groups and joint faces are not in the stored mesh. Results are keyed by node id so values line up, but adjusted coordinates and added groups are lost to post-processing. Confirm it is intended.
4. **A hand-made archive can carry `.puzzle/jobs.json`** (`archive.read` accepts any member in the manifest; `copy.save_as` only leaves it out on write). Its entries name a `workdir` and job ids; `Session.resume` would then read results from that path. Suggest dropping `.puzzle/` other than kept meshes on read.
5. **`find_run` hit with a missing mesh** (`solve.py:183`): `read_mesh` raises `KeyError` and the study can never be re-solved with `reuse=True` until the run is deleted. Treat as a cache miss.
6. **Trust is by folder path** (`trust.py`): content that arrives later in a trusted folder (a `git pull`, a sync client, a restore of a version that came in an archive before trust was given) runs without a new prompt. VS Code does the same; a hash of the code-bearing objects at trust time would close it.

---

## Done well

- One command layer with autonomy checks, proposals and an append-only history; batches are all-or-none on a trial copy with one undo.
- Solves run outside the project lock on a deep-copied snapshot, and results are keyed by content, so editing during a solve is safe.
- Failed, cancelled and timed-out runs are never stored; a run's `runs` row is committed last.
- Meshes are stored once by content hash; field ids are stored once per run of identical ids; `read_field` design (row group per step) is right for a viewer.
- Versions: content-addressed blobs written by temp-and-replace, manifests atomic, path checks on every manifest, a version before every restore, pruning that keeps user and known-good versions.
- Archives: format check, per-member SHA-256, traversal and link refusal, size and ratio limits, unpack to a staging folder then one rename, untrusted and "advise" on arrival, cleanup on failure.
- The job runner: atomic job records, `flock`-guarded single daemon, 0700 folder and 0600 socket, job ids validated before use as file names, remote fetch verified against a manifest before the host deletes its copy, deletion only of folders it owns.
- Remote scripts quote consistently (`shlex.quote`), and the scheduler driver handles a cancel during submit.

## Design-level recommendations (10× models, multi-user and remote use)

1. **Make "what a result depends on" one function with file contents in it** (BE-1, BE-2, to-check 1): `graph.keyed(project, root)` used by solve, status, optimise and the GUI; referenced files hashed with a size+mtime cache; adapter epochs. Add golden-key tests.
2. **Replace the mtime lock with an OS file lock and make saves transactional** (BE-4, 5, 6): temp-and-replace per changed file, fsync, a small journal, history append that repairs a torn tail. This is the minimum for GUI + MCP + CLI on one folder, and a prerequisite for projects on network shares.
3. **Stop reloading and rewriting the world per command** (BE-11): in-memory project with a change token, per-object dirty tracking, incremental status (only studies downstream of the changed object need new hashes: the dependency graph is already there).
4. **Stream results** (BE-18, BE-12): adapters yield steps; the store appends per step; readers get lazy handles; the `runs` table is cached by Delta version; `fields` partitioned by run only. Consider float32 storage for display-only fields as an option (halves disk and I/O) and a per-run summary row (min/max per field and step) so the tree, legends and agents never read arrays.
5. **Own every solver process tree on every platform** (BE-3): named containers, job objects on Windows, process groups on POSIX; port the runner daemon to Windows (named pipe or localhost TCP with a token) so solves outlive the GUI there too and Stop means stop.
6. **Schema versions and migrations** (BE-14) before the first release that others will open projects from.
7. **For multi-user/remote**: the one-file-per-object layout already merges well in git; add a project-level "format + writer version" in `project.toml`, make history entries carry the hash of the state they apply to (detects out-of-band edits and lets two machines' histories be reconciled), and keep results strictly a cache addressed by run key so a shared results store (object storage via Delta) becomes possible without changing the model side.

## Not covered

- `jobs/machine.py` (WSL distributions), `runner/backends.py` (PSI/J, scheduler state parsing) and `runner/client.py`/`build.py` were skimmed only; `jobs/remote.py` was read for copying, cancel and quoting, not the Slurm/PBS follow and outcome logic.
- `operators/sandbox_win.py` (AppContainer) was not read.
- `optimise.py` replay/resume correctness, `shootout.py`, `convergence.py` and `adapt.py` beyond their store look-ups.
- The MCP server's own threading and the GUI's use of `Session` (reload timing, worker threads).
- No real solver run was made; cache-hit and cancel paths in `run_study` were read, with only the container-stop behaviour exercised directly.
- Note: `git status` showed `CLAUDE.md` modified at the end of the review; this reviewer did not write to the repository.
