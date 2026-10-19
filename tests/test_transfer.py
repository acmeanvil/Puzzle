import numpy as np
import pytest
from conftest_geometry import bar_project, write_bar_step

from puzzle.adapters.common import InputError
from puzzle.adapters.elmer import ElmerAdapter
from puzzle.errors import PuzzleError
from puzzle.mesh import generate_mesh
from puzzle.model import Physics, SolveStep, Study
from puzzle.results.model import Field, Results, StepResult
from puzzle.transfer import Input, map_field, pick


def _mesh(tmp_path, size_mm, order=2):
    step = write_bar_step(tmp_path / "bar.step")
    project, comp = bar_project(
        step, [Physics(type="heat_transfer")], mesh_size_mm=size_mm, element_order=order
    )
    return generate_mesh(project, comp.id, tmp_path, tmp_path / f"m{size_mm}").mesh


def _linear(mesh) -> Field:
    x, y, z = mesh.coords.T
    return Field("temperature", "node", [""], mesh.node_ids, (300 + 50 * x + 900 * z)[:, None])


def test_identical_meshes_copy_node_by_node(tmp_path):
    mesh = _mesh(tmp_path, 5)
    field = _linear(mesh)
    mapped = map_field(field, mesh, mesh)
    assert np.array_equal(mapped.ids, mesh.node_ids) and np.array_equal(mapped.values, field.values)


@pytest.mark.parametrize("order", [1, 2])
def test_interpolation_is_exact_for_linear_fields(tmp_path, order):
    coarse, fine = _mesh(tmp_path, 5, order), _mesh(tmp_path, 2.5)
    mapped = map_field(_linear(coarse), coarse, fine)
    x, y, z = fine.node_coords(mapped.ids).T
    assert np.abs(mapped.values[:, 0] - (300 + 50 * x + 900 * z)).max() < 1e-9


def test_nearest_mapping_takes_a_source_value(tmp_path):
    coarse, fine = _mesh(tmp_path, 5), _mesh(tmp_path, 2.5)
    field = _linear(coarse)
    mapped = map_field(field, coarse, fine, mapping="nearest")
    assert set(np.round(mapped.values[:, 0], 9)) <= set(np.round(field.values[:, 0], 9))


def test_errors(tmp_path):
    mesh = _mesh(tmp_path, 5)
    results = Results("x", [StepResult(1, "", 1.0, fields={"temperature": _linear(mesh)})])
    with pytest.raises(PuzzleError) as err:
        pick(results, "displacement")
    assert err.value.code == "not_found" and err.value.details["fields"] == ["temperature"]
    cells = Field("stress", "element", ["xx"], np.array([1]), np.array([[1.0]]))
    with pytest.raises(PuzzleError) as err:
        map_field(cells, mesh, mesh)
    assert err.value.code == "unsupported"


def test_adapters_refuse_inputs_they_cannot_apply(tmp_path):
    step = write_bar_step(tmp_path / "bar.step")
    project, comp = bar_project(step, [Physics(type="solid_mechanics")], mesh_size_mm=5)
    study = project.add(
        Study(name="s", component=comp.id, solver="elmer", steps=[SolveStep(type="stationary")])
    )
    mesh = generate_mesh(project, comp.id, tmp_path, tmp_path / "m").mesh
    stress = Field("stress", "node", ["xx"], mesh.node_ids, np.zeros((len(mesh.node_ids), 1)))
    load = Input(stress, "prestress", source="Temperature")  # Elmer replays no prestress
    with pytest.raises(InputError) as err:
        ElmerAdapter().write_input(project, study, mesh, tmp_path / "case", [load])
    assert err.value.code == "unsupported" and "Temperature" in err.value.message
