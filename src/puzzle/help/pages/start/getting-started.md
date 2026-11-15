---
title: Getting started
section: start
order: 0
keys: start, page:start, section:results, tree:section
summary: Your first analysis, from geometry to results, in six steps.
adrs: 0083
---

Puzzle sets up one model and runs it on several open solvers: CalculiX, Code_Aster,
Elmer, FEniCSx and OpenCourant for solids and fields, Code_Saturne and OpenFOAM for
flow, PyNite for frames. You build the model once; the study says which solver runs it.

If you'd rather not do the steps yourself, ask the agent (the Agent panel, or
[Agent and Advisor](help:guides/agent-and-advisor)): it can set up, check and explain
any of this, and waits for your approval before it changes anything.

## Six steps

1. **Geometry.** Geometry → New part… draws one from a history of operations, or
   Geometry → Import geometry… reads a STEP, IGES or BREP file. Name the faces you will
   load or hold as selections: Pick, then double-click faces in the view.
   [Geometry](help:guides/geometry).
2. **Materials.** Materials → Add material… picks a material; the material builder
   says what a study still needs from it. [Materials](help:guides/materials).
3. **Mesh.** Mesh → New mesh recipe sets the element size and shape; Model → Components
   → New component… puts the part, its material and the recipe together; Mesh → Mesh
   shows the mesh. [Meshing](help:guides/meshing), [Components](help:guides/components).
4. **Loads and supports.** Right-click the component's physics in the model tree, Add
   feature: a fixed support, a force, a pressure, a temperature...
   [Loads and supports](help:guides/loads-and-supports).
5. **Study.** Model → Studies → New study… chooses the analysis and the solver; Model →
   Run → Run study (Ctrl+R) solves it. [Studies and solvers](help:guides/studies-and-solvers).
6. **Results.** The Results ribbon above the view, or the Results menu: deformation,
   stresses, probes, reactions, paths, cuts, animation and plots.
   [Results](help:guides/results).

The Problems tab (under the view) says what is missing before a run, and the Advisor
(Window → Advisor) what to do next.

## Getting help

- **F1** opens this help at whatever has focus: a panel, a tree item, a row of the
  Properties panel.
- **Search** at the top of this window finds any page by its words.
- **Reference** pages list everything Puzzle knows, from its own data: every
  [load and support](help:reference/features), [solver setting](help:reference/settings),
  [element](help:reference/elements) and [problem code](help:reference/problems).
- The **agent** reads the same pages, so you can ask it to explain one.
