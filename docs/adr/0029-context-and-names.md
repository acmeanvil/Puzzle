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

## Built (2026-10-23)

- **Contexts:** `MainWindow.set_context` ("model", "geometry", "sketch", "results") owns the results bar's and the ribbon's visibility. Tab changes, sketches, runs and derived results call it.
- **Properties:**
  - unset values read "(none)", or a field's own words ("From the file" for a source unit);
  - Yes and No replace true and false;
  - floats show at six significant digits;
  - a geometry shows only its own mode's fields (file fields for an imported part, the history for a built one) through the `APPLIES` rules in `gui/properties.py`.
- **Empty lists and dicts** are not shown. Their Add actions are in the item's context menu, rather than an "Add…" row, so no row exists that does nothing.
- **The rule test:** `tests/test_gui_names.py` selects every tree item of the example and benchmark projects and of a history geometry. It fails on any shown text that is an id, `null`/`None`, "N items" or a snake_case key, and names the field.

## Consequences

- The screens get quieter at once; the rule stops recurring.
- Field labels need a table entry per new field (the test names the missing ones).
- Hiding mode-specific fields needs a small per-kind rule ("show `source_unit` only when `source` is set"), kept beside the labels.

## Alternatives considered

- Fix the two screens and move on: the rule would drift again with each new panel.
- Generate labels from Pydantic field descriptions: descriptions are written for agents and are too long for a property row.
