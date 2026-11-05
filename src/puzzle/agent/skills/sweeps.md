---
name: sweeps
description: parameter and material sweeps: one study run once per point (values, a list operator, or candidate materials at a location), each point's results, comparing points
adr: 0018
---
Read this when the user wants a study run for several values of a parameter, or with
several materials in one place ("try steel, aluminium and titanium for the bracket"),
or wants the results of such runs.

A sweep is a step of type `parametric_sweep` in a study's `steps`. Each of its
parameters is one of:

- a **parameter**: `name` is a parameter set's parameter or a Number or Slider operator,
  with `values` (quantities), or `source` (a list operator's id, ADR 0062) in place of
  `values`;
- a **material choice**: `location` is a material assignment's selection in the
  study's component ("Bar/Body"), `materials` the project materials (ids) to try there.

`combination` is `grid` (every combination) or `paired` (the i-th values together;
equal lengths). Several sweep steps multiply. Each point is an ordinary run: cached by
its run key, stored with its point (`{"Bar/Body": "Copper", "t": "12 mm"}`). A sweep
of materials only meshes once.

## Steps

1. `outline(ids=true)`: the study, its component, the material assignments'
   selections, the parameter sets and their parameters. Material and operator ids are
   needed below; `get_object(name)` gives one object's id.
2. A parameter to sweep must exist and the model must read it (a history argument
   naming it, for example). A parameter set, if there is none:
   `{"command": "create", "kind": "parameters", "data": {"name": "Dimensions",
   "values": {"t": {"value": 10, "unit": "mm"}}}}`.
3. Add the sweep step (one `submit`):
   `{"command": "edit", "object": "Static", "ops": [{"op": "append", "path": "steps",
   "value": {"type": "parametric_sweep", "combination": "grid", "parameters": [
   {"name": "Bar/Body", "location": "Bar/Body", "materials": ["<steel id>",
   "<copper id>"]},
   {"name": "t", "values": [{"value": 8, "unit": "mm"}, {"value": 12, "unit": "mm"}]}
   ]}}]}`. This grid is 4 runs. Where the step sits among the steps doesn't change
   the runs.
   - A material not yet in the project: `{"command": "add_library_material", "key":
     "<key>"}` first (`materials_library(text)` lists keys).
   - Values from a list: make a Series or Range with `operator_add` (skill
     `schematic-operators`), then `{"name": "t", "source": "<operator id>",
     "port": "out"}` with no `values`. Its values are SI, so points read "t = 0.012 m".
4. `check_study(study)`: problems before running. Tell the user how many runs the
   sweep makes (the product of the lists, for a grid) before starting.
5. Run it: `submit` `{"command": "run_study", "study": "Static"}`. The answer has
   `points`: each point's labels, `run_id` and `cached`.

## Read each point's results

The `result_*` tools read a study's newest run, which is only one point. For a point:

- Make a one-case linear combination naming it, then query it by its name:
  `{"command": "create", "kind": "result", "data": {"name": "Static: Copper",
  "method": "linear", "cases": [{"study": "<study id>", "point": "Copper"}]}}`, then
  `result_stats(study="Static: Copper", field="stress")`. `point` is the point's title
  as the results list it: material names, and `name = value unit` for parameters,
  joined by ", " ("Copper, t = 12 mm").
- One number per point, as a table: a Reduce on the study, then a Plot of it against
  the sweep: `operator_add("plot", "Peak per point", {"value": {"source":
  "Peak stress"}}, {"against": "sweep"})`; read it with `operator_value(name)`.
- The points with their run ids: `operator_add("points_table", "Points",
  {"source": {"source": "Static", "port": "points"}})`.
- `runs()` lists every stored run but not its point.

The side-by-side views, the difference or ratio to a baseline point, and the summary
bar chart are in the GUI only: Results → Compare sweep points… (or the study's
"<study>: compare" heading in the tree). A study's Materials… (right-click) edits the
material choices as a table.

## Units

Values are quantities with units as typed (`{"value": 12, "unit": "mm"}`); Puzzle
converts to SI for the solver. Results are SI.

## Mistakes to avoid

- Reading a sweep with `result_stats(study=...)`: that is whichever point was stored
  last. Name the point through a combination, as above.
- A material choice whose `location` isn't one of the component's material
  assignments: refused (`inconsistent`). Assign a material there first.
- A parameter nothing in the model reads: every point gives the same answer.
- Mixing `values` and `materials` in one parameter, or `values` with `source`: refused.
- `paired` with lists of different lengths: refused.
- Running a large grid without saying how many solves it costs.
