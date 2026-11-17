# 0091. Keep future work in one list

- Status: Proposed
- Date: 2026-11-17

## Context

ADR 0090's review and its clean-up packages keep turning up work that is worth doing but is not part of the clean-up. ADR 0072 lists what earlier ADRs left unbuilt; this record is for work asked for since, so the clean-up can finish without growing. The maintainer opened it on 2026-11-17, while the ADR freeze still stands for everything else.

## Decision

Future work is listed here, each item with who asked, why, and what "done" means. An item becomes its own ADR (or an amendment to its source ADR) when it is taken up. Nothing here is built until the review's work is complete and pushed to `main`.

### 1. Preloaded bolts under temperature: the harder cases

Asked for by the maintainer (2026-11-17): temperature loading on preloaded bolts matters in several industries, fusion among them. The first step is built with ADR 0090's clean-up (uniform heating and cooling of a bolt and sleeve of different expansion, a gradient, preload then heat then load, on CalculiX and Code_Aster, beam and modelled bolts, against closed forms). Held for here:

- **Cryogenic ranges:** cool-down from room temperature to 77 K and 4 K, with expansion given as a table of mean coefficients or integrated thermal strain (materials contract nonlinearly there), checked against a hand calculation from the integrated strains.
- **Temperature-dependent modulus and expansion in the bolt and the clamped parts:** the preload change from both the differential strain and the stiffness change, checked against the two-spring joint with E(T).
- **Thermal transients feeding the structural steps:** a transient heat study transferred per time into a multi-step structural study that holds the preload locked, so the bolt force is followed through a heat-up or a cool-down (the bolt lagging the flange); checked against a lumped two-body model.
- **Loss of clamp and regain:** a joint that opens during a transient and closes again, with the contact state and bolt force followed.
- **Creep and relaxation of preload at temperature**, when a creep material model exists.
- **Threaded contacts, tapped thread ends and line welds under temperature**, refused today (`common.check_thermal_helpers`).
- **Left by the first step (built 2026-11-17, ADR 0090 package 6):** a beam bolt hotter or colder than the faces it bears on (it takes their temperature; only modelled bolts cover it); rigid ends and the solid formulation under temperature (refused: +13.6 % and −7.4 % measured); `through` stacks' own benchmark; Code_Aster's frictionless modelled joint, which fails ("matrice non inversible") even unheated; Code_Aster's separating-load share 2.2 % off through its RBE3 weights.

### 2. Unknown value keys on a feature are refused

Found while building item 1's first step: gravity given `z` instead of `gz` was dropped without a word. Done when a feature's unknown value key is a coded error at the command, with the catalog's names offered.
- **Reference data:** cryogenic properties (integrated contraction, E, yield) for the common bolt and structure alloys in the generic library, each with its source.

Done when each case has a benchmark with a stated tolerance on every solver that claims it, a guide section, and the agent's skill.

## Consequences

One place to look for what was asked and deferred. The list must be pruned as items are taken up, or it becomes a second ADR 0072.

## Alternatives considered

- **Add these to ADR 0072's list:** that list is what earlier ADRs promised and did not build; these are new requests.
- **One ADR per request now:** the freeze exists so the code settles first.
