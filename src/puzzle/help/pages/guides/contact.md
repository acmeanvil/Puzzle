---
title: Contact
section: guide
order: 70
keys: panel:contacts
summary: Join the parts of an assembly: find where they touch, choose bonded or sliding contact, check it before a run and read its results.
adrs: 0039, 0056, 0076, 0080
---

A contact joins two face selections: where parts touch, or where the bodies of one
imported part touch. You need one whenever a component has two or more parts, or one
part holding several bodies, that must carry load to each other.

Each contact has a **source** (slave) side and a **target** (master) side. Make the
smaller or finer-meshed face the source.

## How to find the contacts of an assembly

1. Select the component in the model tree.
2. Model → Contacts → Find contacts… (or right-click the component, Find contacts…).
   Each row is a pair of parts, or of bodies, that touch: the faces on each side, the
   gap and the overlap in the units shown.
3. Choose a type for each row, and a friction coefficient for a frictional one. A type
   no solver of the component's studies can write is greyed, with its reason.
4. Choosing a row shows both sides' faces in the view. Untick the rows you don't want.
5. OK makes the face selections and the contacts in one batch: one undo removes all.

An imported STEP assembly is usually one part with several bodies. Its bodies are
searched too, so you don't need to split it first. Model → Components → One part per
body… splits it if you would rather work part by part.

## How to add one contact by hand

1. Make the two face selections (Pick, then double-click faces, then Geometry →
   Selections → New from picks…).
2. Model → Contacts → Add contact…, and give the source, target and type.

## Contact types

| Type | What it does | Written by |
| --- | --- | --- |
| Bonded | glued: no gap, no sliding | CalculiX, Code_Aster; Elmer, FEniCSx conformal |
| No separation | never opens, slides freely; linear, so one solve | CalculiX, Code_Aster |
| Frictionless | may open and slide | CalculiX, Code_Aster |
| Frictional | may open; slides past μ (required) | CalculiX, Code_Aster |
| Rough | may open; never slides while closed | CalculiX, Code_Aster |

- A bonded contact is **conformal** by default: the parts share nodes and nothing is
  written. Set its meshing to nonconformal to mesh the parts apart and tie them
  (CalculiX `*TIE`, Code_Aster `LIAISON_MAIL`). Elmer refuses nonconformal bonds.
- Every other type is always nonconformal, and nonlinear except no separation.
- **Self-contact** (the same selection on both sides, a tube folding onto itself) is
  for explicit dynamics on OpenCourant only.
- [What each solver does](help:reference/capabilities) lists every contact a solver
  writes.

## How to check contacts before a run

1. Model → Contacts → Contact tool (or Window → Contacts, Ctrl+7).
2. The **Setup** tab lists every contact with what the solver is given, in its own
   words. Cells edit in place; an edit on several selected rows applies to each.
3. **Initial state**: Check meshes if needed, then colours each source face: closed
   green, near yellow, open orange, far red, overhanging purple. It gives the share of
   the face in touch, the largest gap and penetration, and warnings with the fix.
4. Warnings don't stop a run. They are also listed in Problems and the Advisor.

Contacts on curved faces meshed differently start with small gaps or penetrations, a
few per cent of an element. That is meshing, and counts as closed.

## Options worth knowing

- **Search distance**: how far apart faces may start and still count as in reach.
  Empty means 1 % of the model's size.
- **Stiffness** (penalty contacts): accurate, balanced (the default) or fast. On
  CalculiX these are 50, 10 and 1 × E/h.
- **Interface** (frictionless and frictional only): as is (the default); adjust moves
  the source nodes within the search distance onto the target, closing small gaps;
  offset starts the faces a given distance apart. A negative offset is an interference:
  model the parts touching and give the press fit as, say, −0.02 mm. Benchmarked
  against Lamé on both solvers, pressure within 0.3 %.
- **Weak springs** hold a body that rests only on sliding contact while the contact
  closes: three springs of 1e-6 × E × V / L² each. The study's weak springs setting is
  auto (the default), always or never. If they carry more than 0.1 % of the load, the
  run warns [weak_springs_carry](help:problem:weak_springs_carry): the body never
  seated.
- **Threads**: right-click a contact between two coaxial cylinders, Make it a thread…
  (or Model → Contacts → Thread on a contact…). The contact then acts along the
  flanks, giving each thread's load and the nut's wedge. Written by CalculiX and
  Code_Aster, and by FEniCSx in 2D axisymmetric models; Elmer refuses threads.

## How to read contact results

After a run on CalculiX or Code_Aster, the Contact tool's **Results** tab gives each
contact's normal and frictional force, area in contact and sticking, and its largest
pressure, penetration, sliding and gap. Its buttons draw the fields on the source faces:
[contact pressure](help:result:contact_pressure),
[contact status](help:result:contact_status) (0 open, 1 sliding, 2 sticking),
[gap](help:result:contact_gap) and [sliding](help:result:contact_sliding).

- An area in contact near zero: the contact never closed.
- Tangential over normal force equal to μ with nothing sticking: the whole face slides.
- A penetration above about 1 % of an element: the penalty is soft; try accurate.

## Mistakes to avoid

- A gap tolerance typed in the wrong unit finds false pairs across gaps. Read the gap
  column before making anything.
- Frictionless or frictional contact in a study run by Elmer or FEniCSx: refused
  ([unsupported](help:problem:unsupported)). Use CalculiX or Code_Aster, or bond.
- Frictional contact on Code_Aster needs a first-order mesh and no support on the
  source face's nodes. Otherwise set the recipe's element order to 1, swap the sides,
  or run on CalculiX.
- A curved face meeting another along a line or at a point (a cylinder on a plate):
  Code_Aster's pairing fails, so it refuses it. Run it on CalculiX.
- The source running past its target's edge (overhang): CalculiX can invert elements
  there. Make the smaller face the source.
- Turning weak springs off to silence a warning: the body then has nothing holding it.
- A thread between cylinders whose radii differ by more than 2 % of d is refused
  ([thread_radii](help:problem:thread_radii)). Model both at the screw's minor radius.
  Thread-root peak stresses aren't represented, and threads take no friction yet.

## For agents and scripts

The agent finds contacts with [contacts_find](help:mcp:contacts_find), splits a part
with [parts_split](help:mcp:parts_split), checks them with
[contact_check](help:mcp:contact_check), reads results with
[contact_results](help:mcp:contact_results), sets threads with
[contact_thread](help:mcp:contact_thread) and [thread_results](help:mcp:thread_results),
and reports weak springs with [weak_springs](help:mcp:weak_springs). In a terminal,
`puzzle contacts PROJECT` ([cli:contacts](help:cli:contacts)) prints each contact's
setup and initial state.

## See also

- [Bolts and welds](help:guides/bolts-and-welds)
- [Components](help:guides/components)
- [Studies and solvers](help:guides/studies-and-solvers)
- [Problem codes](help:reference/problems)
