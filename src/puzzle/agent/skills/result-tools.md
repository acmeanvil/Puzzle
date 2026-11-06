---
name: result-tools
description: reading numbers from results: kept result tools (point, selection max/min/mean/integral, reaction force and moment, path, linearized stress, plane, section) and the result_* tools
adr: 0042
---
Read this when the user asks for a number from a solved study: a reaction at a support,
the largest stress on a face, a deflection at a point, a value along a line, a
linearized stress through a wall, the force across a cut. Also read it before making
an operator or optimisation output that needs such a number.

There are two ways to read a result:

- **Quick reads** with the `result_*` tools: nothing is kept in the project.
- **Result tools** (kind `probe`): kept objects under Results in the tree. Each is
  evaluated again for its source's newest run whenever it is read, so it follows
  every solve. Values are never stored. Use one when the user wants to keep the
  number, plot it over steps, or feed it to an operator or optimisation.

## Quick reads

1. `result_summary(study)`: the steps (with their `index`) and the fields of the
   newest run. Field names are the stored ones: `displacement`, `stress`, `strain`,
   `temperature`, `reaction_force`, `heat_flux`, ...
2. Then one of:
   - `result_stats(study, field, step, selection, component)`: min, max, mean and
     where the extremes are. `component` is a stored component (`x`, `y`, `z`, `xx`,
     `xy`, ...); left out, it is the magnitude or the von Mises stress.
   - `result_value_at(study, field, x, y, z, step)`: the value at a point in metres.
     A null coordinate averages along that axis.
   - `result_integral(study, field, selection, step, component)`: integral and mean
     over a body or face selection.
   - `result_total(study, field, selection, step)`: the sum over a selection's nodes.
     Use it for `reaction_force` on a support face. Never for stresses.
   - `result_history(study, field, selection, reduce)`: `reduce` "total" or "mean" at
     every step or time.
3. `render(study, field)` for a picture, when the client can show one.

`study` may also be a derived result's name (ADR 0025). `selection` is
`Part/Selection` as in the mesh. `step` is a step's `index`; left out, the last.

## Result tools

Create one with `submit`. `source` is the study's (or derived result's) **id**: get it
from `outline` with `ids=true`, or `get_object(name)`. Names are not accepted there.
`submit`'s answer lists what it made under `created`, each with its `id`, so a plane
made first can be named by the probe in the next submit; or give the plane your own
`id` in its data and make both in one batch.

| `type` | Needs | Gives |
| --- | --- | --- |
| `point` | `point` [x, y, z] in m | `value`, `components` |
| `selection` | `selection`, `reduction` max, min, mean or integral | `value` (max/min also `point`) |
| `reaction` | `selection` (a support's); `about` [x, y, z] optional | force `components`, `moment`, `moment_total`, `about` |
| `path` | `point` (start), `end`, `samples` (default 50) | the series (`probe_path`); `probes` gives the largest |
| `linearized` | `point`, `end` across the wall | membrane, bending, peak; `equivalent` at both ends |
| `plane` | `construction` (a plane), `reduction` | the result over the cut |
| `section` | `construction` (a plane) | force and moment across the cut, `normal_force`, `shear_force` |

`quantity` is a result key `field:what`: `displacement:total`, `displacement:y`,
`stress:von_mises`, `stress:p1` (maximum principal), `stress:p3`, `stress:max_shear`,
`stress:xx`, `temperature:value`. Reaction, linearized and section tools set their own
quantity. `step`: -1 (the default) is the last step.

Examples (one command each, passed to `submit`):

```
{"command": "create", "kind": "probe", "data": {"name": "Reaction on Bar/Root",
  "source": "<study id>", "type": "reaction", "selection": "Bar/Root"}}

{"command": "create", "kind": "probe", "data": {"name": "Peak stress on fillet",
  "source": "<study id>", "type": "selection", "selection": "Bracket/Fillet",
  "quantity": "stress:von_mises", "reduction": "max"}}

{"command": "create", "kind": "probe", "data": {"name": "Deflection along the beam",
  "source": "<study id>", "type": "path", "point": [0, 0.005, 0.005],
  "end": [0.2, 0.005, 0.005], "quantity": "displacement:y", "samples": 50}}

{"command": "create", "kind": "probe", "data": {"name": "Wall at nozzle",
  "source": "<study id>", "type": "linearized", "point": [0.1, 0, 0],
  "end": [0.112, 0, 0], "components": "line", "bending": "normal",
  "allowable": 138000000}}
```

A plane or section tool needs a construction plane first. Make it with one `submit`:

```
{"command": "create", "kind": "construction", "data": {"name": "Mid-span",
  "type": "plane", "origin": [0.1, 0, 0], "normal": [1, 0, 0]}}
```

`get_object("Mid-span")` then gives its `id`. Then, in a second `submit`:

```
{"command": "create", "kind": "probe", "data": {"name": "Force at mid-span",
  "source": "<study id>", "type": "section", "construction": "<plane id>"}}
```

A section's force is what the side the normal points to passes to the other side. A
construction `path` (`"type": "path"`, `start`, `end` in m) can feed a path or
linearized tool the same way, in place of `point` and `end`.

To remove a tool: `{"command": "delete", "object": "<tool name>"}`.

## Reading them

- `probes()`: every result tool, read now: `probe`, `type`, `value`, `label`, `unit`,
  `step`, and the type's own keys. A tool that can't be read gives `error` and `code`.
- `probe_path(name)`: a path's or linearization's series: `distance` (m) and `value`
  (null outside the model).
- `probe_history(name)`: the tool at every step: `[{step, value}]` (load-deflection,
  transients).
- A linearized tool's `checks` (with `allowable`) compare Pm, PL + Pb and PL + Pb + Q
  with S, 1.5 S and 3 S (ASME VIII-2 5.2.2.4). Which stress is primary is the
  engineer's call: say so.

## Units

Everything SI: m, N, N·m, Pa, K. Points are in metres on the undeformed geometry.
`allowable` is in Pa (138 MPa is `138000000`). Report numbers in the user's units.

## Mistakes to avoid

- `source` given as a name: refused. Use the id.
- Trusting that a reaction tool was accepted: it is made on any selection of any
  study (only `selection` is checked), and fails only when read. On Elmer, FEniCSx
  or a CFD run, which write no reaction forces, reading it gives `unsupported`; a
  selection with no reaction values gives `not_found`. Use CalculiX or Code_Aster,
  and name the held selection (a face that isn't held reads no support reaction).
- Reading a support's reaction from `result_stats` of `reaction_force`: that gives one
  node's value. Sum it (`result_total`) or use a reaction tool.
- A reaction selection that touches a loaded face: shared nodes carry the load too.
- A peak stress at a sharp corner or a point load: it grows with every refinement.
  Read it away from the singularity, or linearize through the wall.
- A path or linearization whose line leaves the material: points outside are left out;
  a linearization is refused, saying where it left. Keep both ends inside the wall.
- `integral` over an edge or a point selection: refused; use a face or a body.
- Picking path ends or a moment point in the view is GUI only (Results ribbon:
  Probe, Reaction…, On a selection…, Path…); agents give coordinates.
