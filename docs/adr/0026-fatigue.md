# 0026. Fatigue: data in the material builder, a life tool in results

- Status: Accepted
- Date: 2026-10-22

## Context

The maintainer's Tension-meter app has fatigue data and methods that Puzzle should match (reviewed 2026-10-22):

- **Material data:** measured S-N curves (stress amplitude against cycles, with basis and source), and strain-life models: Coffin-Manson-Basquin (σf′, b, εf′, c) and Langer (NUREG/CR-6909: ln N = A − B ln(εa% − C)).
- **The stress-life method of Shigley (ch. 6):**
  - the endurance limit with its Marin factors (surface, size, load, temperature, reliability, miscellaneous) and notch sensitivity;
  - the S-N line from 10³ to 10⁶ cycles;
  - mean-stress corrections: modified Goodman, Gerber, ASME-elliptic, Soderberg, Smith-Dolan, none;
  - Miner's rule over a duty cycle.

The maintainer chose "data and a life tool" for this round. The life tool is the analogue of the Ansys Fatigue Tool: maps of life, damage and safety factor over the model.

## Decision

1. **Fatigue data with the material** (`Material.fatigue`, edited in the builder's Fatigue tab):
   - **Data:**
     - S-N curves: each with a name, basis (R ratio or mean stress, test conditions), points (cycles, stress amplitude), source and reference;
     - a strain-life model, either Coffin-Manson-Basquin or Langer.
   - **Strengths:** `tensile_strength` and `yield_strength` join the property registry (Structural), since the mean-stress corrections and Shigley's estimates need them.
   - **Plots:** curves are drawn log-log in the builder.

   The code is ported from Tension-meter, so both apps compute the same lives (tests reuse its cases).
2. **A fatigue tool among the derived results** (ADR 0025), kind `fatigue`:
   - **Loading:** the cycle comes from a combination's range and mean (two cases, or a load and its reverse, or zero-based: one case and zero). A scale factor applies, and a duty cycle of several cycle types with counts uses Miner's rule.
   - **Stress measure:** von Mises (signed by the largest principal stress, so compression doesn't count as tension), maximum principal, or a component.
   - **Mean-stress correction:** one of Tension-meter's.
   - **Life model:**
     - the material's S-N curve (log-log interpolation);
     - Shigley's S-N line from Sut, with the Marin factors as tool settings (the part's surface finish and size, the reliability);
     - or, for strain life, the strain-life model with the strain range.
   - **Outputs:** life (cycles, "infinite" above the endurance limit), damage at a design life, and safety factor. Each is a field on the mesh, shown like any result with its worst location.
3. **Honesty about scope.** The tool is high-cycle, proportional loading. Non-proportional multiaxial fatigue (critical plane) and rainflow counting of load histories are out of scope for now, and the tool says so where it would matter, e.g. when the principal directions differ between the two cases.

## Consequences

- Fatigue becomes part of a study's results without a separate application, and is consistent with Tension-meter's hand calculations.
- The material builder's completeness gains a Fatigue row (which life models a material supports).
- The tool's correctness rests on combinations (ADR 0025) and on the ported formulas, which are checked against Shigley's worked examples, as Tension-meter's tests are.

## Alternatives considered

- Fatigue as a separate application exporting results: duplicates the result machinery and loses the maps.
- Critical-plane multiaxial methods now: more general, but heavier, and not what was asked first.
