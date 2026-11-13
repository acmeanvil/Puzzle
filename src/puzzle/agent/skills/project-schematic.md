---
name: project-schematic
description: how a project fits together: what feeds what (transfers, imported fields), what is shared, what a change would put out of date; making transfers
adr: 0058
---
The schematic is the whole project as nodes and wires, built from the model each time
and never stored. Read it to explain a project, to say what a change reaches before
making it, or to find why a study is out of date.

## Read it

1. `schematic()` returns:
   - `nodes`: each with `name`, `kind` (File, Geometry, Material, Mesh recipe, Parameter
     set, Imported field, Component, Study, Results, Derived result, Group), `column`
     (Inputs, Resources, Models, Studies, Results), `state` and its `reason` (as the
     tree: up_to_date, out_of_date, not_run, failed, missing, unused, complete),
     `details`, `chips` (small references used there: "CS: Flange", "Amplitude: Ramp",
     "Result tool: Tip"), a `badge` (a sweep's "× 12 points", a shoot-out's solvers),
     `inputs` and `outputs` (a study's outputs are the fields its physics produce, even
     before it has run).
   - `wires`: `kind` `data` (a transfer, an imported field into what it drives, runs
     into a derived result; `carries` says what: "temperature, last step, interpolate",
     "p ×1.2") or `use` (what each thing is built from: file → geometry → component →
     study, material and mesh recipe → component, parameter set → the study sweeping it).
   - `flow`: the same as short lines ("Thermal -> Static (temperature, ...)",
     "geometry Base used by Bolted bracket, Bonded bracket"). The outline's `Flow:`
     section is these lines too.
2. `schematic(node="<name>")` adds `node.upstream` (everything it is built from or fed
   by) and `node.downstream` (everything that would go out of date if it changed).
   Before editing a shared material, geometry or mesh recipe, say what is downstream.

## Explain a state

A study `out_of_date` with "the model changed since the last run": walk its upstream
for what changed (the user's edit history, `history`, says when). A Results node is the
study's latest run; its state is that run's against the model now. A file node
`missing` means the project folder lacks the file a geometry or imported field reads.

## Make a transfer (a data wire)

One study's result into another, one `submit`:
`{"command": "create", "kind": "transfer", "data": {"name": "Temperature from Thermal",
"source": "<study id or name>", "field": "temperature", "target": "<study>",
"role": "load", "mapping": "interpolate", "target_step": 0}}`.
- `field` is one of the source's outputs from `schematic()` (temperature, displacement,
  stress, ...); `source_step` -1 (the default) is its last result step.
- `role`: `load` (a temperature becomes thermal strain), `initial_condition` (the
  starting state), `prestress` (a static study's stress stiffening a modal or buckling
  one, CalculiX). `mapping`: `interpolate` or `nearest`; one mesh copies values.
- Transfers can't form a cycle; the study fed sits to the right of its source.

## Mistakes

- Use wires are not data: a component "used by" three studies shares its geometry,
  mesh and materials, nothing flows between those studies unless a transfer says so.
- Where a node sits means nothing; positions are each user's own, kept outside the
  project.
- Coordinate systems, amplitudes, bolt types and construction geometry are chips, not
  nodes: look for them in `chips`.
- Operators (Reduce, Check, Plot, ...) are nodes of kind Operator in the Results column,
  their wires data wires; to make or read them, see the skill `schematic-operators`.


## Building in the schematic (ADR 0071 decision 8)

The schematic builds a project as the tree does. To wire one object into another (a
material onto a component, a mesh recipe onto a component, a component into a study, a
coordinate system or amplitude onto a component's load, a study onto a result tool, a
construction plane onto a result tool, a bolt type onto a bolt), call `link` with the
two names: it applies the one thing the wire can mean, or returns `choices` (index and
label, such as "Material of Bracket (new)" or "Coordinate system of Pressure on
Bracket/Inner") to pass back as `which`. A wire that fits nowhere says what the target
takes. `unlink` removes a reference; one an object can't be without (a study's
component, a component's mesh recipe) is refused, so link another in to replace it.
Coordinate systems, amplitudes, bolt types, construction geometry and result tools are
nodes of their own now, wired to what uses them, and so is every load and support
("Pressure on Bracket/Inner", wired into its component). To wire onto one load, pass
`feature` with its name as shown (or its path, physics.0.features.1): a coordinate
system or amplitude goes onto that load, and a geometry sets where it acts ("Acts on
Bracket/Outer"). Create objects with `submit` as usual.
