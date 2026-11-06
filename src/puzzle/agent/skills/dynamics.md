---
name: dynamics
description: structural dynamics and buckling: transient response to shocks or ramps, harmonic response over frequencies, Rayleigh or modal damping, linear buckling with or without a preload
adr: 0041
---
Read this before adding a transient or harmonic step to a structural study, adding
damping, or setting up a buckling (stability) check.

There are no special step types. A `transient` step that solves solid mechanics is
structural dynamics (Newmark, average acceleration). A `frequency_domain` step is a
harmonic response: one result step per frequency. A `buckling` step (ADR 0028) is a
linear eigenvalue solve: one result step per mode, its value the load factor.

## Steps

1. `outline` with `ids: true`: find the component (its id), its supports and loads, and
   whether the materials have a density (dynamics needs one).
2. A new study (one `submit`):
   `{"command": "create", "kind": "study", "data": {"name": "Shock", "component":
   "<component id>", "solver": "calculix", "steps": [{"type": "transient", "physics":
   ["solid_mechanics"], "settings": {"time_step": 1e-4, "end_time": 0.01,
   "method": "direct", "rayleigh_alpha": 0.0, "rayleigh_beta": 0.0}}]}}`.
   Or add a step to a study: `{"command": "edit", "object": "<study>", "ops": [{"op":
   "append", "path": "steps", "value": {...}}]}`.
3. **Transient** settings: `time_step` and `end_time` (s); `method` direct or modal
   (modal adds `modes`, default 20); `hht_alpha` from −1/3 to 0 (0 = no numerical
   damping). Use about 20 or more steps per period of the highest mode that matters.
4. **Harmonic** step: `{"type": "frequency_domain", "physics": ["solid_mechanics"],
   "settings": {"start_frequency": 10.0, "end_frequency": 1000.0, "points": 50,
   "spacing": "linear", "method": "direct"}}` (Hz; `spacing` linear or log), or a list:
   `"frequencies": "50, 100, 200"` (text, Hz).
5. **Damping**: `rayleigh_alpha` (mass part, 1/s) and `rayleigh_beta` (stiffness part,
   s) in the step settings, C = αM + βK. All dynamic steps of one study must use the
   same pair. `damping_ratio` (0.05 = 5 %, every mode) works with the modal method only.
6. **Loads in time**: without an amplitude a load acts at full value from t = 0 (a
   sudden load). To ramp it, create an amplitude: `{"command": "create", "kind":
   "amplitude", "data": {"id": "<new uuid>", "name": "Ramp", "points": [[0, 0],
   [0.005, 1]]}}` (time s, factor), then set the feature's `amplitude` to that id. Only
   `force`, `pressure` (and `heat_flux`) follow amplitudes. Giving your own `id` lets
   one batch create the amplitude and use it.
7. **Buckling** step: `{"type": "buckling", "physics": ["solid_mechanics"],
   "settings": {"modes": 4, "preload": false}}`. The step's loads are what the factor
   multiplies. With a preload (self weight, a bolt), put a `stationary` step first,
   set `"preload": true` on the buckling step, and give each load its steps with the
   feature's `steps`: the dead load `[1]` (held into step 2), the load to scale `[2]`.
   A support with no `steps` acts in both. `shift` (search
   near a factor) works on Code_Aster only.
8. `check_study`, then run:
   `submit(command={"command": "run_study", "study": "<name>"})`.
9. Results:
   - `result_summary`: every result step with its fields' ranges: `time_s` for a
     transient; for buckling a `mode` and its load factor (shown under `frequency_hz`:
     there it is the factor, not Hz). Harmonic steps come in frequency order without it.
   - `result_history(study, field, selection, reduce)`: a value at every time or
     frequency (`value`: the time in s or the frequency in Hz). Field
     `"displacement_amplitude"` with `"mean"` on the tip face is the frequency
     response; `"displacement"` gives a transient history.
   - Harmonic fields: `displacement_amplitude` (m, per component),
     `displacement_phase` (degrees) and `displacement` drawn at the instant of the
     largest peak.
   - `render(study, field, mode=k)` draws buckling mode k.

## Which solver

| | CalculiX | Code_Aster | Elmer | FEniCSx |
| --- | --- | --- | --- | --- |
| Transient direct | yes | yes | yes | yes (3D only) |
| Transient modal | yes | refused | refused | refused |
| `hht_alpha` | yes | yes | refused | yes |
| Harmonic | modal always (`modes`) | direct | direct | direct (3D only) |
| `damping_ratio` | modal and harmonic | refused | refused | refused |
| Buckling | yes (no `shift`) | yes | refused | refused |

## Mistakes to avoid

- No density in a material: transient, harmonic and modal steps are refused
  (`incomplete_material`).
- A `damping_ratio` with `method: direct` is refused; use Rayleigh, or the modal method
  on CalculiX.
- Two dynamic steps with different Rayleigh pairs in one study are refused.
- A load factor below 1 means it buckles before the applied load; negative means the
  load reversed. Linear buckling overestimates thin shells: say so.
- `result_stats` and `result_value_at` take `step` as the solve step number and read
  its last time, frequency or mode. Use `result_history` or `result_summary` for the
  others.
- Elmer starts from zero acceleration, so after a sudden load its early history lags
  the others; a smaller `time_step` closes the gap.
- A static or transient structural study needs a support (`fixed`, `displacement`,
  `remote_displacement`, an elastic support) or it is refused as `unconstrained`. Give
  a harmonic study supports too: a free body has a zero-frequency response.
