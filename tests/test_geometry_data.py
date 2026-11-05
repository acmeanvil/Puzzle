"""Geometry from data (ADR 0062 phase 3): points, grids and profiles as operators feeding
histories, patterns in two directions, at positions and along a path, selections by
rule with data inputs and as sets, a construction plane a slider moves."""

import math

import numpy as np
import pytest

from puzzle.model.schema import (
    ConstructionGeometry,
    Geometry,
    HistoryStep,
    NamedSelection,
    Operator,
    OperatorInput,
    Project,
)
from puzzle.operators import evaluate as ev

pytest.importorskip("OCP")


def mm(v):
    return OperatorInput(value={"value": v, "unit": "mm"})


def n(v):
    return OperatorInput(value=v)


def wire(op, port="out"):
    return OperatorInput(source=op.id, port=port)


def volume(project, geo) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    from puzzle.geometry.prep import shape_of

    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape_of(project, geo, "."), props)
    return props.Mass()


def test_points_grid_and_profile_values():
    p = Project("p")
    xs = p.add(
        Operator(name="xs", type="range", inputs={"start": mm(0), "end": mm(20), "count": n(3)})
    )
    pts = p.add(Operator(name="P", type="points", inputs={"x": wire(xs), "y": mm(5)}))
    grid = p.add(
        Operator(
            name="G",
            type="grid",
            inputs={"nu": n(2), "nv": n(3), "size_u": mm(10), "size_v": mm(20), "z0": mm(1)},
        )
    )
    box = p.add(
        Operator(
            name="B",
            type="grid",
            region="box",
            inputs={"nu": n(2), "nv": n(2), "nw": n(2), "x1": mm(10), "y1": mm(10), "z1": mm(10)},
        )
    )
    hexagon = p.add(
        Operator(
            name="Hex", type="profile", shape="polygon", inputs={"radius": mm(10), "sides": n(6)}
        )
    )
    ctx = ev.Context(p, None)
    assert np.allclose(ev.value(ctx, pts).xyz, [[0, 0.005, 0], [0.01, 0.005, 0], [0.02, 0.005, 0]])
    g = ev.value(ctx, grid).xyz
    assert (
        g.shape == (6, 3)
        and np.allclose(g[-1], [0.01, 0.02, 0.001])
        and np.allclose(g[:, 2], 0.001)
    )
    assert len(ev.value(ctx, box).xyz) == 8
    spec = ev.value(ctx, hexagon).spec
    assert len(spec["points"]) == 6 and np.allclose(np.hypot(*np.array(spec["points"]).T), 0.01)
    # Points are a table too: their columns back as lists.
    assert ev.value(ctx, pts).table().columns == ["x", "y", "z"]


def test_a_profile_operator_drives_an_extrusion():
    """A hexagon of circumradius r extruded by h: (3√3/2) r² h."""
    p = Project("p")
    p.add(
        Operator(
            name="Hex", type="profile", shape="polygon", inputs={"radius": mm(10), "sides": n(6)}
        )
    )
    geo = p.add(
        Geometry(
            name="Prism",
            history=[
                HistoryStep(kind="extrude", name="E", args={"profile": "Hex", "distance": 0.004})
            ],
        )
    )
    assert volume(p, geo) == pytest.approx(1.5 * math.sqrt(3) * 0.01**2 * 0.004, rel=1e-9)


def test_a_list_entry_sets_a_dimension():
    p = Project("p")
    p.add(
        Operator(name="sizes", type="range", inputs={"start": mm(10), "end": mm(30), "count": n(3)})
    )
    p.add(Operator(name="P", type="points", inputs={"x": mm(7), "y": mm(8)}))
    geo = p.add(
        Geometry(
            name="Box",
            history=[
                HistoryStep(
                    kind="box", name="B", args={"dx": "sizes[2]", "dy": "P[0].x", "dz": "P[0].y"}
                )
            ],
        )
    )
    assert volume(p, geo) == pytest.approx(0.030 * 0.007 * 0.008, rel=1e-9)


def _patterned(first="add", **args):
    p = Project("p")
    hole = HistoryStep(
        kind="box", name="Peg", args={"dx": 0.002, "dy": 0.002, "dz": 0.002, "mode": "add"}
    )
    base = HistoryStep(
        kind="box",
        name="Base",
        args={"dx": 0.05, "dy": 0.05, "dz": 0.001, "z0": -0.001, "mode": first},
    )
    return p, [
        base,
        hole,
        HistoryStep(kind="pattern", name="Rows", args={"feature": "Peg", **args}),
    ]


def test_patterns_in_two_directions_at_positions_and_along_a_path():
    peg, base = 0.002**3, 0.05 * 0.05 * 0.001
    p, steps = _patterned(count=3, dx=0.005, count2=2, dy2=0.01)
    geo = p.add(Geometry(name="Grid", history=steps))
    assert volume(p, geo) == pytest.approx(base + 6 * peg, rel=1e-9)
    # At a Grid operator's points, as offsets (the first the original).
    p, steps = _patterned(positions="Spots")
    p.add(
        Operator(
            name="Spots",
            type="grid",
            inputs={"nu": n(2), "nv": n(2), "size_u": mm(10), "size_v": mm(10)},
        )
    )
    geo = p.add(Geometry(name="At", history=steps))
    assert volume(p, geo) == pytest.approx(base + 4 * peg, rel=1e-9)
    # Along a polyline: four copies at equal lengths along its 30 mm.
    p, steps = _patterned("new", count=4, along="Path")  # the path stays a step, not a body
    path = HistoryStep(
        kind="polyline", name="Path", args={"points": [[0, 0, 0.01], [0.03, 0, 0.01]]}
    )
    geo = p.add(Geometry(name="Along", history=[path, *steps]))
    assert volume(p, geo) == pytest.approx(base + 4 * peg, rel=1e-9)


def _plate(p, selections):
    box = HistoryStep(kind="box", name="Plate", args={"dx": 0.04, "dy": 0.02, "dz": 0.01})
    return p.add(Geometry(name="Plate", history=[box], selections=selections))


def _members(p, geo, name):
    from puzzle.geometry.naming import check_selections

    report = next(r for r in check_selections(p, geo, ".").reports if r.selection == name)
    return sorted(report.resolved)


def test_selections_by_rule_with_data_and_as_sets():
    p = Project("p")
    p.add(Operator(name="reach", type="slider", inputs={"value": mm(5)}))
    sels = [
        NamedSelection(
            name="Low end",
            entity="face",
            rule={"box": {"low": [-0.001, -0.001, -0.001], "high": ["reach", 0.021, 0.011]}},
        ),
        NamedSelection(name="Up", entity="face", rule={"normal": {"direction": [0, 0, 1]}}),
        NamedSelection(name="Big", entity="face", rule={"min_area": 0.0005}),
        NamedSelection(name="Big or low", entity="face", rule={"union": ["Big", "Low end"]}),
        NamedSelection(name="Big not up", entity="face", rule={"difference": ["Big", "Up"]}),
    ]
    geo = _plate(p, sels)
    low = _members(p, geo, "Low end")
    assert len(low) == 1  # only the x = 0 face fits inside x ≤ 5 mm
    assert len(_members(p, geo, "Up")) == 1
    big = _members(p, geo, "Big")  # the two 40 × 20 faces (800 mm²)
    assert len(big) == 2
    assert _members(p, geo, "Big or low") == sorted(set(big) | set(low))
    assert len(_members(p, geo, "Big not up")) == 1
    # The slider moves the box: past the plate's end, all six faces are inside.
    slider = next(o for o in p.of_type(Operator) if o.name == "reach")
    slider.inputs["value"] = mm(50)
    assert len(_members(p, geo, "Low end")) == 6


def test_a_slider_moves_a_construction_plane():
    p = Project("p")
    p.add(Operator(name="cut_at", type="slider", inputs={"value": mm(12)}))
    plane = p.add(
        ConstructionGeometry(
            name="Cut",
            type="plane",
            origin=(0, 0, 0),
            normal=(1, 0, 0),
            drives={"origin.0": "cut_at"},
        )
    )
    origin, normal = plane.frame(p)
    assert np.allclose(origin, [0.012, 0, 0]) and np.allclose(normal, [1, 0, 0])


def test_geometric_operators_stale_what_they_feed():
    from puzzle.model.schema import Component, MeshRecipe, PartInstance, Study
    from puzzle.project.graph import run_hashes

    p = Project("p")
    hexagon = p.add(
        Operator(
            name="Hex", type="profile", shape="polygon", inputs={"radius": mm(10), "sides": n(6)}
        )
    )
    geo = p.add(
        Geometry(
            name="Prism",
            history=[
                HistoryStep(kind="extrude", name="E", args={"profile": "Hex", "distance": 0.004})
            ],
        )
    )
    mesh = p.add(MeshRecipe(name="M"))
    comp = p.add(
        Component(name="C", mesh=mesh.id, parts=[PartInstance(name="Prism", geometry=geo.id)])
    )
    study = p.add(Study(name="S", component=comp.id, solver="calculix", steps=[]))
    assert geo.id
    before = run_hashes(p)[study.id]
    hexagon.inputs["sides"] = n(8)
    assert run_hashes(p)[study.id] != before


def test_the_schematic_wires_a_profile_into_its_geometry():
    from puzzle.schematic import build

    p = Project("p")
    hexagon = p.add(
        Operator(
            name="Hex", type="profile", shape="polygon", inputs={"radius": mm(10), "sides": n(6)}
        )
    )
    geo = p.add(
        Geometry(
            name="Prism",
            history=[
                HistoryStep(kind="extrude", name="E", args={"profile": "Hex", "distance": 0.004})
            ],
        )
    )
    s = build(p, [])
    wire_ = next(w for w in s.wires if w.source == str(hexagon.id))
    assert wire_.target == str(geo.id) and wire_.type == "profile"


def test_points_feed_a_table_operator():
    p = Project("p")
    grid = p.add(
        Operator(
            name="G",
            type="grid",
            inputs={"nu": n(3), "nv": n(2), "size_u": mm(20), "size_v": mm(10)},
        )
    )
    by_y = p.add(
        Operator(
            name="Sorted", type="sort", by=["y"], descending=True, inputs={"table": wire(grid)}
        )
    )
    got = ev.value(ev.Context(p, None), by_y)
    assert got.columns == ["x", "y", "z"] and got.frame["y"].to_list()[0] == pytest.approx(0.01)
