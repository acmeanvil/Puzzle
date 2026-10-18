# Laminar channel flow (plane Poiseuille)

Water (ρ = 998.2 kg/m³, μ = 1.002e-3 Pa·s) enters a channel 10 mm high and 100 mm long at a uniform 1 mm/s, so Re = 10 on the plate gap. The side faces are symmetry planes, which makes the flow two-dimensional, and it develops into plane Poiseuille flow within a few millimetres:

- u(y) = 6U·y(h − y)/h², so the centreline speed is **1.5 U**
- dp/dx = −12μU/h² = **−0.1202 Pa/m**

```sh
python benchmarks/channel_flow/build.py      # regenerate the project files
puzzle run benchmarks/channel_flow --study "Channel flow"
```

Code_Saturne 9.2, structured hexahedra (0.5 mm, 37,989 cells), 500 steady SIMPLE iterations, measured 2026-10-17:

| Quantity | Code_Saturne | Theory | Error |
| --- | --- | --- | --- |
| Peak velocity at x = 70 mm ÷ U | 1.4966 | 1.5 | −0.23% |
| Velocity profile at x = 70 mm (L2) | | parabola | 0.21% |
| dp/dx between x = 50 and 80 mm | −0.1191 Pa/m | −0.1202 Pa/m | −0.95% |

`tests/integration/test_channel_flow.py` runs the project and requires 1% on the peak velocity, 1% on the profile and 2% on the pressure gradient. Results are per cell; the test reads them at cell centres.
