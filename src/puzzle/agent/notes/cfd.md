---
link: https://www.code-saturne.org/documentation/
---

# Code_Saturne and OpenFOAM: what Puzzle has learned

Curated from Puzzle's own development notes: behaviour measured or decided while building the adapters. Read with the skills and ADRs.

## Code_Saturne specifics

an imposed wall flux is positive leaving the fluid (the adapter flips Puzzle's sign); turbulent intensity is in percent; the steady algorithm takes thousands of iterations for conduction-dominated scalars (use `pseudo_time_step`). RANS wall functions under-predict friction ~15% at Re_tau ~ 400; benchmark them at Re ≥ 1e5.

## OpenFOAM's `etc/bashrc`

reads the positional arguments it is sourced with (it can source files named there), so the image's entrypoint clears them first. Its pressure is kinematic (p/ρ); the adapter writes and reads Pa. `scalarTransport` is not under-relaxed and sits in `residualControl`, or simpleFoam stops when only the flow has converged.

## Code_Saturne's Gmsh reader

names groups by physical tag number, not name: zones select `"2"`, not `"Inlet"` (`write_med` returns the tags). Results are per cell (`location="element"`), matched to Puzzle elements by node-average centres (`match_points`, float32-tolerant).
