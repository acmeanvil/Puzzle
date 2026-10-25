"""Result tools (ADR 0042) on a cantilever against statics and beam theory.

A 200 x 10 x 10 mm steel bar, clamped at x = 0, 100 N down (-y) at the tip (CalculiX):

- **Reaction** on the clamped face: the force is the load (exact by equilibrium, to the
  solver's printed digits), and its moment about the face's centroid is P L = 20 N m
  about z.
- **Over a selection:** the tip face's mean deflection is P L³ / 3 E I within 3 %
  (shear and the clamp's give make it a little more), its integral is the mean times
  the face's area, and its max lies on the face.
- **Path:** the deflection along the axis follows the beam's curve P x² (3L - x) / 6 E I
  within 3 % beyond the first quarter.
"""

import numpy as np
import pytest
from conftest_geometry import BAR, bar_project, write_bar_step
from solvers import over

from puzzle.model.schema import Feature, Physics, Quantity, ResultProbe, SolveStep, Study
from puzzle.post import probes
from puzzle.project import save_project
from puzzle.results.store import ResultStore
from puzzle.solve import run_study

P, E = 100.0, 210e9
L, B = BAR[0], BAR[1]
I = B**4 / 12  # noqa: E741
DY = "displacement:y"


@pytest.fixture(scope="module")
def solved(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("tools")
    physics = Physics(
        type="solid_mechanics",
        features=[
            Feature(type="fixed", selection="Bar/Root"),
            Feature(type="force", selection="Bar/Tip", values={"y": Quantity(value=-P, unit="N")}),
        ],
        settings={"temperature_dependent_materials": False},
    )
    root = tmp / "p"
    root.mkdir()
    project, comp = bar_project(write_bar_step(root / "bar.step"), [physics], mesh_size_mm=2.5)
    steps = [SolveStep(type="stationary")]
    study = project.add(Study(name="Bend", component=comp.id, solver="calculix", steps=steps))
    save_project(project, root)
    store = ResultStore(root / "results")
    run_study(project, study.id, root, tmp / "w", store=store)
    return project, store, study


def _probe(study, **kw):
    return ResultProbe(name="t", source=study.id, **kw)


@over("calculix")
def test_reaction_balances_the_load(solved, solver):
    project, store, study = solved
    r = probes.evaluate(project, store, _probe(study, type="reaction", selection="Bar/Root"))
    print(f"reaction {r['components']} N, moment {r['moment']} N m about {r['about']}")
    # Exact to the solver's printed digits (the .frd keeps about 5 per node).
    assert r["components"]["y"] == pytest.approx(P, rel=1e-5)
    assert abs(r["components"]["x"]) < 1e-4 * P and abs(r["components"]["z"]) < 1e-4 * P
    assert r["about"] == pytest.approx([0.0, B / 2, B / 2], abs=1e-9)  # the face's centroid
    assert r["moment"]["z"] == pytest.approx(P * L, rel=1e-5)
    assert r["value"] == pytest.approx(P, rel=1e-5) and r["unit"] == "force"
    # About another point: the same force, the moment moved by r x F.
    far = probes.evaluate(
        project,
        store,
        _probe(study, type="reaction", selection="Bar/Root", about=(L, B / 2, B / 2)),
    )
    assert far["moment"]["z"] == pytest.approx(0.0, abs=1e-4 * P * L)


@over("calculix")
def test_results_over_a_selection(solved, solver):
    project, store, study = solved
    beam = P * L**3 / (3 * E * I)
    tip = {
        how: probes.evaluate(
            project,
            store,
            _probe(
                study,
                type="selection",
                selection="Bar/Tip",
                quantity="displacement:y",
                reduction=how,
            ),
        )
        for how in ("mean", "integral", "min", "max")
    }
    print(f"tip mean deflection {tip['mean']['value']:.6g} m, beam theory {-beam:.6g} m")
    assert tip["mean"]["value"] == pytest.approx(-beam, rel=3e-2)
    assert tip["mean"]["measure"] == pytest.approx(B * B, rel=1e-9)
    assert tip["integral"]["value"] == pytest.approx(tip["mean"]["value"] * B * B, rel=1e-9)
    assert tip["min"]["value"] <= tip["mean"]["value"] <= tip["max"]["value"]
    assert tip["min"]["point"][0] == pytest.approx(L)
    with pytest.raises(Exception, match="no selection 'Bar/Nope'"):
        probes.evaluate(project, store, _probe(study, type="selection", selection="Bar/Nope"))


@over("calculix")
def test_path_follows_the_deflection_curve(solved, solver):
    project, store, study = solved
    probe = _probe(
        study,
        type="path",
        quantity="displacement:y",
        point=(0.0, B / 2, B / 2),
        end=(L, B / 2, B / 2),
        samples=21,
    )
    p = probes.evaluate(project, store, probe)
    x, v = np.array(p["distance"]), np.array(p["values"])
    curve = -P * x**2 * (3 * L - x) / (6 * E * I)
    far = x >= L / 4
    worst = float(np.max(np.abs(v[far] / curve[far] - 1)))
    print(f"path: {len(x)} points, worst {100 * worst:.2f} % from beam theory beyond L/4")
    assert p["outside"] == 0 and worst < 3e-2
    assert p["value"] == pytest.approx(v[-1])  # the largest is at the tip
    # Over steps (one static step): one value, the same.
    (h,) = probes.history(project, store, probe)
    assert h["value"] == pytest.approx(p["value"])


@over("calculix")
def test_the_outline_names_every_tool(solved, solver):
    from puzzle.commands.outline import outline

    project, _store, study = solved
    for probe in (
        _probe(study, type="reaction", selection="Bar/Root"),
        _probe(study, type="selection", selection="Bar/Tip", quantity=DY, reduction="mean"),
        _probe(study, type="path", quantity=DY, point=(0, 0, 0), end=(L, 0, 0), samples=5),
    ):
        project.add(probe.model_copy(update={"name": f"Tool {probe.type}"}))
    text = outline(project)
    assert "reaction on Bar/Root of Bend" in text
    assert "mean of" in text and "on Bar/Tip of Bend" in text
    assert "along (0, 0, 0) m to (0.2, 0, 0) m, 5 points" in text
