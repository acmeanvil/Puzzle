---
name: materials
description: adding a material to a project, assigning it to bodies, checking it has what a study needs, temperature tables, plasticity and orthotropic models
adr: 0020
---
Read this before adding or assigning a material, when a problem says
`incomplete_material`, or when the user asks for plasticity, orthotropic or
temperature-dependent properties.

A project material is a copy. Adding one from the library copies it in; later library
changes never reach it. Puzzle ships generic materials (structural steel with EN
1993-1-2 temperature tables, stainless 304, aluminium 6061-T6, Ti-6Al-4V, copper,
electrical steel, PTFE, air, water, and fastener grades in the group Fastener). The
user may have their own library on top. Agents never write the library: the user does
that in the Material builder (toolbar Material builder…).

A component uses a material through an assignment: a material and a selection, usually
a part's body (`"Bar/Body"`).

## Steps

1. `outline(ids=true)`: the component, its parts and body selections, and the
   project's materials with their ids.
2. Find a library material: `materials_library(text="steel")` gives `key` and `name`.
   The text matches the name, group or specification.
3. Check it before adding: `material_check(material="structural_steel")` (a library key
   or a project material's name). It returns `models` (the material models it has) and
   `analyses`: for each solver and analysis, `missing` properties and `refused` models.
4. Add it: `{"command": "add_library_material", "key": "structural_steel"}`, with
   `"name"` to rename the copy. The result gives the new material's `id`.
5. Assign it (the material by id, not name):
   `{"command": "edit", "object": "<component>", "ops": [{"op": "append", "path":
   "materials", "value": {"material": "<material id>", "selection": "Bar/Body"}}]}`.
   Each body needs one assignment; check every part has one.
6. `problems()`: `incomplete_material` names the material, the study and the missing
   property keys.

## A material of your own

`{"command": "create", "kind": "material", "data": {"name": "Cast iron",
"properties": {"density": {"value": 7200, "unit": "kg/m^3"},
"youngs_modulus": {"value": 110, "unit": "GPa"}, "poissons_ratio": {"value": 0.26}}}}`.
Add one property later: `{"op": "set", "path": "properties.yield_strength", "value":
{"value": 250, "unit": "MPa"}}`.

Property keys: `density`, `youngs_modulus`, `poissons_ratio`, `yield_strength`,
`tensile_strength`, `proof_strength`, `thermal_expansion`, `thermal_conductivity`,
`specific_heat`, `dynamic_viscosity`, `relative_permittivity`,
`relative_permeability`, `electrical_conductivity`.

What a study needs:

- solid mechanics: Young's modulus, Poisson's ratio; density for modal, transient,
  harmonic and body loads (gravity, acceleration, rotation); thermal expansion with a
  temperature load or the thermal expansion coupling;
- heat transfer: conductivity; density and specific heat when transient or with flow;
- fluid flow: density, dynamic viscosity;
- electrostatics: relative permittivity; magnetics: relative permeability and
  electrical conductivity; electric currents: electrical conductivity.

## Temperature tables

`tables` holds a property against temperature:
`{"op": "set", "path": "tables.thermal_conductivity", "value": {"unit": "W/(m*K)",
"points": [{"temperature": 293.15, "value": 53.3}, {"temperature": 873.15, "value":
34.0}]}}`. Temperatures are in kelvin; values are in the table's `unit`. Values are
held constant past the ends. A table for thermal expansion is a secant (mean)
coefficient; set `reference_temperature` (K) to the temperature it is measured from.

Heat transfer uses the conductivity, specific heat and density tables by default,
which makes the solve nonlinear. To use the single values, set the heat physics'
`temperature_dependent_materials` to false:
`{"op": "set", "path": "physics.<i>.settings.temperature_dependent_materials",
"value": false}` on the component.

## Material models

In `models`, each a typed block. Values are SI numbers (Pa, W/(m K)), not quantities.

- `plasticity`: von Mises, isotropic hardening. `{"op": "set", "path":
  "models.plasticity", "value": {"curves": [{"points": [[0, 250e6], [0.05, 320e6]]}]}}`:
  (equivalent plastic strain, true stress in Pa), starting at zero strain (the yield
  stress), strains increasing. CalculiX and Code_Aster.
- `orthotropic_elasticity`: `e1`, `e2`, `e3`, `nu12`, `nu13`, `nu23`, `g12`, `g13`,
  `g23`, `axis1`, `axis2`. CalculiX and Code_Aster.
- `anisotropic_conductivity`: `k1`, `k2`, `k3`, `axis1`, `axis2`. CalculiX and
  Code_Aster.
- `hyperelasticity`: refused by every solver so far.

Elmer and FEniCSx refuse these models, but only for a study that solves the model's
physics: a plastic steel still serves a heat study there. For the material axes, give
the assignment an `axes` (a coordinate system's id); without it, `axis1` and `axis2`
are used.

## Units

A property is a quantity, `{"value": 210, "unit": "GPa"}`; any unit pint knows works,
and Puzzle converts to SI for solvers. A unitless one (Poisson's ratio, relative
permittivity) is `{"value": 0.3}`. Tables and models are SI numbers, temperatures in K.

## Mistakes to avoid

- Assigning by the material's name: `material` is the id.
- Editing a project material and expecting the library to change, or the reverse.
- Forgetting density for a modal study or a gravity load: `incomplete_material`.
- A table in °C: table temperatures are kelvin (20 °C = 293.15 K).
- Writing plasticity for a study on Elmer or FEniCSx: it is refused; use CalculiX or
  Code_Aster.
- Generic values are starting points: say so when a result depends on them, and ask
  for the supplier's data for a real design.
