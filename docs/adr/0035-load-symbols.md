# 0035. Draw supports and loads in the view

- Status: Accepted (2026-10-23)
- Date: 2026-10-23

## Context

A feature (a support, a load, a temperature) is shown today by highlighting its selection when the feature is chosen in the tree. Nothing shows the model's loads all at once, or which way a force points. PrePoMax and Ansys draw symbols (review item 7): arrows for forces and pressures, hatching or triangles for fixed supports, and coloured faces for temperatures and fluxes. Reviewers check a model by looking at them.

## Decision

1. **Symbols per feature type,** drawn on the feature's selection in the mesh or geometry preview:

   | Feature | Symbol |
   | --- | --- |
   | Fixed support | small tetra glyphs on the face, blue |
   | Displacement | arrows per constrained direction with a bar, blue |
   | Force | one arrow per face at its centre, in the force direction, red |
   | Pressure | arrows normal to the face, pointing in for positive pressure, red |
   | Temperature, heat flux, convection | the face tinted (temperature by value), with a label |
   | Gravity, centrifugal (ADR 0037) | one arrow, or an axis with a curved arrow, at the model's corner |
   | Flow inlet, outlet, wall | arrows into the face, out of it, or a wall hatch |
   | Electromagnetic | potentials as tinted faces, coil current along the coil path |

   Symbols are sized to the model, not to the load (sizes by value mislead with mixed units). Their number per face is capped by spacing.
2. **When they show.**
   - All of a component's symbols show when the component or its physics is selected.
   - One feature's symbols show, emphasised, when that feature is selected.
   - A view-toolbar toggle (ADR 0031) turns them off.
   - In results they are off by default.
3. **They come from the model, not the solver input,** through one function (`post.symbols(project, component, mesh)`) that returns glyph sets. The view draws them and tests check them in `Viewport.shown`, as for other scene content.

## Built (2026-10-23)

- **`post/symbols.py` `symbols(component, mesh, entities_of, only)`** gives one glyph set per feature, sized to 6 % of the model's diagonal:
  - cones for fixed supports;
  - arrows per prescribed direction for displacements;
  - one arrow at the faces' centre for a force;
  - arrows into the face for pressure, heat flux and an inlet, and out of it for an outlet;
  - a tint for the rest (temperature, convection, walls, symmetry, electromagnetics, custom equations).
- **Normals** are taken outward from the mesh's centre, which is right for convex-ish parts.
- **When they show:**
  - all of a component's symbols show when the component, its physics or a study's component is selected;
  - one feature's show when that feature is selected;
  - results show none;
  - the view toolbar's "Supports and loads" turns them off.
- **Not yet:**
  - value labels on tinted faces;
  - body-load symbols (gravity, centrifugal), which come with ADR 0037;
  - larger cones on slender models, left for the Phase F polish.

## Consequences

- A wrong direction or a missing support is seen before the run.
- Glyph sampling on large faces must stay cheap: points are taken from face triangulations already built for the preview.
- Each new feature type needs a symbol (or a documented default: a tinted face with its label).

## Alternatives considered

- Arrows scaled by load value: they mislead when loads differ by orders of magnitude, and are unclear for pressure against force.
- Symbols only for the selected feature (today): the model can't be checked as a whole.
