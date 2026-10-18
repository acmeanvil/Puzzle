import numpy as np
import pytest
from conftest_geometry import BAR, bar_project, write_bar_step

from puzzle.errors import PuzzleError
from puzzle.mesh import generate_mesh
from puzzle.model import Physics
from puzzle.results.model import Field, Results, StepResult
from puzzle.results.query import integrate, stats, summary, value_at

L, B, H = BAR


def _bar(tmp_path, order):
    step = write_bar_step(tmp_path / "bar.step")
    project, comp = bar_project(step, [Physics(type="heat_transfer")], element_order=order)
    mesh = generate_mesh(project, comp.id, tmp_path, tmp_path / "m").mesh
    x = mesh.coords[:, 0]
    temperature = Field("temperature", "node", [""], mesh.node_ids, (300 + 100 * x)[:, None])
    stress = np.zeros((len(x), 6))
    stress[:, 0] = 1e6 * x  # uniaxial: von Mises = |sxx|
    results = Results(
        "test",
        [
            StepResult(
                1,
                "stationary",
                1.0,
                fields={
                    "temperature": temperature,
                    "stress": Field(
                        "stress",
                        "node",
                        ["xx", "yy", "zz", "xy", "yz", "zx"],
                        mesh.node_ids,
                        stress,
                    ),
                    "electric_energy": Field(
                        "electric_energy", "global", [""], np.array([0]), np.array([[2.0e-9]])
                    ),
                },
            )
        ],
    )
    return mesh, results


@pytest.mark.parametrize("order", [1, 2])
def test_integrals_of_a_linear_field_are_exact(tmp_path, order):
    mesh, results = _bar(tmp_path, order)
    body = integrate(results, mesh, "temperature", "Bar/Body")
    assert body["volume"] == pytest.approx(L * B * H, rel=1e-9)
    assert body["mean"] == pytest.approx(300 + 100 * L / 2, rel=1e-9)
    tip = integrate(results, mesh, "temperature", "Bar/Tip")
    assert tip["area"] == pytest.approx(B * H, rel=1e-9)
    assert tip["integral"] == pytest.approx((300 + 100 * L) * B * H, rel=1e-9)


def test_stats_and_values(tmp_path):
    mesh, results = _bar(tmp_path, 2)
    s = stats(results, mesh, "stress")
    assert s["of"] == "von_mises" and s["max"] == pytest.approx(1e6 * L)
    assert s["max_at"]["xyz"][0] == pytest.approx(L)
    root = stats(results, mesh, "temperature", selection="Bar/Root")
    assert root["min"] == root["max"] == pytest.approx(300)
    yy = stats(results, mesh, "stress", component="yy")
    assert yy["max"] == 0.0
    v = value_at(results, mesh, "temperature", [L / 2, None, None])
    assert v["values"][0] == pytest.approx(310) and v["nodes"] > 1


def test_summary_and_errors(tmp_path):
    mesh, results = _bar(tmp_path, 1)
    (step,) = summary(results)
    assert step["fields"]["electric_energy"] == pytest.approx(2e-9)
    assert step["fields"]["temperature"] == {
        "of": "value",
        "min": 300.0,
        "max": pytest.approx(320.0),
    }
    with pytest.raises(PuzzleError) as err:
        stats(results, mesh, "velocity")
    assert err.value.code == "not_found" and "temperature" in err.value.details["fields"]
    with pytest.raises(PuzzleError) as err:
        integrate(results, mesh, "temperature", "Bar/Nowhere")
    assert err.value.code == "not_found"
