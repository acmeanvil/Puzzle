"""Hexahedra for bodies swept round an arc and for bodies that are blocks once cut (ADR
0072 section 5, `mesh/blocks.py` `bend`/`bend_sweep` and `decompose`), on 20-node
bricks, against closed forms:

- a quarter bend of tube, one end held, the other pushed along the bend's axis (out of
  its plane): Castigliano's curved cantilever, the bending moment P R sin φ and the
  torque P R (1 − cos φ) along it, plus shear (Cowper's coefficient for a hollow
  circle), δ = P R³ [π/4 / (E I) + (3π/4 − 2) / (G J)] + P R (π/2) / (κ G A). The
  formula leaves out the section's curvature, of order (r / R)²: CalculiX and Code_Aster
  agree to five digits and sit +1.08 % from it at R = 10 r, +0.25 % at R = 20 r;
- a square bar stepped down on all four sides (not a prism in any direction), pulled:
  the axial stress in each segment is F / A of that segment.
"""

import math

import numpy as np
import pytest
from solvers import over

from puzzle.materials import add_library_material
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
from puzzle.project import save_project
from puzzle.solve import run_study

q = lambda v, u: Quantity(value=v, unit=u)  # noqa: E731
E, NU = 200e9, 0.3
CONSTANT = {"temperature_dependent_materials": False}


def _project(history, selections, features, size_mm, solver):
    pytest.importorskip("OCP")
    p = Project(name="swept")
    geo = p.add(Geometry(name="G", history=history, selections=[
        NamedSelection(name="Body", entity="body", members=["Body"]), *selections]))  # fmt: skip
    steel = add_library_material(p, "structural_steel")
    steel.properties["youngs_modulus"] = q(E, "Pa")
    steel.properties["poissons_ratio"] = q(NU, "dimensionless")
    mesh = p.add(MeshRecipe(name="M", global_size=q(size_mm, "mm"), element_shape="hexahedra",
                            element_order=2, hexahedron_nodes=20))  # fmt: skip
    comp = p.add(Component(name="C", parts=[PartInstance(name="S", geometry=geo.id)],
                           mesh=mesh.id, materials=[MaterialAssignment(material=steel.id,
                                                                       selection="S/Body")],
                           physics=[Physics(type="solid_mechanics", settings=CONSTANT,
                                            features=features)]))  # fmt: skip
    study = p.add(Study(name="S", component=comp.id, solver=solver,
                        steps=[SolveStep(type="stationary")]))  # fmt: skip
    return p, study


def _run(tmp_path, p, study):
    root = tmp_path / "p"
    save_project(p, root)
    return run_study(p, study.id, root, tmp_path / "w", timeout_s=1800)


def _all_hexahedra(run):
    kinds = {b.element for b in run.mesh.blocks if b.dim == 3}
    assert kinds == {"hex20"}, kinds


@over("calculix", "code_aster")
def test_a_bent_tube_swept_round_its_arc(tmp_path, solver):
    big_r, ro, ri, force = 0.2, 0.01, 0.007, 100.0
    history = [HistoryStep(kind="revolve", name="Bend", args={
        "profile": {"circle": [big_r, 0.0, ro], "holes": [{"circle": [big_r, 0.0, ri]}]},
        "angle": 90.0, "axis": "v"})]  # fmt: skip
    selections = [NamedSelection(name="Root", entity="face", members=["Bend/start"]),
                  NamedSelection(name="Tip", entity="face", members=["Bend/end"])]  # fmt: skip
    features = [Feature(type="fixed", selection="S/Root"),
                Feature(type="force", selection="S/Tip", values={"y": q(force, "N")})]  # fmt: skip
    p, study = _project(history, selections, features, 2.0, solver)
    run = _run(tmp_path, p, study)
    _all_hexahedra(run)
    u = run.results.last("displacement")
    xyz = run.mesh.node_coords(u.ids)
    tip = np.abs(xyz[:, 0]) < 1e-7  # the end face lies in x = 0
    deflection = float(u.values[tip, 1].mean())
    g = E / (2 * (1 + NU))
    inertia = math.pi * (ro**4 - ri**4) / 4
    area = math.pi * (ro**2 - ri**2)
    m = ri / ro
    kappa = (6 * (1 + NU) * (1 + m**2) ** 2
             / ((7 + 6 * NU) * (1 + m**2) ** 2 + (20 + 12 * NU) * m**2))  # fmt: skip
    expected = (force * big_r**3 * (math.pi / 4 / (E * inertia)
                                    + (3 * math.pi / 4 - 2) / (g * 2 * inertia))
                + force * big_r * (math.pi / 2) / (kappa * g * area))  # fmt: skip
    print(f"{solver}: tip deflection {deflection:.6g} m (closed form {expected:.6g}, "
          f"{deflection / expected - 1:+.3%})")  # fmt: skip
    assert deflection == pytest.approx(expected, rel=5e-3)


@over("calculix", "code_aster")
def test_a_bar_stepped_on_four_sides_cut_into_blocks(tmp_path, solver):
    from puzzle.post.probes import _cut

    big, small, h1, h2, force = 0.04, 0.02, 0.06, 0.06, 40e3
    off = (big - small) / 2
    history = [
        HistoryStep(kind="box", name="Big", args={"dx": big, "dy": big, "dz": h1}),
        HistoryStep(kind="box", name="Small", args={"dx": small, "dy": small, "dz": h2,
                                                    "x0": off, "y0": off, "z0": h1,
                                                    "mode": "add"}),
    ]  # fmt: skip
    selections = [NamedSelection(name="Root", entity="face", members=["Big/-z"]),
                  NamedSelection(name="Tip", entity="face", members=["Small/+z"])]  # fmt: skip
    features = [Feature(type="fixed", selection="S/Root"),
                Feature(type="force", selection="S/Tip", values={"z": q(force, "N")})]  # fmt: skip
    p, study = _project(history, selections, features, 2.5, solver)
    run = _run(tmp_path, p, study)
    _all_hexahedra(run)
    step = [s for s in run.results.steps if "stress" in s.fields][-1]
    zi = step.fields["stress"].components.index("zz")
    for z, side in ((h1 / 2, big), (h1 + h2 / 2, small)):  # mid-segment, past the step
        cut, faces, areas, _c, _n = _cut(run.mesh, step, (big / 2, big / 2, z), (0.0, 0.0, 1.0))
        sv = np.asarray(cut.point_data["stress"], float).reshape(cut.n_points, -1)
        mean = float((sv[faces][:, :, zi].mean(axis=1) * areas).sum() / areas.sum())
        print(f"{solver}: at z {z * 1e3:g} mm: sigma_zz {mean / 1e6:.5g} MPa "
              f"(F/A {force / side**2 / 1e6:.5g}), area {areas.sum() / side**2:.5f}")  # fmt: skip
        assert mean == pytest.approx(force / side**2, rel=5e-3)
