"""Frames, ADR 0079 step 2, on PyNite: load combinations, P-Delta and AISC's Direct
Analysis Method, against closed forms.

- Combinations: a cantilever with a dead and a live load at its tip; each combination's
  root moment is the factored sum (superposition, exact for a linear frame), and each is
  a result step of its own, named.
- P-Delta: a cantilever column of height h with an axial P and a lateral H at its top.
  The exact second-order base moment is M = H tan(kh)/k, k = √(P/EI) (Timoshenko & Gere,
  the beam-column); the first-order one H h. At P = 0.3 of Euler's π²EI/(4h²), the
  amplification is 1.4; meshed in 16 members, PyNite's P-Δ gets it within 0.5 %.
- Direct Analysis (AISC 360 C2): the same with EI × 0.8 and a notional load 0.002 P added
  to H: M = (H ± 0.002 P) tan(k'h)/k', k' = √(P/0.8EI), one combination per sense.
"""

import math

import numpy as np
import pytest
from solvers import over

from puzzle import frames
from puzzle.frames.combinations import asce7
from puzzle.model.schema import (
    BeamSection,
    Component,
    Feature,
    LoadCombination,
    MaterialAssignment,
    MeshRecipe,
    PartInstance,
    Physics,
    Project,
    Quantity,
    SolveStep,
    Study,
)
from puzzle.project import save_project
from puzzle.solve import run_study

q = lambda v, u: Quantity(value=v, unit=u)  # noqa: E731
W12 = {"shape": "library", "standard": "aisc", "designation": "W12X26"}


def _frame(nodes, members, features, orientation, size=0.25, settings=None, combos=()):
    pytest.importorskip("OCP")
    p = Project(name="frame")
    geo = p.add(frames.geometry("Frame", nodes, members))
    from puzzle.materials import add_library_material

    steel = add_library_material(p, "structural_steel")
    mesh = p.add(MeshRecipe(name="M", global_size=q(size, "m"), element_order=1))
    section = BeamSection(selection="F", orientation=orientation, **W12)
    comp = p.add(Component(
        name="Frame", parts=[PartInstance(name="F", geometry=geo.id)], mesh=mesh.id,
        materials=[MaterialAssignment(material=steel.id, selection="F")],
        beam_sections=[section],
        physics=[Physics(type="solid_mechanics", features=features,
                         settings={"temperature_dependent_materials": False})]))  # fmt: skip
    study = p.add(Study(name="S", component=comp.id, solver="pynite",
                        steps=[SolveStep(type="stationary", settings=settings or {})],
                        combinations=list(combos)))  # fmt: skip
    return p, study, steel, section


def _solve(tmp_path, p, study):
    root = tmp_path / "frame"
    save_project(p, root)
    return run_study(p, study.id, root, tmp_path / "w")


def _base_moment(step, mesh, component="y"):
    r = step.fields["reaction_moment"]
    k = int(np.argmin(np.linalg.norm(mesh.node_coords(r.ids), axis=1)))
    return abs(float(r.values[k, "xyz".index(component)]))


@over("pynite")
def test_combinations_are_factored_sums(tmp_path, solver):
    length, dead, live = 4.0, 10e3, 6e3
    tip = lambda force, case: Feature(type="force", selection="F/B", case=case,  # noqa: E731
                                      values={"z": q(-force, "N")})  # fmt: skip
    combos = [LoadCombination(name=n, factors=f) for n, f in asce7(["D", "L"])]
    assert [c.name for c in combos] == ["1.4D", "1.2D + 1.6L"]
    p, study, *_ = _frame({"A": [0, 0, 0], "B": [length, 0, 0]}, [["AB", "A", "B"]],
                          [Feature(type="fixed", selection="F/A"), tip(dead, "D"),
                           tip(live, "L")], (0.0, 1.0, 0.0), size=1.0, combos=combos)  # fmt: skip
    run = _solve(tmp_path, p, study)
    assert [s.label() for s in run.results.steps] == ["1.4D", "1.2D + 1.6L"]
    for step, c in zip(run.results.steps, combos, strict=True):
        exact = (c.factors.get("D", 0) * dead + c.factors.get("L", 0) * live) * length
        got = _base_moment(step, run.mesh)
        print(f"{c.name}: root moment {got:.6g} N m against {exact:.6g}")
        assert got == pytest.approx(exact, rel=1e-6)


def _column_project(fraction, settings):
    h, lateral = 4.0, 5e3
    probe = BeamSection(selection="F", orientation=(0.0, 1.0, 0.0), **W12)
    _a, i1, _i2, _j = frames.constants(probe)  # bent about the section's first axis (y)
    combos = [LoadCombination(name="D + W", factors={"D": 1.0, "W": 1.0})]
    features = [
        Feature(type="fixed", selection="F/A"),
        Feature(type="force", selection="F/B", case="D", values={"z": q(-1.0, "N")}),
        Feature(type="force", selection="F/B", case="W", values={"x": q(lateral, "N")}),
    ]
    p, study, steel, _section = _frame({"A": [0, 0, 0], "B": [0, 0, h]}, [["AB", "A", "B"]],
                                       features, (0.0, 1.0, 0.0), size=0.25,
                                       settings=settings, combos=combos)  # fmt: skip
    e = steel.properties["youngs_modulus"].to_si()
    ei = e * i1
    axial = fraction * math.pi**2 * ei / (4 * h**2)
    comp = next(o for o in p.objects.values() if isinstance(o, Component))
    comp.physics[0].features[1].values["z"] = q(-axial, "N")
    return p, study, h, lateral, ei, axial


@over("pynite")
def test_p_delta_amplifies_a_column(tmp_path, solver):
    p, study, h, lateral, ei, axial = _column_project(0.3, {"second_order": True})
    run = _solve(tmp_path, p, study)
    k = math.sqrt(axial / ei)
    exact = lateral * math.tan(k * h) / k
    got = _base_moment(run.results.steps[0], run.mesh)
    print(f"P-Delta: base moment {got:.6g} N m against {exact:.6g} (first order {lateral * h:.6g})")
    assert got == pytest.approx(exact, rel=5e-3)
    assert got > 1.3 * lateral * h


@over("pynite")
def test_direct_analysis_reduces_stiffness_and_adds_notional_loads(tmp_path, solver):
    p, study, h, lateral, ei, axial = _column_project(
        0.3, {"direct_analysis": True, "notional_directions": "x"}
    )
    run = _solve(tmp_path, p, study)
    labels = [s.label() for s in run.results.steps]
    assert labels == ["D + W, notional +x", "D + W, notional -x"]
    k = math.sqrt(axial / (0.8 * ei))
    for step, sign in zip(run.results.steps, (1.0, -1.0), strict=True):
        h_total = lateral + sign * 0.002 * axial
        exact = h_total * math.tan(k * h) / k
        got = _base_moment(step, run.mesh)
        print(f"{step.label()}: base moment {got:.6g} N m against {exact:.6g}")
        assert got == pytest.approx(exact, rel=5e-3)
