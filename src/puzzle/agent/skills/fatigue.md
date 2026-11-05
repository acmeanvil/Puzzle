---
name: fatigue
description: fatigue life, damage and safety factor from stored stresses: material S-N curves and strain-life data, duties of cycles, mean-stress corrections, Marin factors, notch factor Kf
adr: 0026
---
Read this when the user asks how long a part lasts under repeated loading, for a
fatigue safety factor, or to add S-N or strain-life data to a material.

The fatigue tool is a derived result (kind `result`, `method` `fatigue`; skill
`derived-results`). It reads stresses from stored runs, so it needs no solve of its
own, and gives at every node:

- `fatigue_life`: repetitions of the duty to failure (cycles, for one block of one
  cycle); 1e12 where life is infinite;
- `fatigue_damage`: Miner's damage at the design life (≥ 1 fails);
- `fatigue_safety_factor`: the factor on every stress that makes the life the design
  life, between 1/15 and 15 (15 is the cap: "at least 15");
- `fatigue_alternating`, `fatigue_mean`: the stresses the life model saw (Pa, × Kf).

It is high-cycle and proportional loading only, and warns where the loading looks
non-proportional at the critical node.

## Steps

1. The material. `material_check(material)` shows what it has. The Goodman, Gerber and
   Smith-Dolan corrections, Shigley's line and Neuber need its `tensile_strength`;
   ASME-elliptic and Soderberg its `yield_strength`. Without them the tool fails with
   `incomplete_material`.
   Add one with `{"command": "edit", "object": "Steel", "ops": [{"op": "set",
   "path": "properties.tensile_strength", "value": {"value": 690, "unit": "MPa"}}]}`.
   - An S-N curve (points are cycles, then stress amplitude in Pa; cycles increasing):
     `{"command": "edit", "object": "Steel", "ops": [{"op": "append", "path":
     "fatigue.sn_curves", "value": {"name": "R = -1", "basis": "R = -1, polished",
     "points": [[1000, 500e6], [1000000, 250e6]], "source": "test report"}}]}`.
   - A strain-life model: `{"op": "set", "path": "fatigue.strain_life", "value":
     {"model": "coffin_manson_basquin", "fatigue_strength": 900e6,
     "strength_exponent": -0.09, "fatigue_ductility": 0.5, "ductility_exponent":
     -0.6}}`, or `{"model": "langer", "langer_a": 6.891, "langer_b": 1.920,
     "langer_c": 0.112}` (`langer_c` in percent strain).
2. The loading. Each block of the duty is one kind of cycle:
   - `fully_reversed`: `case` and its reverse (mean zero);
   - `zero_based`: `case` and nothing (amplitude = mean = half the stress);
   - `range`: between the two cases of a `range` combination (`combination`, its id);
     make that first (skill `derived-results`).
   A case is `{"study": "<study id>", "step": -1, "point": null}`. `scale` multiplies
   the block's stresses; `cycles` is how many of it per duty.
3. Create the tool (one `submit`):
   `{"command": "create", "kind": "result", "data": {"name": "Fatigue", "method":
   "fatigue", "fatigue": {"blocks": [{"loading": "fully_reversed", "case": {"study":
   "<study id>"}, "cycles": 1}], "stress": "signed_von_mises", "mean_stress":
   "goodman", "life_model": "shigley", "design_life": 1e6, "surface": "machined",
   "size": "rotating_round", "diameter": 0.02, "reliability": 99}}}`.
4. `derived_result(name="Fatigue")`: the runs read and the `warnings` (infinite life
   everywhere, Neuber's q and Kf, non-proportional loading, stresses past the S-N
   curve). Then `result_stats(study="Fatigue", field="fatigue_safety_factor")`: its
   `min` and `min_at` are the critical node. Likewise `fatigue_life`, `fatigue_damage`.

## Settings

- `stress`: `signed_von_mises` (von Mises signed by the largest principal stress, so
  compression doesn't count as tension), `max_principal`, or a component `xx`…`zx`.
- `mean_stress`: `goodman`, `gerber`, `asme_elliptic`, `soderberg`, `smith_dolan`,
  `none`.
- `life_model`: `shigley` (S-N line from Sut with the Marin factors), `sn_curve` (the
  material's curve named `sn_curve`, else its first), `strain_life` (needs a stored
  `strain` field; no mean-stress correction).
- Marin factors (Shigley only): `surface` polished, ground, machined, hot_rolled,
  forged; `size` none, rotating_round or non_rotating_round (`diameter`), rectangle
  (`height`, `width`), axial; `temperature` (°C);
  `reliability` (%, above 0 and below 100; default 50);
  `misc_factor`; `endurance_limit` a measured S'e in Pa (default 0.5 Sut, 700 MPa
  above Sut 1400 MPa; steels only).
- Notch factor, for a stress raiser the mesh doesn't resolve (threads, welds, a coarse
  fillet); leave it 1 where the mesh shows the notch: `notch_from` `direct` with
  `notch_factor` (Kf ≥ 1), or `neuber` with `notch_kt` and `notch_radius` (m),
  `notch_shear` for torsion. Kf goes on the alternating stress; on the mean too only
  with `notch_on_mean`.
- `design_life`: repetitions of the duty, for damage and the safety factor.

## Units

Stresses Pa, lengths m (`diameter` 0.02 is 20 mm), temperature °C here, reliability %,
life in cycles or repetitions of the duty. Material properties are quantities with a
unit (`{"value": 690, "unit": "MPa"}`); S-N and strain-life numbers are bare SI.

## Mistakes to avoid

- Points in MPa in an S-N curve: they are Pa.
- A notch factor where the mesh already resolves the notch: it counts Kt twice.
- A safety factor of 15 is "15 or more", not exactly 15.
- Reading life 1e12 as a number: it means infinite.
- A `range` block whose combination isn't a `range`: refused.
- Stresses per element only: the tool needs nodal stresses.
- Non-proportional or variable-amplitude histories: the tool doesn't rainflow-count;
  give each cycle kind as a block with its count, and say the result is an estimate.

In the GUI: Results → New fatigue tool…; S-N and strain-life data in the Material
builder's Fatigue tab.
