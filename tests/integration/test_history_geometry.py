"""A part defined by its history (ADR 0014), meshed, solved, edited and solved again.

The bracket plate (80 x 40 x 10 mm, two bolt holes of radius `hole_r`, rounded
corners) is fixed at its -x face and loaded at its +x face. Its selections use
persistent names, so after the hole and corner radii change they still find the right
faces: no warnings, the fixed face still at x = 0, and a fresh (not reused) result.
"""

import numpy as np
import pytest
from solvers import available

pytest.importorskip("OCP")

from puzzle.materials import add_library_material  # noqa: E402
from puzzle.mesh import generate_mesh  # noqa: E402
from puzzle.model import (  # noqa: E402
    Component,
    Feature,
    Geometry,
    MaterialAssignment,
    MeshRecipe,
    NamedSelection,
    ParameterSet,
    PartInstance,
    Physics,
    Project,
    Quantity,
    SolveStep,
    Study,
)
from puzzle.model.schema import HistoryStep  # noqa: E402
from puzzle.results.store import ResultStore  # noqa: E402
from puzzle.solve import run_study  # noqa: E402

pytestmark = pytest.mark.skipif(not available("calculix"), reason="calculix not available")


def _project():
    p = Project(name="history bracket")
    params = p.add(
        ParameterSet(
            name="Globals",
            values={
                "hole_r": Quantity(value=5, unit="mm"),
                "corner_r": Quantity(value=4, unit="mm"),
            },
        )
    )
    geo = p.add(
        Geometry(
            name="Bracket",
            history=[
                HistoryStep(kind="box", name="Plate", args={"dx": 0.08, "dy": 0.04, "dz": 0.01}),
                HistoryStep(kind="hole", name="Hole1", args={"x": 0.02, "y": 0.02, "r": "hole_r"}),
                HistoryStep(kind="hole", name="Hole2", args={"x": 0.06, "y": 0.02, "r": "hole_r"}),
                HistoryStep(
                    kind="fillet",
                    name="Round",
                    args={"rule": {"parallel": "z", "outer": True}, "r": "corner_r"},
                ),
            ],
            selections=[
                NamedSelection(name="Body", entity="body", members=["body-1"]),
                NamedSelection(name="Fixed", entity="face", members=["Plate/-x"]),
                NamedSelection(name="Load", entity="face", members=["Plate/+x"]),
                NamedSelection(
                    name="Bolt holes", entity="face", members=["Hole1/wall", "Hole2/wall"]
                ),
            ],
        )
    )
    steel = add_library_material(p, "structural_steel")
    mesh = p.add(MeshRecipe(name="Tets", global_size=Quantity(value=3, unit="mm")))
    comp = p.add(
        Component(
            name="Bracket",
            parts=[PartInstance(name="Bracket", geometry=geo.id)],
            mesh=mesh.id,
            materials=[MaterialAssignment(material=steel.id, selection="Bracket/Body")],
            physics=[
                Physics(
                    type="solid_mechanics",
                    features=[
                        Feature(type="fixed", selection="Bracket/Fixed"),
                        Feature(
                            type="force",
                            selection="Bracket/Load",
                            values={"z": Quantity(value=-500, unit="N")},
                        ),
                    ],
                )
            ],
        )
    )
    study = p.add(
        Study(
            name="Static",
            component=comp.id,
            solver="calculix",
            steps=[SolveStep(type="stationary")],
        )
    )
    return p, params, comp, study


def _tip(run):
    u = run.results.last("displacement")
    x = run.mesh.node_coords(u.ids)[:, 0]
    return u.values[x > 0.08 - 1e-9, 2].mean()


def test_selections_survive_parameter_edits(tmp_path):
    project, params, comp, study = _project()
    store = ResultStore(tmp_path / "results")
    meshed = generate_mesh(project, comp.id, tmp_path, tmp_path / "m0")
    assert meshed.warnings == []
    fixed = meshed.mesh.node_coords(meshed.mesh.groups["Bracket/Fixed"].node_ids)
    holes = meshed.mesh.node_coords(meshed.mesh.groups["Bracket/Bolt holes"].node_ids)
    assert np.allclose(fixed[:, 0], 0.0)
    radius = np.hypot(holes[:, 0] - np.where(holes[:, 0] < 0.04, 0.02, 0.06), holes[:, 1] - 0.02)
    assert np.allclose(radius, 0.005, rtol=1e-3)

    first = run_study(project, study.id, tmp_path, tmp_path / "run0", store=store)

    params.values["hole_r"] = Quantity(value=8, unit="mm")
    params.values["corner_r"] = Quantity(value=1, unit="mm")
    meshed = generate_mesh(project, comp.id, tmp_path, tmp_path / "m1")
    assert meshed.warnings == []
    holes = meshed.mesh.node_coords(meshed.mesh.groups["Bracket/Bolt holes"].node_ids)
    radius = np.hypot(holes[:, 0] - np.where(holes[:, 0] < 0.04, 0.02, 0.06), holes[:, 1] - 0.02)
    assert np.allclose(radius, 0.008, rtol=1e-3)  # the same selection, on the new holes

    second = run_study(project, study.id, tmp_path, tmp_path / "run1", store=store)
    assert not second.cached  # the edit changed the study's inputs
    print(f"tip deflection: {_tip(first):.4e} m with 5 mm holes, {_tip(second):.4e} m with 8 mm")
    assert abs(_tip(second)) > abs(_tip(first))  # bigger holes: a softer bracket


def _angle_bracket():
    """An angle bracket from an extruded L profile (thickness `t`), a bolt hole through
    the foot and a fillet in the inner corner; fixed at its back, loaded at its toe."""
    p = Project(name="angle bracket")
    params = p.add(
        ParameterSet(
            name="Globals",
            values={"t": Quantity(value=8, unit="mm"), "fillet_r": Quantity(value=3, unit="mm")},
        )
    )
    p_t = "t"
    geo = p.add(
        Geometry(
            name="Angle",
            history=[
                HistoryStep(
                    kind="extrude",
                    name="L",
                    args={
                        "profile": {
                            "points": [
                                [0, 0],
                                [0.06, 0],
                                [0.06, p_t],
                                [p_t, p_t],
                                [p_t, 0.04],
                                [0, 0.04],
                            ]
                        },
                        "distance": 0.03,
                    },
                ),
                HistoryStep(
                    kind="hole", name="Bolt", args={"axis": "y", "x": 0.04, "z": 0.015, "r": 0.003}
                ),
                HistoryStep(
                    kind="fillet",
                    name="Corner",
                    args={"r": "fillet_r", "rule": {"between": ["L/side-3", "L/side-4"]}},
                ),
            ],
            selections=[
                NamedSelection(name="Solid", entity="body", members=["Body"]),
                NamedSelection(name="Back", entity="face", members=["L/side-6"]),
                NamedSelection(name="Toe", entity="face", members=["L/side-2"]),
                NamedSelection(name="Bore", entity="face", members=["Bolt/wall"]),
            ],
        )
    )
    steel = add_library_material(p, "structural_steel")
    mesh = p.add(MeshRecipe(name="Tets", global_size=Quantity(value=2.5, unit="mm")))
    comp = p.add(
        Component(
            name="Bracket",
            parts=[PartInstance(name="Bracket", geometry=geo.id)],
            mesh=mesh.id,
            materials=[MaterialAssignment(material=steel.id, selection="Bracket/Solid")],
            physics=[
                Physics(
                    type="solid_mechanics",
                    features=[
                        Feature(type="fixed", selection="Bracket/Back"),
                        Feature(
                            type="force",
                            selection="Bracket/Toe",
                            values={"y": Quantity(value=-200, unit="N")},
                        ),
                    ],
                )
            ],
        )
    )
    study = p.add(
        Study(
            name="Static",
            component=comp.id,
            solver="calculix",
            steps=[SolveStep(type="stationary")],
        )
    )
    return p, params, comp, study


def test_profile_bracket_from_parameters(tmp_path):
    """Thickening the bracket through its parameter regenerates profile, hole and fillet;
    every selection lands on the same faces and the stiffer bracket deflects less."""
    project, params, comp, study = _angle_bracket()

    def toe(run):
        u = run.results.last("displacement")
        ids = run.mesh.groups["Bracket/Toe"].node_ids
        return u.at(ids)[:, 1].mean()

    meshed = generate_mesh(project, comp.id, tmp_path, tmp_path / "m0")
    assert meshed.warnings == []
    back = meshed.mesh.node_coords(meshed.mesh.groups["Bracket/Back"].node_ids)
    toe_nodes = meshed.mesh.node_coords(meshed.mesh.groups["Bracket/Toe"].node_ids)
    assert np.allclose(back[:, 0], 0.0) and np.allclose(toe_nodes[:, 0], 0.06)
    assert toe_nodes[:, 1].max() == pytest.approx(0.008, abs=1e-6)  # the toe is t high
    thin = run_study(project, study.id, tmp_path, tmp_path / "run0")

    params.values["t"] = Quantity(value=12, unit="mm")
    meshed = generate_mesh(project, comp.id, tmp_path, tmp_path / "m1")
    assert meshed.warnings == []
    toe_nodes = meshed.mesh.node_coords(meshed.mesh.groups["Bracket/Toe"].node_ids)
    assert toe_nodes[:, 1].max() == pytest.approx(0.012, abs=1e-6)  # the same face, taller
    bore = meshed.mesh.node_coords(meshed.mesh.groups["Bracket/Bore"].node_ids)
    assert np.allclose(np.hypot(bore[:, 0] - 0.04, bore[:, 2] - 0.015), 0.003, rtol=1e-3)
    thick = run_study(project, study.id, tmp_path, tmp_path / "run1")
    print(f"toe deflection: {toe(thin):.4e} m at t = 8 mm, {toe(thick):.4e} m at 12 mm")
    assert toe(thin) < 0 and abs(toe(thick)) < 0.5 * abs(toe(thin))  # stiffer, roughly t^3
