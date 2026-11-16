---
title: Bolts and welds
section: guide
order: 80
keys: kind:bolt_type, section:bolt types, panel:bolts, panel:welds
summary: Join parts with pretensioned bolts and with welds, then read each bolt's forces and each weld's line force against its code.
adrs: 0050, 0077, 0078
---

Bolts and welds join parts without bonding their faces. A bolt is a beam (or a drawn
solid) between two holes; a weld is a connection along an edge. Use them for bolted
flanges, brackets and base plates, and for welded joints whose strength you must check.

## How to make a bolt type

A bolt type holds a bolt's size, grade, preload and joint style once, for every bolt of
that kind. What a bolt sets itself wins over its type.

1. Model → Bolts and fasteners → New bolt type… (or right-click Bolt types in the tree).
2. Choose the standard and size: ISO metric coarse (M3 to M64) or fine, UNC, UNF, or a
   custom diameter.
3. Choose the grade: a project material, or a bolting grade from the library's Fastener
   group (ISO 898-1 Class 8.8, SAE J429 Grade 5, ASTM A325, F1554 anchor rods...).
4. Choose the preload: none, a force, a share of the yield load (70 % by default), or a
   torque with a nut factor K (F = T / (K d); 0.2 plain, 0.15 lubricated).
5. Choose the joint: hole walls, bolt and nut, cap screw in a tapped hole, stud with
   two nuts, or stud in a tapped hole.

## How to add bolts

- **Many at once**: Model → Bolts and fasteners → Generate bolts…. Holes come from a
  rule (hole diameters, parts, an axis, lengths), from two face selections, or from
  picks. The bolts are previewed as tubes; Make creates the ticked ones in one batch.
  Coaxial holes through several plates make one bolt, not one per pair of layers.
- **From holes**: Model → Bolts and fasteners → Fasteners from holes… makes one
  fastener per stack of coaxial holes.
- **One bolt**: Model → Bolts and fasteners → Add fastener…, between two hole faces, or
  to ground (an anchor into a foundation not modelled).

The rule is kept on the component: run Generate bolts… again after the geometry
changes, and it says which bolts are new and which lost their holes.

## Beam bolts and modelled bolts

A bolt is a **beam** by default: a Timoshenko beam between spiders on the two ends,
with no mesh. It gives the bolt's forces and moments cheaply. Ends are distributing
(the default: they spread load without stiffening the hole) or rigid.

A **modelled** bolt is drawn from the fastener tables as parts of its own: head,
under-head fillet, shank, thread and nut, meshed as a body of revolution. Use it for the
fillet's or thread's stress, or bending in a short or prying joint. In the Bolt table,
right-click a row: Model the bolt, Model the bolt, heads bonded, or Back to a beam bolt.

- The head and nut sit on the plates through frictional contact (μ 0.15), which makes
  the solve nonlinear; heads bonded keeps it linear.
- Its section carried the preload within 0.5 % in the M16 benchmark, and the fillet's
  peak matched Tipton's factor within 1 %.
- The thread is a smooth cylinder, so its root notch is a factor, Thread Kf (default 3).
  The fatigue tool applies it at the thread.

## Pretension and the preload step

Tightened bolts are loaded before anything else. On CalculiX and Code_Aster, Puzzle
puts a preload step in front of the first static step when that step also loads. The
loads move to the later steps and the bolts are locked after preloading. Results then
have a Preload step first. The study's preload step setting is auto (the default),
always or never.

## How to read bolt results

Window → Bolts (Ctrl+5), or Model → Bolts and fasteners → Bolt table.

- **Properties**: one row per bolt. A value taken from the type shows faded; typing one
  sets it on the bolt, clearing it gives the type's back. An edit on several selected
  rows applies to each.
- **Results**: for a run and step, each bolt's axial, shear, torsion and bending
  forces, its stress, and its share of its grade's proof and yield loads. Above 1 of
  proof, the bolt is past its proof load.

<!-- key: form:weld_add -->
## Welds

A **light weld** joins two parts along an edge with springs across the joint; the parts
keep their own meshes. Its result is the **line force** (N/m) at stations along the
path, which is what the codes check. It runs on CalculiX, Code_Aster and FEniCSx (they
agree to four digits); Elmer and 2D models refuse it.

**Model → Bolts and fasteners → Add weld…** (also a component's Fasteners folder and the
Welds panel): pick the edge at the joint's corner in the view (Pick, the Edges filter),
or choose an edge selection, then the type (fillet, or a CJP or PJP groove), the leg
(a groove's depth) and an unequal fillet's second leg, continuous or intermittent
(segment and pitch), and a name. Of the edge's two faces, the one on the other part is
the seat; the form says which part it joins and the faces the legs lie on before OK,
and makes the selections and the weld as one undo. Welds appear under the component's
Fasteners folder. A **modelled weld** draws the bead as a
solid part of its own, to see the stresses in and around it.

### Weld strength and the Welds panel

Window → Welds (Ctrl+Shift+L), or Results → Result tools → Weld results. Per weld: its
length, total force, largest line force, and its utilisation at the governing station,
worst first.

- **AISC 360-16 J2** for carbon steel: F_nw = 0.60 F_EXX on the throat, φ 0.75 (LRFD)
  or Ω 2.00 (ASD). The filler is the weld's, else E70XX.
- Stainless by AISC 370-21 J2 (E308L, E316L, E2209), aluminium by the Aluminum Design
  Manual J.2 (ER4043 by default).
- The **instantaneous centre** check (AISC Manual Part 8) for the group's in-plane force
  and moment.
- **Fatigue**: AISC 360 Appendix 3 categories A to F, or ASME VIII-2 5.5.5 structural
  stress: Weld fatigue… below.

The base metal (J4) and the minimum weld size (Table J2.4) are yours to check.

<!-- key: form:weld_fatigue -->
### Weld fatigue

**Model → Bolts and fasteners → Weld fatigue…** (also a weld's and a run's right-click,
and the Welds panel) reads a study's latest run. Choose the weld and the method:

- **AISC 360-16 Appendix 3**: the detail's category (Table A-3.1) and the cycles. F is
  shear on the fillet's throat from the line forces; A to E′ the base metal at the
  weld's toe (the largest principal stress range at its footprint, mesh-dependent). The
  form gives the range, the allowable F_SR and their ratio (1 or less passes).
- **ASME VIII-2 5.5.5**: the equivalent structural stress at the attachment's toe (or
  the plate's), membrane f/t plus bending 6m/t², each station's permissible cycles on
  Table 3-F.2M's curve (−3σ by default), plotted along the weld, and with cycles given
  the damage at the worst station. A solid's thickness at the toe must be given; a
  shell's is its own.

Steps: empty is the last step from zero, one step is from zero, two the range between
them. Nothing in the project changes.

<!-- key: form:vdi2230 -->
## A joint by VDI 2230

**Model → Bolts and fasteners → VDI 2230 check…** (also a bolt type's and a bolt's
right-click, and the Bolt table's) checks a concentric single-bolt joint by VDI
2230-1:2015 by hand, without a solve: size, grade (or a given Rp0.2), hex or socket head,
a nut or a tapped hole, the clamp length and any unthreaded shank, the bolt's, plates'
and nut's moduli, thread and head friction, the interfaces and Rz for embedding, the
load introduction factor n, the axial working load and the tightening factor αA. From a
bolt type it starts with the type's size, grade, head and joint; from a bolt, its
length between the ends too.

The table updates as you type: resiliences and the load factor Φ, the assembly preload
FMzul at 90 % of Rp0.2 and its tightening torque and nut factor K, embedding and its
preload loss, and with a working load the bolt's extra load, working stress and fatigue
safety; with αA the least preload and the residual clamp load. The check with the
largest utilisation is marked as governing. Save as CSV… or Save as text… keeps the
table. Nothing in the project changes.

## Mistakes to avoid

- Two bolts on one face selection, or a rigid end on a fixed face: CalculiX refuses.
  Use separate hole selections and distributing ends.
- Frictional contact plus bolts on Code_Aster may not converge: run it on CalculiX.
- Modelled bolts with frictional heads on Code_Aster need a first-order mesh.
- Don't model every bolt: frictional heads make each one slow. Model those whose
  stresses matter.
- Read a modelled bolt's force from the Bolt table, not a node near the pretension cut.
- A weld on parts that are bonded too: both carry the load. Remove the bond.
- A weld's peak line force sits at ends and corners. Compare it with the mean, and say
  where it is.

## For agents and scripts

The agent uses [bolts_generate](help:mcp:bolts_generate), [bolt_sizes](help:mcp:bolt_sizes),
[bolt_results](help:mcp:bolt_results), [bolt_make_modelled](help:mcp:bolt_make_modelled)
and [bolt_make_beam](help:mcp:bolt_make_beam). Hand checks of a joint by VDI 2230-1
(resiliences, load factor, assembly preload and torque) are
[bolt_vdi2230](help:mcp:bolt_vdi2230), and standard heads, nuts and washers
[fastener_tables](help:mcp:fastener_tables). Welds: [weld_add](help:mcp:weld_add),
[weld_results](help:mcp:weld_results), [weld_fatigue](help:mcp:weld_fatigue),
[weld_make_modelled](help:mcp:weld_make_modelled) and
[weld_make_line](help:mcp:weld_make_line).

## See also

- Theory: [Bolted joints](help:theory/bolted-joints), [Thread loads](help:theory/thread-loads), [Weld strength](help:theory/weld-strength), [Fatigue](help:theory/fatigue).
- [Contact](help:guides/contact)
- [Loads and supports](help:guides/loads-and-supports)
- [Results](help:guides/results)
- [Material properties](help:reference/properties)
