---
title: Getting started
section: start
order: 0
keys: start, page:start, section:results, tree:section
summary: Your first analysis, from geometry to results, in six steps.
adrs: 0083, 0090
---

Puzzle sets up one model and runs it on several open solvers: CalculiX, Code_Aster,
Elmer, FEniCSx and OpenCourant for solids and fields, Code_Saturne and OpenFOAM for
flow, PyNite for frames. You build the model once; the study says which solver runs it.

If you'd rather not do the steps yourself, ask the agent (the Agent panel, or
[Agent and Advisor](help:guides/agent-and-advisor)): it can set up, check and explain
any of this, and waits for your approval before it changes anything.

## Six steps

1. **Geometry.** Geometry → Import geometry… (or the toolbar, or drop a STEP, IGES,
   BREP or STL file on the window) reads a CAD file; Geometry → New part… draws one
   from a history of operations. [Geometry](help:guides/geometry).
2. **New analysis.** Model → Studies → New analysis… (the toolbar, or the note that
   comes after an import) asks the analysis (static structural, modal, buckling, steady
   heat), the material and how fine a mesh, in one form. It makes the mesh recipe, the
   component, its material and physics and the study as one undo; each is an ordinary
   object to change later. [Components](help:guides/components),
   [Meshing](help:guides/meshing), [Materials](help:guides/materials).
3. **Loads and supports.** Pick (P), double-click the faces in the view, then Add load
   or support… in the pick bar: what it is, the faces and its values in one form. The
   faces become a selection named after it. [Loads and supports](help:guides/loads-and-supports).
4. **Run.** Run (Ctrl+R) on the toolbar runs the study: the selected one, or the only
   one, or the one you choose. [Studies and solvers](help:guides/studies-and-solvers).
5. **Results.** The view shows them when the run ends. The Results ribbon above the
   view has Probe (double-click the part to read a value), Reaction…, paths, cuts,
   animation and plots. [Results](help:guides/results).
6. **Export.** Export… on the Results ribbon writes the results for other software.
   [Results](help:guides/results).

Every step can be done by hand too: Mesh → New mesh recipe, Model → Components → New
component…, Model → Studies → New study… and the physics' right-click Add load or support make
each object on its own.

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
