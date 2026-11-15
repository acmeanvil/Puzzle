---
title: Materials
section: guide
order: 30
keys: kind:material, section:materials
summary: Add a material from the library or make your own, give it to bodies, and check it holds what each study and solver needs.
adrs: 0020
---

Every body a study solves needs a material, and the material needs the properties that
analysis uses: a modal study needs density, a heat study conductivity. Puzzle tells you
what is missing before a run, per solver.

A project material is a **copy**. Adding one from the library copies it in; later
library changes never reach it, and editing it never changes the library.

## How to add a material from the library

1. Materials → Add material… (or the toolbar, or right-click the Materials heading, Add
   from library…).
2. The picker groups the library by type, group and alloy, with a search.
3. Choose one. Its copy appears under Materials in the tree, where Properties edits it.

Puzzle ships generic materials: structural steel with EN 1993-1-2 temperature tables,
stainless steel, aluminium, titanium, copper, electrical steel, PTFE, air and six gases
at 20 °C and 1 atm, water and sea water. The groups Filler metal and Fastener hold weld
fillers and bolt grades. Your own library loads on top.

## How to give bodies a material

1. Materials → Assign material…, or right-click the component's Material assignments
   folder, Assign material….
2. Choose the material and the bodies (a body selection such as `Bar/Body`).

A body selection's right-click menu has Assign material… too. Each body needs one
assignment; the Problems tab names a body left without one.

## How to make or change a material in the builder

Materials → Material builder… opens your library. Its tabs:

- **Properties:** values by group, each in any unit of the right kind. Table… beside a
  property gives values at several temperatures.
- **Models:** plasticity, Johnson–Cook, failure, orthotropic elasticity, hyperelasticity
  and anisotropic conductivity (below).
- **Fatigue:** S-N curves and a strain-life model, drawn log-log.
- **Solver keywords**, **Sources** and **Completeness** (below).

New, New variant (it inherits from its parent), Remove mine, Remove from library and
Find duplicates… manage the library; Undo takes a change back. Save to my materials keeps
it; Add to project copies it in. Right-click a project material, Save to my materials…,
to keep an edited copy for other projects.

To share a library, Materials → Export material library… writes one file; Materials →
Import material library… reads one into yours.

## How to check what a material still needs

The builder's **Completeness** tab lists, for each solver and analysis, the properties
missing and the models refused. A study whose material lacks a property reports
`incomplete_material` in the Problems tab, and the run is refused until it is filled.

What each analysis needs:

| Physics | Needs |
| --- | --- |
| Solid mechanics | Young's modulus, Poisson's ratio; density for modal, transient, harmonic and body loads; thermal expansion with a temperature load |
| Heat transfer | conductivity; density and specific heat when transient |
| Fluid flow | density, dynamic viscosity |
| Electrostatics | relative permittivity |
| Magnetic fields | relative permeability, electrical conductivity |

## Temperature tables

A property's table holds its value at several temperatures, linear between them and
held constant past the ends. A table for thermal expansion is a secant (mean)
coefficient, measured from its reference temperature.

Heat transfer uses the conductivity, specific heat and density tables by default, which
makes the solve nonlinear. To use the single values, set the heat physics'
temperature-dependent materials setting off.

## Material models

| Model | What | Solvers |
| --- | --- | --- |
| Plasticity | von Mises, isotropic hardening: true stress against equivalent plastic strain | CalculiX, Code_Aster, OpenCourant |
| Johnson–Cook | rate-dependent flow stress for impact | OpenCourant |
| Failure | Johnson–Cook strain to failure; failed elements are deleted | OpenCourant |
| Orthotropic elasticity | nine constants in the material axes | CalculiX, Code_Aster |
| Anisotropic conductivity | three conductivities in the material axes | CalculiX, Code_Aster |
| Hyperelasticity | Neo-Hookean, Mooney-Rivlin | refused by every solver so far |

- Plasticity's Hardening curve… starts at the yield stress at zero plastic strain, with
  strains increasing. Curve builder… makes one from Ramberg-Osgood, the ASME Annex 3-D
  curve or a bilinear fit.
- Failure needs plasticity (a table or Johnson–Cook), and only explicit runs use it.
- For material axes, give the assignment a coordinate system in Properties
  ([Components](help:guides/components)).

A model is refused only for a study that solves its physics: a plastic steel still
serves a heat study on Elmer or FEniCSx.

## Mistakes to avoid

- Forgetting density for a modal study or a gravity load: `incomplete_material`.
- Editing a project material and expecting the library to change, or the reverse.
- Plasticity on a study run by Elmer or FEniCSx: refused. Use CalculiX or Code_Aster.
- Generic values are starting points. Use the supplier's data for a real design.

## For agents and scripts

[materials_library](help:mcp:materials_library) searches the library,
[material_check](help:mcp:material_check) says what a material lacks per solver and
analysis, and [materials_export](help:mcp:materials_export) writes your library inside
the project. Materials are added and assigned with [submit](help:mcp:submit). On the
command line: `puzzle materials-export`, `puzzle materials-import`,
`puzzle materials-remove` and `puzzle materials-duplicates`.

## See also

- [Material properties](help:reference/properties)
- [Components](help:guides/components)
- [Analyses](help:guides/analyses)
- [Studies and solvers](help:guides/studies-and-solvers)
