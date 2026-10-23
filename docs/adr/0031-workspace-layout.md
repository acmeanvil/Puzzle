# 0031. Give the model the room: collapsible docks, a view toolbar, an icon toolbar

- Status: Accepted (2026-10-23, with the maintainer's answers)
- Date: 2026-10-23

## Context

In today's main window (review item 3, screenshot of the cantilever results):

- **The Agent dock** takes about a third of the width even when unused.
- **The Messages dock** (the solver log) takes about a quarter of the height.
- **The main toolbar** is ten text buttons. "Delete", "Reload" and "Pick" don't say what they act on.
- **There are no view controls** beyond the mouse: no fit, standard views, edge display, projection, or screenshot.

PrePoMax and Ansys keep the model centred, with a slim output pane and a view toolbar. Puzzle's view is rendered off screen (`SceneView`), so any overlay controls must be Qt widgets around or over it, not VTK widgets.

## Decision

1. **Docks start collapsed and open when needed.**
   - **Agent:** a side tab, opened by clicking it or by a proposal arriving.
   - **Messages:** opens on a run, an error or a warning, and folds back to its tab bar after a successful run.
   - **Layout is kept:** sizes and open state are saved per user with `QMainWindow.saveState` in `settings.toml` (ADR 0024), with Window → Reset layout.
2. **A view toolbar over the 3D view** (a translucent Qt toolbar in the view's top-right corner):
   - fit all;
   - isometric, and ±X, ±Y, ±Z views;
   - edges on or off, wireframe;
   - perspective or orthographic;
   - copy and save an image;
   - section cut (ADR 0038).

   Shortcuts: F fit, 1–6 for the standard views, I isometric. Each control calls a `Viewport` method, so agents and tests reach the same actions.
3. **The main toolbar gets icons with short labels, grouped:** Project (New, Open, Undo), Model (Mesh, Run), Materials (Add, Builder), Selection (Pick).
   - "Delete" and "Reload" leave the toolbar for context menus and the Project menu, where they act on what is selected.
   - **Icons are bundled SVGs from a permissively licensed set,** compatible with GPL-3.0 and recoloured to the palette. Which set (Lucide, ISC; Tabler, MIT; or another) is chosen later, with the Qt polish ADR (plan Phase F). Until then a small interim set is used, kept behind one icon lookup so the set can be swapped.
   - **Labels or not is a setting** (Settings → Interface, ADR 0024): icons with labels under them, or icons only with tooltips. Tooltips are always there.
4. **The status bar carries the essentials:** the unit system (ADR 0030), the current context, background tasks with a cancel button, and the last message.

## Consequences

- The view gains about 40 % of the window on a laptop screen.
- Icons add a small asset folder and a licence notice in `docs/` and the About box.
- Docks that open by themselves must not steal focus while the user types (the Agent dock opens without focus).

## Alternatives considered

- Leave layout to the user (docks can already be closed): first impressions and every new user's session start cluttered.
- A ribbon for the whole window, as Ansys: heavier, and the Geometry and Mesh tabs already have their own ribbons (ADR 0017, 0019).
