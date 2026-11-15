---
title: The Puzzle window
section: guide
order: 10
keys: panel:model, panel:properties, panel:view, panel:messages, kind:group, kind:labels, kind:units
summary: Find your way round the window: the model tree, the Properties panel, the 3D view, the messages, layouts, units and undo.
adrs: 0024, 0029, 0030, 0033, 0045
---

The window has four working areas: the model tree on the left, the Properties panel
beside it, the 3D view in the centre and the messages below it. Every change you make in
any of them is one command in the project's history, so Undo takes it back.

The toolbar holds New…, Open…, Undo, Mesh (Ctrl+M), Run (Ctrl+R), Add material…,
Material builder… and Pick (P). Agent, Messages and Settings… sit at its right end. The
menu bar reaches everything the ribbons and right-click menus do.

## How to use the model tree

The **Model** tab lists the project under headings in the order a model is built:
Parameters, Geometry, Materials, Meshes, Components, Bolt types, Studies, Transfers,
Imported fields, Amplitudes, Coordinate systems, Construction geometry, Operators and
more. A heading shows once it holds something.

1. Right-click the project's top item to make the first of anything: New part…, New
   mesh recipe, New component…, New study… and the rest.
2. Right-click a heading or an item for what it can do. A heading's menu adds to it.
3. Select an item to show its fields in Properties and draw it in the view.
4. Delete removes what is selected (the Delete key, or Project → Delete). Several items
   selected go as one undo.

Inside an object, items sit in default folders. A component has Geometry, Material
assignments and Fasteners; each physics has Contacts and Loads and boundary conditions;
a geometry has History, Bodies and Selections. A folder's right-click adds what it holds.

### Renaming and groups

- **Rename:** select an item and press F2 (or Project → Rename). Runs and a swept
  study's group of runs can be renamed too; those names are kept in the project.
- **Groups** are folders of your own. Project → New group… (or right-click a heading,
  New group…) makes one holding what is selected.
- Drag items in or out, or use right-click → Move to group, Out of group and Ungroup.
- New group inside… groups items inside an object: a geometry's selections, a
  component's parts or contacts, a physics' loads.
- A group shows in the folders its members span. Dropping an item into another kind of
  folder is refused. Hiding a group hides what it holds.

## How to edit in the Properties panel

Every field of the selected item is editable here, and each edit is one undoable
command. Quantities show and are typed as "5 mm"; a bare number is in the unit shown.
Anything with a fixed set of values is a dropdown of readable names.

- A quantity typed as `= t` follows the parameter `t`
  ([Loads and supports](help:guides/loads-and-supports)).
- While a geometry or mesh tool is open, the panel holds its options instead.
- F1 on a row opens the help for that field.

## How to move round the 3D view

- **Rotate:** left-drag. **Pan:** shift-drag or middle-drag. **Zoom:** right-drag, the
  wheel, two-finger scroll or a trackpad pinch.
- **Pick:** turn on Pick (P), choose Faces, Edges, Bodies or Points in the pick bar,
  then double-click. Drag still rotates. B switches to box select.
- The view toolbar, at the view's top right: Fit all (F), Refresh view (F5), Isometric
  (I), the six axis views +X to −Z (1 to 6), Display, Element edges, Wireframe,
  Orthographic, Section cut, Max and min markers, Supports and loads, Beams drawn with
  their sections, Bolt force labels, Clear probes (Esc), Copy image and Save image….
- The shortcuts work while the view has focus, never while you type.

## The messages area

The Messages button folds the area below the view open or shut. Its tabs:

- **Output:** what the solvers and Puzzle print. Window → Output in a window opens it
  alone, with search and coloured lines.
- **Solution:** a solve's progress and convergence (Window → Solution, Ctrl+6).
- **History:** every command, who made it and when.
- **Problems:** what stops a run, such as a missing material property or support. See
  [problem codes](help:reference/problems).
- **Plots** and **Table:** result plots and tables from result tools and operators.

## How to arrange the panels

Window lists every panel with its shortcut (Model Ctrl+1, Properties Ctrl+2, Advisor
Ctrl+3 and so on). A panel closed with its × comes back from there. F6 and Shift+F6 move
the keyboard focus between panels.

- **Window → Layouts:** Modelling, Results and Agent beside the model are built in.
  Save current layout… keeps your own; Delete a layout… removes one.
- **Window → Auto-hide** folds a panel into a bar on its edge.
- **Dock all panels** brings floating panels back; **Reset layout** starts again.

## How to choose the units shown

Puzzle stores values as you type them and gives solvers SI. Units only change what is
shown, and what a bare number means.

1. Project → Settings… → Units → Display units. The default is SI (m, kg, s, Pa, K).
2. Choose SI with mm, MPa and °C (engineering), SI consistent in mm, Imperial, or
   My own, per quantity, which sets each kind of quantity apart.

A project can carry its own units for everyone who opens it. Click the units button in
the status bar (it reads, for example, "mm · N · MPa · °C (project)"). Tick Use the
project's units and choose a system. Untick it and each person sees their own again.

## How to undo

Undo (Ctrl+Z, ⌘Z on a Mac, or the toolbar) takes back the last command. While you are
picking, it takes back the last pick first. Undo refuses when the objects have changed
since; the History tab shows what each command did.

## Options worth knowing

| Setting | Where | Default |
| --- | --- | --- |
| Display units | Settings → Units | SI |
| Project units | status-bar units button | off until set; engineering when first made |
| Layout | Window → Layouts | Modelling |

## Mistakes to avoid

- Typing a bare number after changing units: it is read in the unit now shown.
- Expecting units to change results: they never do.
- Looking for an item under a heading that isn't there: the heading appears once it
  holds something. Right-click the project's top item instead.
- Editing project files by hand while Puzzle is open: every change should go through
  the window, the agent or the `puzzle` commands, so history and undo stay whole.

## For agents and scripts

The agent reads the same tree as text with [outline](help:mcp:outline) and one object
with [get_object](help:mcp:get_object). It changes the project with
[submit](help:mcp:submit), takes changes back with [undo](help:mcp:undo) and reads
[history](help:mcp:history). On the command line: `puzzle outline`, `puzzle history`,
`puzzle undo` and `puzzle settings` ([command line](help:reference/cli)).

## See also

- [Getting started](help:start/getting-started)
- [Geometry](help:guides/geometry)
- [Agent and Advisor](help:guides/agent-and-advisor)
- [Versions and projects](help:guides/versions-and-projects)
