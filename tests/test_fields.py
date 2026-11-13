"""Imported fields (ADR 0044): reading, units, placement, mapping and the check."""

import numpy as np
import pytest

from puzzle.fields import check, cloud, import_file, map_values, sniff, use_values
from puzzle.model import (
    FieldColumn,
    FieldPlacement,
    FieldUse,
    ImportedField,
    Project,
    Quantity,
)


def _grid(n=6, size=0.1):
    g = np.linspace(0.0, size, n)
    x, y, z = np.meshgrid(g, g, g, indexing="ij")
    return np.column_stack([x.ravel(), y.ravel(), z.ravel()])


def _write(path, points, values, header="x [mm],y [mm],z [mm],T [degC]", scale=1000.0):
    rows = [header] + [
        ",".join(f"{c:.9g}" for c in (*(p * scale), v)) for p, v in zip(points, values, strict=True)
    ]
    path.write_text("# exported by a test\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return path


def _field(tmp_path, points, values, **extra):
    path = _write(tmp_path / "in.csv", points, values)
    rel, digest = import_file(tmp_path / "project", path)
    found = sniff(tmp_path / "project" / rel)
    f = ImportedField(name="Temps", file=rel, file_hash=digest,
                      columns=[FieldColumn(**c) for c in found["columns"]],
                      **{k: found[k] for k in ("delimiter", "skip_rows", "header", "length_unit")},
                      **extra)  # fmt: skip
    p = Project(name="fields")
    p.add(f)
    return p, f, tmp_path / "project"


def test_a_puzzle_point_cloud_is_sniffed_with_its_units(tmp_path):
    path = _write(tmp_path / "pc.csv", _grid(2), np.arange(8.0))
    found = sniff(path)
    assert found["skip_rows"] == 1 and found["header"] and found["delimiter"] == ","
    assert found["length_unit"] == "mm"
    assert [(c["name"], c["role"], c["unit"]) for c in found["columns"]] == [
        ("x", "x", ""), ("y", "y", ""), ("z", "z", ""), ("T", "value", "degC")]  # fmt: skip


def test_whitespace_tables_without_a_header(tmp_path):
    path = tmp_path / "plain.dat"
    path.write_text("0 0 0 1.5\n1 0 0 2.5\n0 1 0 3.5\n", encoding="utf-8")
    found = sniff(path)
    assert found["delimiter"] is None and not found["header"]
    assert [c["role"] for c in found["columns"]] == ["x", "y", "z", "value"]


def test_units_and_placement_bring_the_points_onto_the_model(tmp_path):
    points = _grid(3)
    placement = FieldPlacement(rotation=(0.0, 0.0, 90.0), translation=(1.0, 0.0, 0.0))
    p, f, root = _field(tmp_path, points, 20.0 + 0 * points[:, 0], placement=placement)
    c = cloud(p, root, f)
    assert np.allclose(c.values["T"], 293.15)  # °C to K
    expected = points @ np.array([[0, 1, 0], [-1, 0, 0], [0, 0, 1]]) + [1.0, 0.0, 0.0]  # (-y, x, z)
    assert np.allclose(c.points, expected, atol=1e-12)


def test_a_linear_field_maps_exactly_and_the_mappings_differ_as_they_should(tmp_path):
    points = _grid(5)
    linear = 300 + 100 * points[:, 0] - 50 * points[:, 2]
    targets = np.random.default_rng(1).uniform(0.0, 0.1, (200, 3))
    exact = 300 + 100 * targets[:, 0] - 50 * targets[:, 2]
    values, dist = map_values(points, linear, targets, "linear")
    assert np.allclose(values, exact, atol=1e-9)
    assert dist.max() <= np.sqrt(3) * 0.0125 + 1e-12
    near, _ = map_values(points, linear, targets, "nearest")
    assert np.abs(near - exact).max() > 1e-3  # nearest steps, linear doesn't
    idw, _ = map_values(points, linear, targets, "inverse_distance")
    assert np.abs(idw - exact).max() < np.abs(near - exact).max() + 1e-12


def test_points_on_a_plane_interpolate_in_the_plane(tmp_path):
    """A wall's pressure from CFD: points on a face, targets on the same face."""
    g = np.linspace(0, 0.1, 6)
    x, y = np.meshgrid(g, g, indexing="ij")
    points = np.column_stack([x.ravel(), y.ravel(), np.zeros(x.size)])
    p = 1e5 + 2e5 * points[:, 1]
    targets = np.column_stack([np.full(5, 0.033), np.linspace(0.01, 0.09, 5), np.zeros(5)])
    values, _ = map_values(points, p, targets, "linear")
    assert np.allclose(values, 1e5 + 2e5 * targets[:, 1])


def test_beyond_the_data_nearest_zero_or_refused(tmp_path):
    from puzzle.errors import PuzzleError

    points, values = _grid(3), np.ones(27)
    far = np.array([[1.0, 1.0, 1.0]])
    assert map_values(points, values, far, max_distance=0.01, beyond="nearest")[0][0] == 1.0
    assert map_values(points, values, far, max_distance=0.01, beyond="zero")[0][0] == 0.0
    with pytest.raises(PuzzleError, match="more than 0.01 m"):
        map_values(points, values, far, max_distance=0.01, beyond="refuse")


def test_the_check_reports_coverage_and_warns_in_plain_words(tmp_path):
    points = _grid(4)
    p, f, root = _field(tmp_path, points, np.full(len(points), 50.0))
    on = _grid(7)  # the same box: covered
    report = check(p, root, f, on)
    assert report["points"] == 64 and not report["sparse"]
    assert report["coverage"] == pytest.approx(1.0)
    assert report["columns"]["T"]["min"] == pytest.approx(323.15)
    # A model twice as long: half of it far from the data.
    longer = _grid(7) * [2.0, 1.0, 1.0]
    report = check(p, root, f, longer, tolerance=0.005)
    assert report["coverage"] < 0.7
    assert any("more than 5 mm from any data point" in w for w in report["warnings"])


def test_a_millimetre_file_read_as_metres_is_caught(tmp_path):
    points = _grid(4)
    p, f, root = _field(tmp_path, points, np.ones(len(points)))
    f.length_unit = "m"  # the classic mistake: the file is in mm
    report = check(p, root, f, _grid(5))
    assert any("1e+03 times the target's size: check its length unit" in w
               for w in report["warnings"])  # fmt: skip


def test_a_use_scales_offsets_and_moves_the_field(tmp_path):
    points = _grid(5)
    p, f, root = _field(tmp_path, points, 20 + 1000 * points[:, 0])  # °C: 20 + x/mm
    use = FieldUse(field=f.id, column="T", scale=2.0, offset=Quantity(value=10, unit="K"),
                   placement=FieldPlacement(translation=(0.01, 0.0, 0.0)))  # fmt: skip
    targets = np.array([[0.05, 0.05, 0.05]])
    values, _ = use_values(p, root, use, targets)
    # At x = 50 mm on the model the moved field reads its x = 40 mm: 60 °C = 333.15 K.
    assert values[0] == pytest.approx(2 * 333.15 + 10)


def test_feature_field_uses_keep_older_hashes_and_stale_their_users(tmp_path):
    from puzzle.model import Component, Feature, MeshRecipe, PartInstance, Physics
    from puzzle.model.schema import Geometry
    from puzzle.project.graph import content_hashes

    p, f, _root = _field(tmp_path, _grid(2), np.ones(8))
    geo = p.add(Geometry(name="G", source="g.step"))
    mesh = p.add(MeshRecipe(name="M", global_size=Quantity(value=1, unit="mm")))
    feature = Feature(type="temperature", selection="P/Face",
                      values={"T": Quantity(value=300, unit="K")})  # fmt: skip
    c = p.add(Component(name="C", parts=[PartInstance(name="P", geometry=geo.id)], mesh=mesh.id,
                        physics=[Physics(type="heat_transfer", features=[feature])]))  # fmt: skip
    plain = content_hashes(p)[c.id]
    feature.fields["T"] = FieldUse(field=f.id, column="T")
    used = content_hashes(p)[c.id]
    assert used != plain and f.id in c.dependencies()
    f.file_hash = "changed"
    assert content_hashes(p)[c.id] != used
    feature.fields.clear()
    assert content_hashes(p)[c.id] == plain


def test_the_cli_imports_a_field_and_checks_it(tmp_path, capsys):
    from puzzle.cli import main
    from puzzle.project.store import load_project, save_project

    root = tmp_path / "proj"
    save_project(Project(name="cli"), root)
    src = _write(tmp_path / "temps.csv", _grid(3), np.full(27, 80.0))
    assert main(["import-field", str(root), str(src), "--name", "Oven",
                 "--translate", "0.5", "0", "0"]) == 0  # fmt: skip
    [f] = load_project(root).of_type(ImportedField)
    assert f.name == "Oven" and f.length_unit == "mm" and f.placement.translation[0] == 0.5
    assert (root / f.file).exists() and len(f.file_hash) == 64
    assert main(["field-check", str(root), "Oven"]) == 0
    out = capsys.readouterr().out
    assert "27 points" in out and "353.15" in out  # 80 °C in K


def test_the_agent_imports_a_field_from_inside_the_project_only(tmp_path):
    pytest.importorskip("mcp")
    import asyncio

    from puzzle.agent.mcp import build_server
    from puzzle.commands import Session
    from puzzle.project.store import save_project

    root = tmp_path / "proj"
    save_project(Project(name="agent"), root)
    (root / "fields").mkdir()
    _write(root / "fields" / "t.csv", _grid(3), np.full(27, 40.0))
    _write(tmp_path / "outside.csv", _grid(2), np.ones(8))
    server = build_server(root, "test")

    def call(name, args):
        result = asyncio.run(server.call_tool(name, args))
        return result, result.structured_content.get("result", result.structured_content)

    _, out = call("field_import", {"file": "fields/t.csv", "name": "Bake"})
    assert out["read"]["length_unit"] == "mm" and out["create"]["kind"] == "field"
    Session(root).submit(out["create"])
    _, report = call("field_check", {"field": "Bake"})
    assert report["points"] == 27 and report["columns"]["T"]["mean"] == pytest.approx(313.15)
    with pytest.raises(Exception, match="inside the project"):  # never outside it
        call("field_import", {"file": "../outside.csv"})


def test_the_outline_lists_a_field_and_its_uses(tmp_path):
    from puzzle.commands.outline import outline
    from puzzle.model import Component, Feature, MeshRecipe, PartInstance, Physics
    from puzzle.model.schema import Geometry

    p, f, _root = _field(tmp_path, _grid(2), np.ones(8))
    geo = p.add(Geometry(name="G", source="g.step"))
    mesh = p.add(MeshRecipe(name="M", global_size=Quantity(value=1, unit="mm")))
    feat = Feature(type="body_temperature", selection="P/Body",
                   values={"T": Quantity(value=20, unit="degC")},
                   fields={"T": FieldUse(field=f.id, column="T", scale=2)})  # fmt: skip
    p.add(Component(name="Oven", parts=[PartInstance(name="P", geometry=geo.id)], mesh=mesh.id,
                    physics=[Physics(type="solid_mechanics", features=[feat])]))  # fmt: skip
    text = outline(p)
    assert "Imported field Temps: fields/in.csv, values T, points in mm" in text
    assert "used by body_temperature.T on P/Body in Oven x2" in text
