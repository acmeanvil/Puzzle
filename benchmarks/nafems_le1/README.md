# NAFEMS LE1: elliptic membrane

A quarter elliptic annulus (inner semi-axes 2.0 × 1.0 m, outer 3.25 × 2.75 m, 0.1 m thick), E = 210 GPa, ν = 0.3, under 10 MPa outward pressure on the outer edge, with symmetry on x = 0 and y = 0. Target: **σyy = 92.7 MPa at point D (2, 0)**.

Puzzle models it as a 3D solid of second-order tetrahedra with u_z = 0 on z = 0, and reads σyy at D as the mean over the nodes on the line x = 2, y = 0 through the thickness.

```sh
python benchmarks/nafems_le1/build.py          # regenerate the project files
puzzle run benchmarks/nafems_le1 --study LE1
puzzle show benchmarks/nafems_le1 --study LE1 --field stress --scalar stress
```

CalculiX 2.23, measured 2026-10-17:

| Mesh (global / inner edge) | Nodes | σyy at D | Error |
| --- | --- | --- | --- |
| 200 / 50 mm | 6,843 | 92.46 MPa | −0.26% |
| 100 / 20 mm (project default) | 39,604 | 92.70 MPa | 0.00% |
| 100 / 10 mm | 107,102 | 92.71 MPa | +0.01% |
| 50 / 10 mm | 226,807 | 92.72 MPa | +0.02% |

`tests/integration/test_nafems_le1.py` runs the project default and requires agreement within 1%.
