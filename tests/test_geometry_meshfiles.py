"""Faceted geometry (G2): STL, OBJ and 3MF become faces and a volume, then a mesh.

A 30 x 20 x 10 mm block is written in each format the way exporters write it; Gmsh
must find its six faces (patches by feature angle) in metres, and mesh it with a
selection on one end.
"""

import zipfile

import numpy as np
import pytest

from puzzle.geometry import import_step
from puzzle.geometry.meshfile import triangles, write_stl

CORNERS = np.array(
    [
        [0, 0, 0],
        [30, 0, 0],
        [30, 20, 0],
        [0, 20, 0],
        [0, 0, 10],
        [30, 0, 10],
        [30, 20, 10],
        [0, 20, 10],
    ],
    float,
)  # mm
QUADS = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (2, 3, 7, 6), (1, 2, 6, 5), (0, 4, 7, 3)]
TRIS = [(a, b, c) for a, b, c, d in QUADS] + [(a, c, d) for a, b, c, d in QUADS]


def _stl(path):
    write_stl(CORNERS[np.array(TRIS)], path)  # in mm, as exporters write STL
    return path


def _obj(path):
    lines = [f"v {x} {y} {z}" for x, y, z in CORNERS]
    lines += ["f " + " ".join(str(i + 1) for i in q) for q in QUADS]  # quads: split on read
    path.write_text("\n".join(lines) + "\n")
    return path


def _3mf(path, unit="millimeter", scale=1.0):
    verts = "".join(
        f'<vertex x="{x * scale}" y="{y * scale}" z="{z * scale}"/>' for x, y, z in CORNERS
    )
    tris = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in TRIS)
    model = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<model unit="{unit}" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
        f'<resources><object id="1" type="model"><mesh><vertices>{verts}</vertices>'
        f"<triangles>{tris}</triangles></mesh></object></resources>"
        '<build><item objectid="1"/></build></model>'
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("3D/3dmodel.model", model)
    return path


@pytest.mark.parametrize("make", [_stl, _obj, _3mf], ids=["stl", "obj", "3mf"])
def test_faceted_files_give_six_faces_in_metres(tmp_path, make):
    path = make(tmp_path / f"block.{make.__name__[1:]}")
    tris = triangles(path)
    assert tris.max(axis=(0, 1)) == pytest.approx([0.03, 0.02, 0.01])
    model = import_step(path)
    faces = model.of_dim(2)
    assert len(faces) == 6
    assert sum(f.measure for f in faces) == pytest.approx(
        2 * (0.03 * 0.02 + 0.03 * 0.01 + 0.02 * 0.01)
    )
    assert len(model.of_dim(3)) == 1


def test_3mf_states_its_unit(tmp_path):
    inches = _3mf(tmp_path / "block.3mf", unit="inch", scale=1 / 25.4)
    assert triangles(inches).max(axis=(0, 1)) == pytest.approx([0.03, 0.02, 0.01])


def test_a_curved_facet_surface_is_one_face(tmp_path):
    """A faceted cylinder: its wall's facets turn by a few degrees each, so they group
    into one face; the flat ends are faces of their own."""
    n, r, h = 48, 5.0, 20.0
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    ring = np.column_stack([r * np.cos(t), r * np.sin(t)])
    tris = []
    for i in range(n):
        j = (i + 1) % n
        a0, a1 = [*ring[i], 0], [*ring[j], 0]
        b0, b1 = [*ring[i], h], [*ring[j], h]
        tris += [[a0, a1, b1], [a0, b1, b0], [[0, 0, 0], a1, a0], [[0, 0, h], b0, b1]]
    write_stl(np.array(tris, float), tmp_path / "pin.stl")
    model = import_step(tmp_path / "pin.stl")
    assert len(model.of_dim(2)) == 3


def test_a_faceted_part_meshes_with_selections(tmp_path):
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
    )
    from puzzle.model.schema import Quantity

    _stl(tmp_path / "block.stl")
    model = import_step(tmp_path / "block.stl")
    end = next(f.name for f in model.of_dim(2) if f.center[0] == pytest.approx(0, abs=1e-9))
    p = Project(name="facets")
    geo = p.add(
        Geometry(
            name="Block",
            source="block.stl",
            selections=[
                NamedSelection(name="Solid", entity="body", members=["body-1"]),
                NamedSelection(name="End", entity="face", members=[end]),
            ],
        )
    )
    steel = add_library_material(p, "structural_steel")
    mesh = p.add(MeshRecipe(name="m", global_size=Quantity(value=3, unit="mm"), element_order=1))
    comp = p.add(
        Component(
            name="C",
            parts=[PartInstance(name="B", geometry=geo.id)],
            mesh=mesh.id,
            materials=[MaterialAssignment(material=steel.id, selection="B/Solid")],
        )
    )
    meshed = generate_mesh(p, comp.id, tmp_path, tmp_path / "m")
    assert meshed.warnings == []
    assert any(b.dim == 3 and len(b.ids) > 100 for b in meshed.mesh.blocks)
    xs = meshed.mesh.node_coords(meshed.mesh.groups["B/End"].node_ids)[:, 0]
    assert np.allclose(xs, 0.0, atol=1e-9)
