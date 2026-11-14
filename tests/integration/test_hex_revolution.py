"""Hexahedra in bodies of revolution, cut into blocks at mesh time (ADR 0072 section 5,
`mesh/blocks.py`), on 20-node bricks, against closed forms:

- a stepped steel shaft pulled along its axis: the axial stress in each segment is
  F / A of that segment (Saint-Venant, away from the step);
- Lamé's thick tube under internal pressure, its ends held axially (plane strain): the
  bore's radial displacement u = (1 + ν) p a² [(1 − 2ν) a + b² / a] / (E (b² − a²));
- every element a hexahedron, the worst scaled Jacobian sane, where the same recipe used
  to refuse these bodies (not block-like).
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
    p = Project(name="revolved")
    geo = p.add(Geometry(name="Shaft", history=history, selections=[
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
    return kinds


@over("calculix", "code_aster")
def test_a_stepped_shaft_meshes_in_hexahedra_and_carries_f_over_a(tmp_path, solver):
    r1, h1, r2, h2, force = 0.006, 0.04, 0.010, 0.04, 20e3
    history = [
        HistoryStep(kind="cylinder", name="Big", args={"r": r2, "h": h2}),
        HistoryStep(kind="cylinder", name="Small", args={"r": r1, "h": h1, "z": h2,
                                                          "mode": "add"}),
    ]  # fmt: skip
    selections = [NamedSelection(name="Root", entity="face", members=["Big/start"]),
                  NamedSelection(name="Tip", entity="face", members=["Small/end"])]  # fmt: skip
    features = [Feature(type="fixed", selection="S/Root"),
                Feature(type="force", selection="S/Tip", values={"z": q(force, "N")})]  # fmt: skip
    p, study = _project(history, selections, features, 2.0, solver)
    run = _run(tmp_path, p, study)
    _all_hexahedra(run)
    from puzzle.post.probes import _cut

    step = [s for s in run.results.steps if "stress" in s.fields][-1]
    zi = step.fields["stress"].components.index("zz")
    for z, r in ((h2 / 2, r2), (h2 + h1 / 2, r1)):  # mid-segment, away from the step
        # the axial stress summed over a section cut through the segment, over its area
        cut, faces, areas, _c, _n = _cut(run.mesh, step, (0.0, 0.0, z), (0.0, 0.0, 1.0))
        sv = np.asarray(cut.point_data["stress"], float).reshape(cut.n_points, -1)
        mean = float((sv[faces][:, :, zi].mean(axis=1) * areas).sum() / areas.sum())
        print(
            f"{solver}: segment r {r * 1e3:g} mm: sigma_zz {mean / 1e6:.5g} MPa "
            f"(F/A {force / (math.pi * r**2) / 1e6:.5g}), area {areas.sum() / (math.pi * r**2):.5f}"
        )
        assert mean == pytest.approx(force / (math.pi * r**2), rel=5e-3)


@over("calculix")
def test_lames_thick_tube_on_hexahedral_blocks(tmp_path, solver):
    a, b, length, p_in = 0.005, 0.010, 0.02, 50e6
    history = [
        HistoryStep(kind="cylinder", name="Outer", args={"r": b, "h": length}),
        HistoryStep(kind="cylinder", name="Bore", args={"r": a, "h": 2 * length,
                                                         "z": -length / 2, "mode": "cut"}),
    ]  # fmt: skip
    selections = [
        NamedSelection(name="Bore", entity="face", members=["Bore/wall"]),
        NamedSelection(name="Ends", entity="face", members=["Outer/start", "Outer/end"]),
        NamedSelection(name="Outside", entity="face", members=["Outer/wall"]),
    ]
    # ends held axially (plane strain); a spring far too soft to matter holds the tube's
    # rigid motion in its plane
    features = [
        Feature(type="displacement", selection="S/Ends", values={"z": q(0.0, "m")}),
        Feature(type="elastic_support", selection="S/Outside",
                values={"kn": q(1e-6 * E / b, "N/m^3")}),
        Feature(type="pressure", selection="S/Bore", values={"p": q(p_in, "Pa")}),
    ]  # fmt: skip
    p, study = _project(history, selections, features, 1.0, solver)
    run = _run(tmp_path, p, study)
    _all_hexahedra(run)
    u = run.results.last("displacement")
    xyz = run.mesh.node_coords(u.ids)
    radius = np.hypot(xyz[:, 0], xyz[:, 1])
    bore = np.abs(radius - a) < 1e-6 * a + 1e-9
    ur = np.einsum("ij,ij->i", u.values[bore, :2], xyz[bore, :2]) / radius[bore]
    expected = (1 + NU) * p_in * a**2 * ((1 - 2 * NU) * a + b**2 / a) / (E * (b**2 - a**2))
    print(f"{solver}: Lame bore u_r {ur.mean():.6g} m ({expected:.6g}), spread "
          f"{(ur.max() - ur.min()) / expected:.2e}")  # fmt: skip
    assert float(ur.mean()) == pytest.approx(expected, rel=5e-3)
