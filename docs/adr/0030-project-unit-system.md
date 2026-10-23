# 0030. A unit system per project, chosen when it is made

- Status: Proposed
- Date: 2026-10-23

## Context

Display units are a per-user preference today (ADR 0024), SI by default. The review (item 2) found that SI reads badly for mechanical work:

- stresses show as 1.21e+08 Pa;
- 20 °C shows as 293.15 K.

PrePoMax asks for the unit system at File → New (unitless, SI, mm-ton-s-°C, m-ton-s-°C, in-lbf-s-°F) and shows it in the status bar. A project's people usually agree on its units (a shop works in mm and MPa, a client in inches), while a user's default suits the projects they start.

ADR 0024's rule stays: **display units never change results.** Values are stored as typed and converted to SI for solvers.

## Decision

1. **A project may name its display unit system** in `project.toml` (`units = "engineering"`). It is one of ADR 0024's systems: SI, mm-N-MPa-tonne ("engineering"), consistent mm, imperial, or custom per quantity.
   - When set, it wins over the user's preference while that project is open.
   - When unset (older projects), the user's preference applies, as now.
   - Changing it is a command (`set_units`), so it is undoable and logged, like every project change.
2. **New Project asks.** The dialog offers the systems by name ("Millimetre, newton, megapascal (mm, N, MPa, t, °C)"), defaulting to the user's preference. The user's preference becomes "units for new projects".
3. **The status bar shows the system** ("mm · N · MPa · °C"). Clicking it opens the project's unit choice.
4. **Numbers read as people write them.**
   - Colour bars, the results bar, probes (ADR 0032) and tables format values with three to four significant digits.
   - Exponents are used only outside 1e-3 to 1e5 in the shown unit.
   - Temperatures show in the system's temperature unit (°C for engineering and SI-with-°C).
5. **Agents read units from the outline** (one line: "units: engineering (mm, N, MPa, °C)"), and MCP tools keep returning SI with units named, as now.

## Consequences

- A project opened by two people shows the same numbers to both. A user who prefers otherwise can still override it per session from the status bar (not saved to the project).
- `settings.display_units()` gains a project argument. Every GUI caller goes through one helper (`units.shown`), so the change is contained.
- Tests that type millimetres can set the project's system instead of the `engineering_units` fixture.

## Alternatives considered

- Change the per-user default to engineering units: it helps most users, but it doesn't make a project read the same for everyone.
- Store values in the project's units: it breaks ADR 0024's rule that values stay as typed and SI for solvers, and makes unit changes rewrite files.
