# 0024. One application Settings dialog for per-user preferences

- Status: Accepted
- Date: 2026-10-22

## Context

The maintainer's answers to ADRs 0019 to 0023 put several choices in "the application's Settings". Puzzle has no such place yet: the agent keeps its own file (`<user data dir>/agent.toml`, ADR 0015), and everything else is either in a project or fixed in code. The choices are preferences of a person on a machine, not of a model: two people opening the same project may want different units or previews, and the project's results must not depend on them.

## Decision

1. **One preferences file per user**, `<user data dir>/settings.toml`, read and written by `puzzle.settings`. Like `agent.toml`, it is never in a project. Each preference is typed, with a readable name, a default and a group. Unknown keys are kept, so a newer Puzzle's preferences survive an older one.
2. **One Settings dialog** (Puzzle → Settings… on macOS, File → Settings… elsewhere). Groups are listed down the side and generated from the preference definitions, with readable names and dropdowns (ADR 0003's rule). Agent settings move into it, as their own group.
3. **Preferences never change results.** Anything that changes what a solver computes stays in the project and its run key: element types chosen for a mesh, solver settings, a Netgen optimisation pass. A preference only chooses defaults offered when something is made, what the GUI shows, and how it behaves.
4. **The first preferences**, from ADRs 0019 to 0023:

| Group | Preference | Choices (default first) |
| --- | --- | --- |
| Units | Display units | SI; an SI variant (mm-N-MPa-tonne, ...); the user's own choice per quantity; Imperial |
| Meshing | Element types for new meshes | each solver's preferred types; Puzzle's neutral defaults |
| Meshing | Mesh preview | on every change (debounced); on demand |
| Meshing | Offer Netgen as Gmsh's optimiser | on; off |
| Solvers | Settings shown | standard; power user (the whole option set) |
| Agent | Provider, model, autonomy defaults, images | as `agent.toml` today |

## Built (2026-10-22)

`puzzle.settings` defines the preferences and reads and writes `settings.toml`. `PUZZLE_USER_DIR` overrides the user folder, which tests use. The Settings dialog has a page per group (Units, Geometry, Meshing, Solvers, Agent), each with readable choices and a Restore defaults button. The Agent page writes `agent.toml` and stores API keys in the keychain. `puzzle settings` lists and sets preferences from the command line.

Applied now:

- **Display units** in the properties panel (a bare number is in the unit shown), the pick bar's measurements, step and tool forms, and the sketcher's dimensions.
- **Geometry tool preview**, live or on demand (a Preview button).

The meshing and solver preferences are stored and shown greyed, saying which ADR brings them into use (0019, 0023). Results' colour bars still show SI and will follow the display units when result fields carry their units.

## Consequences

- Display units touch every place a value is shown or typed: the properties panel, step and tool forms, the sketcher, results. They convert through `puzzle.model.units` at the edge, and values stay SI inside (CLAUDE.md conventions).
- Tests set preferences explicitly, as they set `PUZZLE_MATERIALS`, so a developer's own settings never change a test.
- New features add their preferences to the definitions; the dialog shows them without more GUI work.

## Alternatives considered

- Preferences in each project: shared by everyone who opens it, which is wrong for display choices and invites results to depend on them.
- Qt's `QSettings`: platform-native storage, but opaque to the CLI and agents, and harder to test than a TOML file.

## Amended (2026-11-17, ADR 0090 package 10)

Every typed quantity is read by `units.parse_quantity`: a bare number is in the unit shown, `5mm` and a decimal comma are read, and a unit that is unknown or of another dimension is refused in words. A form gives back the stored value of a field that wasn't edited, so switching units and pressing OK changes nothing stored. Left: six panels show fixed units (kN, MPa, cm⁴, N·m), labelled but not following Settings > Units (PANEL-19), and optimisation plots are in SI.

## Amended (2026-11-18, ADR 0090 package 11)

Display units default to the engineering system (mm, MPa, °C) instead of SI (the maintainer, 2026-11-17; ADR 0090 UX-11). A user who had chosen a system keeps it; stored values and what solvers get are unchanged. The Settings dialog names the group of panel preferences "Agent panel", apart from the Agent page of models and keys, and shows no ADR numbers.

## Amended (2026-11-18, ADR 0090 package 16)

The Built section is stale (ADR-E-8): the meshing and solver preferences are
in use (Mesh preview, Netgen optimiser, element types, solvers' level), results draw in
the display units, the dialog is Project → Settings…, and the groups are eleven
(Interface, 3D mouse, Results, Versions, Connections and Schematic added).
