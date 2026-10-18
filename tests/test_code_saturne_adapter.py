import json
import struct

import numpy as np
import pytest
from conftest_geometry import write_bar_step

from puzzle.adapters.code_saturne import CodeSaturneAdapter
from puzzle.adapters.code_saturne.ensight import read_case, read_variable
from puzzle.adapters.common import InputError, match_points
from puzzle.geometry import import_step
from puzzle.materials import add_library_material
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


def _text(s: str) -> bytes:
    return s.encode().ljust(80, b"\0")


def _write_ensight(d, coords, hexa, velocity, pressure):
    n = len(coords)
    geo = (
        _text("C Binary")
        + _text("test")
        + _text("test")
        + _text("node id assign")
        + _text("element id assign")
    )
    geo += (
        _text("part")
        + struct.pack("<i", 1)
        + _text("Fluid")
        + _text("coordinates")
        + struct.pack("<i", n)
    )
    for axis in range(3):
        geo += coords[:, axis].astype("<f4").tobytes()
    geo += _text("hexa8") + struct.pack("<i", len(hexa)) + (hexa + 1).astype("<i4").tobytes()
    (d / "r.geo").write_bytes(geo)
    for name, values in (("velocity", velocity), ("pressure", pressure)):
        data = _text(name) + _text("part") + struct.pack("<i", 1) + _text("hexa8")
        data += np.ascontiguousarray(values.T).astype("<f4").tobytes()
        (d / f"r.{name}.00001").write_bytes(data)
    (d / "R.case").write_text(
        "FORMAT\ntype: ensight gold\n\nGEOMETRY\nmodel: r.geo\n\nVARIABLE\n"
        "vector per element: 1 Velocity r.velocity.*****\n"
        "scalar per element: 1 Pressure r.pressure.*****\n"
    )


def test_ensight_reader(tmp_path):
    cube = np.array(
        [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]],
        float,
    )
    coords = np.vstack([cube, cube + [2, 0, 0]])
    hexa = np.array([np.arange(8), np.arange(8, 16)])
    velocity = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]])
    pressure = np.array([[7.0], [8.0]])
    _write_ensight(tmp_path, coords, hexa, velocity, pressure)

    parts, variables = read_case(tmp_path / "R.case")
    (block,) = parts[0].blocks
    assert block.element == "hexa8"
    assert np.allclose(block.centroids, [[0.5, 0.5, 0.5], [2.5, 0.5, 0.5]])
    assert np.allclose(read_variable(variables["Velocity"][2], parts, 3), velocity)
    assert np.allclose(read_variable(variables["Pressure"][2], parts, 1), pressure)


def test_match_points_tolerates_float32():
    reference = np.random.default_rng(1).random((500, 3))
    ids = np.arange(1000, 1500)
    shuffle = np.random.default_rng(2).permutation(500)
    points = reference[shuffle].astype(np.float32).astype(np.float64)
    assert np.array_equal(match_points(points, reference, ids), ids[shuffle])
    with pytest.raises(InputError):
        match_points(np.array([[5.0, 5.0, 5.0]]), reference, ids)


def _channel(tmp_path, features, **step):
    stepfile = write_bar_step(tmp_path / "bar.step")
    model = import_step(stepfile)
    p = Project(name="c")
    geo = p.add(
        Geometry(
            name="Duct",
            source=stepfile.name,
            selections=[
                NamedSelection(name="Fluid", entity="body", members=["body-1"]),
                NamedSelection(name="In", entity="face", members=model.faces_on_plane("x", 0.0)),
                NamedSelection(name="Out", entity="face", members=model.faces_on_plane("x", 0.2)),
            ],
        )
    )
    water = add_library_material(p, "water")
    mesh = p.add(
        MeshRecipe(
            name="h",
            global_size=Quantity(value=5, unit="mm"),
            element_order=1,
            element_shape="hexahedra",
        )
    )
    comp = p.add(
        Component(
            name="c",
            parts=[PartInstance(name="Duct", geometry=geo.id)],
            mesh=mesh.id,
            materials=[MaterialAssignment(material=water.id, selection="Duct/Fluid")],
            physics=[Physics(type="fluid_flow", features=features)],
        )
    )
    study = p.add(
        Study(
            name="s",
            component=comp.id,
            solver="code_saturne",
            steps=[SolveStep(type="stationary", **step)],
        )
    )
    m = generate_mesh(p, comp.id, tmp_path, tmp_path / "m").mesh
    return CodeSaturneAdapter().write_input(p, study, m, tmp_path / "case")


def test_case_files(tmp_path):
    inlet = Feature(
        type="velocity_inlet", selection="Duct/In", values={"U": Quantity(value=2, unit="mm/s")}
    )
    outlet = Feature(type="pressure_outlet", selection="Duct/Out")
    spec_path = _channel(tmp_path, [inlet, outlet], settings={"iterations": 50})
    spec = json.loads(spec_path.read_text())
    work = spec_path.parent
    assert (work / "cs_setup.py").exists() and (work / "mesh.msh").read_text().startswith(
        "$MeshFormat\n2.2"
    )
    assert spec["density"] == pytest.approx(998.2) and spec["iterations"] == 50
    zones = {z["label"]: z for z in spec["zones"]}
    assert zones["Duct_In"]["nature"] == "inlet" and zones["Duct_In"]["velocity"] == pytest.approx(
        0.002
    )
    assert zones["Duct_Out"]["nature"] == "outlet"
    assert zones["Duct_In"]["selection"].isdigit()  # Code_Saturne names Gmsh groups by number
    assert CodeSaturneAdapter().command(spec_path)[0] == "sh"


def test_unsupported_inputs_are_refused(tmp_path):
    flow = Feature(
        type="mass_flow_inlet",
        selection="Duct/In",
        values={"m_dot": Quantity(value=1, unit="kg/s")},
    )
    with pytest.raises(InputError, match="mass_flow_inlet"):
        _channel(tmp_path, [flow])
