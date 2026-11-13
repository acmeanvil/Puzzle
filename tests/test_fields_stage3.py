"""Imported fields, stage 3 (ADR 0044): initial stress tensors that turn with their
placement, values binned per element, integration points, the inputs a run makes, and
what each solver refuses. Solver runs are in tests/integration/test_imported_stage3.py."""

import numpy as np
import pytest

from puzzle.adapters.graded import binned, integration_points
from puzzle.fields import (
    STRESS,
    import_file,
    initial_stress_values,
    placement_rotation,
    rotate_tensors,
    sniff,
    stage3_inputs,
)
from puzzle.geometry.model import rotation_matrix
from puzzle.mesh.mesh import CellBlock, Group, Mesh
from puzzle.model import (
    Component,
    Feature,
    FieldColumn,
    FieldPlacement,
    FieldUse,
    ImportedField,
    MaterialAssignment,
    MeshRecipe,
    PartInstance,
    Physics,
    Project,
    Quantity,
    SolveStep,
    Study,
)
from puzzle.model.schema import Geometry, ShellSection


def qty(v, u):
    return Quantity(value=v, unit=u)


def _tensor(v):
    xx, yy, zz, xy, yz, zx = v
    return np.array([[xx, xy, zx], [xy, yy, yz], [zx, yz, zz]])


def test_a_tensor_turns_as_r_sigma_r_transposed():
    r = rotation_matrix((30.0, 40.0, 50.0))
    sigma = np.array([100.0, -20.0, 5.0, 12.0, -7.0, 3.0])
    turned = rotate_tensors(sigma[None], r)[0]
    assert _tensor(turned) == pytest.approx(r @ _tensor(sigma) @ r.T, abs=1e-12)
    assert np.trace(_tensor(turned)) == pytest.approx(np.trace(_tensor(sigma)))  # invariant
    assert rotate_tensors(turned[None], r.T)[0] == pytest.approx(sigma, abs=1e-12)
    # σxx turned +90° about z lies along y.
    quarter = rotation_matrix((0.0, 0.0, 90.0))
    assert rotate_tensors(np.array([[1.0, 0, 0, 0, 0, 0]]), quarter)[0] == pytest.approx(
        [0, 1, 0, 0, 0, 0], abs=1e-12
    )


def test_a_placement_turns_vectors_by_its_rotation_only():
    moved = FieldPlacement(translation=(1.0, 2.0, 3.0), about=(5.0, 0.0, 0.0))
    assert placement_rotation(moved) == pytest.approx(np.eye(3))
    turned = FieldPlacement(rotation=(10.0, 20.0, 30.0), about=(1.0, 1.0, 1.0))
    assert placement_rotation(turned) == pytest.approx(rotation_matrix((10.0, 20.0, 30.0)))


def test_binning_keeps_every_value_within_the_tolerance():
    values = np.linspace(100e9, 200e9, 1000)
    labels, levels, error = binned(values, 0.005)
    assert error <= 0.005
    assert np.max(np.abs(values - levels[labels]) / values) == pytest.approx(error)
    assert 60 <= len(levels) <= 75  # ln 2 / ln(1.005 / 0.995) = 69.3 bins
    same = binned(np.full(7, 3.0))
    assert len(same[1]) == 1 and same[1][0] == 3.0 and same[2] == 0.0  # a uniform field: exact


def test_shape_functions_at_the_integration_points_reproduce_a_quadratic_field():
    """tet10: the corners at CalculiX's natural (0,0,0), (1,0,0), (0,1,0), (0,0,1), edge
    nodes in Gmsh's order; a quadratic field interpolated to the points is exact."""
    corners = np.array([[0.0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]])
    edges = [(0, 1), (1, 2), (2, 0), (0, 3), (2, 3), (1, 3)]
    nodes = np.vstack([corners, [(corners[a] + corners[b]) / 2 for a, b in edges]])

    def f(p):
        return 1 + 2 * p[..., 0] - p[..., 1] + 3 * p[..., 2] ** 2 + p[..., 0] * p[..., 1]

    shape = integration_points("tet10")
    a, b = 0.138196601125011, 0.585410196624968
    points = np.array([[a, a, a], [b, a, a], [a, b, a], [a, a, b]])
    assert shape.sum(axis=1) == pytest.approx(np.ones(4))
    assert shape @ f(nodes) == pytest.approx(f(points))
    assert integration_points("tet4") @ f(corners[:, :]) == pytest.approx(f(corners).mean())
    hexes = np.array([[x, y, z] for z in (-1, 1) for y in (-1, 1) for x in (-1, 1)], float)
    hexes = hexes[[0, 1, 3, 2, 4, 5, 7, 6]]  # Gmsh's hex8 order: around the bottom, the top
    g = 0.577350269189626
    gauss = np.array([[x, y, z] for z in (-g, g) for y in (-g, g) for x in (-g, g)])

    def lin(p):
        return 2 + p[..., 0] - 4 * p[..., 1] + 0.5 * p[..., 2]

    assert integration_points("hex8") @ lin(hexes) == pytest.approx(lin(gauss))


def _stress_field(tmp_path, sigma, placement=None):
    points = np.array([[x, y, z] for x in (0, 1) for y in (0, 1) for z in (0, 1)], float)
    header = "x,y,z," + ",".join(f"{k} [MPa]" for k in STRESS)
    rows = [header] + [",".join(map(str, (*p, *sigma))) for p in points]
    path = tmp_path / "s.csv"
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    root = tmp_path / "project"
    rel, digest = import_file(root, path)
    found = sniff(root / rel)
    f = ImportedField(name="Residual", file=rel, file_hash=digest,
                      columns=[FieldColumn(**c) for c in found["columns"]],
                      placement=placement or FieldPlacement())  # fmt: skip
    p = Project(name="stage3")
    p.add(f)
    return p, f, root


def test_an_initial_stress_from_a_field_turns_with_the_fields_placement(tmp_path):
    r = rotation_matrix((0.0, 0.0, 90.0))
    in_file = [10.0, 0, 0, 0, 0, 0]  # MPa, along x of the file
    p, f, root = _stress_field(tmp_path, in_file, FieldPlacement(rotation=(0.0, 0.0, 90.0)))
    feature = Feature(type="initial_stress", selection="B",
                      fields={k: FieldUse(field=f.id, column=k) for k in STRESS})  # fmt: skip
    at = initial_stress_values(p, root, feature, np.array([[0.5, 0.5, 0.5]]))
    assert at[0] == pytest.approx(rotate_tensors(np.array([in_file]) * 1e6, r)[0], abs=1e-6)
    assert at[0][1] == pytest.approx(10e6)  # along y on the model
    plain = Feature(type="initial_stress", selection="B", values={"sxx": qty(5, "MPa")})
    assert initial_stress_values(p, root, plain, np.zeros((2, 3)))[1] == pytest.approx(
        [5e6, 0, 0, 0, 0, 0]
    )


def test_an_initial_stress_takes_its_components_from_one_placement(tmp_path):
    from puzzle.errors import PuzzleError

    p, f, root = _stress_field(tmp_path, [1, 2, 3, 4, 5, 6])
    feature = Feature(type="initial_stress", selection="B", fields={
        "sxx": FieldUse(field=f.id, column="sxx"),
        "syy": FieldUse(field=f.id, column="syy", placement={"rotation": (0, 0, 10)})})  # fmt: skip
    with pytest.raises(PuzzleError, match="one field with one placement"):
        initial_stress_values(p, root, feature, np.zeros((1, 3)))


def _one_tet_mesh():
    coords = np.array([[0.0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]])
    ids = np.array([1, 2, 3, 4])
    block = CellBlock("tet4", 3, (3, 1), np.array([10]), np.array([[1, 2, 3, 4]]))
    groups = {"P": Group("P", 3, [(3, 1)], ids), "P/Body": Group("P/Body", 3, [(3, 1)], ids)}
    return Mesh(ids, coords, [block], groups)


def _component(p, **extra):
    geo = p.add(Geometry(name="G", source="g.step"))
    mesh = p.add(MeshRecipe(name="M", global_size=qty(1, "mm")))
    return p.add(Component(name="C", parts=[PartInstance(name="P", geometry=geo.id)],
                           mesh=mesh.id, **extra))  # fmt: skip


def test_a_graded_property_reaches_the_adapter_on_its_nodes(tmp_path):
    p, f, root = _stress_field(tmp_path, [200, 0, 0, 0, 0, 0])  # 200 "MPa" read as E
    from puzzle.model.schema import Material

    steel = p.add(Material(name="S", properties={"youngs_modulus": qty(1, "GPa")}))
    use = FieldUse(field=f.id, column="sxx", scale=1000.0)  # 200 MPa × 1000 = 200 GPa
    graded = MaterialAssignment(material=steel.id, selection="P/Body",
                                fields={"youngs_modulus": use})  # fmt: skip
    comp = _component(p, materials=[graded])
    mesh = _one_tet_mesh()
    (made,) = stage3_inputs(p, root, comp, {"solid_mechanics"}, mesh)
    assert made.source == "material:P/Body:youngs_modulus" and made.role == "feature"
    assert made.field.values[:, 0] == pytest.approx(np.full(4, 200e9))
    assert stage3_inputs(p, root, comp, {"heat_transfer"}, mesh) == []  # E unused in heat
    comp.materials[0].fields = {"density": use}
    from puzzle.errors import PuzzleError

    with pytest.raises(PuzzleError, match="can't take density"):
        stage3_inputs(p, root, comp, {"solid_mechanics"}, mesh)


def test_unused_stage3_fields_keep_older_hashes_and_used_ones_stale_their_users(tmp_path):
    from puzzle.model.schema import Material
    from puzzle.project.graph import content_hashes

    p, f, _root = _stress_field(tmp_path, [1, 0, 0, 0, 0, 0])
    steel = p.add(Material(name="S", properties={"youngs_modulus": qty(200, "GPa")}))
    section = ShellSection(selection="P", thickness=qty(1, "mm"))
    assignment = MaterialAssignment(material=steel.id, selection="P")
    comp = _component(p, materials=[assignment], shell_sections=[section])
    plain = content_hashes(p)[comp.id]
    section.fields["thickness"] = FieldUse(field=f.id, column="sxx")
    with_thickness = content_hashes(p)[comp.id]
    assert with_thickness != plain and f.id in comp.dependencies()
    assignment.fields["youngs_modulus"] = FieldUse(field=f.id, column="sxx")
    assert content_hashes(p)[comp.id] not in (plain, with_thickness)
    section.fields.clear()
    assignment.fields.clear()
    assert content_hashes(p)[comp.id] == plain


def test_elmer_and_fenicsx_refuse_stage3_by_name_calculix_and_code_aster_take_it(tmp_path):
    from puzzle.adapters import available_adapters
    from puzzle.model.schema import Material

    p, f, _root = _stress_field(tmp_path, [1, 0, 0, 0, 0, 0])
    steel = p.add(Material(name="S", properties={"youngs_modulus": qty(200, "GPa"),
                                                 "poissons_ratio": qty(0.3, "")}))  # fmt: skip
    use = FieldUse(field=f.id, column="sxx")
    stress = Feature(type="initial_stress", selection="P/Body", fields={"sxx": use})
    comp = _component(
        p,
        materials=[MaterialAssignment(material=steel.id, selection="P/Body",
                                      fields={"youngs_modulus": use})],
        shell_sections=[ShellSection(selection="P", thickness=qty(1, "mm"),
                                     fields={"thickness": use})],
        physics=[Physics(type="solid_mechanics", features=[
            Feature(type="fixed", selection="P/Root"), stress])],
    )  # fmt: skip
    study = p.add(
        Study(name="s", component=comp.id, solver="calculix", steps=[SolveStep(type="stationary")])
    )
    adapters = available_adapters()
    for name in ("elmer", "fenicsx"):
        problems = " | ".join(adapters[name]().check(p, study))
        assert "initial_stress" in problems
        assert "youngs_modulus from an imported field" in problems
        assert "shell thickness from an imported field" in problems
    for name in ("calculix", "code_aster"):
        problems = adapters[name]().check(p, study)
        assert not [x for x in problems if "imported field" in x or "initial_stress" in x]
