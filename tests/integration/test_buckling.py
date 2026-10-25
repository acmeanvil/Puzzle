"""Linear buckling (ADR 0028) against Euler, with and without a preload.

A 10 × 10 mm steel column 300 mm tall, fixed at its base and pushed down at its top by
1 N: the lowest load factor is the Euler load of a fixed-free column,
    P_cr = π² E I / (2 L)²,   I = b⁴/12,
within 0.5 % (quadratic tets; the column is slender, L/r = 104). Its two lowest modes are
equal (a square section). With a static preload P0 held fixed in a step before, the
factor on the 1 N load is P_cr − P0. CalculiX and Code_Aster agree to 0.1 %.
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

L, B = 0.3, 0.01
P0 = 2400.0  # N, about half the critical load


def _column(solver: str, steps, loads) -> tuple[Project, Study, float]:
    p = Project(name="column")
    geo = p.add(
        Geometry(
            name="Column",
            history=[HistoryStep(kind="box", name="C", args={"dx": B, "dy": B, "dz": L})],
            selections=[
                NamedSelection(name="Body", entity="body", members=["body-1"]),
                *(
                    NamedSelection(name=n, entity="face", rule={"plane": {"axis": "z", "at": z}})
                    for n, z in (("Base", 0.0), ("Top", L))
                ),
            ],
        )
    )  # fmt: skip
    from puzzle.materials import add_library_material

    steel = add_library_material(p, "structural_steel")
    mesh = p.add(MeshRecipe(name="Tets", global_size=Quantity(value=2.5, unit="mm")))
    comp = p.add(
        Component(
            name="Column",
            parts=[PartInstance(name="K", geometry=geo.id)],
            mesh=mesh.id,
            materials=[MaterialAssignment(material=steel.id, selection="K/Body")],
            physics=[
                Physics(
                    type="solid_mechanics",
                    features=[Feature(type="fixed", selection="K/Base"), *loads],
                    settings={"temperature_dependent_materials": False},
                )
            ],
        )
    )
    study = p.add(Study(name="Buckle", component=comp.id, solver=solver, steps=steps))
    E = steel.properties["youngs_modulus"].to_si()
    return p, study, np.pi**2 * E * B**4 / 12 / (2 * L) ** 2


def _push(newtons: float, steps=()) -> Feature:
    return Feature(
        type="force",
        selection="K/Top",
        values={"z": Quantity(value=-newtons, unit="N")},
        steps=list(steps),
    )


def _factors(run, index: int) -> list[float]:
    modes = [s for s in run.results.steps if s.index == index and s.kind == "buckling"]
    assert [s.mode for s in modes] == list(range(1, len(modes) + 1))
    assert all("displacement" in s.fields for s in modes)
    return [s.value for s in modes]


def _run(tmp_path, p, study):
    root = tmp_path / "column"
    save_project(p, root)
    return run_study(p, study.id, root, tmp_path / "w")


@over("calculix", "code_aster")
def test_euler_column(tmp_path, solver):
    pytest.importorskip("OCP")
    p, study, critical = _column(
        solver, [SolveStep(type="buckling", settings={"modes": 4})], [_push(1.0)]
    )
    factors = _factors(_run(tmp_path, p, study), 1)
    print(f"{solver} Euler column: {factors[0]:.6g} N, theory {critical:.6g} N, "
          f"{100 * (factors[0] / critical - 1):+.3f} %")  # fmt: skip
    assert factors[0] == pytest.approx(critical, rel=5e-3)
    assert factors[1] == pytest.approx(factors[0], rel=1e-4)  # the square's twin mode
    assert factors[2] > 5 * factors[0]  # the next pair: (3π/2)² / (π/2)² = 9 for a beam


@over("calculix", "code_aster")
def test_preloaded_column(tmp_path, solver):
    pytest.importorskip("OCP")
    steps = [
        SolveStep(type="stationary"),
        SolveStep(type="buckling", settings={"modes": 2, "preload": True}),
    ]
    p, study, critical = _column(solver, steps, [_push(P0, [1]), _push(1.0, [2])])
    run = _run(tmp_path, p, study)
    factor = _factors(run, 2)[0]
    off = 100 * (factor / (critical - P0) - 1)
    print(f"{solver} preloaded column: {factor:.6g} N on top of {P0:g} N, "
          f"theory {critical - P0:.6g} N, {off:+.3f} %")  # fmt: skip
    assert factor == pytest.approx(critical - P0, rel=1e-2)
    assert [s.kind for s in run.results.steps if s.index == 1] == ["stationary"]


def test_solvers_agree(tmp_path):
    pytest.importorskip("OCP")
    from solvers import available

    if not all(available(s) for s in ("calculix", "code_aster")):
        pytest.skip("needs CalculiX and Code_Aster")
    got = {}
    for solver in ("calculix", "code_aster"):
        p, study, _ = _column(
            solver, [SolveStep(type="buckling", settings={"modes": 4})], [_push(1.0)]
        )
        got[solver] = _factors(_run(tmp_path / solver, p, study), 1)
    a, b = np.array(got["calculix"]), np.array(got["code_aster"])
    print(f"buckling shoot-out: CalculiX {a}, Code_Aster {b}")
    assert np.allclose(a, b, rtol=1e-3)
