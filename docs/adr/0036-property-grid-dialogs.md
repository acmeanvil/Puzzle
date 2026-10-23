# 0036. Long editors as property grids with sections, a description box and errors on top

- Status: Accepted (2026-10-23)
- Date: 2026-10-23

## Context

Recent dialogs (combinations, equations, the fatigue tool) are long single-column forms. The review (item 8) found faults in the fatigue tool's dialog:

- more than 1,700 pixels tall;
- table columns cut ("Fully reve", "d case or combina");
- a long note clipped;
- the endurance limit asked in Pa under SI;
- the blocking error at the bottom, below the fold on a laptop.

PrePoMax's editors are property grids: name and value rows grouped in sections, with a description box below that explains the selected row. Puzzle's properties panel is already a tree of rows.

## Decision

1. **One editor component for every long form,** `gui/form.py` `FormPanel`:
   - sections that fold, with the essential section open;
   - rows of name, value and unit, with dropdowns of readable names (ADR 0003);
   - a description box under the grid for the focused row (the text from the field's description table, with the source reference, e.g. "Shigley Eq. 6-18");
   - a message banner at the top for the error that blocks OK, and warnings below it;
   - a fixed width that fits the longest choice, and scrolling inside, never a dialog taller than the screen.
2. **Tables inside forms** size their columns to their contents, with dropdowns no narrower than their longest name. Headers wrap rather than clip.
3. **Units in labels follow the display units** (ADR 0030): "Endurance limit S'e (MPa)".
4. **The same component serves dialogs and the docked panel.** Tool options in the Properties dock (ADR 0017's ToolPanel) and the fatigue, combination, equation, step and material dialogs move to it. That's one place to get spacing, focus order and keyboard use (Enter applies, Esc cancels) right.

## Consequences

- Dialogs get shorter (sections fold) and explain themselves (the description box) without long notes in the form.
- The forms move to `FormPanel` one at a time, newest first. The tests, which set widgets by name, need the widgets kept under the same attribute names.
- The description table doubles as tooltips and as text for the Advisor (ADR 0040).

## Alternatives considered

- Wizards (pages with Next): they hide settings that interact (the life model changes which factors apply), and experts dislike them.
- The plain QFormLayout as now, with fixes: the same problems return in the next dialog.
