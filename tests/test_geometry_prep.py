"""Preparing CAD (G2, ADR 0014): import, heal, remove features, share topology.

Inputs are built as CAD tools write them (millimetres, an XDE assembly, faces that
do not quite meet), and the results are checked by measure: volumes, face counts,
and which names survive.
"""

import math

import pytest

pytest.importorskip("OCP")

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox  # noqa: E402
from OCP.gp import gp_Pnt  # noqa: E402

from puzzle.geometry.history import (  # noqa: E402
    GeometryError,
    Op,
    _faces,
    _static,
    body_signature,
    regenerate,
    signature,
    write_brep,
)


def _write_step(shape, path):
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    from puzzle.geometry.prep import _set_unit

    writer = STEPControl_Writer()
    _set_unit("MM")  # files in mm, as CAD tools write them (see test_geometry_formats)
    writer.Transfer(shape, STEPControl_AsIs)
    writer.Write(str(path))


def _volume(shape, body="Body"):
    return sum(body_signature(b)[0] for b in shape.bodies[body])


def test_import_converts_millimetres_and_names_faces(tmp_path):
    _write_step(BRepPrimAPI_MakeBox(30, 20, 10).Shape(), tmp_path / "block.step")  # mm
    s = regenerate([Op("import", "Block", {"file": "block.step"})], root=tmp_path)
    assert _volume(s) == pytest.approx(0.03 * 0.02 * 0.01)
    assert sorted(s.names) == sorted(f"Block/face-{i}" for i in range(1, 7))
    brep = tmp_path / "block.brep"
    write_brep(BRepPrimAPI_MakeBox(30, 20, 10).Shape(), brep)
    in_mm = regenerate([Op("import", "B", {"file": "block.brep", "unit": "mm"})], root=tmp_path)
    assert _volume(in_mm) == pytest.approx(0.03 * 0.02 * 0.01)


def test_import_keeps_assembly_part_names(tmp_path):
    from test_geometry_formats import _assembly

    _assembly(tmp_path / "assembly.step")
    s = regenerate([Op("import", "Asm", {"file": "assembly.step"})], root=tmp_path)
    assert {"Bracket", "Base plate"} <= set(s.bodies)
    (base,) = s.bodies["Base plate"]
    assert body_signature(base)[1][0] == pytest.approx(0.06)


def test_heal_sews_loose_faces_into_a_solid(tmp_path):
    """Six faces of a box, each moved by 0.2 um (an export that lost its topology)."""
    from OCP.BRep import BRep_Builder
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Trsf, gp_Vec
    from OCP.TopoDS import TopoDS_Compound

    compound, builder = TopoDS_Compound(), BRep_Builder()
    builder.MakeCompound(compound)
    for i, face in enumerate(_faces(BRepPrimAPI_MakeBox(0.03, 0.02, 0.01).Shape())):
        trsf = gp_Trsf()
        trsf.SetTranslation(gp_Vec(0, 0, 2e-7 * (i % 2)))
        builder.Add(compound, BRepBuilderAPI_Transform(face, trsf, True).Shape())
    write_brep(compound, tmp_path / "loose.brep")
    raw = regenerate([Op("import", "Raw", {"file": "loose.brep"})], root=tmp_path)
    assert "Body" not in raw.bodies  # no solid: six separate faces
    healed = regenerate(
        [Op("import", "Raw", {"file": "loose.brep"}), Op("heal", "Fix", {"tolerance": 1e-6})],
        root=tmp_path,
    )
    assert _volume(healed) == pytest.approx(0.03 * 0.02 * 0.01, rel=1e-4)
    assert len(healed.names) == 6  # every face kept its name through sewing


PLATE = [
    Op("box", "Plate", {"dx": 0.08, "dy": 0.04, "dz": 0.01}),
    Op("fillet", "Round", {"r": 0.002, "rule": {"parallel": "z", "outer": True}}),
    Op("hole", "Small", {"x": 0.02, "y": 0.02, "r": 0.002}),
    Op("hole", "Large", {"x": 0.06, "y": 0.02, "r": 0.006}),
]


def test_remove_small_fillets_and_holes_by_size():
    no_rounds = regenerate([*PLATE, Op("remove", "Square", {"rule": {"fillets_below": 0.003}})])
    assert not [n for n in no_rounds.names if n.startswith("Round/")]
    assert "Small/wall" in no_rounds.names  # holes are not fillets
    corners = 4 * (0.002**2 - math.pi * 0.002**2 / 4) * 0.01
    assert _volume(no_rounds) == pytest.approx(_volume(regenerate(PLATE)) + corners, rel=1e-6)

    no_small = regenerate([*PLATE, Op("remove", "Plug", {"rule": {"holes_below": 0.004}})])
    assert "Small/wall" not in no_small.names and "Large/wall" in no_small.names
    assert "Plate/+z" in no_small.names  # neighbours keep their names
    assert _volume(no_small) == pytest.approx(
        _volume(regenerate(PLATE)) + math.pi * 0.002**2 * 0.01, rel=1e-6
    )
    with pytest.raises(GeometryError, match="matches no faces"):
        regenerate([*PLATE, Op("remove", "None", {"rule": {"holes_below": 0.001}})])


def test_remove_a_picked_face():
    s = regenerate([*PLATE, Op("remove", "Plug", {"rule": {"face": "Large/wall"}})])
    assert "Large/wall" not in s.names and "Small/wall" in s.names


def test_fragment_makes_touching_bodies_share_a_face(tmp_path):
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    compound, builder = TopoDS_Compound(), BRep_Builder()
    builder.MakeCompound(compound)
    builder.Add(compound, BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 0.02, 0.02, 0.01).Shape())
    builder.Add(compound, BRepPrimAPI_MakeBox(gp_Pnt(0.02, 0, 0), 0.02, 0.02, 0.01).Shape())
    write_brep(compound, tmp_path / "pair.brep")
    apart = regenerate([Op("import", "Pair", {"file": "pair.brep"})], root=tmp_path)
    joined = regenerate(
        [Op("import", "Pair", {"file": "pair.brep"}), Op("fragment", "Share", {})], root=tmp_path
    )
    assert len(apart.faces()) == 12 and len(joined.faces()) == 11  # one face, shared
    contact = [f for f in joined.faces() if signature(f)[2][0] == pytest.approx(0.02)]
    assert len(contact) == 1
    with pytest.raises(GeometryError, match="at least two bodies"):
        regenerate([Op("box", "One", {"dx": 1, "dy": 1, "dz": 1}), Op("fragment", "F", {})])


def test_a_prepared_import_meshes_with_its_selections(tmp_path):
    """Imported CAD, small holes removed, meshed: the selections land on the faces."""
    import numpy as np

    from puzzle.materials import add_library_material
    from puzzle.mesh import generate_mesh
    from puzzle.model import (
        Component,
        Geometry,
        MaterialAssignment,
        MeshRecipe,
        NamedSelection,
        PartInstance,
        Project,
        Quantity,
    )
    from puzzle.model.schema import HistoryStep

    plate = regenerate(PLATE)
    write_brep(plate.shape, tmp_path / "plate.brep")
    p = Project(name="prepared")
    # Which imported face is the -x end: found once by position, as a pick would.
    imported = regenerate([Op("import", "CAD", {"file": "plate.brep"})], root=tmp_path)
    end = next(n for n, (f,) in imported.names.items() if signature(f)[2][0] == pytest.approx(0))
    geo = p.add(
        Geometry(
            name="Plate",
            history=[
                HistoryStep(kind="import", name="CAD", args={"file": "plate.brep"}),
                HistoryStep(kind="remove", name="Plug", args={"rule": {"holes_below": 0.004}}),
            ],
            selections=[
                NamedSelection(name="Solid", entity="body", members=["Body"]),
                NamedSelection(name="End", entity="face", members=[end]),
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
    meshed = generate_mesh(p, comp.id, tmp_path, tmp_path / "m")
    assert meshed.warnings == []
    xs = meshed.mesh.node_coords(meshed.mesh.groups["P/End"].node_ids)[:, 0]
    assert np.allclose(xs, 0.0)
    _ = _static  # used by the formats helpers


def test_check_reports_what_would_trouble_meshing(tmp_path):
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    from puzzle.geometry.prep import check

    clean = regenerate([Op("box", "B", {"dx": 0.03, "dy": 0.02, "dz": 0.01})])
    assert check(clean.shape) == []

    faces = _faces(BRepPrimAPI_MakeBox(0.03, 0.02, 0.01).Shape())
    shell, builder = TopoDS_Compound(), BRep_Builder()
    builder.MakeCompound(shell)
    for f in faces[:-1]:  # one face missing: an open surface
        builder.Add(shell, f)
    kinds = {f["kind"]: f for f in check(shell)}
    assert kinds["not_solid"]["severity"] == "error"
    assert kinds["open"]["count"] == 4  # the missing face's border

    pair, builder = TopoDS_Compound(), BRep_Builder()
    builder.MakeCompound(pair)
    builder.Add(pair, BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 0.02, 0.02, 0.01).Shape())
    builder.Add(pair, BRepPrimAPI_MakeBox(gp_Pnt(0.02, 0, 0), 0.02, 0.02, 0.01).Shape())
    builder.Add(pair, BRepPrimAPI_MakeBox(gp_Pnt(0.01, 0.01, 0), 0.02, 0.02, 0.01).Shape())
    kinds = {f["kind"]: f for f in check(pair)}
    assert kinds["overlap"]["severity"] == "error" and kinds["overlap"]["count"] == 2
    assert kinds["not_shared"]["count"] == 1  # the first two only touch

    fine = regenerate(
        [
            Op("box", "B", {"dx": 0.03, "dy": 0.02, "dz": 0.01}),
            Op("split", "Cut", {"axis": "x", "at": 0.03 - 1e-6}),  # a 1 um slice
        ]
    )
    kinds = {f["kind"] for f in check(fine.shape)}
    assert {"short_edges", "slivers"} & kinds


def test_reading_after_a_gmsh_import_still_gives_metres(tmp_path):
    """Gmsh and OCP share OCCT's process-wide unit setting; a Gmsh import (which sets it
    to metres) must not make the next read come back scaled twice."""
    from puzzle.geometry import import_step
    from puzzle.geometry.prep import read_cad

    _write_step(BRepPrimAPI_MakeBox(30, 20, 10).Shape(), tmp_path / "block.step")  # mm
    assert import_step(tmp_path / "block.step").of_dim(3)[0].measure == pytest.approx(6e-6)
    shape, _ = read_cad(tmp_path / "block.step")
    from OCP.TopAbs import TopAbs_SOLID

    from puzzle.geometry.history import _subshapes

    (solid,) = _subshapes(shape, TopAbs_SOLID)
    assert body_signature(solid)[0] == pytest.approx(6e-6)  # 30 x 20 x 10 mm in m^3
    assert import_step(tmp_path / "block.step").of_dim(3)[0].measure == pytest.approx(6e-6)
