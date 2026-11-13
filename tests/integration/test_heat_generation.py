"""Heat generation (ADR 0047) on the 200 x 10 x 10 mm steel bar, both ends held at T0,
its sides insulated, so the temperature is one-dimensional:

- **uniform q:** T = T0 + q x (L − x) / (2k), the middle q L² / (8k) above the ends;
- **q from an imported field,** rising linearly q = q0 x / L:
  T = T0 + q0 x (L² − x²) / (6 k L), the largest at x = L / √3.

On every FE solver: CalculiX (*DFLUX BF per element), Code_Aster (SOURCE, a FORMULE
over the cells), Elmer (Heat Source = q / ρ) and FEniCSx (q v dx).
"""

import math

import numpy as np
import pytest
from conftest_geometry import BAR, bar_project, write_bar_step
from solvers import over

from puzzle.fields import new_field_data
from puzzle.materials import library_material
from puzzle.model import (
    Feature,
    FieldUse,
    ImportedField,
    Physics,
    Quantity,
    SolveStep,
    Study,
)
from puzzle.solve import run_study

pytest.importorskip("OCP")

L = BAR[0]
K = library_material("structural_steel").properties["thermal_conductivity"].to_si()
T0, Q = 300.0, 2e6  # K, W/m³
solvers = over("calculix", "code_aster", "elmer", "fenicsx")


def qty(value, unit):
    return Quantity(value=value, unit=unit)


def _bar(tmp_path, solver, source: Feature, field_path=None):
    ends = [Feature(type="temperature", selection=f"Bar/{s}", values={"T": qty(T0, "K")})
            for s in ("Root", "Tip")]  # fmt: skip
    physics = Physics(type="heat_transfer", features=[*ends, source],
                      settings={"temperature_dependent_materials": False})  # fmt: skip
    project, comp = bar_project(write_bar_step(tmp_path / "bar.step"), [physics], mesh_size_mm=5)
    if field_path is not None:
        field = project.add(ImportedField(**new_field_data(tmp_path, field_path, name="Losses")))
        source.fields["q"] = FieldUse(field=field.id, column="q")
    study = project.add(Study(name="heat", component=comp.id, solver=solver,
                              steps=[SolveStep(type="stationary")]))  # fmt: skip
    run = run_study(project, study.id, tmp_path, tmp_path / "run", timeout_s=900)
    t = run.results.last("temperature")
    return run.mesh.node_coords(t.ids)[:, 0], t.values[:, 0]


@solvers
def test_uniform_heat_generation(tmp_path, solver):
    source = Feature(type="heat_generation", selection="Bar/Body", values={"q": qty(Q, "W/m^3")})
    x, t = _bar(tmp_path, solver, source)
    exact = T0 + Q * x * (L - x) / (2 * K)
    rise = Q * L**2 / (8 * K)
    print(f"{solver}: middle {t.max() - T0:.5g} K above the ends (exact {rise:.5g} K)")
    assert np.abs(t - exact).max() < 0.005 * rise


@solvers
def test_heat_generation_from_a_field(tmp_path, solver):
    xs = np.linspace(-0.01, L + 0.01, 45)
    corners = [(y, z) for y in (-0.002, 0.012) for z in (-0.002, 0.012)]
    rows = ["x,y,z,q [W/m^3]"]
    rows += [f"{x:.10g},{y:.10g},{z:.10g},{Q * x / L:.10g}" for x in xs for y, z in corners]
    path = tmp_path / "losses.csv"
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    source = Feature(type="heat_generation", selection="Bar/Body", values={"q": qty(0, "W/m^3")})
    x, t = _bar(tmp_path, solver, source, path)
    exact = T0 + Q * x * (L**2 - x**2) / (6 * K * L)
    peak = Q * L**2 / (9 * math.sqrt(3) * K)  # at x = L / √3
    print(f"{solver}: peak {t.max() - T0:.5g} K (exact {peak:.5g} K)")
    assert np.abs(t - exact).max() < 0.005 * peak
