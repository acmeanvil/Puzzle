"""ADR 0048 round 2 end to end: 2D models drawn with the new shapes, meshed and solved,
against Lamé (as in test_planar_benchmarks.py); and a fragmented planar import meshed
as two regions sharing their edge's nodes.

- plane strain: a quarter ring a = 10, b = 20 mm under 100 MPa inside, drawn as an
  `annulus` intersected with a `rectangle` (the first quadrant);
- axisymmetric: the same ring as a thick cylinder with its ends held axially, drawn as an
  `offset` wall to the right of a line up the bore (r = a), 10 mm long.
"""

import numpy as np
import pytest
from solvers import over

from puzzle.materials import add_library_material, library_material
from puzzle.mesh import generate_mesh
from puzzle.model import (
    Component,
    Feature,
    Geometry,
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
from puzzle.model.schema import HistoryStep
from puzzle.solve import run_study

pytest.importorskip("OCP")

STEEL = library_material("structural_steel").properties
E, NU = (STEEL[k].to_si() for k in ("youngs_modulus", "poissons_ratio"))
A_IN, B_OUT, P = 0.010, 0.020, 100e6


def _q(v, unit):
    return Quantity(value=v, unit=unit)


def _lame(r):
    a2, b2 = A_IN**2, B_OUT**2
    A, B = P * a2 / (b2 - a2), P * a2 * b2 / (b2 - a2)
    return A + B / r**2, (1 + NU) / E * ((1 - 2 * NU) * A * r + B / r)


def _project(steps, region, edges, dimension, features, **comp):
    p = Project(name="shapes")
    sels = [NamedSelection(name="Region", entity="face", members=[region])]
    sels += [NamedSelection(name=n, entity="edge", members=m) for n, m in edges.items()]
    geo = p.add(Geometry(name="Part", history=steps, selections=sels))
    steel = add_library_material(p, "structural_steel")
    mesh = p.add(MeshRecipe(name="Mesh", global_size=_q(1.0, "mm")))
    physics = [Physics(type="solid_mechanics", features=features,
                       settings={"temperature_dependent_materials": False})]  # fmt: skip
    c = p.add(Component(name="Part", dimension=dimension, mesh=mesh.id,
                        parts=[PartInstance(name="Part", geometry=geo.id)],
                        materials=[MaterialAssignment(material=steel.id, selection="Part/Region")],
                        physics=physics, **comp))  # fmt: skip
    return p, c


def _solve(tmp_path, p, c, solver):
    study = p.add(Study(name="s", component=c.id, solver=solver,
                        steps=[SolveStep(type="stationary")]))  # fmt: skip
    return run_study(p, study.id, tmp_path, tmp_path / "run", timeout_s=600)


def _at(run, name, group):
    ids = run.mesh.groups[group].node_ids
    return run.results.last(name).at(ids), run.mesh.node_coords(ids)


@over("calculix", "fenicsx")
def test_plane_strain_ring_from_an_annulus(tmp_path, solver):
    steps = [
        HistoryStep(kind="annulus", name="Ring", args={"r_inner": A_IN, "r_outer": B_OUT}),
        HistoryStep(kind="rectangle", name="Q", args={"dx": 1.0, "dy": 1.0, "mode": "intersect"}),
    ]  # fmt: skip
    edges = {"Bore": ["Ring/inner"], "Outer": ["Ring/outer"], "Xaxis": ["Q/-y"],
             "Yaxis": ["Q/-x"]}  # fmt: skip
    features = [
        Feature(type="displacement", selection="Part/Xaxis", values={"y": _q(0, "mm")}),
        Feature(type="displacement", selection="Part/Yaxis", values={"x": _q(0, "mm")}),
        Feature(type="pressure", selection="Part/Bore", values={"p": _q(P, "Pa")}),
    ]
    p, c = _project(steps, "Ring/face", edges, "2d_planar", features,
                    planar_model="plane_strain")  # fmt: skip
    run = _solve(tmp_path, p, c, solver)
    u, xyz = _at(run, "displacement", "Part/Outer")
    radial = (u[:, 0] * xyz[:, 0] + u[:, 1] * xyz[:, 1]) / np.hypot(xyz[:, 0], xyz[:, 1])
    assert radial.mean() == pytest.approx(_lame(B_OUT)[1], rel=5e-3)
    s, xyz = _at(run, "stress", "Part/Bore")
    on_y = int(np.argmax(xyz[:, 1]))  # (0, a): the hoop stress is σx there
    print(f"{solver}: annulus, hoop at the bore {s[on_y, 0] / 1e6:.2f} MPa "
          f"(exact {_lame(A_IN)[0] / 1e6:.2f})")  # fmt: skip
    assert s[on_y, 0] == pytest.approx(_lame(A_IN)[0], rel=0.02)


@over("calculix", "fenicsx")
def test_axisymmetric_cylinder_from_a_wall(tmp_path, solver):
    h = 0.01
    line = {"points": [[A_IN, 0.0], [A_IN, h]]}  # up the bore: its right is +x (outward)
    steps = [HistoryStep(kind="offset", name="W",
                         args={"line": line, "t": B_OUT - A_IN, "side": "right"})]  # fmt: skip
    edges = {"Bore": ["W/left"], "Outer": ["W/right"], "Bottom": ["W/start"], "Top": ["W/end"]}
    features = [
        Feature(type="displacement", selection="Part/Bottom", values={"y": _q(0, "mm")}),
        Feature(type="displacement", selection="Part/Top", values={"y": _q(0, "mm")}),
        Feature(type="pressure", selection="Part/Bore", values={"p": _q(P, "Pa")}),
    ]
    p, c = _project(steps, "W/face", edges, "2d_axisymmetric", features)
    run = _solve(tmp_path, p, c, solver)
    u, _xyz = _at(run, "displacement", "Part/Outer")
    assert u[:, 0].mean() == pytest.approx(_lame(B_OUT)[1], rel=5e-3)
    s, _xyz = _at(run, "stress", "Part/Bore")
    hoop = np.median(s[:, 2])  # zz is the hoop stress axisymmetric
    print(f"{solver}: wall, hoop at the bore {hoop / 1e6:.2f} MPa "
          f"(exact {_lame(A_IN)[0] / 1e6:.2f})")  # fmt: skip
    assert hoop == pytest.approx(_lame(A_IN)[0], rel=0.02)


def test_a_fragmented_import_meshes_as_two_regions(tmp_path):
    from test_planar_geometry import _faces_step

    two = _faces_step(
        tmp_path / "two.step",
        [
            [(0, 0, 3), (40, 0, 3), (40, 20, 3), (0, 20, 3)],
            [(40, 0, 3), (60, 0, 3), (60, 20, 3), (40, 20, 3)],
        ],
    )
    steps = [HistoryStep(kind="import", name="I", args={"file": str(two), "flat": True}),
             HistoryStep(kind="fragment", name="F", args={})]  # fmt: skip
    p = Project(name="import")
    sels = [NamedSelection(name="Left", entity="face", members=["I/face-1"]),
            NamedSelection(name="Right", entity="face", members=["I/face-2"])]  # fmt: skip
    geo = p.add(Geometry(name="Slice", history=steps, selections=sels))
    steel = add_library_material(p, "structural_steel")
    mesh = p.add(MeshRecipe(name="M", global_size=_q(2, "mm")))
    comp = p.add(Component(name="C", dimension="2d_planar", mesh=mesh.id,
                           parts=[PartInstance(name="Slice", geometry=geo.id)],
                           materials=[MaterialAssignment(material=steel.id, selection=f"Slice/{n}")
                                      for n in ("Left", "Right")]))  # fmt: skip
    m = generate_mesh(p, comp.id, tmp_path, tmp_path / "m").mesh
    left, right = (m.groups[f"Slice/{n}"] for n in ("Left", "Right"))
    shared = set(left.node_ids) & set(right.node_ids)
    assert len(shared) >= 10  # the 20 mm edge at x = 40 mm, meshed once for both
    xyz = m.node_coords(sorted(shared))
    assert abs(xyz[:, 0] - 0.04).max() < 1e-9 and abs(m.node_coords(left.node_ids)[:, 2]).max() == 0
