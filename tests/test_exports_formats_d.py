"""Nastran, Abaqus/CalculiX, glTF and Lorentz-force exports (ADR 0043 steps 6 and 7) on
a hand-made one-element mesh: each file is read back by a small parser of its own
format (VTK's glTF reader for the .glb) and compared with the values put in.

- Node orders: Gmsh's edge nodes are checked against a real Gmsh mesh (each lies at the
  middle of the corners it is listed between), and the decks' orders follow from them.
- Nastran: GRID to the large field's 10 digits, CTETRA connectivity in Nastran's order,
  SET1 members, MAT1; the punch's displacements, eigenvalue (2π f)² and stresses to
  E18.6's seven digits.
- Abaqus: C3D10 in CalculiX's order, sets by name, *TEMPERATURE and *BOUNDARY rows.
- glTF: the chunks and views are 4-byte aligned, POSITION's min/max are the points',
  VTK reads as many points and triangles, and the lowest and highest values get the
  colour map's two ends.
- Lorentz: J × B exactly, and a clear refusal without B.
"""

from __future__ import annotations

import json
import struct
from types import SimpleNamespace

import numpy as np
import pytest

from puzzle.errors import PuzzleError
from puzzle.materials import add_library_material
from puzzle.mesh.mesh import CellBlock, Group, Mesh
from puzzle.model import (
    Component,
    Geometry,
    MaterialAssignment,
    MeshRecipe,
    PartInstance,
    Project,
    SolveStep,
    Study,
)
from puzzle.post.exports import Job
from puzzle.post.exports.decks import GMSH_EDGES, edge_order
from puzzle.results.model import Field, Results, StepResult

S = 0.1  # m, the tet's legs
CORNERS = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], float) * S
EDGES = GMSH_EDGES["tet10"]
COORDS = np.vstack([CORNERS, [(CORNERS[a] + CORNERS[b]) / 2 for a, b in EDGES]])
IDS = np.arange(1, 11)
BASE = np.array([1, 2, 3, 5, 6, 7])  # the z = 0 face: corners 0, 1, 2 and their edges
FREQ = 123.4


def _mesh() -> Mesh:
    block = CellBlock("tet10", 3, (3, 1), np.array([7]), IDS[None, :].copy())
    groups = {
        "Block": Group("Block", 3, [(3, 1)], IDS.copy()),
        "Block/Base": Group("Block/Base", 2, [(2, 1)], BASE.copy()),
    }
    return Mesh(IDS.copy(), COORDS.copy(), [block], groups)


def _u(xyz):
    x, y, z = xyz.T
    return np.column_stack([1e-3 * x, -2e-3 * y + 5e-4 * x, 3e-4 * z - 1e-6])


def _stress(xyz):
    x, y, z = xyz.T
    one = np.ones_like(x)
    return np.column_stack([1e8 * x + 2e6, -3e7 * one, 5e7 * z, 4e6 * y, -1e6 * one, 7e5 * one])


def _results() -> Results:
    u = Field("displacement", "node", ["x", "y", "z"], IDS, _u(COORDS))
    s = Field("stress", "node", ["xx", "yy", "zz", "xy", "yz", "zx"], IDS, _stress(COORDS))
    t = Field("temperature", "node", [""], IDS, (300 + 1000 * COORDS[:, 0])[:, None])
    mode = Field("displacement", "node", ["x", "y", "z"], IDS, 10 * _u(COORDS))
    return Results("calculix", [
        StepResult(1, "stationary", 1.0, fields={"displacement": u, "stress": s,
                                                 "temperature": t}),
        StepResult(2, "modal", FREQ, 1, {"displacement": mode}),
    ])  # fmt: skip


def _job(tmp_path, suffix: str, step: int = 0, **kw) -> Job:
    project = Project(name="tet")
    geo = project.add(Geometry(name="G", source="g.step"))
    steel = add_library_material(project, "structural_steel")
    recipe = project.add(MeshRecipe(name="M"))
    comp = project.add(Component(
        name="Tet", mesh=recipe.id, parts=[PartInstance(name="Block", geometry=geo.id)],
        materials=[MaterialAssignment(material=steel.id, selection="Block")],
    ))  # fmt: skip
    study = project.add(
        Study(name="S", component=comp.id, solver="calculix", steps=[SolveStep(type="stationary")])
    )
    record = SimpleNamespace(study_id=str(study.id), study_name="S", solver="calculix")
    results = _results()
    return Job(project, record, _mesh(), results, results.steps[step],
               tmp_path / f"out{suffix}", **kw)  # fmt: skip


def test_gmsh_edge_orders_are_gmshs():
    import gmsh

    from puzzle.geometry import gmsh_session

    for element, gtype, setup in (
        ("tet10", 11, lambda: None),
        ("hex20", 17, lambda: _transfinite(gmsh)),
        ("wedge15", 18, lambda: _extruded(gmsh)),
    ):
        with gmsh_session(element):
            if element == "wedge15":
                setup()
            else:
                gmsh.model.occ.addBox(0, 0, 0, 1, 1, 1)
                gmsh.model.occ.synchronize()
                setup()
            gmsh.option.setNumber("Mesh.SecondOrderIncomplete", 1)
            gmsh.option.setNumber("Mesh.MeshSizeMax", 0.6)
            gmsh.model.mesh.generate(3)
            gmsh.model.mesh.setOrder(2)
            tags, coords, _ = gmsh.model.mesh.getNodes()
            xyz = dict(zip(tags, coords.reshape(-1, 3), strict=True))
            _, nodes = gmsh.model.mesh.getElementsByType(gtype)
            n = len(GMSH_EDGES[element]) + {"tet10": 4, "hex20": 8, "wedge15": 6}[element]
            assert len(nodes), element
            for row in nodes.reshape(-1, n)[:5]:
                p = np.array([xyz[t] for t in row])
                corners = n - len(GMSH_EDGES[element])
                for k, (a, b) in enumerate(GMSH_EDGES[element]):
                    assert np.allclose(p[corners + k], (p[a] + p[b]) / 2), (element, k)


def _transfinite(gmsh):
    gmsh.option.setNumber("Mesh.RecombineAll", 1)
    gmsh.option.setNumber("Mesh.Recombine3DAll", 1)
    for _, tag in gmsh.model.getEntities(1):
        gmsh.model.mesh.setTransfiniteCurve(tag, 3)
    for _, tag in gmsh.model.getEntities(2):
        gmsh.model.mesh.setTransfiniteSurface(tag)
    gmsh.model.mesh.setTransfiniteVolume(1)


def _extruded(gmsh):
    gmsh.option.setNumber("Mesh.RecombineAll", 0)  # options outlive a session
    gmsh.option.setNumber("Mesh.Recombine3DAll", 0)
    tri = gmsh.model.occ.addRectangle(0, 0, 0, 1, 1)
    gmsh.model.occ.extrude([(2, tri)], 0, 0, 1, numElements=[2], recombine=True)  # prisms
    gmsh.model.occ.synchronize()


def test_deck_orders_follow_from_the_edges():
    # Nastran's and Abaqus's C3D10/CTETRA: edges 1-2, 2-3, 3-1, 1-4, 2-4, 3-4.
    assert edge_order("tet10", [(0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3)]) == [
        0, 1, 2, 3, 4, 5, 6, 7, 9, 8
    ]  # fmt: skip
    assert edge_order("tri6", [(0, 1), (1, 2), (2, 0)]) is None


# ---- Nastran -------------------------------------------------------------------------


def _large_field_cards(text: str) -> list[tuple[str, list[str]]]:
    """Large-field cards: (name, fields), continuations joined, comments skipped."""
    cards = []
    for line in text.splitlines():
        if line.startswith("$") or not line.strip():
            continue
        fields = [line[8 + 16 * k : 24 + 16 * k].strip() for k in range(4)]
        if line.startswith("*"):
            cards[-1][1].extend(fields)
        else:
            cards.append((line[:8].strip().rstrip("*"), fields))
    return [(name, [f for f in fields if f != ""]) for name, fields in cards]


def _members(fields: list[str]) -> list[int]:
    out, k = [], 0
    while k < len(fields):
        if k + 1 < len(fields) and fields[k + 1] == "THRU":
            out += list(range(int(fields[k]), int(fields[k + 2]) + 1))
            k += 3
        else:
            out.append(int(fields[k]))
            k += 1
    return out


def test_nastran_bulk_and_punch(tmp_path):
    from puzzle.post.exports.nastran import write

    job = _job(tmp_path, ".bdf")
    (bdf, pch), notes = write(job)
    cards = _large_field_cards(bdf.read_text())
    grids = {int(f[0]): [float(v) for v in f[1:4]] for n, f in cards if n == "GRID"}
    assert sorted(grids) == list(IDS)
    for i, n in enumerate(IDS):
        assert grids[n] == pytest.approx(COORDS[i], rel=1e-9, abs=1e-15)
    (tetra,) = [f for n, f in cards if n == "CTETRA"]
    assert tetra[0] == "7" and [int(v) for v in tetra[2:]] == [1, 2, 3, 4, 5, 6, 7, 8, 10, 9]
    (mat,) = [f for n, f in cards if n == "MAT1"]
    # MID, E, (G blank), ν, ρ, α, TREF
    assert [float(v) for v in mat[1:]] == pytest.approx([210e9, 0.3, 7850, 12.16e-6, 293.15])
    sets = {int(f[0]): _members(f[1:]) for n, f in cards if n == "SET1"}
    assert sets == {1: list(IDS), 3: list(BASE)}
    text = bdf.read_text()
    assert "$        3  Block/Base (grids)" in text and "$        2  Block (elements)" in text
    assert "SET3" in text and notes["sets"].startswith("1 Block (grids)")

    lines = pch.read_text().splitlines()
    assert all(len(line) == 80 for line in lines)
    assert [int(line[72:]) for line in lines] == list(range(1, len(lines) + 1))
    shown = _punch_displacements(lines, "$DISPLACEMENTS")
    expected = _u(COORDS)
    for i, n in enumerate(IDS):
        assert shown[n] == pytest.approx(expected[i], rel=5e-7, abs=1e-13)
    eigen = next(line for line in lines if line.startswith("$EIGENVALUE"))
    assert float(eigen.split("=")[1].split()[0]) == pytest.approx((2 * np.pi * FREQ) ** 2, 1e-7)
    assert _punch_displacements(lines, "$EIGENVECTOR")[2] == pytest.approx(10 * expected[1], 5e-7)
    # Stresses: element 7, its centre (grid 0) then corners 1-4; σx, τxy, σ1 on the
    # grid's line, ..., mean pressure and von Mises the seventh and eighth words.
    at = lines.index(next(line for line in lines if line.startswith("$ELEMENT TYPE")))
    assert "39  TETRA" in lines[at]
    head = lines[at + 1]
    assert int(head[:10]) == 7 and int(head[18:36]) == 4
    words = _stress_block(lines[at + 2 : at + 2 + 5 * 7])
    stress = _stress(COORDS)
    for grid, row in words.items():
        want = stress[:4].mean(axis=0) if grid == 0 else stress[grid - 1]
        xx, yy, zz, xy, yz, zx = want
        vm = np.sqrt(0.5 * ((xx - yy) ** 2 + (yy - zz) ** 2 + (zz - xx) ** 2)
                     + 3 * (xy**2 + yz**2 + zx**2))  # fmt: skip
        got = [row[0], row[1], row[6], row[7], row[8], row[9], row[14], row[15]]
        assert got == pytest.approx([xx, xy, -(xx + yy + zz) / 3, vm, yy, yz, zz, zx], rel=5e-7)
        assert row[2] >= row[10] >= row[16]  # principal stresses, largest first


def _punch_displacements(lines, kind: str) -> dict[int, list[float]]:
    at = lines.index(next(line for line in lines if line.startswith(kind)))
    out = {}
    for line in lines[at + 1 :]:
        if line.startswith("$TITLE"):
            break
        if line.startswith("$") or line.startswith("-CONT-"):
            continue
        assert line[10:18].strip() == "G"
        out[int(line[:10])] = [float(line[18 + 18 * k : 36 + 18 * k]) for k in range(3)]
    return out


def _stress_block(lines) -> dict[int, list[float]]:
    out = {}
    for k in range(0, len(lines), 7):
        first = lines[k]
        grid = int(first[10:18])
        words = [float(first[18 + 18 * i : 36 + 18 * i]) for i in range(3)]
        for line in lines[k + 1 : k + 7]:
            words += [float(line[18 + 18 * i : 36 + 18 * i]) for i in range(3)
                      if line[18 + 18 * i : 36 + 18 * i].strip()]  # fmt: skip
        assert len(words) == 20
        out[grid] = words
    return out


# ---- Abaqus / CalculiX ---------------------------------------------------------------


def test_abaqus_mesh_temperatures_and_boundary(tmp_path):
    from puzzle.post.exports.abaqus import write, write_displacement

    (inp, temps), notes = write(_job(tmp_path, ".inp"))
    text = inp.read_text()
    assert "*ELEMENT, TYPE=C3D10, ELSET=E_BLOCK\n7, 1, 2, 3, 4, 5, 6, 7, 8, 10, 9\n" in text
    assert "*NSET, NSET=N_BLOCK_BASE\n1, 2, 3, 5, 6, 7,\n" in text
    assert "*ELASTIC\n2.1e+11, 0.3" in text and "*EXPANSION, ZERO=293.15" in text
    assert "*SOLID SECTION, ELSET=E_BLOCK, MATERIAL=M1" in text
    rows = [line for line in text.splitlines() if line[:1].isdigit() and line.count(",") == 3]
    xyz = {int(r.split(",")[0]): [float(v) for v in r.split(",")[1:]] for r in rows[:10]}
    assert np.allclose([xyz[n] for n in IDS], COORDS, rtol=1e-12)
    t = temps.read_text().splitlines()
    data = dict(line.split(",") for line in t[t.index("*TEMPERATURE") + 1 :])
    assert float(data["2"]) == pytest.approx(300 + 1000 * S, rel=1e-10)
    assert "10 nodes" in notes["temperatures"]

    (inp, bc), notes = write_displacement(_job(tmp_path, "_u", selection="Block/Base"))
    lines = bc.read_text().splitlines()
    rows = lines[lines.index("*BOUNDARY") + 1 :]
    assert len(rows) == 3 * len(BASE)
    node, first, last, value = rows[4].split(",")
    assert (int(node), int(first), int(last)) == (2, 2, 2)
    assert float(value) == pytest.approx(_u(COORDS)[1, 1], rel=1e-12)


def test_abaqus_without_temperatures_writes_the_mesh_only(tmp_path):
    from puzzle.post.exports.abaqus import write

    files, notes = write(_job(tmp_path, ".inp", step=1))
    assert len(files) == 1 and notes["temperatures"].startswith("none")


# ---- glTF ----------------------------------------------------------------------------


def _read_glb(path):
    data = path.read_bytes()
    magic, version, total = struct.unpack_from("<4sII", data)
    assert (magic, version, total) == (b"glTF", 2, len(data))
    length, kind = struct.unpack_from("<I4s", data, 12)
    assert kind == b"JSON" and length % 4 == 0
    doc = json.loads(data[20 : 20 + length])
    at = 20 + length
    blen, bkind = struct.unpack_from("<I4s", data, at)
    assert bkind == b"BIN\x00" and blen % 4 == 0 and at % 4 == 0
    return doc, data[at + 8 : at + 8 + blen]


def _accessor(doc, blob, index):
    a = doc["accessors"][index]
    v = doc["bufferViews"][a["bufferView"]]
    dtype = {5126: np.float32, 5125: np.uint32}[a["componentType"]]
    width = {"SCALAR": 1, "VEC3": 3}[a["type"]]
    raw = np.frombuffer(blob, dtype, a["count"] * width, v["byteOffset"])
    return raw.reshape(a["count"], width) if width > 1 else raw


def test_gltf_is_valid_and_vtk_reads_it(tmp_path):
    import matplotlib
    from vtkmodules.vtkIOGeometry import vtkGLTFReader

    from puzzle.post.exports.gltf import srgb_to_linear, write

    job = _job(tmp_path, ".glb", quantity="temperature:value", scale=0.0)
    (glb,), notes = write(job)
    doc, blob = _read_glb(glb)
    assert doc["asset"]["version"] == "2.0"
    legend = doc["asset"]["extras"]["legend"]
    assert (legend["min"], legend["max"]) == pytest.approx((300, 300 + 1000 * S))
    assert legend["unit"] == "K" and legend["colormap"] == "turbo"
    for v in doc["bufferViews"]:
        assert v["byteOffset"] % 4 == 0 and v["byteOffset"] + v["byteLength"] <= len(blob)
    assert doc["buffers"][0]["byteLength"] == len(blob)
    (mesh,) = doc["meshes"]
    prim = mesh["primitives"][0]
    points = _accessor(doc, blob, prim["attributes"]["POSITION"])
    a = doc["accessors"][prim["attributes"]["POSITION"]]
    assert a["min"] == list(points.min(axis=0)) and a["max"] == list(points.max(axis=0))
    triangles = _accessor(doc, blob, prim["indices"]).reshape(-1, 3)
    assert len(points) == 10 and len(triangles) == 16  # 4 six-node faces, split in four
    assert triangles.max() < len(points)

    rgb = _accessor(doc, blob, prim["attributes"]["COLOR_0"])
    values = _accessor(doc, blob, prim["attributes"]["_VALUE"])
    turbo = matplotlib.colormaps["turbo"]
    assert rgb[values.argmin()] == pytest.approx(srgb_to_linear(turbo(0.0)[:3]), abs=1e-6)
    assert rgb[values.argmax()] == pytest.approx(srgb_to_linear(turbo(1.0)[:3]), abs=1e-6)

    reader = vtkGLTFReader()
    reader.SetFileName(str(glb))
    reader.Update()
    blocks = reader.GetOutput()
    it = blocks.NewIterator()
    it.InitTraversal()
    read = it.GetCurrentDataObject()
    assert read.GetNumberOfPoints() == 10 and read.GetNumberOfCells() == 16


def test_gltf_deforms_by_the_scale(tmp_path):
    from puzzle.post.exports.gltf import write

    (glb,), _ = write(_job(tmp_path, ".glb", quantity="displacement:y", scale=100.0))
    doc, blob = _read_glb(glb)
    prim = doc["meshes"][0]["primitives"][0]
    points = _accessor(doc, blob, prim["attributes"]["POSITION"])
    moved = COORDS + 100 * _u(COORDS)
    assert points.min(axis=0) == pytest.approx(moved.min(axis=0), rel=1e-6)


# ---- Lorentz force -------------------------------------------------------------------


def test_lorentz_force_is_j_cross_b(tmp_path):
    from puzzle.post.exports.lorentz import write

    rng = np.random.default_rng(1)
    j, b = rng.normal(size=(10, 3)) * 1e6, rng.normal(size=(10, 3))
    step = StepResult(1, "stationary", 1.0, fields={
        "current_density": Field("current_density", "node", ["x", "y", "z"], IDS, j),
        "magnetic_flux_density": Field("magnetic_flux_density", "node", ["x", "y", "z"],
                                       IDS[::-1].copy(), b[::-1].copy()),
    })  # fmt: skip
    job = _job(tmp_path, ".csv")
    job.step = step
    (csv,), notes = write(job)
    header = csv.read_text().splitlines()[0]
    assert header == "x [m],y [m],z [m],f_x [N/m^3],f_y [N/m^3],f_z [N/m^3],|f| [N/m^3]"
    rows = np.loadtxt(csv, delimiter=",", skiprows=1)
    assert rows[:, :3] == pytest.approx(COORDS)
    exact = np.column_stack([j[:, 1] * b[:, 2] - j[:, 2] * b[:, 1],
                             j[:, 2] * b[:, 0] - j[:, 0] * b[:, 2],
                             j[:, 0] * b[:, 1] - j[:, 1] * b[:, 0]])  # fmt: skip
    assert rows[:, 3:6] == pytest.approx(exact, rel=1e-8)
    assert rows[:, 6] == pytest.approx(np.linalg.norm(exact, axis=1), rel=1e-8)

    del step.fields["magnetic_flux_density"]
    with pytest.raises(PuzzleError, match="no magnetic flux density B"):
        write(job)
