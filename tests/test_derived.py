"""Derived results (ADR 0025) on hand-made results: combinations and equations.

Two load cases on three nodes. Case A pulls along x (σxx = 100 MPa), case B along y
(σyy = 100 MPa). Their sum is equal biaxial tension, whose von Mises stress is 100 MPa,
not 200: von Mises is taken after combining, as Ansys does.
"""

from datetime import datetime
from uuid import uuid4

import numpy as np
import pytest

from puzzle.errors import PuzzleError
from puzzle.mesh import Mesh
from puzzle.model.schema import (
    ParameterSet,
    Project,
    Quantity,
    ResultCase,
    ResultDefinition,
    Study,
)
from puzzle.post import derived
from puzzle.post.quantities import catalog
from puzzle.results.model import Field, Results, StepResult
from puzzle.results.store import RunRecord

IDS = np.array([1, 2, 3])
MPA = 1e6


def mesh():
    return Mesh(IDS.copy(), np.array([[0.0, 0, 0], [1, 0, 0], [2, 0, 0]]))


def step(sxx, syy, uy):
    zeros = np.zeros(3)
    stress = np.stack([sxx, syy, zeros, zeros, zeros, zeros], axis=1) * MPA
    u = np.stack([zeros, uy, zeros], axis=1)
    return StepResult(
        1,
        "stationary",
        1.0,
        fields={
            "stress": Field("stress", "node", ["xx", "yy", "zz", "xy", "yz", "zx"], IDS, stress),
            "displacement": Field("displacement", "node", ["x", "y", "z"], IDS, u),
        },
    )


A = step(np.full(3, 100.0), np.zeros(3), np.array([0.0, -1e-3, -2e-3]))
B = step(np.zeros(3), np.full(3, 100.0), np.array([0.0, 1e-3, 3e-3]))


def von_mises(results):
    item = next(i for i in catalog([("stress", "node", ["xx", "yy", "zz", "xy", "yz", "zx"])]))
    from puzzle.post.quantities import compute

    f = results.steps[0].fields["stress"]
    return compute(item, f.values, f.components) / MPA


def test_linear_takes_von_mises_after_combining():
    _, r, items, _ = derived.combine("linear", [(mesh(), A, 1.0), (mesh(), B, 1.0)])
    assert von_mises(r) == pytest.approx([100.0] * 3)  # equal biaxial, not 200
    _, r, _, _ = derived.combine("linear", [(mesh(), A, 2.0), (mesh(), A, -0.5)])
    assert r.steps[0].fields["displacement"].values[:, 1] == pytest.approx([0, -1.5e-3, -3e-3])
    assert "stress:von_mises" in {i.key for i in items}


def test_srss_range_and_envelope():
    _, r, _, _ = derived.combine("srss", [(mesh(), A, 1.0), (mesh(), A, 1.0)])
    assert r.steps[0].fields["displacement"].values[2, 1] == pytest.approx(2e-3 * np.sqrt(2))

    _, r, items, _ = derived.combine("range", [(mesh(), A, 1.0), (mesh(), B, 1.0)])
    f = r.steps[0].fields
    assert f["stress_range"].values[0, :2] / MPA == pytest.approx([100, -100])  # A − B
    assert f["stress_mean"].values[0, :2] / MPA == pytest.approx([50, 50])
    shown = {i.key: i for i in items}
    assert shown["stress_range:von_mises"].label == "Equivalent (von Mises) stress (range)"
    assert shown["stress_range:von_mises"].unit == "pressure"
    assert von_mises(
        Results("x", [StepResult(1, "s", 0, fields={"stress": f["stress_range"]})])
    ) == (
        pytest.approx([np.sqrt(3) * 100] * 3)  # the range of a pure shear swing
    )

    _, r, items, _ = derived.combine(
        "envelope", [(mesh(), A, 1.0), (mesh(), B, 2.0)], "displacement:y", "max"
    )
    f = r.steps[0].fields
    assert f["envelope"].values[:, 0] == pytest.approx([0, 2e-3, 6e-3])
    assert f["envelope_case"].values[1:, 0] == pytest.approx([2, 2])
    assert items[0].label == "Maximum deformation Y over the cases" and items[0].unit == "length"
    with pytest.raises(PuzzleError, match="no result 'stress:nope'"):
        derived.combine("envelope", [(mesh(), A, 1.0)], "stress:nope")


def test_equations_are_a_small_safe_language():
    v = {"von_mises": np.array([1e8, 2e8]), "deformation": np.array([1.0, 3.0]), "t": 0.5}
    assert derived.evaluate_expression("von_mises / 1e6", v) == pytest.approx([100, 200])
    assert derived.evaluate_expression(
        "where(deformation > max(deformation) * t, 1, 0)", v
    ) == pytest.approx([0, 1])
    assert derived.evaluate_expression("max(von_mises, 1.5e8) / 1e6", v) == pytest.approx(
        [150, 200]
    )
    assert derived.evaluate_expression("sqrt(deformation) * pi", v)[1] == pytest.approx(
        np.sqrt(3) * np.pi
    )
    with pytest.raises(PuzzleError, match="did you mean von_mises"):
        derived.evaluate_expression("von_mise * 2", v)
    for bad in ("__import__('os')", "deformation.real", "[1, 2]", "open('x')", "lambda: 1"):
        with pytest.raises(PuzzleError):
            derived.evaluate_expression(bad, v)


class FakeStore:
    """Two runs of one study (an older and a newer), in memory."""

    def __init__(self, study_id):
        def record(run_id, when):
            return RunRecord(
                run_id,
                "k",
                str(study_id),
                "Loads",
                "calculix",
                "native",
                "m",
                "completed",
                datetime(2026, 10, when),
                1.0,
                1,
                datetime(2026, 10, when),
            )

        self.records = [record("old", 1), record("new", 2)]
        self.results = {"old": Results("calculix", [B]), "new": Results("calculix", [A, B])}

    def runs(self):
        return self.records

    def read_results(self, run_id):
        return self.results[run_id]

    def read_mesh(self, mesh_id):
        return mesh()


def test_definitions_evaluate_from_the_newest_run():
    p = Project("p")
    study = p.add(Study(name="Loads", component=uuid4(), solver="calculix", steps=[]))
    p.add(ParameterSet(name="Limits", values={"allowable": Quantity(value=250, unit="MPa")}))
    store = FakeStore(study.id)
    combo = p.add(
        ResultDefinition(
            name="A plus B",
            method="linear",
            cases=[ResultCase(study=study.id, step=0), ResultCase(study=study.id, step=1)],
        )
    )
    d = derived.evaluate(p, store, combo)
    assert d.run_ids == ["new", "new"] and von_mises(d.results) == pytest.approx([100.0] * 3)

    ratio = p.add(
        ResultDefinition(
            name="Usage",
            method="equation",
            source=combo.id,
            expression="von_mises / allowable",
        )
    )
    d = derived.evaluate(p, store, ratio)
    assert d.items[0].label == "Usage"
    assert d.results.steps[0].fields["equation"].values[:, 0] == pytest.approx([0.4] * 3)

    combo.source = ratio.id  # nonsense, but a cycle must not hang
    loop = ResultDefinition(name="Loop", method="equation", source=uuid4(), expression="1")
    loop.source = loop.id
    p.add(loop)
    with pytest.raises(PuzzleError, match="depends on itself"):
        derived.evaluate(p, store, loop)
    other = p.add(Study(name="Unrun", component=uuid4(), solver="calculix", steps=[]))
    lonely = ResultDefinition(name="L", method="linear", cases=[ResultCase(study=other.id)])
    with pytest.raises(PuzzleError, match="has no run yet"):
        derived.evaluate(p, store, lonely)


def test_a_definition_must_be_complete():
    with pytest.raises(ValueError, match="needs at least one case"):
        ResultDefinition(name="x", method="srss")
    with pytest.raises(ValueError, match="quantity"):
        ResultDefinition(name="x", method="envelope", cases=[ResultCase(study=uuid4())])
    with pytest.raises(ValueError, match="source and an expression"):
        ResultDefinition(name="x", method="equation", source=uuid4(), expression=" ")
