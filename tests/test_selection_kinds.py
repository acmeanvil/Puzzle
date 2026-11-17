"""A feature on a kind of selection its solver doesn't apply is refused (ADR 0090
FENICS-1): `common.check_selection_kinds`, and FEniCSx's use of it."""

import json

import numpy as np
import pytest
from conftest_geometry import BAR, bar_project, write_bar_step

from puzzle.adapters.common import InputError, check_selection_kinds, selection_kind
from puzzle.adapters.fenicsx import FenicsxAdapter
from puzzle.geometry import import_step
from puzzle.mesh import generate_mesh
from puzzle.mesh.mesh import Group, Mesh
from puzzle.model import Feature, Physics, Quantity, SolveStep, Study
from puzzle.model.schema import Geometry, NamedSelection


def _mesh(planar=False):
    mesh = Mesh(node_ids=np.arange(1, 5), coords=np.zeros((4, 3)), planar=planar)
    for name, dim in (("B", 3), ("F", 2), ("E", 1), ("P", 0)):
        mesh.groups[name] = Group(name, dim, [(dim, 1)], np.array([1]))
    return mesh


def test_kinds_follow_the_model_dimension():
    solid, flat = _mesh(), _mesh(planar=True)
    assert [selection_kind(solid.groups[g], solid) for g in "BFEP"] == [
        "body", "boundary", "edge", "point"]  # fmt: skip
    # in 2D a region is the body and its edges the boundary
    assert [selection_kind(flat.groups[g], flat) for g in "FEP"] == ["body", "boundary", "point"]


def test_a_face_condition_on_an_edge_is_refused_unless_accepted():
    mesh = _mesh()
    pressure = Feature(type="pressure", selection="E", values={"p": Quantity(value=1, unit="Pa")})
    with pytest.raises(InputError) as info:
        check_selection_kinds([pressure], mesh, "Solver", {})
    assert info.value.code == "unsupported"
    assert "Pressure" in info.value.message and "'E'" in info.value.message
    assert "an edge" in info.value.message and "a face" in info.value.message
    assert info.value.details == {"feature": "pressure", "selection": "E", "kind": "edge"}
    fixed = Feature(type="fixed", selection="P")
    with pytest.raises(InputError, match="a point"):
        check_selection_kinds([fixed], mesh, "Solver", {})
    check_selection_kinds([fixed], mesh, "Solver", {"fixed": ("boundary", "point")})
    # its catalog kind is always accepted; a body load on a body too
    check_selection_kinds([Feature(type="fixed", selection="F"),
                           Feature(type="gravity", selection="B")], mesh, "Solver", {})  # fmt: skip
    with pytest.raises(InputError, match="a body selection"):
        check_selection_kinds([Feature(type="pressure", selection="B")], mesh, "Solver", {})


def test_in_2d_an_edge_is_the_boundary():
    mesh = _mesh(planar=True)
    check_selection_kinds([Feature(type="pressure", selection="E")], mesh, "Solver", {})
    with pytest.raises(InputError, match="to a point selection"):
        check_selection_kinds([Feature(type="pressure", selection="P")], mesh, "Solver", {})


def _fenicsx(tmp_path, physics, features):
    tmp_path.mkdir(parents=True, exist_ok=True)
    step = write_bar_step(tmp_path / "bar.step")
    model = import_step(step)
    edge = next(e.name for e in model.of_dim(1)
                if abs(e.bbox[0] - BAR[0]) < 1e-6 and abs(e.bbox[3] - BAR[0]) < 1e-6)  # fmt: skip
    corner = next(p.name for p in model.of_dim(0) if abs(p.bbox[0] - BAR[0]) < 1e-6)
    project, comp = bar_project(step, [Physics(type=physics, features=features)], mesh_size_mm=5)
    geo = project.get(comp.parts[0].geometry, Geometry)
    geo.selections += [
        NamedSelection(name="TipEdge", entity="edge", members=[edge]),
        NamedSelection(name="TipCorner", entity="vertex", members=[corner]),
    ]
    study = project.add(Study(name="s", component=comp.id, solver="fenicsx",
                              steps=[SolveStep(type="stationary")]))  # fmt: skip
    mesh = generate_mesh(project, comp.id, tmp_path, tmp_path / "m").mesh
    return FenicsxAdapter().write_input(project, study, mesh, tmp_path / "case")


def test_fenicsx_refuses_a_pressure_or_a_flux_on_an_edge_or_a_point(tmp_path):
    fixed = Feature(type="fixed", selection="Bar/Root")
    pressure = Feature(type="pressure", selection="Bar/TipEdge",
                       values={"p": Quantity(value=1, unit="MPa")})  # fmt: skip
    with pytest.raises(InputError, match="Pressure to an edge selection") as info:
        _fenicsx(tmp_path / "p", "solid_mechanics", [fixed, pressure])
    assert info.value.code == "unsupported"
    flux = Feature(type="heat_flux", selection="Bar/TipCorner",
                   values={"q": Quantity(value=1, unit="W/m^2")})  # fmt: skip
    hot = Feature(type="temperature", selection="Bar/Root",
                  values={"T": Quantity(value=100, unit="degC")})  # fmt: skip
    with pytest.raises(InputError, match="to a point selection"):
        _fenicsx(tmp_path / "q", "heat_transfer", [hot, flux])


def test_fenicsx_writes_held_values_on_an_edge_and_a_point(tmp_path):
    """Held values are written, and a point selection's nodes go into mesh.msh as vertex
    cells (dolfinx reads a group through its elements; the mesher keeps none on points)."""
    held = [Feature(type="fixed", selection="Bar/Root"),
            Feature(type="fixed", selection="Bar/TipEdge"),
            Feature(type="displacement", selection="Bar/TipCorner",
                    values={"z": Quantity(value=0.1, unit="mm")})]  # fmt: skip
    script = _fenicsx(tmp_path, "solid_mechanics", held)
    case = json.loads((script.parent / "case.json").read_text(encoding="utf-8"))
    assert {f["group"] for f in case["features"]} == {"Bar/Root", "Bar/TipEdge", "Bar/TipCorner"}
    import gmsh

    from puzzle.geometry import gmsh_session

    with gmsh_session("check"):
        gmsh.open(str(script.parent / "mesh.msh"))
        groups = gmsh.model.getPhysicalGroups()
        named = {gmsh.model.getPhysicalName(d, t): (d, t) for d, t in groups}
        dim, tag = named["Bar/TipCorner"]
        assert dim == 0
        (entity,) = gmsh.model.getEntitiesForPhysicalGroup(dim, tag)
        types, ids, _ = gmsh.model.mesh.getElements(0, int(entity))
        assert list(types) == [15] and len(ids[0]) == 1
