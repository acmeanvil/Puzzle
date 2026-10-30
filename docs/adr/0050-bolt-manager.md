# 0050. Bolts: types, a generator, and a bolt table with results along each bolt

- Status: Accepted (2026-10-30, with the maintainer's answers)
- Date: 2026-10-30

## Context

Fasteners (ADR 0027) are now beam bolts by default: light, exact for a Timoshenko beam, on CalculiX and Code_Aster. A model is made of them one at a time ("Add fastener…": two face selections, a diameter, a material, the ends), or from coaxial holes ("Fasteners from holes…", `geometry/holes.py`). Real assemblies have tens or hundreds of bolts of a few kinds, and the questions about them are tabular: which bolt carries most, how close each is to its proof load, how the bending runs along it.

The maintainer asked for:
- a way to specify each bolt's type and what it attaches to;
- an **automatic bolt generator**, its anchors given by direct picks, selection sets, or a geometry rule (sorting the model's holes), including through the agent;
- a **tabbed bolt table**: one tab to see and edit every bolt's properties, one with each bolt's loads and moments along its length.

Nastran (CBAR/CBEAM with RBE2/RBE3 spiders, a bolt preload), Ansys (bolt beams, bolt pretension, the Bolt Tools and the bolt table in Workbench) and Algor's beam bolts are the reference points.

## Decision

1. **Bolt types.** A project object, **Bolt type** (kind `bolt_type`), is a bolt's specification once, shared by every bolt of that kind:
   - **size:** nominal diameter, and the tensile stress area (from the standard, or computed from the pitch);
   - **grade:** a material (E, ν, and the proof and yield strengths the checks use), from the library or given;
   - **preload:** none, a force, a percentage of the yield load (σy As; **70 % by default**), or a torque with a nut factor K (F = T / (K d));
   - **formulation:** beam (default) or solid (ADR 0027);
   - **ends:** rigid or distributing;
   - **joint style**, which sets what each end attaches to, mixed as the joint needs:
     - **through bolt with a nut:** the annulus under the head to the annulus under the nut;
     - **cap screw in a tapped hole:** the annulus under the head to the tapped hole's wall (over the thread's engaged length);
     - **stud with two nuts:** annulus to annulus;
     - **stud in a tapped hole with a nut:** the tapped hole's wall to the nut's annulus;
     - **hole wall to hole wall** (as now), for a quick model.

     An annulus is the face under a head or nut out to its bearing diameter, split from the plate's face by the generator (the face split of the Geometry tab) when it isn't a face of its own; the spider takes that face's nodes.

   A built-in list covers the published sizes: ISO metric coarse and fine (M3–M36) and UNC and UNF (#4–1½"), with stress areas from the pitch (As = π/4 (d − 0.9382 p)²), head and nut bearing diameters. Grades are materials: ISO 898-1 classes 4.6, 8.8, 10.9 and 12.9, SAE J429 grades 2, 5 and 8, and the ASTM bolt and stud specifications (A193 B7, B16, B8, B8M; A320 L7; A307; A449; A354 BD; F3125 A325 and A490), shipped in the generic materials with proof, yield and tensile strengths (built). Users add their own types. A bolt names its type (`BeamFastener.type`); a value set on the bolt itself overrides the type's.
2. **What a bolt attaches to:** its two ends, each a face selection (a hole wall or an annulus), as now. It may also have more than two plates (a bolt through a stack): the ends are its first and last faces, and the plates between are joined by the same beam through intermediate nodes.
3. **The bolt generator** (Bolts → Generate bolts…, a panel with the view live) proposes bolts, previewed in the view and listed with a tick each. Generate makes them, with their face selections, in one command (one undo). Anchors come from any of:
   - **picks:** hole faces picked in the view, paired by axis;
   - **selection sets:** each face of one set paired with the coaxial face of another (a flange's holes to its mate's);
   - **a rule:** the model's cylindrical faces filtered and paired: a diameter range, parts or part pairs, an axis direction, coaxial within a tolerance, a minimum and maximum length, through holes only. The rule is stored with the bolts it made, so it can be run again after the geometry changes and says what it would add or remove;
   - **the agent:** "put M10s through every 10.5 mm hole between the flange and the housing" becomes a rule (MCP `bolts_generate`, made as a proposal), shown in the same preview before anything is made.

   Each proposed bolt gets a type (chosen, or matched by hole diameter), a name by a pattern ("Flange bolt 1…n"), and a group in the tree (ADR tree groups).
4. **The bolt table** (Window → Bolts, a docked panel with two tabs):
   - **Properties:** one row per bolt: name, type, the two ends, diameter, length (computed), preload, formulation, end style, group. Cells edit in place (each edit a command; several rows at once for a column). Rows are filtered and sorted, a bolt selected in the table is selected in the tree and drawn in the view, and the table exports to CSV.
   - **Results:** for a run and step chosen in the tab:
     - each bolt's axial force, shear, torsion and bending moment at both ends, and the largest bending moment;
     - its utilisation against its grade's proof and yield loads: σ = F/As + M/Z, with Z = πd³/32 on the stress area's diameter, and against yield. The worst is sorted first and marked;
     - a selected bolt's **loads and moments along its length** as a plot: N, V and T constant along a beam bolt and M linear between its two ends (from both solvers' end forces), or per section for a solid bolt;
     - the table and plot export to CSV.
5. **Checks:**
   - the generator's rules on a flange with holes of three sizes (counts and pairings);
   - the table's utilisation against a hand calculation;
   - the along-length moments against a cantilevered bolt's M(x) = P (L − x);
   - a preload from a torque against F = T/(K d);
   - editing in the table as commands (undo).

**Order of work:**
1. bolt types and the built-in standard list;
2. the bolt table's Properties tab;
3. the Results tab, with utilisation and the along-length plot;
4. the generator: picks and selection sets, then rules, then the agent;
5. joint styles with head and nut annuli and tapped holes, and bolts through stacks.

## Consequences

- Bolts become a managed set: specified once by type, placed in bulk, checked in one table, and regenerated when the geometry changes.
- `BeamFastener` gains a type reference and overrides; the adapters read the resolved values, so solvers see no change.
- The built-in standards data is published standard values (stress areas, proof loads), shipped with Puzzle like the generic materials.

## The maintainer's answers (2026-10-30)

1. **Standards:** ISO metric and UNC, both coarse and fine pitch. The ASTM bolt and stud material specifications go into the shipped (generic) materials.
2. **Preload:** offered as a force, a percentage of yield, or a torque; the default is **70 % of yield**.
3. **Attachment:** both, mixed and matched by the bolting type: a bolt and nut is annulus to annulus, a bolt in a tapped hole is annulus to hole wall, and so on.
4. **The table:** a docked window.

## Built

- **Bolting materials** (2026-10-30): 17 entries in `materials/generic.toml` under the group Bolting Steel (ASTM A193 B7, B16, B8 class 1, B8M class 2; A320 L7; A307 A; A449; A354 BD; F3125 A325, A490; SAE J429 grades 2, 5, 8; ISO 898-1 4.6, 8.8, 10.9, 12.9), with the standards' minimum proof (`proof_strength`, a new property), yield and tensile strengths for the size range named in each description.
