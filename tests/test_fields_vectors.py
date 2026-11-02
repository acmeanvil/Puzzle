"""Imported fields, stage 2 (ADR 0044): vector values that rotate with their points,
consistent nodal forces of tractions and body forces, and conservative mapping.

The meshes here are boxes of tetrahedra built by hand (straight-sided, so the answers
are exact): a volume or face integral of a polynomial the elements represent comes out
to rounding.
"""

import numpy as np
import pytest

from puzzle.adapters.common import body_forces, face_shares, node_weights, traction_forces
from puzzle.errors import PuzzleError
from puzzle.fields import conserve, import_file, sniff, turn, use_values, vector_values
from puzzle.mesh.mesh import CellBlock, Group, Mesh
from puzzle.model import (
    Feature,
    FieldColumn,
    FieldPlacement,
    FieldUse,
    ImportedField,
    Project,
    Quantity,
)

# Six tetrahedra per cube (Kuhn's split), corners as offsets (i, j, k) of a cube's nodes.
KUHN = [
    [(0, 0, 0), (1, 0, 0), (1, 1, 0), (1, 1, 1)],
    [(0, 0, 0), (1, 0, 0), (1, 0, 1), (1, 1, 1)],
    [(0, 0, 0), (0, 1, 0), (1, 1, 0), (1, 1, 1)],
    [(0, 0, 0), (0, 1, 0), (0, 1, 1), (1, 1, 1)],
    [(0, 0, 0), (0, 0, 1), (1, 0, 1), (1, 1, 1)],
    [(0, 0, 0), (0, 0, 1), (0, 1, 1), (1, 1, 1)],
]
EDGES = [(0, 1), (1, 2), (2, 0), (0, 3), (2, 3), (1, 3)]  # Gmsh's tet10 order
SIZE = (0.2, 0.1, 0.05)  # m


def box(n=(4, 2, 2), quadratic=True) -> Mesh:
    """A box SIZE meshed in tets, with the groups "Box" (the body) and "Box/Top"
    (the z = SIZE[2] face, triangles)."""
    nx, ny, nz = n
    ids = {}
    coords = []

    def node(p):
        key = tuple(np.round(p, 12))
        if key not in ids:
            ids[key] = len(ids) + 1
            coords.append(p)
        return ids[key]

    h = np.asarray(SIZE) / n
    tets = []
    for i in range(nx):
        for j in range(ny):
            for k in range(nz):
                for t in KUHN:
                    corners = [np.array([i + a, j + b, k + c]) * h for a, b, c in t]
                    row = [node(p) for p in corners]
                    if quadratic:
                        row += [node((corners[a] + corners[b]) / 2) for a, b in EDGES]
                    tets.append(row)
    conn = np.array(tets, dtype=np.int64)
    xyz = np.array(coords)
    top = []  # the top face's triangles: tets' faces with all corners at z = top
    z = SIZE[2]
    for row in conn:
        for face in [(0, 1, 2), (0, 3, 1), (1, 3, 2), (2, 3, 0)]:
            c = [row[f] for f in face]
            if np.allclose(xyz[np.array(c) - 1, 2], z):
                if quadratic:
                    mids = {frozenset(EDGES[e]): row[4 + e] for e in range(6)}
                    c += [mids[frozenset((face[a], face[b]))] for a, b in ((0, 1), (1, 2), (2, 0))]
                top.append(c)
    tri = np.array(top, dtype=np.int64)
    node_ids = np.arange(1, len(xyz) + 1, dtype=np.int64)
    blocks = [
        CellBlock("tet10" if quadratic else "tet4", 3, (3, 1), np.arange(1, len(conn) + 1), conn),
        CellBlock(
            "tri6" if quadratic else "tri3",
            2,
            (2, 1),
            np.arange(len(conn) + 1, len(conn) + 1 + len(tri)),
            tri,
        ),
    ]
    groups = {
        "Box": Group("Box", 3, [(3, 1)], node_ids),
        "Box/Top": Group("Box/Top", 2, [(2, 1)], np.unique(tri)),
    }
    return Mesh(node_ids, xyz, blocks, groups)


VOLUME = SIZE[0] * SIZE[1] * SIZE[2]
AREA = SIZE[0] * SIZE[1]


@pytest.mark.parametrize("quadratic", [False, True])
def test_node_weights_add_up_to_the_volume_and_the_area(quadratic):
    mesh = box(quadratic=quadratic)
    v = node_weights(mesh.groups["Box"], mesh, "body")
    a = node_weights(mesh.groups["Box/Top"], mesh, "face")
    assert sum(v.values()) == pytest.approx(VOLUME, rel=1e-12)
    assert sum(a.values()) == pytest.approx(AREA, rel=1e-12)
    shares = face_shares(mesh.blocks[1], mesh)
    assert shares.sum() == pytest.approx(AREA, rel=1e-12)


@pytest.mark.parametrize("quadratic", [False, True])
def test_consistent_forces_integrate_the_field_exactly(quadratic):
    mesh = box(quadratic=quadratic)
    xyz = dict(zip(mesh.node_ids.tolist(), mesh.coords, strict=True))

    def linear(ids):  # f = (x, 2, -y) N/m^3: ∫ x dV = V Lx / 2, ∫ y dV = V Ly / 2
        p = np.array([xyz[int(i)] for i in ids])
        return np.column_stack([p[:, 0], np.full(len(p), 2.0), -p[:, 1]])

    total = sum(body_forces(mesh.groups["Box"], mesh, linear).values())
    expected = VOLUME * np.array([SIZE[0] / 2, 2.0, -SIZE[1] / 2])
    np.testing.assert_allclose(total, expected, rtol=1e-12)
    t = np.array([3.0, -1.0, 5.0])
    face = sum(
        traction_forces(
            mesh.groups["Box/Top"], mesh, lambda ids: np.tile(t, (len(ids), 1))
        ).values()
    )
    np.testing.assert_allclose(face, AREA * t, rtol=1e-12)
    if quadratic:  # a quadratic field too: ∫ x² dV = V Lx² / 3

        def square(ids):
            p = np.array([xyz[int(i)] for i in ids])
            return np.column_stack([p[:, 0] ** 2, 0 * p[:, 0], 0 * p[:, 0]])

        total = sum(body_forces(mesh.groups["Box"], mesh, square).values())
        assert total[0] == pytest.approx(VOLUME * SIZE[0] ** 2 / 3, rel=1e-12)


def test_a_placement_turn_is_the_rotation_vectors_follow():
    p = Project(name="t")
    f = ImportedField(name="F", file="x.csv", placement=FieldPlacement(rotation=(0, 0, 90)))
    use = FieldUse(field=f.id, column="a", placement=FieldPlacement(translation=(1, 2, 3)))
    r = turn(p, f, use)
    np.testing.assert_allclose(r @ [1.0, 0, 0], [0, 1.0, 0], atol=1e-15)
    back = FieldUse(field=f.id, column="a", placement=FieldPlacement(rotation=(0, 0, -90)))
    np.testing.assert_allclose(turn(p, f, back), np.eye(3), atol=1e-15)


def test_conserve_keeps_the_total_and_leaves_zero_alone():
    rng = np.random.default_rng(1)
    values = rng.normal(size=(50, 3))
    values[:10] = 0.0
    weights = rng.uniform(0.5, 1.5, 50)
    total = np.array([4.0, -2.0, 7.0])
    out = conserve(total, values, weights)
    np.testing.assert_allclose(weights @ out, total, rtol=1e-12)
    assert not out[:10].any()
    turned = np.array([[0.0, -1, 0], [1, 0, 0], [0, 0, 1]])  # the same after a rotation
    np.testing.assert_allclose(
        conserve(turned @ total, values @ turned.T, weights), out @ turned.T, atol=1e-12
    )


def _cloud(tmp_path, points, columns, header, length_unit="mm", name="in.csv", **extra):
    """An imported field from points (m) and columns, written in `length_unit`."""
    scale = {"mm": 1000.0, "m": 1.0}[length_unit]
    rows = [header] + [
        ",".join(f"{c:.12g}" for c in (*(p * scale), *vals))
        for p, vals in zip(points, columns, strict=True)
    ]
    path = tmp_path / name
    path.write_text("\n".join(rows) + "\n")
    rel, digest = import_file(tmp_path / "project", path)
    found = sniff(tmp_path / "project" / rel)
    return ImportedField(
        name=name,
        file=rel,
        file_hash=digest,
        columns=[FieldColumn(**c) for c in found["columns"]],
        **{k: found[k] for k in ("delimiter", "skip_rows", "header", "length_unit")},
        **extra,
    )


def _traction_field(tmp_path, turned=False, n=5):
    """A uniform traction (1, 2, 3) kPa on a grid over the box's top; `turned`: the file
    holds it turned +90° about z and moved 1 m along y, points and vectors alike."""
    g = np.linspace(0, SIZE[0], n)
    h = np.linspace(0, SIZE[1], n)
    pts = np.array([(x, y, SIZE[2]) for x in g for y in h])
    t = np.tile([1.0, 2.0, 3.0], (len(pts), 1))
    if turned:
        r = np.array([[0.0, -1, 0], [1, 0, 0], [0, 0, 1]])
        pts, t = pts @ r.T + [0, 1.0, 0], t @ r.T
    placement = (
        FieldPlacement(rotation=(0, 0, -90), about=(0, 1.0, 0), translation=(0, -1.0, 0))
        if turned
        else FieldPlacement()
    )
    f = _cloud(
        tmp_path, pts, t, "x [mm],y [mm],z [mm],tx [kPa],ty [kPa],tz [kPa]", placement=placement
    )
    return f


@pytest.mark.parametrize("turned", [False, True])
def test_a_vector_turns_with_its_points(tmp_path, turned):
    mesh = box()
    f = _traction_field(tmp_path, turned)
    p = Project(name="t")
    p.add(f)
    feature = Feature(
        type="traction",
        selection="Box/Top",
        values={},
        fields={c: FieldUse(field=f.id, column=c) for c in ("tx", "ty", "tz")},
    )
    xyz = mesh.node_coords(mesh.groups["Box/Top"].node_ids)
    out = vector_values(p, tmp_path / "project", feature, xyz)
    for c, v in zip(("tx", "ty", "tz"), (1e3, 2e3, 3e3), strict=True):
        np.testing.assert_allclose(out[c], v, rtol=1e-9)


def test_a_turned_vector_must_come_whole_from_one_field(tmp_path):
    f = _traction_field(tmp_path, turned=True)
    p = Project(name="t")
    p.add(f)
    feature = Feature(
        type="traction",
        selection="Box/Top",
        values={"tz": Quantity(value=1.0, unit="Pa")},
        fields={c: FieldUse(field=f.id, column=c) for c in ("tx", "ty")},
    )
    with pytest.raises(PuzzleError, match="must all come from one field"):
        vector_values(p, tmp_path / "project", feature, np.zeros((3, 3)))


def test_conservative_mapping_keeps_the_total_from_a_coarse_source(tmp_path):
    """A coarse wall load (3 x 2 cells with their areas, varying) onto the finer top."""
    mesh = box(n=(8, 4, 2))
    nx, ny = 3, 2
    dx, dy = SIZE[0] / nx, SIZE[1] / ny
    centres = np.array(
        [((i + 0.5) * dx, (j + 0.5) * dy, SIZE[2]) for i in range(nx) for j in range(ny)]
    )
    t = np.column_stack(
        [
            1e3 * (1 + (centres[:, 0] / SIZE[0]) ** 2),
            0 * centres[:, 0] + 50.0,
            -2e3 + 0 * centres[:, 0],
        ]
    )
    area = np.full(len(centres), dx * dy * 1e6)  # mm²
    f = _cloud(
        tmp_path,
        centres,
        np.column_stack([t, area]),
        "x [mm],y [mm],z [mm],tx [Pa],ty [Pa],tz [Pa],dA [mm^2]",
    )
    p = Project(name="t")
    p.add(f)
    uses = {
        c: FieldUse(field=f.id, column=c, mapping="conservative", weight="dA")
        for c in ("tx", "ty", "tz")
    }
    feature = Feature(type="traction", selection="Box/Top", fields=uses)
    group = mesh.groups["Box/Top"]
    ids = group.node_ids
    w = node_weights(group, mesh, "face")
    weights = np.array([w.get(int(i), 0.0) for i in ids])
    out = vector_values(p, tmp_path / "project", feature, mesh.node_coords(ids), weights)
    source = (t * (dx * dy)).sum(axis=0)
    mapped = np.column_stack([out[c] for c in ("tx", "ty", "tz")])
    np.testing.assert_allclose(weights @ mapped, source, rtol=1e-10)
    forces = traction_forces(group, mesh, lambda q: mapped[np.searchsorted(ids, q)])
    np.testing.assert_allclose(sum(forces.values()), source, rtol=1e-10)
    # without the conservative step the coarse cells' linear map misses the total
    plain = vector_values(
        p,
        tmp_path / "project",
        feature.model_copy(
            update={
                "fields": {c: u.model_copy(update={"mapping": "linear"}) for c, u in uses.items()}
            }
        ),
        mesh.node_coords(ids),
    )
    missed = weights @ np.column_stack([plain[c] for c in ("tx", "ty", "tz")])
    assert abs(missed[0] - source[0]) / abs(source[0]) > 1e-3


def test_conservative_mapping_without_weights_or_for_a_temperature_is_refused(tmp_path):
    mesh = box()
    f = _traction_field(tmp_path)
    p = Project(name="t")
    p.add(f)
    uses = {c: FieldUse(field=f.id, column=c, mapping="conservative") for c in ("tx", "ty", "tz")}
    feature = Feature(type="traction", selection="Box/Top", fields=uses)
    xyz = mesh.node_coords(mesh.groups["Box/Top"].node_ids)
    with pytest.raises(PuzzleError, match="needs each point's area"):
        vector_values(p, tmp_path / "project", feature, xyz, np.ones(len(xyz)))
    with pytest.raises(PuzzleError, match="force-type values only"):
        use_values(p, tmp_path / "project", uses["tx"], xyz, np.ones(len(xyz)), None)


def test_a_scalar_conservative_pressure_keeps_its_total(tmp_path):
    mesh = box(n=(6, 3, 2))
    pts = np.array([(x, y, SIZE[2]) for x in (0.02, 0.1, 0.18) for y in (0.025, 0.075)])
    p_values = 1e5 * (1 + pts[:, 0])
    cell = (SIZE[0] / 3) * (SIZE[1] / 2)
    f = _cloud(
        tmp_path,
        pts,
        np.column_stack([p_values, np.full(6, cell)]),
        "x [m],y [m],z [m],p [Pa],dA",
        length_unit="m",
    )
    p = Project(name="t")
    p.add(f)
    use = FieldUse(field=f.id, column="p", mapping="conservative", weight="dA")
    group = mesh.groups["Box/Top"]
    w = node_weights(group, mesh, "face")
    weights = np.array([w[int(i)] for i in group.node_ids])
    values, _ = use_values(
        p, tmp_path / "project", use, mesh.node_coords(group.node_ids), weights, "face"
    )
    assert weights @ values == pytest.approx(p_values.sum() * cell, rel=1e-10)


def test_new_features_and_their_field_values_are_listed_per_solver():
    from puzzle.adapters import available_adapters
    from puzzle.model.catalog import spec

    assert spec("solid_mechanics", "body_force").entity == "body"
    assert set(spec("solid_mechanics", "traction").values) == {"tx", "ty", "tz"}
    adapters = available_adapters()
    for name in ("calculix", "code_aster"):
        caps = adapters[name].capabilities
        assert {"body_force", "traction"} <= caps.features
        assert {"traction:tx", "body_force:fz", "displacement:y"} <= caps.field_features


def test_a_weight_left_at_its_default_keeps_older_hashes():
    from puzzle.model import Component, PartInstance, Physics
    from puzzle.project.graph import _own

    f = ImportedField(name="F", file="x.csv")
    use = FieldUse(field=f.id, column="p")
    feature = Feature(type="pressure", selection="A/B", fields={"p": use})
    comp = Component(
        name="C",
        parts=[PartInstance(name="A", geometry=f.id)],
        mesh=f.id,
        physics=[Physics(type="solid_mechanics", features=[feature])],
    )
    data = _own(comp)
    assert "weight" not in data["physics"][0]["features"][0]["fields"]["p"]


def _rectangle_2d(x0, x1, h, n=4) -> Mesh:
    """A 2D model's rectangle [x0, x1] x [0, h] at z = 0 in tri3s (n x n squares, two
    each), with the groups "P/Region" and "P/Right" (the x = x1 edge, line2s)."""
    xs, ys = np.linspace(x0, x1, n + 1), np.linspace(0.0, h, n + 1)
    coords = np.array([[x, y, 0.0] for y in ys for x in xs])

    def nid(i, j):
        return j * (n + 1) + i + 1

    tris = []
    for i in range(n):
        for j in range(n):
            a, b, c, d = nid(i, j), nid(i + 1, j), nid(i + 1, j + 1), nid(i, j + 1)
            tris += [[a, b, c], [a, c, d]]
    lines = [[nid(n, j), nid(n, j + 1)] for j in range(n)]
    region = CellBlock("tri3", 2, (2, 1), np.arange(1, len(tris) + 1), np.array(tris))
    edge = CellBlock("line2", 1, (1, 1), np.arange(1, n + 1) + len(tris), np.array(lines))
    ids = np.arange(1, len(coords) + 1)
    groups = {
        "P/Region": Group("P/Region", 2, [(2, 1)], ids),
        "P/Right": Group("P/Right", 1, [(1, 1)], np.unique(lines)),
    }
    return Mesh(ids, coords, [region, edge], groups, planar=True)


@pytest.mark.parametrize("dimension", ["2d_planar", "2d_axisymmetric"])
def test_2d_loads_are_whole_for_calculix_and_per_unit_for_code_aster(dimension):
    """ADR 0048: a traction on an edge and a body force on the region sum to their
    integrals times the thickness (plane stress) or 2 pi r (axisymmetric); Code_Aster's
    are per metre of depth or per radian."""
    from puzzle.model import Component, PartInstance
    from puzzle.model.schema import ShellSection

    a, b, h, t, c = 0.01, 0.02, 0.01, 0.002, 1e9
    mesh = _rectangle_2d(a, b, h)
    mesh.axisymmetric = dimension == "2d_axisymmetric"
    some = ImportedField(name="F", file="x.csv").id  # any id: nothing is looked up
    thick = ShellSection(selection="P", thickness=Quantity(value=2, unit="mm"))
    comp = Component(name="C", dimension=dimension, mesh=some, shell_sections=[thick],
                     parts=[PartInstance(name="P", geometry=some)])  # fmt: skip
    edge, region = mesh.groups["P/Right"], mesh.groups["P/Region"]

    def shear(ids):
        return np.tile([5e6, 1e6, 0.0], (len(ids), 1))

    def radial(ids):
        return np.column_stack([c * mesh.node_coords(ids)[:, 0], 0 * ids, 0 * ids])

    pull = sum(traction_forces(edge, mesh, shear, comp).values())
    each = sum(traction_forces(edge, mesh, shear, comp, per_unit=True).values())
    spin = sum(body_forces(region, mesh, radial, comp).values())
    if mesh.axisymmetric:  # ∫ t 2πr dl at r = b; ∫ c r 2πr dA = 2π c h (b³ - a³) / 3
        np.testing.assert_allclose(pull, np.array([5e6, 1e6, 0]) * 2 * np.pi * b * h)
        np.testing.assert_allclose(each, np.array([5e6, 1e6, 0]) * b * h)
        np.testing.assert_allclose(spin[0], 2 * np.pi * c * h * (b**3 - a**3) / 3)
    else:  # ∫ t dl and ∫ c x dA, times the thickness
        np.testing.assert_allclose(pull, np.array([5e6, 1e6, 0]) * h * t)
        np.testing.assert_allclose(each, np.array([5e6, 1e6, 0]) * h)
        np.testing.assert_allclose(spin[0], c * h * (b**2 - a**2) / 2 * t)
    with pytest.raises(PuzzleError, match="conservative mapping in a 2D model"):
        node_weights(region, mesh, "body")
