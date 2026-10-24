"""Bolt pretension (ADR 0037) against the joint diagram.

A steel bolt (R = 5 mm, L = 50 mm) in two conformal halves, the section between them;
fixed at the bottom, its top on an elastic support as stiff as the bolt (k_s = k_b =
EA/L), standing in for the clamped parts.
- Step 1: pretension F. The bolt carries F whatever the stiffnesses.
- Step 2: locked at that length, a force P pulls the top. Bolt and support share it by
  stiffness, so the bolt carries F + P k_b/(k_b + k_s) = F + P/2.
An adjustment d instead of a force: the bolt carries d k_b k_s/(k_b + k_s) = d k_b/2.
Axial stress is uniform away from the ends: within 0.5 %.
"""

import numpy as np
import pytest
from solvers import over

from puzzle.model.schema import (
    Component,
    Feature,
    Geometry,
    HistoryStep,
    MaterialAssignment,
    MeshRecipe,
    NamedSelection,
    PartInstance,
    Physics,
    Project,
    Quantity,
    SolveStep,
    Study,
)
from puzzle.project import save_project
from puzzle.solve import run_study

R, L = 0.005, 0.05
F, P = 10e3, 4e3  # N


def _bolt(solver: str, pretension: Feature) -> tuple[Project, Study, float]:
    p = Project(name="bolt")
    geo = p.add(
        Geometry(
            name="Bolt",
            history=[
                HistoryStep(kind="cylinder", name="Bolt", args={"r": R, "h": L}),
                HistoryStep(kind="split", name="Cut", args={"axis": "z", "at": L / 2}),
            ],
            selections=[
                NamedSelection(name="Body", entity="body", members=["Body"]),
                *(
                    NamedSelection(name=n, entity="face", rule={"plane": {"axis": "z", "at": z}})
                    for n, z in (("Bottom", 0.0), ("Section", L / 2), ("Top", L))
                ),
            ],
        )
    )  # fmt: skip
    from puzzle.materials import add_library_material

    steel = add_library_material(p, "structural_steel")
    E = steel.properties["youngs_modulus"].to_si()
    mesh = p.add(MeshRecipe(name="Tets", global_size=Quantity(value=2.5, unit="mm")))
    kn = E / L  # k_s = kn A = EA/L = k_b
    comp = p.add(
        Component(
            name="Bolt",
            parts=[PartInstance(name="B", geometry=geo.id)],
            mesh=mesh.id,
            materials=[MaterialAssignment(material=steel.id, selection="B/Body")],
            physics=[
                Physics(
                    type="solid_mechanics",
                    features=[
                        Feature(type="fixed", selection="B/Bottom"),
                        Feature(
                            type="elastic_support",
                            selection="B/Top",
                            values={"kn": Quantity(value=kn, unit="N/m^3")},
                        ),
                        pretension,
                        Feature(
                            type="force",
                            selection="B/Top",
                            values={"z": Quantity(value=P, unit="N")},
                            steps=[2],
                        ),
                    ],
                    settings={"temperature_dependent_materials": False},
                )
            ],
        )
    )
    study = p.add(
        Study(
            name="Tighten",
            component=comp.id,
            solver=solver,
            steps=[SolveStep(type="stationary"), SolveStep(type="stationary")],
        )
    )
    return p, study, E


def _tension(run, index: int) -> float:
    """The bolt's axial force in solve step `index`, from the stress away from the ends."""
    s = [st for st in run.results.steps if st.index == index and "stress" in st.fields][-1]
    s = s.fields["stress"]
    xyz = run.mesh.node_coords(s.ids)
    mid = (np.abs(xyz[:, 2] - L / 4) < 0.1 * L) | (np.abs(xyz[:, 2] - 3 * L / 4) < 0.1 * L)
    c = {k: i for i, k in enumerate(s.components)}
    return float(s.values[mid, c["zz"]].mean()) * np.pi * R**2


@over("calculix")
def test_pretension_force_then_locked(tmp_path, solver):
    pytest.importorskip("OCP")
    section = Feature(
        type="bolt_pretension", selection="B/Section", values={"F": Quantity(value=F, unit="N")}
    )
    p, study, _ = _bolt(solver, section)
    root = tmp_path / "bolt"
    save_project(p, root)
    run = run_study(p, study.id, root, tmp_path / "w")
    first, second = _tension(run, 1), _tension(run, 2)
    print(f"{solver} pretension: step 1 {first:.6g} N (F {F:g}), "
          f"step 2 {second:.6g} N (F + P/2 {F + P / 2:g})")  # fmt: skip
    assert first == pytest.approx(F, rel=5e-3)
    assert second == pytest.approx(F + P / 2, rel=5e-3)


@over("calculix")
def test_pretension_adjustment(tmp_path, solver):
    pytest.importorskip("OCP")
    d = 1e-5
    section = Feature(
        type="bolt_adjustment", selection="B/Section", values={"d": Quantity(value=d, unit="m")}
    )
    p, study, E = _bolt(solver, section)
    root = tmp_path / "bolt"
    save_project(p, root)
    run = run_study(p, study.id, root, tmp_path / "w")
    expected = d * E * np.pi * R**2 / L / 2
    first = _tension(run, 1)
    print(f"{solver} adjustment: {first:.6g} N, expected {expected:.6g} N")
    assert first == pytest.approx(expected, rel=5e-3)
