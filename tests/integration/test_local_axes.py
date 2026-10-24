"""Local coordinate systems (ADR 0037) against closed forms.

Disc: a thin steel disc (R = 50 mm, 2 mm thick) whose rim is moved in cylindrical axes
by u_r = δ, u_θ = δ', u_z = 0. In plane stress the answer is a uniform expansion plus a
rigid turn, u = (δ e_r + δ' e_θ) r/R, with σ_r = σ_θ = E δ / (R (1 − ν)). Holding the rim
in z adds a boundary layer about one thickness wide, so the check is made inside
r < R/2: displacements within 0.5 %, stress within 2 %.

Cantilever (CalculiX, the only solver taking some components in local axes): the tip
is moved δ along an x axis turned 30° about z, the other two local components free.
The tip's displacement projected on that axis must be δ exactly.
"""

import shutil
from pathlib import Path

import numpy as np
import pytest
from solvers import over

from puzzle.model.schema import (
    Component,
    CoordinateSystem,
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
from puzzle.project import load_project, save_project
from puzzle.solve import run_study

EXAMPLE = Path(__file__).parents[2] / "examples" / "cantilever"
R, H = 0.05, 0.002
DELTA, TURN = 1e-5, 2e-5  # m: radial and tangential rim displacement


def _disc(solver: str) -> tuple[Project, Study]:
    p = Project(name="disc")
    geo = p.add(
        Geometry(
            name="Disc",
            history=[HistoryStep(kind="cylinder", name="Disc", args={"r": R, "h": H})],
            selections=[
                NamedSelection(name="Body", entity="body", members=["body-1"]),
                NamedSelection(name="Rim", entity="face", rule={"radius": R}),
            ],
        )
    )
    from puzzle.materials import add_library_material

    steel = add_library_material(p, "structural_steel")
    axes = p.add(CoordinateSystem(name="Polar", type="cylindrical"))
    mesh = p.add(MeshRecipe(name="Tets", global_size=Quantity(value=2.5, unit="mm")))
    comp = p.add(
        Component(
            name="Disc",
            parts=[PartInstance(name="D", geometry=geo.id)],
            mesh=mesh.id,
            materials=[MaterialAssignment(material=steel.id, selection="D/Body")],
            physics=[
                Physics(
                    type="solid_mechanics",
                    features=[
                        Feature(
                            type="displacement",
                            selection="D/Rim",
                            values={
                                "x": Quantity(value=DELTA, unit="m"),
                                "y": Quantity(value=TURN, unit="m"),
                                "z": Quantity(value=0, unit="m"),
                            },
                            axes=axes.id,
                        )  # fmt: skip
                    ],
                    settings={"temperature_dependent_materials": False},
                )
            ],
        )
    )
    study = p.add(
        Study(name="Expand", component=comp.id, solver=solver, steps=[SolveStep(type="stationary")])
    )
    return p, study


@over("calculix", "code_aster", "elmer", "fenicsx")
def test_cylindrical_rim_displacement(tmp_path, solver):
    pytest.importorskip("OCP")
    p, study = _disc(solver)
    root = tmp_path / "disc"
    save_project(p, root)
    run = run_study(p, study.id, root, tmp_path / "w")

    u = run.results.last("displacement")
    xyz = run.mesh.node_coords(u.ids)
    r = np.hypot(xyz[:, 0], xyz[:, 1])
    inner = (r > 0.1 * R) & (r < 0.5 * R)
    er = xyz[inner, :2] / r[inner, None]
    et = np.column_stack([-er[:, 1], er[:, 0]])
    ur = np.einsum("ij,ij->i", u.values[inner, :2], er) / r[inner]
    ut = np.einsum("ij,ij->i", u.values[inner, :2], et) / r[inner]

    s = run.results.last("stress")
    sxyz = run.mesh.node_coords(s.ids)
    centre = np.hypot(sxyz[:, 0], sxyz[:, 1]) < 0.5 * R
    c = {k: i for i, k in enumerate(s.components)}
    steel = next(o for o in p.objects.values() if o.kind == "material")
    E, nu = steel.properties["youngs_modulus"].to_si(), steel.properties["poissons_ratio"].to_si()
    expected = E * DELTA / (R * (1 - nu))
    sigma = float(s.values[centre, c["xx"]].mean())
    print(f"{solver} cylindrical axes: u_r/r {ur.mean() * R:.6g} (δ {DELTA:g}), "
          f"u_θ/r {ut.mean() * R:.6g} (δ' {TURN:g}), σ {sigma:.6g} vs {expected:.6g}, "
          f"{100 * (sigma / expected - 1):+.2f} %")  # fmt: skip
    assert ur.mean() * R == pytest.approx(DELTA, rel=5e-3)
    assert ut.mean() * R == pytest.approx(TURN, rel=5e-3)
    assert sigma == pytest.approx(expected, rel=2e-2)


@over("calculix")
def test_partial_displacement_in_rotated_axes(tmp_path, solver):
    root = tmp_path / "cantilever"
    shutil.copytree(
        EXAMPLE, root, ignore=shutil.ignore_patterns("results*", ".puzzle", "history.jsonl")
    )
    p = load_project(root)
    comp = p.of_type(Component)[0]
    study = next(s for s in p.of_type(Study) if s.name == "Tip load")
    study.solver = solver
    angle = np.radians(30)
    x_axis = (float(np.cos(angle)), float(np.sin(angle)), 0.0)
    axes = p.add(CoordinateSystem(name="Turned", x_axis=x_axis, xy_direction=(0.0, 1.0, 0.0)))
    tip = comp.physics[0].features[1].selection
    delta = 1e-4
    comp.physics[0].features[1] = Feature(
        type="displacement",
        selection=tip,
        values={"x": Quantity(value=delta, unit="m")},
        axes=axes.id,
    )
    save_project(p, root)
    run = run_study(p, study.id, root, tmp_path / "w")
    u = run.results.last("displacement")
    xyz = run.mesh.node_coords(u.ids)
    at_tip = xyz[:, 0] > xyz[:, 0].max() - 1e-9
    along = u.values[at_tip] @ np.asarray(x_axis)
    print(f"{solver} turned axes: tip along local x {along.mean():.6g}, prescribed {delta:g}")
    assert np.allclose(along, delta, rtol=1e-6)
