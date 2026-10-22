"""Constrained sketches (G5, ADR 0014): solved by SolveSpace, extruded and revolved.

Checked by measure: solved positions against the dimensions, volumes and areas
against closed forms, and the sides named after the sketch's own edges.
"""

import math

import pytest

pytest.importorskip("OCP")
pytest.importorskip("py_slvs")

from puzzle.geometry.history import GeometryError, Op, body_signature, regenerate, signature  # noqa: E402
from puzzle.geometry.sketch import describe, solve  # noqa: E402


def plate(**extra):
    """A W x 30 mm plate (W a parameter) with a 4 mm bore 20 mm in and 15 mm up; drawn
    roughly, as a user would, and put right by its constraints."""
    sketch = {
        "points": {
            "A": [0, 0],
            "B": [0.05, 0.002],
            "C": [0.052, 0.03],
            "D": [0.001, 0.029],
            "O": [0.02, 0.014],
        },
        "lines": {"bottom": ["A", "B"], "right": ["B", "C"], "top": ["C", "D"], "left": ["D", "A"]},
        "circles": {"bore": ["O", 0.003]},
        "constraints": [
            ["fix", "A"],
            ["horizontal", "bottom"],
            ["horizontal", "top"],
            ["vertical", "left"],
            ["vertical", "right"],
            ["length", "bottom", "W"],
            ["length", "left", 0.03],
            ["radius", "bore", 0.004],
            ["hdistance", "A", "O", 0.02],
            ["vdistance", "A", "O", 0.015],
        ],
    }
    sketch.update(extra)
    return sketch


def test_a_sketch_solves_to_its_dimensions():
    s = solve(plate(), {"W": 0.06})
    assert s.status == "ok" and s.dof == 0
    assert describe(s, plate()) == "Fully constrained"
    assert s.points["C"] == pytest.approx((0.06, 0.03), abs=1e-12)
    assert s.points["O"] == pytest.approx((0.02, 0.015), abs=1e-12)
    assert s.radii["bore"] == pytest.approx(0.004)


def test_missing_and_conflicting_constraints_are_reported():
    loose = plate(constraints=plate()["constraints"][:-2])  # the bore's position is free
    s = solve(loose, {"W": 0.06})
    assert s.status == "ok" and s.dof == 2
    assert describe(s, loose) == "2 degrees of freedom left"
    over = plate(constraints=[*plate()["constraints"], ["length", "right", 0.05]])
    s = solve(over, {"W": 0.06})
    assert s.status == "conflict" and s.failed == [10]  # the 11th fights the others
    with pytest.raises(GeometryError, match="Constraints 11 conflict"):
        regenerate(
            [Op("extrude", "P", {"profile": {"sketch": over}, "distance": 0.01})], {"W": 0.06}
        )


def test_an_extruded_sketch_names_its_sides_and_follows_its_parameter():
    ops = [Op("extrude", "Plate", {"profile": {"sketch": plate()}, "distance": 0.01})]
    for width in (0.06, 0.08):
        shape = regenerate(ops, {"W": width})
        volume = body_signature(shape.bodies["Body"][0])[0]
        assert volume == pytest.approx((width * 0.03 - math.pi * 0.004**2) * 0.01, rel=1e-9)
        (right,) = shape.names["Plate/right"]
        assert signature(right)[2][0] == pytest.approx(width)  # the side named by its line
        (bore,) = shape.names["Plate/bore"]
        assert signature(bore)[1] == pytest.approx(2 * math.pi * 0.004 * 0.01)


def test_arcs_tangent_to_lines_make_a_slot():
    """An obround: two 40 mm lines joined by two tangent arcs of radius 5 mm."""
    sketch = {
        "points": {
            "A": [0, 0],
            "B": [0.04, 0.001],
            "C": [0.041, 0.01],
            "D": [0.0, 0.011],
            "O1": [0.04, 0.005],
            "O2": [0.0, 0.005],
        },
        "lines": {"lower": ["A", "B"], "upper": ["C", "D"]},
        "arcs": {"east": ["O1", "B", "C"], "west": ["O2", "D", "A"]},
        "constraints": [
            ["fix", "A"],
            ["horizontal", "lower"],
            ["length", "lower", 0.04],
            ["tangent", "east", "lower"],
            ["tangent", "east", "upper"],
            ["tangent", "west", "upper"],
            ["tangent", "west", "lower"],
            ["radius", "east", 0.005],
            ["equal", "east", "west"],
        ],
    }
    s = solve(sketch)
    assert s.status == "ok" and s.dof == 0, describe(s, sketch)
    shape = regenerate([Op("extrude", "Slot", {"profile": {"sketch": sketch}, "distance": 0.002})])
    volume = body_signature(shape.bodies["Body"][0])[0]
    assert volume == pytest.approx((0.04 * 0.01 + math.pi * 0.005**2) * 0.002, rel=1e-9)
    assert {"Slot/east", "Slot/west", "Slot/lower", "Slot/upper"} <= set(shape.names)


def test_a_revolved_sketch_makes_a_ring():
    """A 10 x 5 mm rectangle 20 mm off the axis, revolved: a ring of 2 pi R A."""
    sketch = {
        "points": {"A": [0.02, 0], "B": [0.03, 0], "C": [0.03, 0.005], "D": [0.02, 0.005]},
        "lines": {
            "inner_base": ["A", "B"],
            "outer": ["B", "C"],
            "lid": ["C", "D"],
            "bore": ["D", "A"],
        },
        "constraints": [
            ["fix", "A"],
            ["horizontal", "inner_base"],
            ["horizontal", "lid"],
            ["vertical", "bore"],
            ["length", "inner_base", 0.01],
            ["length", "bore", 0.005],
        ],
    }
    ops = [Op("revolve", "Ring", {"profile": {"sketch": sketch}, "plane": "xz", "axis": "v"})]
    shape = regenerate(ops)
    volume = body_signature(shape.bodies["Body"][0])[0]
    assert volume == pytest.approx(2 * math.pi * 0.025 * 0.01 * 0.005, rel=1e-9)
    assert "Ring/outer" in shape.names and "Ring/bore" in shape.names


def test_an_open_outline_is_refused_by_point():
    kept = [c for c in plate()["constraints"] if c[1] != "left"]
    lines = {"bottom": ["A", "B"], "right": ["B", "C"], "top": ["C", "D"]}
    open_ = plate(lines=lines, constraints=kept)
    with pytest.raises(GeometryError, match="not closed at points A, D"):
        regenerate(
            [Op("extrude", "P", {"profile": {"sketch": open_}, "distance": 0.01})], {"W": 0.06}
        )


def test_an_agent_checks_a_sketch(tmp_path):
    pytest.importorskip("mcp")
    import asyncio
    import json

    from mcp.server.mcpserver.exceptions import ToolError

    from puzzle.agent.mcp import build_server
    from puzzle.model.schema import ParameterSet, Project, Quantity
    from puzzle.project import save_project

    p = Project(name="sketches")
    p.add(ParameterSet(name="Sizes", values={"W": Quantity(value=60, unit="mm")}))
    save_project(p, tmp_path)
    server = build_server(tmp_path, "test")

    def call(sketch):
        result = asyncio.run(server.call_tool("sketch_check", {"sketch": sketch}))
        return result.structured_content

    out = call(plate())
    assert out["message"] == "Fully constrained" and out["points"]["C"] == pytest.approx(
        [0.06, 0.03]
    )
    out = call(plate(constraints=[*plate()["constraints"], ["length", "right", 0.05]]))
    assert out["status"] == "conflict" and out["conflicting"] == [11]
    with pytest.raises(ToolError) as err:
        asyncio.run(
            server.call_tool("sketch_check", {"sketch": plate(lines={"bottom": ["A", "B"]})})
        )
    assert "no line 'top'" in json.loads(str(err.value).split(": ", 1)[1])["message"]


def test_a_sketched_part_meshes_with_sketch_named_selections(tmp_path):
    """A selection on "Plate/right" (the sketch's line) follows the width parameter."""
    import numpy as np

    from puzzle.materials import add_library_material
    from puzzle.mesh import generate_mesh
    from puzzle.model import Component, MaterialAssignment, MeshRecipe, PartInstance
    from puzzle.model.schema import (
        Geometry,
        HistoryStep,
        NamedSelection,
        ParameterSet,
        Project,
        Quantity,
    )

    p = Project(name="sketched")
    sizes = p.add(ParameterSet(name="Sizes", values={"W": Quantity(value=60, unit="mm")}))
    geo = p.add(
        Geometry(
            name="Plate",
            history=[
                HistoryStep(
                    kind="extrude",
                    name="Plate",
                    args={"profile": {"sketch": plate()}, "distance": 0.01},
                )
            ],
            selections=[
                NamedSelection(name="Solid", entity="body", members=["Body"]),
                NamedSelection(name="Right", entity="face", members=["Plate/right"]),
                NamedSelection(name="Bore", entity="face", members=["Plate/bore"]),
            ],
        )
    )
    steel = add_library_material(p, "structural_steel")
    mesh = p.add(MeshRecipe(name="m", global_size=Quantity(value=4, unit="mm"), element_order=1))
    comp = p.add(
        Component(
            name="C",
            parts=[PartInstance(name="P", geometry=geo.id)],
            mesh=mesh.id,
            materials=[MaterialAssignment(material=steel.id, selection="P/Solid")],
        )
    )
    for width in (60, 80):
        p.objects[sizes.id] = sizes.model_copy(
            update={"values": {"W": Quantity(value=width, unit="mm")}}
        )
        meshed = generate_mesh(p, comp.id, tmp_path, tmp_path / f"m{width}")
        assert meshed.warnings == []
        xs = meshed.mesh.node_coords(meshed.mesh.groups["P/Right"].node_ids)[:, 0]
        assert np.allclose(xs, width / 1000)
        bore = meshed.mesh.node_coords(meshed.mesh.groups["P/Bore"].node_ids)
        assert np.allclose(np.hypot(bore[:, 0] - 0.02, bore[:, 1] - 0.015), 0.004, atol=1e-6)


def _holes(r=0.003):
    """Two bolt holes, 15 mm apart, as a sketch of two circles."""
    return {
        "points": {"O1": [0.035, 0.015], "O2": [0.05, 0.015]},  # clear of the bore at x = 20 mm
        "circles": {"left": ["O1", r], "right": ["O2", r]},
        "constraints": [
            ["fix", "O1"],
            ["fix", "O2"],
            ["radius", "left", r],
            ["equal", "left", "right"],
        ],
    }


def test_sketches_are_steps_that_features_use_by_name():
    """ADR 0017: an outline sketch extruded, then holes sketched on its top face and cut."""
    ops = [
        Op("sketch", "Outline", {"sketch": plate(), "plane": "xy"}),
        Op("extrude", "Plate", {"profile": {"sketch": "Outline"}, "distance": 0.01}),
        Op("sketch", "Bolt holes", {"sketch": _holes(), "face": "Plate/end"}),
        Op(
            "extrude",
            "Bolts",
            {"profile": {"sketch": "Bolt holes"}, "distance": -0.01, "mode": "cut"},
        ),
    ]
    shape = regenerate(ops, {"W": 0.06})
    volume = body_signature(shape.bodies["Body"][0])[0]
    assert volume == pytest.approx(
        (0.06 * 0.03 - math.pi * 0.004**2 - 2 * math.pi * 0.003**2) * 0.01, rel=1e-9
    )
    assert "Bolts/left" in shape.names and "Plate/right" in shape.names
    with pytest.raises(GeometryError, match="no earlier sketch 'Later'"):
        regenerate([Op("extrude", "P", {"profile": {"sketch": "Later"}, "distance": 0.01})])
    with pytest.raises(GeometryError, match="no solid yet"):
        regenerate(ops[:1], {"W": 0.06})
