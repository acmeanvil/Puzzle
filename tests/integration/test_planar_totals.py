"""Totals in 2D models mean the whole part (ADR 0048 decision 6), on CalculiX and
Code_Aster (the solvers that write reaction forces):

- **axisymmetric:** a tube a = 10, b = 20 mm, 50 mm long, held at its foot and pulled by
  a pressure p on its top ring: the foot's reaction is p π (b² - a²), the full 360°;
- **plane strain:** a 40 x 20 mm strip pressed by p on its top: the foot's reaction is
  p W per metre of depth;
- **plane stress:** the same strip 2 mm thick, pulled by p at its end: the held end's
  reaction is p H t.

Each total is summed by the reaction tool (`post.probes.reaction`) and by
`results.query.total`, which both read the stored `reaction_force`.
"""

import math

import pytest
from solvers import over
from test_planar_benchmarks import SIDES, _project, _rectangle, _solve, _structure, mm, qty

from puzzle.model import Feature
from puzzle.model.schema import ShellSection
from puzzle.post.probes import reaction
from puzzle.results.query import total

pytest.importorskip("OCP")

P = 10e6


def _check(run, selection, axis, exact):
    step = run.results.steps[-1]
    found = reaction(run.mesh, step, selection)["components"][axis]
    t = total(run.results, run.mesh, "reaction_force", selection)
    summed = t["total"][t["components"].index(axis)]
    assert summed == pytest.approx(found, rel=1e-9)
    return found


@over("calculix", "code_aster")
def test_axisymmetric_reaction_is_the_whole_ring(tmp_path, solver):
    a, b, L = 0.01, 0.02, 0.05
    features = [
        Feature(type="displacement", selection="Part/Bottom", values={"y": mm(0)}),
        Feature(type="pressure", selection="Part/Top", values={"p": qty(-P, "Pa")}),
    ]
    p, c = _project([_rectangle("S", a, 0, b - a, L)], SIDES, "2d_axisymmetric",
                    _structure(features), size=2.0)  # fmt: skip
    run = _solve(tmp_path, p, c, solver)
    exact = -P * math.pi * (b**2 - a**2)
    found = _check(run, "Part/Bottom", "y", exact)
    print(f"{solver}: axisymmetric foot reaction {found:.6g} N (full ring {exact:.6g})")
    assert found == pytest.approx(exact, rel=2e-3)


@over("calculix", "code_aster")
def test_plane_strain_reaction_is_per_metre(tmp_path, solver):
    W, H = 0.04, 0.02
    features = [
        Feature(type="displacement", selection="Part/Bottom", values={"y": mm(0)}),
        Feature(type="displacement", selection="Part/Left", values={"x": mm(0)}),
        Feature(type="pressure", selection="Part/Top", values={"p": qty(P, "Pa")}),
    ]
    p, c = _project([_rectangle("S", 0, 0, W, H)], SIDES, "2d_planar", _structure(features),
                    planar_model="plane_strain")  # fmt: skip
    run = _solve(tmp_path, p, c, solver)
    exact = P * W
    found = _check(run, "Part/Bottom", "y", exact)
    print(f"{solver}: plane strain foot reaction {found:.6g} N/m (per metre {exact:.6g})")
    assert found == pytest.approx(exact, rel=2e-3)


@over("calculix", "code_aster")
def test_plane_stress_reaction_is_the_parts_thickness(tmp_path, solver):
    L, H, t = 0.04, 0.02, 0.002
    features = [
        Feature(type="displacement", selection="Part/Left", values={"x": mm(0)}),
        Feature(type="displacement", selection="Part/Bottom", values={"y": mm(0)}),
        Feature(type="pressure", selection="Part/Right", values={"p": qty(-P, "Pa")}),
    ]
    p, c = _project([_rectangle("S", 0, 0, L, H)], SIDES, "2d_planar", _structure(features),
                    shell_sections=[ShellSection(selection="Part", thickness=mm(2))])  # fmt: skip
    run = _solve(tmp_path, p, c, solver)
    exact = -P * H * t
    found = _check(run, "Part/Left", "x", exact)
    print(f"{solver}: plane stress end reaction {found:.6g} N (thickness {exact:.6g})")
    assert found == pytest.approx(exact, rel=2e-3)


@over("calculix", "code_aster")
def test_result_tools_on_an_axisymmetric_cylinder(tmp_path, solver):
    """Kept result tools on the Lamé cylinder (a = 10, b = 20 mm, 100 MPa inside, its
    ends held axially): the linearized hoop membrane through the wall is p a / (b - a)
    = 100 MPa exactly (∫ σθ dr = p a), the hoop being the out-of-plane component; the
    axial force across a cut and the foot's reaction are 2 ν A π (b² - a²) for the full
    ring (σ_axial = ν (σr + σθ) = 2 ν A, A = p a² / (b² - a²))."""
    from test_planar_benchmarks import A_IN, B_OUT, NU
    from test_planar_benchmarks import P as PRESSURE

    from puzzle.model.schema import ResultProbe
    from puzzle.post.probes import evaluate
    from puzzle.project.layout import RESULTS_DIR
    from puzzle.results.store import ResultStore

    h = 0.01
    features = [
        Feature(type="displacement", selection="Part/Bottom", values={"y": mm(0)}),
        Feature(type="displacement", selection="Part/Top", values={"y": mm(0)}),
        Feature(type="pressure", selection="Part/Left", values={"p": qty(PRESSURE, "Pa")}),
    ]  # fmt: skip
    p, c = _project([_rectangle("S", A_IN, 0, B_OUT - A_IN, h)], SIDES, "2d_axisymmetric",
                    _structure(features), size=0.5)  # fmt: skip
    from puzzle.model import SolveStep, Study
    from puzzle.solve import run_study

    study = p.add(Study(name="s", component=c.id, solver=solver,
                        steps=[SolveStep(type="stationary")]))  # fmt: skip
    store = ResultStore(tmp_path / RESULTS_DIR)
    run_study(p, study.id, tmp_path, tmp_path / "run", store=store, timeout_s=600)
    wall = ResultProbe(name="Wall", source=study.id, type="linearized", quantity="stress:von_mises",
                       point=(A_IN, h / 2, 0.0), end=(B_OUT, h / 2, 0.0), components="line",
                       samples=41)  # fmt: skip
    found = evaluate(p, store, wall)
    hoop = found["parts"]["start"]["membrane"]["components"]["hh"]
    print(f"{solver}: linearized hoop membrane {hoop / 1e6:.3f} MPa (exact 100)")
    assert hoop == pytest.approx(PRESSURE * A_IN / (B_OUT - A_IN), rel=5e-3)

    A = PRESSURE * A_IN**2 / (B_OUT**2 - A_IN**2)
    axial = 2 * NU * A * math.pi * (B_OUT**2 - A_IN**2)
    from puzzle.model.schema import ConstructionGeometry

    plane = p.add(ConstructionGeometry(name="Mid", type="plane", origin=(0.015, h / 2, 0.0),
                                       normal=(0.0, 1.0, 0.0)))  # fmt: skip
    cut = ResultProbe(name="Cut", source=study.id, type="section", quantity="stress:von_mises",
                      construction=plane.id)  # fmt: skip
    found = evaluate(p, store, cut)
    print(f"{solver}: axial force across the cut {found['normal_force']:.6g} N (exact {axial:.6g})")
    assert abs(found["normal_force"]) == pytest.approx(axial, rel=5e-3)
    assert found["totals"] == "for the full 360°"
    foot = ResultProbe(name="Foot", source=study.id, type="reaction",
                       quantity="reaction_force:total", selection="Part/Bottom")  # fmt: skip
    found = evaluate(p, store, foot)
    print(f"{solver}: foot reaction {found['components']['y']:.6g} N (exact ±{axial:.6g})")
    assert abs(found["components"]["y"]) == pytest.approx(axial, rel=5e-3)
    assert found["totals"] == "for the full 360°"
