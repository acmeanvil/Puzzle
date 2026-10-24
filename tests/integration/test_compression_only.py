"""Compression-only support (ADR 0037) against a rigid footing on a Winkler foundation.

A steel block (B = 100 mm along x, D = 20 mm, 50 mm tall: rigid next to the foundation)
on a foundation of kn per area that pushes but never pulls, loaded down by P at e = B/4
from its middle (through a remote force on the top). Past e = B/6 the heel lifts off:
the contact length is c = 3 (B/2 − e) and the toe settles q_max/kn, q_max = 2P/(3 D (B/2 − e)).
A tiny tangential support (1e-6 kn) holds the block sideways. Both within 2 %.
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

B, D, H = 0.1, 0.02, 0.05
P, E_LOAD, KN = 2e4, 0.025, 1e11  # N, m, N/m³


def _footing(solver: str) -> tuple[Project, Study]:
    p = Project(name="footing")
    rules = (("Bottom", "z", 0.0), ("Top", "z", H), ("Ysym", "y", 0.0))
    geo = p.add(
        Geometry(
            name="Block",
            history=[HistoryStep(kind="box", name="F", args={"dx": B, "dy": D, "dz": H})],
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
    q = lambda v, u: Quantity(value=v, unit=u)  # noqa: E731
    comp = p.add(
        Component(
            name="Footing",
            parts=[PartInstance(name="F", geometry=geo.id)],
            mesh=mesh.id,
            materials=[MaterialAssignment(material=steel.id, selection="F/Body")],
            physics=[
                Physics(
                    type="solid_mechanics",
                    features=[
                        Feature(
                            type="compression_only_support",
                            selection="F/Bottom",
                            values={"kn": q(KN, "N/m^3")},
                        ),
                        Feature(
                            type="elastic_support",
                            selection="F/Bottom",
                            values={"kn": q(0, "N/m^3"), "kt": q(1e-6 * KN, "N/m^3")},
                        ),
                        Feature(type="displacement", selection="F/Ysym", values={"y": q(0, "m")}),
                        Feature(
                            type="remote_force",
                            selection="F/Top",
                            values={
                                "Fz": q(-P, "N"),
                                "px": q(B / 2 + E_LOAD, "m"),
                                "py": q(D / 2, "m"),
                                "pz": q(H, "m"),
                            },
                        ),  # fmt: skip
                    ],
                    settings={"temperature_dependent_materials": False},
                )
            ],
        )
    )
    study = p.add(
        Study(name="Rock", component=comp.id, solver=solver, steps=[SolveStep(type="stationary")])
    )
    return p, study


@over("calculix", "code_aster")
def test_footing_lifts_off(tmp_path, solver):
    pytest.importorskip("OCP")
    p, study = _footing(solver)
    root = tmp_path / "footing"
    save_project(p, root)
    run = run_study(p, study.id, root, tmp_path / "w")
    u = run.results.last("displacement")
    xyz = run.mesh.node_coords(u.ids)
    bottom = np.isclose(xyz[:, 2], 0.0, atol=1e-9)
    x, w = xyz[bottom, 0], u.values[bottom, 2]
    toe = x > B / 2  # surely in contact: the block is rigid, so w is linear in x
    slope, w0 = np.polyfit(x[toe], w[toe], 1)
    contact = B - (-w0 / slope)
    settle = -(slope * B + w0)
    half = B / 2 - E_LOAD
    expected_c, expected_w = 3 * half, 2 * P / (3 * D * half) / KN
    print(f"{solver} footing: contact {contact * 1e3:.4g} mm (theory {expected_c * 1e3:.4g}), "
          f"toe {settle:.5g} m (theory {expected_w:.5g})")  # fmt: skip
    assert contact == pytest.approx(expected_c, rel=2e-2)
    assert settle == pytest.approx(expected_w, rel=2e-2)
