# 0045. Look and feel: one theme from tokens, an icon set, better docking, feedback and accessibility

- Status: Accepted (2026-10-25, with the maintainer's answers)
- Date: 2026-10-25

## Context

Phase F of the plan (docs/plan-2026-q4.md) was set for after the features settle. The GUI's look today grew one panel at a time:

- **Colours and styles:** 52 inline `setStyleSheet` calls with hard-coded colours. The amber of "needed" and the greys of hints are written several times over, and the three ribbons (Geometry, Mesh, Results) each carry their own copy of one style sheet.
- **Themes:** no theme of Puzzle's own. Qt's default style per platform with the system palette, so a dark system theme half-works: the interim icons follow the palette, but text drawn in fixed black (the legend) or "gray" does not.
- **Icons:** ten interim 24-pixel line drawings (`gui/icons.py`, ADR 0031), made to be replaced by "the icon set proper" in this phase. Menus and ribbons have no icons.
- **Docking:** Qt's `QDockWidget` (ADR 0031): Model, Properties, Messages (folding to its tab bar), Agent and Advisor. There are no tabbed groups, no auto-hiding side bars and no saved layouts beyond the last one.
- **The 3D view:** a plain white background. Overlays (labels, the cut bar, the view toolbar) are Qt widgets over the off-screen render, which must stay so (CLAUDE.md: on-screen OpenGL left unrefreshed rows on this Mac).
- **Feedback:** messages in the status bar; long work runs in the background with a status line, and finished runs fold the Messages dock open.
- **Accessibility:** menus now reach everything, with shortcuts (the menu bar). State marks carry a shape as well as a colour (✓ ● ○), and the legend offers viridis. No high-contrast theme, and focus order is not reviewed.

Reference points: Ansys Mechanical and Discovery (a graded 3D background, auto-hiding panels, quiet chrome), COMSOL (one toolkit's look on every platform), PrePoMax (dense, functional), and Salome (native Qt widgets, the review's "dated" complaint).

## Decision (proposed)

1. **Design tokens in one place.** A `gui/theme.py` holds every colour, spacing step, radius and font size as named tokens, in a light set and a dark set:
   - colours: surface, raised surface, text, muted text, accent, focus, the state colours (done, needed, warning, error, running), and selection;
   - spacing on a 4-pixel grid; one set of radii; three font sizes plus the title size.

   Puzzle's application style sheet and Qt palette are generated from them. Inline `setStyleSheet` calls go: widgets take an object name or a property (`role="muted"`, `state="needed"`) and the one style sheet styles them. The three ribbons become one ribbon component with one style.
2. **Themes:** Settings > Interface > Theme: follow the system (default), light, dark, or high contrast. The 3D view follows it too: its background (a soft vertical gradient in light, as Ansys draws it, or plain), the legend's and labels' text, and the edge colours.
3. **One widget style on every platform:** Qt's Fusion style, coloured by the tokens, so Puzzle looks and behaves the same on macOS, Windows and Linux, and the style sheet styles every control. (The alternative, each platform's native style, ignores parts of a style sheet on macOS.) **Question 1.**
4. **An icon set:** an open set with a permissive licence compatible with GPL-3.0, drawn as SVG in the theme's colours, at 16, 20 and 24 pixels. Candidates (all line icons, as the interim ones are):
   - Tabler (MIT, about 5,000);
   - Lucide (ISC, about 1,500);
   - Phosphor (MIT, six weights);
   - Material Symbols (Apache-2.0).

   Engineering icons no set has are drawn for Puzzle in the chosen set's style: a fixed support, a force, a pressure, a mesh, a probe, a beam section, a fastener and so on. Icons go on the ribbons, the toolbar, the menus and the tree's sections; the labels-under-icons setting stays. **Question 2.**
5. **Docking:** evaluate the Qt Advanced Docking System (PySide6-QtAds, LGPL-2.1, which GPL-3.0 can use). It gives:
   - docks tabbed together (Agent and Advisor in one place);
   - auto-hiding side bars, like Ansys's unpinned panels;
   - floating windows on a second screen;
   - saved and named layouts ("modelling", "results", "two screens").

   It is adopted if it installs from conda-forge or PyPI on all three platforms, works with the off-screen view, and passes the GUI tests offscreen. Otherwise Puzzle keeps `QDockWidget` and adds tabbed groups and named layouts itself. **Question 3.**
6. **A start page** when no project is open: recent projects with their last state, New, Open, the cantilever example, and a link to the Advisor, in the same widgets (no QML, so one toolkit stays).
7. **Feedback:**
   - **toasts:** non-blocking notes for a finished run, export or mesh, with an action ("Show results", "Open folder");
   - **progress:** in the status bar for long work, with Cancel;
   - **busy states** on the panel waiting for work (the Advisor, Properties).

   Motion only where it helps (a dock folding, 150 ms), never on the 3D view.
8. **Density and type:** the system UI font; a comfortable and a compact density (Settings > Interface), for laptops and large screens.
9. **Accessibility:**
   - keyboard paths through the tree, Properties, the ribbons and every dialog, with focus order reviewed;
   - a visible focus ring from the tokens;
   - the high-contrast theme;
   - state shown by shape and words, never colour alone (as ✓ ● ○ already are);
   - colour-blind-safe defaults where colour carries meaning;
   - text scaling that follows the system.
10. **Checked by looking:** a script renders the main screens in light and dark (the tree and Properties, the Geometry, Mesh and Results ribbons, the Advisor, the agent, the legend, dialogs) to PNGs for review on each change to the theme. It is not a pixel-compared test.

**Order of work:**
1. tokens and themes (with the 3D view);
2. inline styles moved into the one style sheet, and the one ribbon;
3. the icon set and Puzzle's own engineering icons;
4. docking (after the packaging check);
5. the start page, toasts and progress;
6. accessibility and density.

## Consequences

- One place decides how Puzzle looks; dark mode and a high-contrast theme come with it rather than per panel.
- Every new panel must use roles and tokens instead of inline colours. A test can enforce it: no colour literals in `gui/` outside `theme.py`.
- A third-party docking library is a new dependency to package on three platforms, which is why it is checked first and has a fallback.
- Fusion gives up macOS-native controls; some Mac users notice. The tokens make it look intentional rather than foreign.

## The maintainer's answers (2026-10-25)

1. **Widget style:** Fusion everywhere.
2. **Icon set:** Tabler to start.
3. **Docking:** try the Qt Advanced Docking System first. *Superseded the same day; see "Docking, decided" below.*
4. **The 3D background:** a soft gradient by default, changeable in Settings.

## Alternatives considered

- **A ready-made theme** (qt-material, QDarkStyle): quick, but it styles Qt's controls only, not Puzzle's own panels, and its colours would still fight the inline styles.
- **QML (Qt Quick) for the interface:** modern visuals, but a second toolkit beside the widgets the whole GUI is built on, and the off-screen 3D view embeds in widgets.
- **Each platform's native look:** familiar per platform, but three looks to test, and style sheets only partly apply on macOS.

## Built (2026-10-25)

- **Steps 1 and 3, and most of step 2:**
  - `gui/theme.py`: tokens for light, dark and high contrast; Fusion on every platform; the palette and one style sheet made from the tokens; `role(widget, "muted" | "title" | "error" | "needed" | "done")` in place of inline colours (36 inline styles moved; a few computed colours remain).
  - Preferences under Settings > Interface: Theme (follow the system, light, dark, high contrast) and 3D background (soft gradient by default, or plain). The 3D view keeps a light ground in every theme, where result colours read best.
  - Ribbon buttons are drawn from palette colours, so they follow the theme.
  - **Icons:** Tabler's outline set (MIT), vendored under `gui/tabler/` with its licence, mapped in `icons.TABLER`. Puzzle's own mesh and wireframe icons are drawn in Tabler's grid and stroke. Icons are redrawn in the theme's text colour when the theme changes.
- **Docking (step 4): the packaging check.** PySide6-QtAds 5.1.1 (LGPL-2.1) has PyPI wheels for macOS, Linux and Windows, but none on conda-forge. It pins PySide6-Essentials 6.11.2 from PyPI. In a test it docked the off-screen 3D view, tabbed Agent with Advisor, auto-hid Messages in a side bar, and saved and restored layouts. Adopting it means taking PySide6 from PyPI in the pixi `gui` feature instead of conda-forge, while VTK keeps linking conda-forge's own Qt (it loads none of Qt's libraries unless its Qt module is imported, which Puzzle doesn't). That choice is the maintainer's: until then the GUI keeps `QDockWidget`.

- **Heading colours (2026-10-29, the maintainer's request):** the model tree's and Properties' headings coloured by depth from the theme's `levels` (the project and sections, then what is under them, ...), a medium weight; items that don't expand keep the plain text colour (`theme.colour_levels`; Settings > Interface > Heading colours).

## Docking, decided (2026-10-25, superseding answer 3)

The packaging check showed the Advanced Docking System would cost flexibility:
- it pins one exact PySide6 version, so every Qt upgrade waits on that project;
- it has no conda-forge build, which Puzzle's packaging aims for;
- it would put a second Qt beside VTK's.

So Puzzle keeps Qt's own `QDockWidget`, behind one module of its own, `gui/docking.py` (`Docks`). The window adds, shows, tabs, hides and saves its panels only through it, so a different docking library can replace Qt's later by changing that module alone. On top of Qt's docks it gives what the Advanced Docking System offered:
- **tab groups:** the Agent and the Advisor share the right side as tabs;
- **named layouts:** Window → Layouts → Modelling, Results, Agent beside the model, and the user's own saved ones ("Save current layout…"), kept per user;
- **auto-hide:** a side panel can be set to hide into a slim bar on its edge; clicking its name there shows it, and it hides again once the view is used;
- **floating** panels on a second screen, as Qt's docks already allow.
