# 0030. A unit system per project, chosen when it is made

- Status: Accepted (2026-10-23, with the maintainer's answer)
- Date: 2026-10-23

## Context

Display units are a per-user preference today (ADR 0024), SI by default. The review (item 2) found that SI reads badly for mechanical work:

- stresses show as 1.21e+08 Pa;
- 20 °C shows as 293.15 K.

PrePoMax asks for the unit system at File → New (unitless, SI, mm-ton-s-°C, m-ton-s-°C, in-lbf-s-°F) and shows it in the status bar. A project's people usually agree on its units (a shop works in mm and MPa, a client in inches), while a user's default suits the projects they start.

ADR 0024's rule stays: **display units never change results.** Values are stored as typed and converted to SI for solvers.

## Decision

1. **A project has a local switch, "Use the project's units", that overrides the global setting** (the maintainer's choice, 2026-10-23).
   - **When on,** the project names its display unit system in `project.toml` (`units = "engineering"`), one of ADR 0024's systems: SI, mm-N-MPa-tonne ("engineering"), consistent mm, imperial, or custom per quantity. It wins over the user's global preference for everyone who opens that project.
   - **When off,** or unset (older projects), the user's global preference applies, as now.
   - Turning it on or off, or changing the system, is a command (`set_units`), so it is undoable and logged, like every project change.
2. **New Project asks.** The dialog offers the systems by name ("Millimetre, newton, megapascal (mm, N, MPa, t, °C)"), defaulting to the user's preference. The user's preference becomes "units for new projects".
3. **The status bar shows the system** ("mm · N · MPa · °C"). Clicking it opens the project's unit choice.
4. **Numbers read as people write them.**
   - Colour bars, the results bar, probes (ADR 0032) and tables format values with three to four significant digits.
   - Exponents are used only outside 1e-3 to 1e5 in the shown unit.
   - Temperatures show in the system's temperature unit (°C for engineering and SI-with-°C).
5. **Agents read units from the outline** (one line: "units: engineering (mm, N, MPa, °C)"), and MCP tools keep returning SI with units named, as now.

## Consequences

- A project opened by two people shows the same numbers to both when its switch is on. A user who prefers otherwise turns the switch off, which is a change to the project, so it is visible and undoable.
- `settings.display_units()` gains a project argument. Every GUI caller goes through one helper (`units.shown`), so the change is contained.
- Tests that type millimetres can set the project's system instead of the `engineering_units` fixture.

## Alternatives considered

- Change the per-user default to engineering units: it helps most users, but it doesn't make a project read the same for everyone.
- Store values in the project's units: it breaks ADR 0024's rule that values stay as typed and SI for solvers, and makes unit changes rewrite files.
