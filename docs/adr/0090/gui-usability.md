# Puzzle GUI usability review

Reviewer brief: "Do a GUI review with an eye for useability by the user understanding that useability is paramount. The app needs maximum functionality with the simplest user interface."

Date: 2026-11-16. Branch `development` at `da26cde`. Windows 11, 3840 x 2160 at 150 % (2560 x 1440 logical).

## How this was done

The GUI was run on the native platform (so the 3D view draws) on scratch copies of `examples/cantilever` and `examples/bracket_assembly`, with a scratch `PUZZLE_USER_DIR`. Nothing in the repository was changed.

All evidence is under one folder, called `U\` below:

`<scratch>\review\usability\`

| Folder | What | Made by |
| --- | --- | --- |
| `U\theme\light\` | The maintainer's own `scripts/theme_screens.py` output (1500 x 950 window) | `theme_screens.py` |
| `U\s1\` | Bracket assembly at 1500 x 950: start page, tree, tabs, every dock, every Settings page, every top-level menu as a sheet, right-click menus; `dump.txt` holds every menu and context menu as text with counts | `U\cap1.py` |
| `U\s2\` | Maximised (2560 x 1369): empty new project, cantilever meshed, run, results, pick mode, a geometry tool, 14 dialogs; bracket contact search and bolt Properties; `dump.txt` | `U\cap2.py` |
| `U\s3\` | Laptop sizes (1536 x 824, 1366 x 728): dock widths per tab, results, the Legend panel, a refused run; `dump.txt` | `U\cap3.py` |

Read first: CLAUDE.md, ADRs 0017, 0029, 0033, 0036, 0040, 0045, 0071 (decision 8), 0072 (forms), and the manual's getting-started, interface, loads-and-supports and results pages.

Severity: **high** = blocks or badly slows a core task, or misleads; **medium**; **low** = polish. Effort: S (hours to a day), M (days), L (a week or more).

## Summary

What is good, and should be kept:

- **The Advisor** is the best onboarding device here: computed, short, one button per step (`U\s2\a1_empty_project.png`, `U\s1\03_model_study_advisor.png`).
- **State icons and tooltips in the tree** say why a study will not run (`Missing input: ... has no fixed or displacement condition`).
- **Names, not ids**, hold almost everywhere. Dropdowns of readable names are consistent.
- **Toasts with an action** ("Tip load failed, Show why") and one-form creation dialogs (New study, New component) are right.
- **The Geometry tab's inline tool with live preview** is the right pattern; the rest of the app should copy it.
- **Find contacts** is a clear one-table dialog (`U\s2\f1_find_contacts.png`).

What is wrong, in one paragraph: the application has grown by adding a menu item, a dock and a Properties row for every feature, and the simple path is no longer visible among them. The menu bar holds 342 commands, the project's right-click 98, and there are 13 dockable panels plus 6 message tabs. A first static analysis needs four concepts no competitor asks a beginner for (a named selection before a load, a mesh recipe, a component, a physics) and about 15 separate actions. Adding a load cannot pick a face. Properties shows the schema rather than the task. On a laptop the Geometry tab pushes the window off the screen and crushes the side panels. There is no Redo.

Counts: **8 high, 22 medium, 8 low** (38 findings).

## Measured facts

Menu bar (from `U\s1\dump.txt`, with the study "Bolted" selected):

| Menu | Commands | Submenus | Deepest level |
| --- | --- | --- | --- |
| Project | 28 | 2 | 2 |
| Geometry | 71 | 8 | 2 |
| Materials | 5 | 0 | 1 |
| Mesh | 13 | 4 | 2 |
| Model | 73 | 11 | 4 |
| Results | 111 | 15 | 4 |
| Window | 33 | 2 | 2 |
| Help | 8 | 0 | 1 |
| **Total** | **342** | **42** | |

Other counts:

- Results → Operators → New operator holds 95 of the Results menu's 111 commands.
- Right-click on the project's top item: 98 commands (the same 95 operators plus 11).
- Right-click on a study: 26 commands. On a component: 23. On a geometry: 59, of which "Add step" is a flat list of 46.
- "Add feature" for solid mechanics: 25 items in one flat list.
- View toolbar: 21 icon-only buttons.
- Docks: Model, Properties, Sections, Messages (6 tabs: Output, Solution, History, Problems, Plots, Table), Agent, Advisor, Bolts, Contacts, Versions, Optimisation, Convergence, Jobs, Members, Welds, Schematic.
- Settings: 13 groups (one name, "Agent", appears twice), 57 preferences.
- Main toolbar: New, Open, Undo, Mesh, Run study, Add material, Material builder, Pick, then Agent, Messages, Settings.

The core task, counted (import a STEP, mesh, material, support, load, run, stress, probe, export), following the manual's own steps:

| # | Action | Where | Dialogs |
| --- | --- | --- | --- |
| 1 | New project (folder, name, units) | toolbar | 1 + folder browser |
| 2 | Import geometry | Geometry menu or Advisor | file dialog |
| 3 | Pick on, filter Faces, double-click a face, New selection…, type a name | toolbar, pick bar | 1 |
| 4 | Same again for the loaded face | | 1 |
| 5 | Add material | toolbar | 1 |
| 6 | New mesh recipe, then edit Global size in Properties | Mesh menu | 0 |
| 7 | New component (geometry, recipe, name) | Model → Components | 1 |
| 8 | Assign material (material, body selection) | right-click component | 1 |
| 9 | Add physics → Solid mechanics | right-click component | 0 |
| 10 | Add feature → Fixed support, choose the selection | right-click physics | 1 |
| 11 | Add feature → Force, choose the selection, expand Values in Properties, type the force | right-click physics, Properties | 1 |
| 12 | New study (component, solver, analysis, physics, name) | Model → Studies | 1 |
| 13 | Select the study, Run | toolbar | 0 |
| 14 | Select the run in the tree, choose von Mises in Result | tree, results bar | 0 |
| 15 | Probe, double-click the part | Results ribbon | 0 |
| 16 | Export… | Results → Export | 1 + folder |

That is 16 steps, about 10 dialogs, 5 different places to start an action (toolbar, menu bar, tree right-click, pick bar, Properties), and 9 nouns to learn (geometry or part, selection, mesh recipe, component, physics, feature, study, step, run). Ansys Mechanical and Fusion do the same job in about 7 steps with 4 nouns (geometry, material, load or support, solve), because the mesh, the "component", the physics and the study exist by default and a load is put on a picked face.

## Findings

### High

**UX-1. The Geometry tab forces the window to 1597 px wide and crushes the side panels, for good.** High. Both. Effort S to M.

- Where: `gui/ribbon.py` / the Geometry ribbon's minimum width; `gui/docking.py`.
- Evidence: `U\s3\dump.txt` lines "WIDTHS". At 1536 x 824: Model 360, Properties 360, Advisor 420 before; after clicking the Geometry tab the window is 1597 wide (wider than the screen it was sized for), Model 191, Properties 191, Advisor 98. Going back to Model does not restore them. At 1366 x 728 the window also grows to 1597. The window's `minimumSizeHint` is 1597 on the Geometry tab, 981 on Mesh, 892 on results, 450 on Model.
- Seen in: `U\s3\l_1536_geometry.png` (tab bar reads "Model | Geometry | M◂▸", the hint text is cut at "The tools are in the ribbon abov", the Advisor wraps one word per line, Properties values cut to "From the f"). The maintainer's own script shows the same: `U\theme\light\geometry.png`, `U\theme\light\results.png` (Properties "Total defo", tree "Tip load · Cal…").
- A maximised 2560-wide window is unaffected (`U\s2\dump.txt`), which is probably why this went unseen.
- Recommendation:
  1. Give every ribbon the pick bar's treatment (`QLayout.SetNoConstraint`, already used in `PickBar` with the comment "never wider than the view lets it be"), and let the folded ribbon fold further into a single "Tools ▾" overflow button, so its minimum width is near zero.
  2. Give Model, Properties and the right-hand docks a minimum width of about 260 px so the centre gives way first.
  3. Add a test that opens each tab at 1366 x 728 and asserts `window.width() <= 1366` and each side dock >= 260.

**UX-2. A load or support cannot be put on a picked face; it needs a selection named beforehand, and its value is asked nowhere.** High. Both, novices most. Effort M.

- Where: `gui/menus.py` `add_feature`; Properties.
- Evidence: `U\s2\d15_add_feature_via_menu.png`: the whole "Add Pressure" dialog is one dropdown, "Apply to which face selection?". No value, no direction, no pick. With no selections yet the only response is a status-bar line for 8 seconds: "No face selections on X yet: pick some in the view first". After OK the user must find the new item in the tree and open the collapsed "Values" group in Properties to type the number (`U\s2\d16_after_add_pressure.png`: Type, Selection, a folded Values, then When, Amplitude, Axes, Applies in steps, Case).
- Every reference tool (Ansys, Abaqus/CAE, COMSOL, Fusion, SimScale) lets the user click the face while defining the load, and shows the magnitude in the same panel.
- Recommendation: make adding a load the same inline tool the Geometry tab already has.
  1. "Add load or support" opens a `FormPanel` in the Properties dock (as `ToolPanel` does), titled e.g. "New force".
  2. First row "Applied to": a dropdown of existing selections plus a **Pick in view** button. Picks made there create a selection named after the load ("Force 1 faces") in the same batch.
  3. If faces are already picked when the command starts, use them.
  4. The feature's values (magnitude, direction) are rows of the same form, with units; expert rows (When, Amplitude, Axes, Steps, Case) sit in a folded "Advanced" section.
  5. The load symbol previews live in the view; Apply submits one batch.
  6. Editing an existing load reopens the same panel.

**UX-3. A first analysis needs a mesh recipe, a component, a physics and a study made by hand, in that order, from three different menus.** High. Novices. Effort M.

- Where: getting-started steps 3 to 5; Mesh menu, Model → Components, tree right-click, Model → Studies.
- Evidence: the step table above; `U\s2\a1_empty_project.png` (the Advisor's fourth step reads "Component: Make a mesh recipe first, then a component").
- "Component" and "mesh recipe" are Puzzle's own words. A new user does not know they need either, and the order (recipe before component, component before physics, physics before loads, then study) is only discoverable from the Advisor.
- Recommendation: a "New analysis…" form, offered automatically after the first geometry is imported or drawn, and placed first on the toolbar and the Advisor.
  - Fields: Analysis (Static structural, Modal, Steady heat, …), Material (library picker), Mesh (Coarse / Medium / Fine, sized from the bounding box).
  - It creates the mesh recipe, the component with every part, the material assignment on all bodies, the physics and the study in one batch, named after the geometry. Solver defaults to the best available for that analysis, with a "Solver" row folded under Advanced.
  - Nothing is removed: experts still build each object by hand. The objects are ordinary and editable.
  - The agent gets the same thing as one MCP tool, in line with the "plain, intent-level way in" principle.

**UX-4. There is no Redo and no Edit menu.** High. Both. Effort S to M.

- Where: `gui/main_menu.py`, `Window.TOOLBAR`.
- Evidence: `U\s1\dump.txt`: the only shortcuts are Ctrl+Z (Project → Undo), no Ctrl+Y or Ctrl+Shift+Z; "redo" appears nowhere in `gui/`. `commands/session.py` line 354 says "Undoing an undo redoes it", but nothing in the GUI offers that.
- Undo, Delete and Rename live under Project, between "Roll back to last known good…" and "New parameter set".
- Recommendation: add an **Edit** menu after Project: Undo, Redo (Ctrl+Y and Ctrl+Shift+Z), Delete, Rename, Duplicate, New group…, Find in tree… Put Redo beside Undo on the toolbar. Undo and Redo should name what they act on ("Undo Add force").

**UX-5. The menus are a catalogue, not a path: 342 commands, two menus four levels deep, and things filed where a user would not look.** High. Both. Effort M.

- Evidence: the table above; `U\s1\menu_Results.png`, `U\s1\menu_Model.png`, `U\s1\dump.txt`.
- Specific misfilings:
  - **Operators (95 commands) are under Results**, though a third of them are inputs, geometry and logic that feed the model.
  - **"Optimise and check the mesh" is under Results** (New optimisation, Check mesh convergence, Adapt the mesh, Add a skin-depth refinement). These are study actions.
  - **Project** holds Settings, Undo, parameters, amplitudes, coordinate systems, construction geometry, Import field, Remote hosts, "Who may connect…" and Trust.
  - **Geometry → Show and suppress** has 16 items mixing visibility of geometry, coordinate systems and construction geometry with colour.
  - **Model → Physics and loads → Add feature → Solid mechanics → item** is four levels for the most common modelling action.
- The same 95-operator tree is repeated on the project's right-click, making that menu 98 commands for what the manual calls "make the first of anything".
- Recommendation: see "A simpler information architecture" at the end. In short: Operators leave the menus for the schematic's palette (one "Add operator…" item that opens it); Optimise and convergence move to a Study menu; Project keeps only project-level things; Add load or support becomes two levels.

**UX-6. The Advisor describes a different study from the one on screen.** High (misleads). Novices. Effort S.

- Where: `advisor.py` / `gui/advisor_panel.py` context.
- Evidence: `U\s2\c4_results_vonmises.png`: von Mises results of "Tip load" are in the view, and the Advisor says "Next: Run", "Study: Conduction on CalculiX", "Results: Comes after a run". `U\theme\light\results.png` shows the same. `U\s2\d16_after_add_pressure.png`: Tip load has a green tick in the tree while the Advisor still says Next: Run for Conduction.
- A beginner following the Advisor will run again, or think the run failed.
- Recommendation: the Advisor's study is, in order, the selected study, the study of the selected run or result, the study last run, then the first. When the project has several studies, replace the single Study / Run / Results rows with one row per study ("Tip load: results ready, Show", "Conduction: not run, Run"). When a run is selected the heading should read "Results of Tip load" and offer next steps for results (Probe, Reaction, Export).

**UX-7. Properties shows the data model, not the task: no basic and advanced split, and fields that do not apply.** High. Both. Effort M.

- Where: `gui/properties.py` (`APPLIES`), every object kind.
- Evidence:
  - Study (`U\s2\c2_running.png`): Shootout (none), Convergence (none), Adapt (none), Morph (none), Remote (none), Seismic (none), Weak springs Auto, Weak spring factor 1e-06, Weak spring limit 0.001, Preload step Auto. The things a user wants (analysis type, time step, large deflection) are a level down or in another dialog.
  - Bolt (`U\s2\f4_bolt_selected_props.png`): about 20 flat rows; "Ground direction X, Y, Z" and "Ground length (none)" shown while Ground is No; "Kind: Beam fastener", "Elements 1", "Thread kf (none)".
  - Load: the value is inside a folded "Values" group while When, Amplitude, Axes, Applies in steps and Case are open.
  - Mesh recipe (`U\s1\06_mesh_tab.png`): Element order "2", Hexahedron nodes "20", Curvature points, Algorithm 2D, Algorithm 3D, Optimiser, all at one level with Global size.
  - Geometry (`U\s3\l_1536_geometry.png`): "Scope: Global", "Owner: (none)".
  - Every object starts with an empty "Tags" row.
- ADR 0029 removed raw values; the next step is hiding what is not needed.
- Recommendation:
  1. An `ADVANCED` table beside `APPLIES` in `properties.py`: per kind, the fields that go in a folded "Advanced" group at the bottom. Start with Tags, Scope, Owner, the weak-spring numbers, Preload step, Elements, Thread kf, the Gmsh algorithms, Hexahedron nodes, When, Amplitude, Axes, Case.
  2. Fields that are really other tools' state (Shootout, Convergence, Adapt, Morph, Remote, Seismic) are hidden while unset; each is reached from its own command.
  3. Extend `APPLIES` for the bolt: Ground direction and Ground length only when Ground is Yes.
  4. Open "Values" by default, and put it directly under Selection.
  5. Use words for choices: Element order "Linear / Quadratic", not 1 / 2.
  6. `tests/test_gui_names.py` can enforce a ceiling, for example no more than 8 rows outside Advanced for any kind.

**UX-8. Errors reach the user as Python and solver internals, and one of them empties the view.** High. Both. Effort S to M.

- Evidence:
  - `U\s2\b3_geometry_tool_fillet.png`: choosing Modify → Fillet on the imported bar with the tool's own defaults (Radius 0.002 m, "Outer vertical edges") shows, in the status bar and Output: `GeometryError: step 'Fillet 1' (fillet) failed: __init__(): incompatible constructor arguments. The following argument types are supported: 1. OCP.BRepFilletAPI.BRepFilletAPI_MakeFillet(S: OCP.TopoDS.TopoDS_Shape, FShape: OCP.ChFi3d.ChFi3d_FilletShape = ...) Invoked with: None`. The part disappears from the view while the tool is open. (Possibly also a defect for the bug reviewers: the preview of a fillet on an imported part passes no shape.)
  - `U\s3\r3_after_refused.png`: a run refused for a missing support puts `[inconsistent] study 'Tip load' solves solid mechanics on 'Cantilever', which has no fixed or displacement condition: the body is free to move as a rigid body` in the status bar, and `error: [inconsistent] …` in Output. The code in brackets is noise to a user, and the sentence says what is wrong but not what to do.
- Recommendation:
  1. One function for user-facing errors (`gui/errors.py` exists): message, then "What to do", then a folded "Details" with the code and raw text. Never show a bracketed code or an exception class name in the status bar.
  2. A failed preview keeps the last good shape in the view and shows the reason in the tool panel's banner (`FormPanel.set_message`), for example "No edges match 'Outer vertical edges' on this part. Pick edges in the view."
  3. Catch non-`PuzzleError` exceptions from OCC at the preview boundary and report "This step could not be built" with Details.
  4. The `unconstrained` message should end with the action: "Add a fixed support or a displacement" and the toast's button should be "Add fixed support…", as the Advisor's already is.

### Medium

**UX-9. The Model workspace has no ribbon, and the workspace switch is hidden in a side panel.** Medium. Both. Effort M.

- Evidence: `U\s1\03_model_study_advisor.png` (nothing above the view on Model), against `U\s1\06_mesh_tab.png` and `U\s2\c4_results_vonmises.png` (ribbons on Mesh and Results). The tabs Model, Geometry, Mesh, Schematic sit inside the left dock, yet they change the centre and the ribbon. When the dock is narrow the tabs scroll out of sight ("M◂▸").
- The stage where the user spends most time (materials, contacts, loads, studies) is the only one with no visible tools; everything is behind right-click or four-level menus.
- Recommendation: move the workspace tabs to a strip across the top of the window, above the ribbon: **Geometry | Mesh | Model | Results | Schematic**, in workflow order. Give Model a ribbon with groups Materials (Add, Assign), Connections (Find contacts, Bolts, Welds), Supports ▾, Loads ▾, Study (New, Settings, Run). Selecting a run switches to Results as now. The left dock then holds only the tree (with the Geometry history and the mesh controls shown as that workspace's side page).

**UX-10. An empty project is a blank screen, and the start page does not teach.** Medium. Novices. Effort S to M.

- Evidence: `U\s2\a1_empty_project.png`: an empty grey view, a tree with one row, an empty Properties table. The only ways to begin are the Advisor's buttons, the menu bar, or right-clicking the project's row (98 commands). "Try the cantilever example" is a single button at the very bottom of the Advisor, 1100 px below the first step.
- `U\s1\00_start.png`: the start page still shows empty Model and Properties panels (a quarter of the screen), a stray "Agent | Advisor" tab strip in the middle of the page and the folded Messages tabs. It offers New and Open only: no example, no tutorial, no link to Getting started. Its tagline names six solvers and omits PyNite and OpenCourant.
- The window accepts only `.puzzle` archives by drag and drop (`window.py` `dragEnterEvent`); a STEP file dropped on it is ignored.
- Recommendation:
  1. Empty project: draw an empty state in the centre of the view with three large buttons, "Import geometry…", "Draw a part…", "Open the cantilever example", and "or drop a STEP file here". Accept STEP, IGES, BREP, STL drops (calling `menus.import_step_file(window, path)`).
  2. Start page: hide every dock; add "Examples" (cantilever, bracket assembly, each a card that opens a scratch copy) and "Getting started" (opens Help). Fix the tagline.
  3. Advisor on an empty project: show only the first needed step expanded; later steps greyed with "after geometry".

**UX-11. Display units default to SI metres and pascals.** Medium. Novices. Effort S.

- Evidence: `U\s2\c4_results_vonmises.png` (legend 1.205e+08, 9.063e+07 … Pa), `U\s1\06_mesh_tab.png` (Global size 0.008 m), `U\s1\08_dock_bolts.png` (Diameter 0.008 m), the fillet tool ("Radius (m) 0.002"), `U\s2\f1_find_contacts.png` (Overlap 0.009499 m²).
- Mechanical users think in mm and MPa. Scientific notation on the legend is the direct cost.
- Recommendation: on first launch ask once ("Which units do you work in?": mm, N, MPa, °C / m, N, Pa, K / in, lbf, psi, °F), stored as the preference. Make engineering units the default answer. The New project dialog already has a Units row; default it to the preference.

**UX-12. The main toolbar spends its space on the wrong things.** Medium. Both. Effort S.

- Evidence: `U\s1\03_model_study_advisor.png`: New, Open, Undo, Mesh, Run study, Add material, Material builder, Pick. Two of eight are materials (the builder is an occasional library tool). There is no Import geometry, no Redo, no Save version, nothing for loads, studies or results.
- Recommendation: New, Open, Save version | Undo, Redo | Import geometry, Add material, Add load ▾, New study, Run ▾ (a split button listing the studies, Run all) | Pick, Measure. Material builder moves to the Materials menu only.

**UX-13. Run and Mesh on the toolbar do nothing unless the right tree item is selected.** Medium. Novices. Effort S.

- Evidence: `U\s2\dump.txt`: with a project open and nothing suitable selected, Mesh answers "Select a component (or a study) to mesh" and Run answers "Select a study to run" or "Make a study first: Model → Studies → New study…", each as a status-bar line that disappears after 5 seconds. `window.run_selected`.
- Recommendation: Run with one study runs it. With several, run the selected one, else the study of the selected run, else open the split button's list. Mesh likewise for components. A refusal is a toast with an action button ("New study…"), not a status line.

**UX-14. Refusals and hints that matter are status-bar text that expires.** Medium. Both. Effort S.

- Evidence: `add_feature`'s "No face selections … pick some in the view first" (8 s); Run and Mesh above (5 s); "Settings saved". The status bar is at the bottom-left, far from where the user clicked.
- Recommendation: a rule in `gui/feedback.py`: anything that explains why an action did not happen is a toast with one action; the status bar is only for passive state (what is shown, units, progress).

**UX-15. The "Add feature" list is 25 items, flat, in schema order, and the word "feature" is used for nothing else.** Medium. Novices. Effort S.

- Evidence: `U\s1\dump.txt` ("Solid mechanics" context menu, 29 leaf items): Fixed support, Prescribed displacement, Symmetry plane, …, Initial velocity, Prescribed velocity, Rigid wall (explicit dynamics only), Line load (frames only), Spring support (at a node).
- The tree folder is "Loads and boundary conditions", the Advisor says "Supports and loads", the menu says "Add feature", the manual says "features".
- Recommendation: one term, "Loads and supports", everywhere a user sees it ("feature" stays in the schema). Split the list into Supports ▸, Loads ▸, Body loads ▸ (gravity, acceleration, rotation, temperature, body force), Bolts ▸, Initial conditions and explicit ▸. Put Fixed support, Force and Pressure at the top level. Grey out, with a tooltip, what the component's studies cannot use (Rigid wall without an explicit step; Line load without members).

**UX-16. The same command has different names and different contents in different places.** Medium. Both. Effort S.

- Evidence, all from `U\s1\dump.txt` and the screenshots:
  - Geometry's right-click "Add step" is a flat list of 46 raw step names (Import, Subd, Keep, Fragment, Remove, Offset (clearance or interference), Bolt, Nut, Washer). The Geometry menu and ribbon group the same things as Create / 2D / Sweep / Modify / Prepare with better names ("SubD from a cage", "Remove features", "Share faces").
  - Pick: the toolbar tooltip says "Double-click faces in the view to pick them"; the pick bar says "Click faces to pick (Shift adds, Ctrl or ⌘ removes)" (`U\s2\c1_pick_mode.png`), on Windows too.
  - "Run study" (toolbar), "Run" (tooltip, study menu, Advisor), "Run study" (Model → Run).
  - "Add material…" (toolbar, menu) and "Add from library…" (Materials heading).
  - "Duplicate component…" (menu) and "Duplicate…" (right-click).
  - "Remove" for loads, contacts, parts, steps; "Delete" for objects; "Remove physics"; "Remove step".
  - "Animate" appears twice on the results screen (results bar "▸ Animate" and ribbon "Animate"), next to "Save GIF…".
  - Mesh menu: "Mesh" is both the menu and its second item; "New mesh recipe"; the tree heading is "Meshes"; the tab is "Mesh".
- Recommendation: build context menus, ribbons and menu-bar submenus from one table of commands per kind (label, group, icon, slot), so they cannot differ. `menus.py` and `main_menu.py` would both read it. Use "Delete" everywhere. Use the platform's modifier names.

**UX-17. The view toolbar is 21 unlabelled icons.** Medium. Both. Effort S to M.

- Evidence: every screenshot with a view, e.g. `U\s1\03_model_study_advisor.png`: fit, refresh, isometric, +X, −X, +Y, −Y, +Z, −Z, display, element edges, wireframe, orthographic, section cut, four toggles lit blue (markers, supports and loads, beams, bolt labels), clear probes, copy image, save image.
- Six axis buttons take a third of it. Four state toggles are distinguishable only by hovering. "Refresh view" should not be needed.
- Recommendation: a view cube or one "Views ▾" button for the seven orientations; one "Show ▾" menu with ticked words (Mesh edges, Supports and loads, Max and min, Beam sections, Bolt labels, Wireframe, Orthographic); keep Fit, Section cut and Image. That is about 6 controls.

**UX-18. No right-click menu in the 3D view.** Medium. Both. Effort M.

- Evidence: `viewport.py` and `window.py` connect a context menu to the tree only.
- In every reference tool the view's right-click is the fastest way to act on what is under the pointer.
- Recommendation: a view context menu built from the pick under the pointer: on a face, "Add load or support here ▸", "New selection from this face", "Hide this body", "Hide all others", "Assign material…"; on empty space, "Fit", "Views ▸", "Show all"; on a result, "Probe here", "Keep probe".

**UX-19. The results screen: tiny legend, an orphan label, duplicated controls, three rows of chrome.** Medium. Both. Effort S to M.

- Evidence: `U\s2\c4_results_vonmises.png`, `U\s3\l_1536_legend_panel.png`.
  - Legend tick labels are about half the size of the interface text, and the unit ("m", "Pa") is drawn on top of the top tick.
  - "In axes" is a bare label with no control when the project has no coordinate system.
  - The results bar, then two ribbon rows, take about 115 logical px above the view; Animate is in both.
  - The run in the tree is named "Tip load · CalculiX · 2026-11-17 02:04": UTC, a day ahead of the local date here.
- Recommendation: legend text at the interface's size, title "von Mises stress (MPa)" above the bar; hide "In axes" with its control; merge the bar into the ribbon as its first group (Result, Step, Deformed, Scale) and drop the second Animate; show local time, or "today 18:04".

**UX-20. A tool's panel replaces Properties at a fixed height and squeezes the tree to three rows.** Medium. Both. Effort S.

- Evidence: `U\s3\l_1536_legend_panel.png` (824 px tall window: the model tree shows three rows; the Legend tool takes the rest). `U\s3\r3_after_refused.png`: the Legend panel is still open after the result it belonged to has gone and the view shows the model.
- Recommendation: put tool panels in a scroll area with no minimum height, so the splitter keeps the user's proportions; close a result tool when the context leaves Results.

**UX-21. Dock chrome draws over itself.** Medium. Both. Effort M.

- Evidence:
  - Folded Messages: the dock group's own tab strip ("Messages | Bolts") is drawn over the inner tabs, hiding "Output" and half of "Solution", and the dock title is clipped (`U\s1\03_model_study_advisor.png`, `U\theme\light\results.png`, `U\s3\l_1536_legend_panel.png`).
  - A "Messages | Bolts" tab strip appears inside the Tool panel (`U\s2\b3_geometry_tool_fillet.png`).
  - A "Bolts" tab is present in a project with no bolts (cantilever).
  - The start page shows an "Agent | Advisor" strip with nothing under it (`U\s1\00_start.png`).
- Recommendation: fold Messages by hiding the dock group and showing a slim status-bar row of buttons (Output, Solution, Problems (n)), instead of shrinking a tabbed dock; show the Bolts, Welds, Members and Contacts panels only when asked for; hide all docks on the start page.

**UX-22. Thirteen panels and six message tabs, with shortcuts in no order.** Medium. Both. Effort M.

- Evidence: Window menu in `U\s1\dump.txt`: Ctrl+1 Model, 2 Properties, 3 Advisor, 4 Sections, 5 Bolts, 6 Solution (listed last), 7 Contacts, 8 Versions, 9 Schematic, 0 Optimisation, Ctrl+Shift+0 Convergence, Ctrl+Shift+J Jobs, K Members, L Welds. "Auto-hide" lists six of them.
- Bolts, Contacts, Members and Welds are four tables of the same kind (setup and results per connection). Jobs, Convergence, Optimisation and Solution are four views of running things.
- Recommendation: three bottom panels. **Messages** (Output, Problems, History). **Connections** (one panel with a selector: Contacts, Bolts, Welds, Members; same toolbar, filter and Export CSV). **Runs** (Solution, Jobs, Convergence, Optimisation as tabs). Plots and Table become one "Data" panel or open with their operator. Versions is a dialog from Project. Number shortcuts follow the order on screen.

**UX-23. Settings: a duplicate group, ADR numbers and environment variables in the help text.** Medium. Both. Effort S.

- Evidence: `U\s1\10_settings_Solvers.png`: groups Units, Interface, 3D mouse, Geometry, Meshing, Results, **Agent**, Versions, Solvers, Connections, Schematic, **Agent**, Advanced. Descriptions read "(ADR 0023)", "(ADR 0067)", "(ADR 0068, 0051)", "Your own OMP_NUM_THREADS or CCX_NPROC_* win", "solve in place through /mnt". 31 strings in `settings.py` and `settings_dialog.py` cite an ADR. The Solution action's tooltip is "A solve's progress and convergence (ADR 0054)". Help has "Design decisions (ADRs)".
- "3D mouse", with 10 preferences, is the third group, ahead of Results and Solvers.
- Recommendation: rename the first Agent group ("Agent behaviour") or merge the two; strip ADR references from every user-facing string (a test can refuse "ADR \d{4}" in `PREFERENCES` descriptions and tooltips) and link "Learn more" to the help page instead; move the WSL staging and PARDISO build rows to Advanced; order groups Units, Interface, Results, Solvers, Meshing, Geometry, Agent, Versions, Connections, Schematic, 3D mouse, Advanced; add a search box.

**UX-24. A running solve shows two progress bars and two stop buttons.** Medium. Both. Effort S.

- Evidence: `U\s2\c2_running.png`: the Solution tab has a bar at "0 %" and a "Stop" button whose text is clipped; the status bar has "Running Tip load…" twice, a second bar, and a "Cancel" button about 500 px wide.
- Recommendation: when the Solution tab is visible, the status bar shows only a short "Running Tip load 0:12" with a small Stop. One word (Stop). An indeterminate bar until the solver reports a fraction.

**UX-25. Solver settings is a flat list with every label prefixed and solver keywords in the labels.** Medium. Both. Effort S.

- Evidence: `U\s2\d06_solver_settings.png`: eight rows starting "All solvers:", four starting "CalculiX:", labels such as "Cut-backs per increment (*CONTROLS I_A)", empty boxes with no placeholder saying what the default is.
- Recommendation: move to `FormPanel` (ADR 0036's "still to move" list): sections "General" and "CalculiX", no prefixes, the default as placeholder text ("default: 1e-6"), the keyword in the description box, not the label.

**UX-26. Experts have no fast paths: no tree filter, no multi-edit, no copy and paste, few duplicates.** Medium. Experts. Effort M.

- Evidence: `tree.py` has no filter or search; `properties.py` handles one item; "Duplicate…" exists for components and studies only (`menus.py`); loads, contacts, bolts, materials and mesh recipes cannot be duplicated or copied to another component; the only shortcuts for creating anything are Ctrl+N and Ctrl+M / Ctrl+R (`U\s3\dump.txt` "ALL SHORTCUTS").
- Recommendation: a filter box above the tree (Ctrl+F); Duplicate (Ctrl+D) on every item; Copy and Paste of loads, contacts and assignments between components; with several items of one kind selected, Properties shows the common fields and one edit applies to all as one batch; a command palette (Ctrl+K or Ctrl+Shift+P) over the same command table as UX-16, which would also make 342 commands searchable.

**UX-27. Selecting something in the tree does not always show it in the view.** Medium. Both. Effort S to M.

- Evidence: `U\s1\04_props_feature.png`: "Fixed support on Base/Bottom" selected; the face is underneath, so the only sign is a thin orange outline along the bottom edge. `U\s2\f4_bolt_selected_props.png`: a bolt selected; nothing in the view changes.
- Recommendation: draw the selected item's faces through the model (x-ray tint or silhouette on top); double-click or F on a tree item zooms and turns the view to face it; highlight a selected bolt's symbol and its two holes.

**UX-28. The tree is deep and repeats itself.** Medium. Both. Effort M.

- Evidence: `U\s1\02_tree_expanded.png`, `U\s1\dump.txt` "TREE": a load is at level 6 (project, Components, component, Solid mechanics, Loads and boundary conditions, load). Empty folders always show (Fasteners, Contacts). A geometry's History is in the tree and in the Geometry tab. Labels carry their kind as a prefix ("Part Base: Base", "Fastener Bolt 1 (Ø 8 mm): Bracket/Hole 1 → B…", "Contact Seat (frictional)") and get truncated.
- Recommendation: hide empty default folders (the heading's right-click already adds); with one physics, show its Contacts and Loads directly under the component; drop the kind prefix where the folder already says it ("Bolt 1 · Ø 8 mm", details in the tooltip); with one component, start it expanded.

**UX-29. The selected tree row loses its state icon.** Medium. Both. Effort S.

- Evidence: `U\s1\03_model_study_advisor.png` ("Bolted"), `U\s2\c2_running.png` ("Tip load"): the icon is drawn in a colour that vanishes on the blue selection.
- Recommendation: draw state icons in the highlighted-text colour when the row is selected (`icons.state_icon` with a selected mode).

**UX-30. New study asks a beginner for a solver and "Physics this step solves".** Medium. Novices. Effort S.

- Evidence: `U\s2\d01_new_study.png`: Component, Solver, Analysis, Physics this step solves, Name.
- Recommendation: order Analysis, Component, Name; hide Physics when the component has one; Solver becomes "Automatic (CalculiX)" with the list in an Advanced fold, and a note per solver of what it can and cannot do for this analysis (the data is in the capability matrices).

### Low

**UX-31. The material builder opens empty with fifteen "Table…" buttons.** Low. Novices. Effort S. `U\s2\d05_material_builder.png`. Open it on the project's first material, or on a "choose a material on the left" empty state; show "Table…" only on hover or as a small icon; show units beside each box.

**UX-32. Bolt and contact tables lead with solver terms.** Low. Novices. Effort S. `U\s1\08_dock_contacts.png`: "Source (slave)", "Target (master)", and a "Solver is given" column quoting `*CONTACT PAIR … *FRICTION`. Keep the column but hide it by default behind "Show what the solver gets".

**UX-33. The keyboard shortcuts window is a message box.** Low. Experts. Effort S. `U\s2\d11_study_materials.png` (the file name is off by one; it is the shortcuts box, 338 x 584, one long unsearchable table). Make it a help page with search, grouped by workspace.

**UX-34. Help is written for the maintainer in places.** Low. Novices. Effort S. Help → "Design decisions (ADRs)" and "Log" sit beside Getting started. Move both under Help → Advanced or About.

**UX-35. The Schematic tab's left panel is a page of instructions.** Low. Both. Effort S. `U\s1\07_schematic_tab.png`: five paragraphs fill the dock, and the overview map covers nodes in the middle of the canvas. Replace the text with a "?" that opens the help page; dock the overview in a corner.

**UX-36. The Mesh tab's side panel explains itself in prose.** Low. Both. Effort S. `U\s1\06_mesh_tab.png`: "Controls, in order: double-click edits; the tools are above the view." and an empty list. Use an empty state with a button ("Add a local size…").

**UX-37. Messages have six tabs of unequal weight.** Low. Both. Effort S. Problems is the one a beginner needs and it is fourth; it shows "(0)" when empty. Put Problems first, show its count as a badge on the toolbar's Messages button, and open it automatically when a run is refused.

**UX-38. Small wording.** Low. Effort S.

- "The other way round" for inverting the colour map (`U\s3\l_1536_legend_panel.png`): "Reversed".
- Legend "Top" and "Bottom": "Maximum" and "Minimum".
- "Custom pde" in Add physics: "Custom PDE".
- "Who may connect…" and "Trust this project…" need a noun: "Agent access…", "Trust scripts in this project…".
- "Roll back to last known good…" sits in the middle of the Project menu between Trust and Undo; it is destructive and belongs in Versions.

## Comparison with the reference tools

| Task | Ansys Mechanical / Fusion / SimScale | Puzzle today | What to take |
| --- | --- | --- | --- |
| Start an analysis | Pick an analysis type; geometry, mesh, connections and solution objects exist with defaults | Make mesh recipe, component, physics, study by hand | UX-3 |
| Apply a load | Select a face, choose the load, type the value in one panel | Name a selection first, choose it from a list, find Values in Properties | UX-2, UX-18 |
| Know what is next | Ansys: lightning bolts and question marks in the tree. COMSOL: the Model Builder's order is the workflow | Advisor (good) plus state icons (good) | Keep; fix UX-6 |
| Everyday settings against expert ones | Ansys Details: categories, with advanced rows hidden until a mode needs them. COMSOL: "Advanced Physics Options" toggle | Everything in one list | UX-7 |
| Workspaces | Fusion and Workbench: workspace switch at the top, one ribbon per workspace | Tabs in the left dock; Model has no ribbon | UX-9 |
| Contacts | Ansys: detected automatically on import | Find contacts… on demand (good dialog) | Offer it automatically when a component gets a second part |
| Results | Legend readable, units in the title, probe by click | Small legend, SI by default | UX-11, UX-19 |
| Undo | All have Undo and Redo | Undo only | UX-4 |

Where Puzzle is already better and should not be simplified away: every action is one undoable command with a history; the Advisor is computed, not scripted; results stay valid and are marked out of date; one model runs on several solvers; the agent can do everything the GUI can.

## A simpler information architecture

The principle: each surface has one job, and every command lives in one table that all of them read.

**Workspace strip (new, top of the window).** Geometry | Mesh | Model | Results | Schematic. It sets the ribbon, the left side page and the context. It replaces the tabs in the left dock.

**Ribbon: the common tools of the current workspace, about 12 to 15 buttons, with words.** It is the "one obvious way" for a beginner.

- Geometry: as now.
- Mesh: as now.
- Model (new): New analysis, Add material, Assign | Find contacts, Bolts ▾, Welds ▾ | Supports ▾, Loads ▾ | New study, Settings, Run ▾.
- Results: Result, Step, Deformed | Probe, Reaction, On a selection, Path, More ▾ | Section cut, Animate, Legend | Export ▾.

**Main toolbar: things true in every workspace.** New, Open, Save version | Undo, Redo | Pick, Measure | at the right: Advisor, Agent, Messages (with a problems badge), Settings.

**Menu bar: complete but shallow, organised by object, at most two levels.**

- **Project**: New, Open, recent, Save version, Versions…, Save as, Archive ▸, Units…, Trust…, Agent access…, Remote hosts…, Settings….
- **Edit** (new): Undo, Redo, Delete, Rename, Duplicate, Copy, Paste, New group, Find….
- **Geometry**: as now, with "Show and hide" moved to View.
- **Mesh**: as now.
- **Model**: Materials ▸, Components ▸, Connections ▸ (contacts, bolts, welds), Loads and supports ▸ (Supports, Loads, Body loads, Initial), Beams and shells ▸, Symmetry ▸, Reference ▸ (parameters, coordinate systems, construction geometry, amplitudes, imported fields). The Materials menu folds in here.
- **Study** (new): New study, Duplicate, Add step ▸, Solver settings, Load combinations, Seismic ▸, Run ▸, Mesh convergence, Adapt the mesh, Optimisation, Mesh shoot-out, Transfers.
- **Results**: Result tools ▸, Combinations and fatigue ▸, Section cut, Animate, Plots, Legend, Export ▸, Open in ParaView. No operators.
- **View** (new, from Window and Geometry): Fit, Views ▸, Show ▸ (edges, symbols, markers…), Show and hide ▸, Colours, 3D background, Panels ▸, Layouts ▸.
- **Help**.
- Operators: one item, "Add operator…" (Ctrl+Space or Tab in the schematic), opening the existing searchable palette. This removes 190 menu entries (95 in the menu bar and 95 on the project's right-click).

That is roughly 150 commands instead of 342, none more than two levels down, with nothing removed from the product.

**Tree: structure and state only.** Right-click offers what applies to that item, built from the same command table, at most about 10 entries: its "Add" actions, Edit, Duplicate, Hide, Delete, Rename. The project's right-click offers "New analysis…", "Import geometry…", "New part…" and "More ▸".

**Properties: the selected thing's everyday fields, Advanced folded.** Creation and editing of loads, contacts and bolts use the same inline panel there (the Geometry tab's pattern), so there is one way to enter values.

**3D view: direct manipulation.** Pick, right-click menu, drop a CAD file.

**Docks: three at the bottom (Messages, Connections, Runs), two at the right (Advisor, Agent).** Opened by what the user does, not all present from the start.

## Top ten, in the order I would do them

1. **UX-1** Laptop layout: the Geometry tab's 1597 px minimum width (S to M). It is a defect that makes the app look broken on the most common screen.
2. **UX-2** Loads and supports as an inline panel with picking and values (M).
3. **UX-3** "New analysis…": default mesh recipe, component, physics and study in one form (M).
4. **UX-4** Redo and an Edit menu (S to M).
5. **UX-6** The Advisor follows the study on screen (S).
6. **UX-7** Properties: Advanced fold, hide what does not apply, Values open (M).
7. **UX-8** Error messages: no codes or Python in the status bar; a failed preview keeps the shape (S to M).
8. **UX-5** Menu restructure from one command table; operators out of the menus (M). Do it with UX-16.
9. **UX-9** Workspace strip on top and a Model ribbon (M).
10. **UX-10 with UX-11** Empty state, start page with examples, drop a STEP file, and a first-run units choice (S to M).

## Counts

| Severity | Count | IDs |
| --- | --- | --- |
| High | 8 | UX-1 to UX-8 |
| Medium | 22 | UX-9 to UX-30 |
| Low | 8 | UX-31 to UX-38 |

## What I could not look at

- **Real mouse and keyboard use.** Everything was driven by script and captured with `grab()`. I did not rotate, hover, drag, box-select, or tab through dialogs by hand, so hover tooltips, focus order, the focus ring, drag and drop in the tree and the feel of picking are unreviewed. UX-18 (no view context menu) is from the code.
- **The sketcher**, the schematic's interaction and operator palette, and the agent panel in conversation (another reviewer covers the last two in depth).
- **Dark and high-contrast themes**, compact density, larger text sizes. Light theme, comfortable density only.
- **macOS and Linux.** Windows 11 at 150 % scaling only.
- **Dialogs not captured**: Generate bolts (my script did not catch it), the fatigue tool, combinations, member design, seismic, weld forms, VDI 2230, field import wizard, optimisation and convergence panels with data, Compare window, linearization. The bolt table and contact tool were seen with setup data only, not results.
- **A real solver failure** (divergence, a crashed solver). I looked at a refused run (missing support) only, so the Solution tab's diagnosis and "Show why" on a true failure are unreviewed.
- **A sweep or shoot-out** end to end; long solves were out of scope.
- **Help window content** beyond opening it; F1 targets.
- **Screen readers.**
- The stray tab strips in UX-21 were seen in grabs at 1500 x 950 and 1536 x 824 and in the maintainer's own script output; I did not confirm by eye on a live window that they are not partly a `grab()` artefact of the folded dock. The dock widths in UX-1 are measured values, not a grab artefact.
- One capture note: in `U\s2\`, the dialog files `d09` to `d12` are each named one step late (`d09_new_mesh.png` is New transfer, `d10` is the study's Materials dialog, `d11` is Keyboard shortcuts, `d12` is Settings). New mesh recipe opens no dialog.
