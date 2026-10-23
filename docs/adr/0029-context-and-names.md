# 0029. Show only what applies, by name: panels follow their context, and no raw value reaches the screen

- Status: Accepted (2026-10-23)
- Date: 2026-10-23

## Context

The interface review against Salome-Meca and PrePoMax (2026-10-23, the Claude Doc "Puzzle against Salome-Meca and PrePoMax", item 1) found two faults in today's screens:

- **The results bar stays above the view in the Geometry tab,** where it has no meaning.
- **The properties panel shows raw values.** A geometry shows "Source unit: null", "History: 0 items", and an empty "Extensions" row.

Both break rules the project already holds: names, not ids or keys, everywhere in the GUI (ADR 0003, CLAUDE.md conventions). Nothing checks the rules automatically, so new panels drift.

## Decision

1. **Each panel belongs to a context and shows only there.**
   - **Contexts:** Model, Geometry, Mesh (ADR 0019), Results, and Sketch.
   - **The results bar** shows only while a run or derived result is shown, and hides when the Geometry or Mesh tab is current.
   - **Ribbons and pick bars** belong to their tab in the same way.
   - `MainWindow` keeps one `context` value. Panels subscribe to it rather than each tab hiding others by hand.
2. **The properties panel never shows a raw value.**
   - **Unset optional values** are shown as "(none)" with the field's readable name, or are hidden when they only apply in another mode (e.g. "Source unit" for a history geometry).
   - **Empty lists and dicts** are folded into a single "Add…" row, not "0 items".
   - **Every field label comes from one table** (`gui/choices.py` labels, extended with a per-kind field-name table). Today some labels are derived from the key by capitalising it.
   - **Units follow the display units** (ADR 0024, ADR 0030).
3. **A test enforces it.** For each object kind, the test fills the properties panel from the example projects and from a project made with every feature type. It fails on any shown text that is `null`, `None`, a UUID, a snake_case key, or an `N items` placeholder. New object kinds are covered by the same test.

## Consequences

- The screens get quieter at once; the rule stops recurring.
- Field labels need a table entry per new field (the test names the missing ones).
- Hiding mode-specific fields needs a small per-kind rule ("show `source_unit` only when `source` is set"), kept beside the labels.

## Alternatives considered

- Fix the two screens and move on: the rule would drift again with each new panel.
- Generate labels from Pydantic field descriptions: descriptions are written for agents and are too long for a property row.
