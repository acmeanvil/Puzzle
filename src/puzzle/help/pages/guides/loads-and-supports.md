---
title: Loads and supports
section: guide
order: 60
keys: kind:amplitude, section:amplitudes, kind:parameters, section:parameters
summary: Hold a model and load it: supports, forces, pressures, remote and body loads, heat loads, loads over time, and values that follow a parameter.
adrs: 0037
---

Loads, supports and other conditions are **features** of a component's physics. Each
acts on a selection and holds its values; a value you leave out stays free. A static or
transient structure needs at least one support, or the run is refused as `unconstrained`.

## How to add a load or support

1. Right-click the physics in the model tree (or its Loads and boundary conditions
   folder), Add feature, and choose one. Model → Physics and loads → Add feature does
   the same.
2. In Properties, choose the selection and type the values, in any unit of the right
   kind ("2 MPa", "5 kN").
3. Right-click the feature, Change selection…, to move it. A support's menu has Reaction
   force… to read what it carries after a run.

The supports and loads symbols in the view show where each acts (the view toolbar's
Supports and loads button hides them).

## Supports

| Feature | Holds | Solvers |
| --- | --- | --- |
| Fixed support | every displacement | every structural solver |
| Prescribed displacement | the components you give (mm); others free | every structural solver |
| Elastic support | a stiffness per area, normal and tangential | CalculiX, Code_Aster, Elmer, FEniCSx |
| Compression-only support | pushes back but lets go; nonlinear | CalculiX, Code_Aster |
| Remote displacement | a rigid motion of the face: three moves, three turns | CalculiX, Code_Aster, Elmer, FEniCSx |

Symmetry and antisymmetry planes are supports too
([Components](help:guides/components)).

## Loads

- **Force:** the total over the face, in N, shared over its area. Not per node.
- **Pressure:** per area; positive pushes into the face.
- **Traction:** a force per area in x, y and z.
- **Remote force and moment:** a force and moment at a point (the face's centroid by
  default), spread over the face, which stays deformable. The rigid version keeps the
  face rigid (CalculiX and Code_Aster).
- **Gravity** (default −9.81 m/s² in z), **linear acceleration** (its inertia, −a) and
  **rotation** (centrifugal, in rpm about an axis) act on bodies and need a density.
- **Temperature (thermal load):** a body temperature that gives thermal strain; it needs
  thermal expansion.
- **Bolt pretension** acts on a bolt's section ([Bolts and welds](help:guides/bolts-and-welds)).

## Heat loads

- **Temperature** held on a face (default 20 °C).
- **Heat flux** into the body through a face, in W/m².
- **Convection** to a fluid: a film coefficient (default 10 W/(m²·K)) and the fluid's
  temperature (default 20 °C).
- **Heat generation** per volume in a body, in W/m³.

The [loads and supports reference](help:reference/features) lists every feature of every
physics, with its values, defaults and solvers.

## How to apply a feature in some steps only

A feature's Steps field in Properties reads All steps. Type the solve steps it applies
in, numbered from 1: `2, 3`. A pretension acts in its first step and is held at its
length after.

A study whose first static step tightens bolts and loads gets a preload step in front on
CalculiX and Code_Aster. The bolts are tightened with the supports only, then the loads
come on with each bolt locked. The study's preload step setting (auto, always, never)
controls it.

## How to vary a load over time

1. Project → New amplitude… (or right-click the Amplitudes heading). Type the table of
   time (s) against factor; it is linear between points and holds its end values.
2. Select the load and choose the amplitude in its Amplitude field.
3. Transient steps multiply the load's values by the factor at each time.

Force, pressure and heat flux take an amplitude on CalculiX, Code_Aster, Elmer and
FEniCSx. OpenCourant also takes it on displacement, gravity, acceleration and velocity.
Right-click an amplitude, Edit points…, to change its table.

## How to make a value follow a parameter

1. Project → New parameter set, then Project → Add parameter…: a named value such as
   `t = 8 mm`.
2. In Properties, type `= t` into any quantity: a load's value, a mesh size. The row then
   shows `= t:` before the value.
3. The value is put in before each solve, and at every point of a sweep or optimisation
   that changes `t`.

Geometry steps take a parameter's name in place of a number. Pre-solve operators (a
Number, Slider or Expression) work as parameters by name too, and can be wired onto a
load in the schematic ([Schematic and operators](help:guides/schematic-and-operators)).

## Options worth knowing

- **Axes:** a coordinate system for a feature's x, y and z (r, θ, z when cylindrical).
  Force, displacement, gravity, acceleration and rotation take one.
- **When:** a Toggle's name; the feature is left out of the solve while it is 0.
- **Load case:** the case a load belongs to, for frame load combinations.

## Mistakes to avoid

- No fixed, displacement, remote displacement, elastic or compression-only support and
  no grounded bolt: the study is `unconstrained` and refused.
- A body load with a material that has no density: `incomplete_material`. On Elmer and
  FEniCSx, put body loads on a material's body selection.
- Typing a force per area into Force: Force is the total. Use pressure or traction.
- A compression-only support makes the step nonlinear and slower.
- Axes on a feature that doesn't take them (pressure, a cylindrical force, remote loads,
  spherical axes) is refused.
- On CalculiX, a cylindrical displacement turns its nodes' axes. Another displacement,
  force or remote load without axes on a selection sharing those nodes is refused.
- 2D models refuse remote loads and pretension.
- An amplitude on a feature its solver can't vary over time is refused, not ignored.

## For agents and scripts

Features are added and edited with [submit](help:mcp:submit).
[check_study](help:mcp:check_study) gives one study's verdict per solver,
[capabilities](help:mcp:capabilities) lists the features a solver takes, and
[problems](help:mcp:problems) lists what stops a run. After a run,
[result_total](help:mcp:result_total) sums a support's reaction force.

## See also

- [Loads and supports reference](help:reference/features)
- [Components](help:guides/components)
- [Studies and solvers](help:guides/studies-and-solvers)
- [Bolts and welds](help:guides/bolts-and-welds)
- [Imported fields](help:guides/imported-fields)
