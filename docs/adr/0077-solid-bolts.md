# 0077. Generate solid bolts from the fastener tables, meshed as bodies of revolution

- Status: Accepted; built on CalculiX and Code_Aster (2026-11-10)
- Date: 2026-11-09

## Context

A bolt today is a beam (ADR 0027) or a small solid bar written into the solver deck
(`formulation="solid"`), joined to its holes by spiders. A beam bolt gives the bolt's
forces and, with ADR 0076, how its thread shares the load, but not the bolt's own
stress field. That field matters at the stress risers: the under-head fillet, the
core into the first engaged thread, and bending in short or prying joints. The deck's
solid bar doesn't capture it either, having no head, fillet or thread run-out.

Puzzle now holds the standard fasteners' dimensions (ADR 0050 with the Lacewing Lab
port: `puzzle/fasteners`, 37 tables: ISO 4014/4017, 4762, ASME B18.2.1, nuts, washers;
head height, width across flats, bearing diameter, thread length b, body diameter,
and ASME's under-head fillet radius R). So a bolt can be drawn as it is made, placed
where a beam bolt would go, and meshed and joined as an ordinary part.

The maintainer's practice: a thread root's fatigue notch is taken as Kf = 3 to 4 on the
basic stresses of the solid geometry, not meshed; Puzzle makes it an input defaulting
to 3.

Gmsh's revolve sweeps a meshed profile round an axis in layers (recombined: hexes, with
prisms on the axis), which suits an idealised bolt: the same ring all the way round,
node rings lining up with the thread's pairs (ADR 0076). Netgen has no swept mesh.

## Decision

1. **A bolt's geometry from its table row.** `puzzle/solid_bolts.py` draws the
   meridian profile of a bolt by head style (hex: ISO 4014/4017, ASME B18.2.1; socket
   head: ISO 4762), from the row: head height k, bearing diameter dw (or the head
   diameter), body diameter ds, thread length b, the under-head fillet r (ASME's R;
   ISO 885's and ISO 4762's r min, added to the tables), the threaded part a smooth
   cylinder at the minor radius d3/2 (ADR 0076 measured it best), a 30° run-out. The
   hex's flats are idealised as the bearing circle (a body of revolution); its
   corners carry no load. Optional nut (ISO 4032, ASME B18.2.2) and washer (ISO 7089,
   ASME B18.22) from their tables. Every face is named: `bearing`, `fillet`, `shank`,
   `thread`, `runout`, `head_top`; the nut's `bearing`, `thread`.
2. **Placed as a beam bolt is.** A bolt (`BeamFastener`) gets `formulation="solid"`
   with the new meaning, or the bolt table's Beam/Solid switch: one batch makes the
   bolt's geometry (a history: the revolved profile) and part on the hole stack's
   axis, its grade as its material, and its joints:
   - head (and nut, washer) to plate: frictional (μ from the bolt type, default 0.15)
     or bonded;
   - its thread into a tapped hole (or the nut's thread): a threaded contact
     (`Contact.thread`, ADR 0076) from the bolt type's thread form;
   - the shank in clearance holes: nothing (a gap);
   - the preload: the existing pretension section (ADR 0037) across the shank;
   - mesh controls: refined at the fillet (a fifth of r) and four nodes per pitch on
     the thread.
   Beam ↔ Solid is one command each way, the bolt's name, type and preload kept. The
   deck's solid bar stays as `formulation="bar"` for old projects.
3. **Meshed as a body of revolution.** The bolt (and nut, washer) mesh by Gmsh's
   revolve of its meshed profile: a set number of sectors (default 24), recombined
   into hexes with prisms on the axis, second order. Parts of revolution are found
   by their history (the revolved profile), so no recognition is needed. Elsewhere the
   model meshes as before; the bolt meets it only through contacts.
4. **Results the bolt table keeps.** A solid bolt's axial force, shear and bending
   come from a section across the shank (ADR 0042's section tool, at the mid-grip), so
   the bolt table and `bolt_results` read as for beam bolts. The thread's per-thread
   loads are the contact's `thread_load`. A solid bolt's thread-root fatigue notch is an
   input, `thread_kf`: on the bolt type (`BoltType.thread_kf`, default 3.0, any value
   of 1 or more; 3 to 4 is usual), overridable per bolt (`BeamFastener.thread_kf`), shown
   in the bolt table and the bolt type's form, and settable by MCP. The fatigue tool
   (ADR 0026) takes it as the notch factor Kf on the basic stresses at the first
   engaged thread's core.
5. **Solvers.** CalculiX first (hexes, contacts, threads, pretension all written);
   Code_Aster next (first-order meshes where friction is used, as its contacts need);
   FEniCSx only bonded heads (its contacts are bonded) with threads.
6. **The model's other paths.** MCP (`bolt_make_solid`, `bolt_make_beam`, with the
   tables' sizes), the bolts skill, the bolt generator's option, schematic nodes for
   the generated parts.

## Benchmarks

- Shank stress under preload = F / A_s (and F / A_ds in the plain shank), within 1 %.
- The under-head fillet's stress factor against published values (Peterson's shoulder
  fillet for r/d, D/d), mesh-converged.
- Solid and beam bolts in the bracket assembly (`examples/bracket_assembly`): bolt
  forces within 1 %, the thread's shares within 5 points.
- The revolved mesh against a tet mesh of the same bolt: stresses within 2 % away from
  the contacts.

## Consequences

- A bolted joint can be checked at its stress risers with the standard bolt's real
  shape, from the same tables and the same bolt tool, not a separate CAD model.
- Contacts make the joint nonlinear (frictional heads), so solves are slower than with
  beam bolts; beam bolts stay the default for many-bolt models.
- The tables need the ISO fillet radii added (ISO 885, ISO 4762).
- To watch: a hex head idealised as a body of revolution stiffens its corners'
  contribution slightly; thread-root peaks are a fatigue factor, not a stress.

## Alternatives considered

- **The deck's solid bar with spiders:** no head, fillet or run-out; spiders stiffen
  the ends. It stays for old projects only.
- **Meshing a true hex head and helical thread:** exact, but 10-20× the elements, and
  helical geometry is fragile in OCCT (ADR 0076).
- **Netgen:** no swept meshes; tets would lose the ring structure the thread pairs use.
- **The user's own CAD bolt:** works today with a threaded contact (ADR 0076), and stays
  the route for special bolts; generated bolts cover the standard ones without CAD.

## Progress (2026-11-10)

- The formulation is `modelled` (`solid` stays the deck's bar with spiders, so old
  projects keep their meaning); a type's `modelled` stays a beam until the bolt is drawn.
- Built: the `bolt` and `nut` history steps and `solid_bolts.dimensions` (an M16 hex
  bolt's bearing face is π(11.25² − 8.6²) mm² exactly), `make_commands` (head point
  on the source hole's outer mouth, the plate's face found under the head, grip and
  length rounded up to 5 mm, a through bolt's nut), `beam_commands`, results at the
  section (`bolts.section_loads`), the Kf input (bolt type, bolt table, fatigue tool at
  the thread's nodes, default 3), MCP, the bolt table and generator, the skill
  `modelled-bolts`.
- The revolved mesh (decision 3, `mesh/revolved.py`): the profile meshed in 2D and
  revolved in four quarter turns with layers (OCC can't revolve a whole turn and keep
  them), fragmented into one conformal mesh, serendipity second order: hex20, wedge15
  on the axis (CalculiX C3D20/C3D15, Code_Aster through MED). Its volume is the solid's
  within 0.5 %; pulled, it gives the same core stress as tetrahedra within 0.04 % and
  F / A within 1 % on CalculiX and Code_Aster (`tests/integration/test_revolved_bolts.py`).
- Measured:
  - Preload (`test_solid_bolts.py`, M16 8.8 into a tapped boss, 40 kN): the section
    carries 39.79 kN (−0.5 %). A node mean of σzz near the axis 3 mm from the cut read
    18 % low: the stress there isn't uniform across, so results read the section.
  - The under-head fillet (M16, ISO 885 minimum r = 0.6 mm, D/d = 1.41): peak principal
    stress over the shank's nominal 2.80 at r/3 elements (2.86 at r/6; a head four times
    as tall 2.94), against Tipton, Sorem and Rolovic's fit (Peterson 3rd ed. Eq. 3.5)
    2.82: −0.9 %. Peterson's Chart 3.4 gives 2.40; its own text says it can be 40 % low
    for sharp fillets, so the benchmark is Eq. 3.5.
  - The bracket assembly (two M8 8.8 through bolts with nuts, 5 kN, frictional seat
    μ 0.2, the base held at its ends so the nuts bear on a free face): modelled against
    beam bolts, the deflection 0.2405 against 0.2440 mm (−1.4 %), axial forces 4942 and
    4964 N against 5000 N (−1.2 %, −0.7 %; the beam's is the pretension itself). Shear
    differs by the ends: the beam's ends are tied to the head and nut annuli, so it
    carries 104 and 31 N as a dowel; the modelled bolt's heads only rub and the seat
    doesn't slip, so its shank carries 2-3 N. The beam's shear is the artefact. The
    modelled solve took 8089 s against 271 s (four frictional head and nut contacts on
    SPOOLES, Windows), and 2521 s against 161 s with PARDISO (ADR 0051, the same
    answers): model the bolts whose stresses matter.
- Washers (2026-11-10): `BoltType.washers` (none, head, nut, both; a bolt's own
  `washers` over it) and `washer` (the series: normal, small, large, extra large,
  structural; ISO 7089-7094, ASME B18.21.1, ASTM F436) draw plain washers from the
  tables (the `washer` history step, a ring meshed as a body of revolution). Each is
  bonded to its head or nut and in the frictional bearing contact with the plate: one
  frictional contact per bearing stack, as without a washer, since the head doesn't
  slip on its washer once tight. The bolt's grip and length grow by the thickness
  (M16 normal: 3.3 mm). Every bond a modelled bolt makes (head on washer, bonded heads)
  is a tie between separate meshes (`meshing` nonconformal): a conformal bond had the
  mesher fragment the bolt with its neighbour and mesh it as tetrahedra. Measured: the
  M16 joint with a washer under the head carries 39.81 kN at the section (−0.5 %), as
  without one (39.79).
- Code_Aster (2026-11-10): the same joint on a first-order mesh (Code_Aster's frictional
  contacts need one: the revolved bolt in hex8 and wedge6, the plates in tet4) carries
  40.31 kN at the section (+0.8 %), in 95 s with the meshing. On a second-order mesh it
  refuses the frictional heads with that reason; `bonded_heads` keeps second order. The
  revolved mesh's quadrilateral faces needed face integration over quad4, quad8 and
  quad9 (`common._quad_shape`, 3 × 3 Gauss; `tests/test_face_quadrature.py`), which
  contact results and remote loads use.

