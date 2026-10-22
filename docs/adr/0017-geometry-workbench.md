# 0017. Edit geometry in a workbench: sketches as steps, tools inline, picks in the view

- Status: Accepted
- Date: 2026-10-22

## Context

The geometry engine (ADR 0014, G1–G5) can build, prepare and sketch parts, but the GUI reached it through right-click menus and modal dialogs. The sketcher was three levels deep: right-click a geometry, Add step, Extrude, then "Sketch…" in the step dialog. Edge and face choices for fillets, chamfers and feature removal were typed as rules (`{"parallel": "z"}`). The maintainer found the sketcher undiscoverable and asked for a geometry editing tab and a better geometry interaction. User-friendliness is a standing requirement (no ids or raw keys, readable choices).

The reference tools (SpaceClaim/Discovery, Onshape, SolidWorks) share a pattern: a feature history beside a tool palette, sketches drawn in the main view on a plane or a face, a tool's options edited beside the view with a live preview, and geometry picked in the view rather than described.

## Decision

1. **A Geometry tab beside the Model tab** in the left dock: the workbench for one part.
   - At the top: the part being edited, with New part and Import.
   - Its history: on/off, reorder, double-click to edit.
   - Its tools in groups:
     - Sketch;
     - Create: box, cylinder, cone, sphere, extrude, revolve, hole;
     - Modify: fillet, chamfer, move, rotate, pattern, split;
     - Prepare: heal, remove features, fragment, enclosure, check;
     - Selections: new from picks, reattach.
2. **Sketches are history steps of their own** (kind `sketch`: a plane or a planar face, an offset, the sketch). Extrude and revolve take a sketch step by name (`{"sketch": "Outline"}`), so one sketch can feed several features and is edited where it sits in the history. Inline sketches inside a profile stay valid.
3. **Sketching happens in the main view.** Starting or editing a sketch turns the centre view into the 2D sketch canvas, with its drawing and constraint tools around it; the Geometry tab holds Finish (one command) and Cancel, which return to 3D.
4. **Tool options are edited inline with a live preview.** Choosing a tool fills the Properties dock with its options. The 3D view previews the part with the pending step, regenerated in the background as values change. Apply makes it one command; Cancel leaves the project untouched.
5. **Geometry is picked in the view.** Edges to fillet or chamfer and faces to remove come from "Pick in view" (with the selection filter set for it). They are stored by their persistent names (ADR 0014 G3: edges by the faces that meet there), with readable presets ("outer vertical edges", "holes smaller than …") beside them. Typed rules remain for scripts and agents.

## Built (2026-10-22)

The Geometry tab sits beside the Model tab: the part, its history (tick on or off, drag, double-click to edit) and New part / Import. While it is open, a ribbon above the 3D view holds the tools in groups (Sketch; Create; Modify; Prepare, with Check; Selections, with New from picks and Reattach). A tool opens in the Properties dock, titled Tool. Its fields come from the same `StepForm` as the dialog, and the view previews the pending history, regenerated in the background 350 ms after the last change. Apply makes one command.

- **Profiles** default to the part's latest sketch.
- **Edges** take readable presets ("Outer vertical edges", "Curved edges") or "Edges picked in the view". The first press of Pick shows the part before the step; the second takes the picks, stored by G3 names (`{"edges": ["Extrude 1/start|line-1", ...]}`).
- **Faces to remove** take thresholds in mm or mm², or picked faces.
- **New part** asks for a name and a plane, opens the sketch in the main view, and on Finish creates the part and opens Extrude on that sketch.
- **New sketch** offers the picked flat face first.

Along the way, three fixes:

- Sketch loops are now sorted into regions by containment, so separate circles are separate profiles, not holes in each other.
- The part dropdown stores ids as text, because Qt does not match UUID objects.
- All OpenCASCADE work is serialised behind one lock (CLAUDE.md), after concurrent previews and selection checks failed inside OCCT.

Tested in `tests/test_gui_workbench.py`: a part sketched, extruded and undone as one command; a fillet on picked edges against its closed form; holes sketched on a picked face and cut; the history's ticks and double-click.

## Consequences

- Every geometry action is reachable from one visible place, and the common path (sketch, extrude, fillet) involves no modal dialogs and no typed rules.
- Sketches in the history give features names users recognise ("Outline", "Bolt holes") and make reuse and later editing natural.
- A live preview costs a background regeneration per change. It is debounced, and it cannot write the project: a preview never goes through `Session`.
- The model tree's geometry nodes and the Geometry tab show the same history; both must refresh from the same project state.
- Sketching on arbitrary planes (faces whose normal is not along an axis) needs a general plane in the profile code; the first version takes axis-aligned planes and faces.

## Alternatives considered

- A separate sketch window opened from the Geometry tab: simpler, but the 3D part is hidden or crowded beside it, and it repeats the modal feel the maintainer found hard to discover.
- Sketches inside the feature that uses them (as in G5): fewer objects, but a sketch can feed one feature only and is edited through that feature.
- Improved dialogs with pick buttons: less work, but still modal, with no live preview.
