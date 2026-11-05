---
name: measuring
description: exact sizes from the CAD geometry: lengths, areas, volumes, radii, centroids, normals, minimum distances and angles between named faces, edges, points and bodies, also across placed parts
adr: 0042
---
Read this when the user asks how big, how thick, how far apart or at what angle
something is in the geometry, or when you need a size before choosing a mesh size,
a tolerance, a load's area or a bolt diameter. Measure; never guess sizes from a
picture or from the mesh.

`geometry_measure` measures named entities of a part from its CAD shape with
OpenCASCADE, so the values are exact and do not depend on the mesh. It regenerates the
part's history first, which can take as long as a regeneration on large parts.

What it gives:

- **one pick**:
  - vertex: its position (`centre`);
  - edge: `length`; a circle also `radius`, `centre` and `axis`; a line `direction`;
  - face: `area`, `centre` (centroid); a plane also `normal` (outward); a cylinder
    `radius` and `axis`; `least_radius` (the tightest curvature, e.g. a fillet);
  - body: `volume`, `area` (surface), `centre`, `bounds`, `oriented` (the smallest
    box's sizes, largest first), `topology`, `sharp_edges`;
- **two picks**, under `pair`: `minimum_distance` with the points `from` and `to`,
  `centre_distance` with `dx`, `dy`, `dz`, and `angle` (degrees) between two planes or
  two straight edges;
- **more picks**, under `totals`: the summed `length`, `area` or `volume`.

Each answer also has `rows`: `[{label, value, unit}]`, ready to read out.

## Steps

1. Find the entity names. Picks name entities, not selections:
   - faces of a history step: `<step name>/<side>`, e.g. `B/+z`, `B/-x`; a sketch's
     sides by its edge names;
   - an imported file's faces: `face-1`, `face-2`, ... in file order;
   - edges: their two faces joined by `|`, e.g. `B/+x|B/+z`; points: three faces,
     `B/+x|B/+y|B/+z`;
   - bodies: the history's body names (often `Body`), an imported STEP's part names,
     or `body-1`.
   A selection's `members` are entity names: `get_object(geometry)` lists each
   selection's `entity` and `members`. A face-to-face contact's faces come from
   `contacts_find`.
2. One part: `geometry_measure(picks, geometry)`:
   ```
   geometry_measure(geometry="Bracket", picks=[
     {"entity": "face", "name": "B/-x"}, {"entity": "face", "name": "B/+x"}])
   ```
   `entity` is one of `body`, `face`, `edge`, `vertex`.
3. Across the parts of an assembly: give `component` instead of `geometry`, and a
   `part` (the part's name in the component) in every pick. The parts are measured
   where they are placed, so a gap between two parts is true:
   ```
   geometry_measure(component="Assembly", picks=[
     {"part": "Plate", "entity": "face", "name": "P/+z"},
     {"part": "Cube", "entity": "face", "name": "C/-z"}])
   ```
4. Read `pair.minimum_distance` for a thickness or a gap (two opposite faces),
   `entities[0].radius` for a hole, `totals.area` for a load face made of several
   faces.

A selection with several faces: measure each member as its own pick; `totals.area` is
the selection's area.

CLI twin: `puzzle measure PROJECT GEOMETRY face:B/+z edge:"B/+x|B/+z" ...`
(`entity:name` per pick; `point:` works for `vertex:`). It prints in display units.
GUI: Measure in the Geometry ribbon's Inspect group, or in the pick bar; pick in the
view and the Properties dock lists the values.

## Units

Answers are SI: m, m², m³; `angle` in degrees; points and `centre` in m. A 30 mm
thickness is `0.03`. Convert to the user's units when you report.

## Mistakes to avoid

- A selection's name as a pick name (`Top`): refused as "no face 'Top' in this
  geometry". Use its members' names.
- Neither `geometry` nor `component`: refused. With `component`, a pick without `part`
  fails.
- Measuring a thickness by `centre_distance`: that is between centroids. Use
  `minimum_distance` between the two opposite faces.
- `angle` is given only for two planes or two straight edges: between planes it is
  between their outward normals, so two opposite faces of a block give 180°.
- File-order names (`face-7`) after an imported file was replaced may now be other
  faces: check the selections first with `selections_check(geometry)`.
- Measuring the mesh (element sizes, node distances) for a CAD size: the mesh is an
  approximation; this tool is exact.
