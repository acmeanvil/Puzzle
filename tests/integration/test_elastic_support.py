"""Elastic support (ADR 0037): a 20 × 20 × 10 mm steel block on a foundation of stiffness
kn per area, held on two side faces by symmetry, pressed by p on its top. The stress is
uniaxial and uniform, so the top moves down exactly p·h/E + p/kn.
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

H, P, KN = 0.01, 10e6, 1e13  # m, Pa, N/m³


def _block(solver: str) -> tuple[Project, Study]:
    p = Project(name="block")
    rules = (("Bottom", "z", 0.0), ("Top", "z", H), ("Xsym", "x", 0.0), ("Ysym", "y", 0.0))
    geo = p.add(
        Geometry(
            name="Block",
            history=[HistoryStep(kind="box", name="B", args={"dx": 0.02, "dy": 0.02, "dz": H})],
            selections=[
                NamedSelection(name="Body", entity="body", members=["body-1"]),
                *(
                    NamedSelection(name=n, entity="face", rule={"plane": {"axis": a, "at": at}})
                    for n, a, at in rules
                ),
            ],
        )
    )
    from puzzle.materials import add_library_material

    steel = add_library_material(p, "structural_steel")
    mesh = p.add(MeshRecipe(name="Tets", global_size=Quantity(value=4, unit="mm")))
    zero = Quantity(value=0, unit="m")
    comp = p.add(
        Component(
            name="Block",
            parts=[PartInstance(name="K", geometry=geo.id)],
            mesh=mesh.id,
            materials=[MaterialAssignment(material=steel.id, selection="K/Body")],
            physics=[
                Physics(
                    type="solid_mechanics",
                    features=[
                        Feature(
                            type="elastic_support",
                            selection="K/Bottom",
                            values={"kn": Quantity(value=KN, unit="N/m^3")},
                        ),
                        Feature(type="displacement", selection="K/Xsym", values={"x": zero}),
                        Feature(type="displacement", selection="K/Ysym", values={"y": zero}),
                        Feature(
                            type="pressure",
                            selection="K/Top",
                            values={"p": Quantity(value=P, unit="Pa")},
                        ),
                    ],
                )
            ],
        )
    )
    study = p.add(
        Study(name="Press", component=comp.id, solver=solver, steps=[SolveStep(type="stationary")])
    )
    return p, study


@over("calculix", "elmer", "fenicsx")
def test_block_on_a_foundation(tmp_path, solver):
    pytest.importorskip("OCP")
    p, study = _block(solver)
    root = tmp_path / "block"
    save_project(p, root)
    run = run_study(p, study.id, root, tmp_path / "w")
    steel = next(o for o in p.objects.values() if o.kind == "material")
    E = steel.properties["youngs_modulus"].to_si()
    expected = -(P * H / E + P / KN)
    u = run.results.last("displacement")
    xyz = run.mesh.node_coords(u.ids)
    top = np.isclose(xyz[:, 2], H, atol=1e-9)
    got = float(u.values[top, 2].mean())
    print(f"{solver} elastic support: {got:.6g} m, theory {expected:.6g}, "
          f"{100 * (got / expected - 1):+.3f} %")  # fmt: skip
    assert got == pytest.approx(expected, rel=2e-3)
