"""Frames of line members (ADR 0079 step 1) against closed forms, on PyNite, with the
same frames on CalculiX's and Code_Aster's beams as the cross-check.

A W12X26 (AISC) in steel, bent about its strong axis; spans of 8 m, so shear
deformation (which CalculiX and Code_Aster include, PyNite's Euler-Bernoulli members
don't) moves the indeterminate moments by about 0.6 %. PyNite's moments are exact for
nodal loads (to 1e-6 here); the beam solvers' within 1 %, and they agree with each
other to every printed digit (propped cantilever M_A 29829.5 N m on both, -0.57 %).

- Propped cantilever, P at midspan: M_A = 3PL/16, R_B = 5P/16 (and with a uniform w on
  PyNite: M_A = wL²/8, R_B = 3wL/8).
- Two-span continuous beam, P at each midspan: M_B = 3PL/16 (the three-moment equation).
- Fixed-feet portal frame, H at the beam: each base moment H h (3k + 1) / (2 (6k + 1)),
  k = (I_b/b) / (I_c/h). It neglects the beam's axial shortening, which parts the two
  base moments by about ±0.6 %; their mean holds to 0.2 %, each to 1 %.
- A released joint: a cantilever with a hinge at L carrying a simply supported span
  (P at its middle): M_A = P L/2, R_C = P/2. CalculiX and Code_Aster refuse releases.
- Self weight: a cantilever's root moment ρ A g L²/2.
"""

import numpy as np
import pytest
from solvers import over

from puzzle import frames
from puzzle.model.schema import (
    BeamSection,
    Component,
    Feature,
    MaterialAssignment,
    MemberRelease,
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

L, P = 8.0, 20e3
q = lambda v, u: Quantity(value=v, unit=u)  # noqa: E731
W12 = {"shape": "library", "standard": "aisc", "designation": "W12X26",
       "orientation": (0.0, 1.0, 0.0)}  # fmt: skip


def _project(solver, nodes, members, features, releases=()):
    pytest.importorskip("OCP")
    p = Project(name="frame")
    geo = p.add(frames.geometry("Frame", nodes, members))
    from puzzle.materials import add_library_material

    steel = add_library_material(p, "structural_steel")
    mesh = p.add(MeshRecipe(name="M", global_size=q(0.5, "m"), element_order=1))
    comp = p.add(
        Component(
            name="Frame",
            parts=[PartInstance(name="F", geometry=geo.id)],
            mesh=mesh.id,
            materials=[MaterialAssignment(material=steel.id, selection="F")],
            beam_sections=[BeamSection(selection="F", **W12)],
            member_releases=list(releases),
            physics=[Physics(type="solid_mechanics", features=features,
                             settings={"temperature_dependent_materials": False})],
        )
    )  # fmt: skip
    study = p.add(Study(name="S", component=comp.id, solver=solver,
                        steps=[SolveStep(type="stationary")]))  # fmt: skip
    return p, study, steel


def _solve(tmp_path, p, study):
    root = tmp_path / "frame"
    save_project(p, root)
    return run_study(p, study.id, root, tmp_path / "w")


def _at(run, field, point, component):
    """A field's value at the node nearest `point`."""
    f = run.results.last(field)
    xyz = run.mesh.node_coords(f.ids)
    k = int(np.argmin(np.linalg.norm(xyz - np.asarray(point), axis=1)))
    return float(f.values[k, f.components.index(component)])


def _pinned(sel):
    zero = {a: q(0.0, "m") for a in "xyz"}
    return Feature(type="displacement", selection=sel, values=zero)


def _down(sel, force=P):
    return Feature(type="force", selection=sel, values={"z": q(-force, "N")})


SOLVERS = over("pynite", "calculix", "code_aster")


@SOLVERS
def test_propped_cantilever(tmp_path, solver):
    nodes = {"A": [0, 0, 0], "M": [L / 2, 0, 0], "B": [L, 0, 0]}
    p, study, _ = _project(solver, nodes, [["AM", "A", "M"], ["MB", "M", "B"]],
                           [Feature(type="fixed", selection="F/A"), _pinned("F/B"),
                            _down("F/M")])  # fmt: skip
    run = _solve(tmp_path, p, study)
    ma = abs(_at(run, "beam_forces", (0, 0, 0), "My"))
    rb = _at(run, "reaction_force", (L, 0, 0), "z")
    print(f"{solver} propped cantilever: M_A {ma:.6g} N m (3PL/16 {3 * P * L / 16:.6g}), "
          f"R_B {rb:.6g} N (5P/16 {5 * P / 16:.6g})")  # fmt: skip
    tol = 1e-6 if solver == "pynite" else 1e-2
    assert ma == pytest.approx(3 * P * L / 16, rel=tol)
    assert rb == pytest.approx(5 * P / 16, rel=tol)


@over("pynite")
def test_propped_cantilever_uniform(tmp_path, solver):
    w = 5e3  # N/m
    nodes = {"A": [0, 0, 0], "B": [L, 0, 0]}
    load = Feature(type="line_load", selection="F/AB", values={"z": q(-w, "N/m")})
    p, study, _ = _project(solver, nodes, [["AB", "A", "B"]],
                           [Feature(type="fixed", selection="F/A"), _pinned("F/B"),
                            load])  # fmt: skip
    run = _solve(tmp_path, p, study)
    ma = abs(_at(run, "beam_forces", (0, 0, 0), "My"))
    rb = _at(run, "reaction_force", (L, 0, 0), "z")
    print(f"{solver} propped cantilever, uniform: M_A {ma:.6g} (wL^2/8 {w * L**2 / 8:.6g}), "
          f"R_B {rb:.6g} (3wL/8 {3 * w * L / 8:.6g})")  # fmt: skip
    assert ma == pytest.approx(w * L**2 / 8, rel=1e-6)
    assert rb == pytest.approx(3 * w * L / 8, rel=1e-6)


@over("pynite")
def test_continuous_beam(tmp_path, solver):
    """On PyNite only: pinned at every support, the beam's roll about its own axis is
    held by a rotational spring at A (`node_spring`), which CalculiX and Code_Aster
    don't take yet (and without it the roll is a mechanism)."""
    nodes = {"A": [0, 0, 0], "M1": [L / 2, 0, 0], "B": [L, 0, 0], "M2": [1.5 * L, 0, 0],
             "C": [2 * L, 0, 0]}  # fmt: skip
    members = [["A1", "A", "M1"], ["A2", "M1", "B"], ["B1", "B", "M2"], ["B2", "M2", "C"]]
    # A holds x and the roll about the beam; B and C hold y and z: a continuous beam.
    roll = Feature(type="node_spring", selection="F/A", values={"krx": q(1e9, "N*m/rad")})
    supports = [_pinned("F/A"), *(Feature(type="displacement", selection=f"F/{n}",
                                          values={"y": q(0.0, "m"), "z": q(0.0, "m")})
                                  for n in ("B", "C"))]  # fmt: skip
    p, study, _ = _project(solver, nodes, members,
                           [*supports, roll, _down("F/M1"), _down("F/M2")])  # fmt: skip
    run = _solve(tmp_path, p, study)
    mb = abs(_at(run, "beam_forces", (L, 0, 0), "My"))
    print(f"{solver} continuous beam: M_B {mb:.6g} N m (3PL/16 {3 * P * L / 16:.6g})")
    assert mb == pytest.approx(3 * P * L / 16, rel=1e-6)


def _portal_base(h: float, b: float, i_col: float, i_beam: float, push: float) -> float:
    k = (i_beam / b) / (i_col / h)
    return push * h * (3 * k + 1) / (2 * (6 * k + 1))


@SOLVERS
def test_portal_frame(tmp_path, solver):
    h, b, H = 4.0, 8.0, 10e3
    nodes = {"A": [0, 0, 0], "B": [0, 0, h], "C": [b, 0, h], "D": [b, 0, 0]}
    members = [["AB", "A", "B"], ["BC", "B", "C"], ["CD", "C", "D"]]
    push = Feature(type="force", selection="F/B", values={"x": q(H, "N")})
    p, study, _ = _project(solver, nodes, members,
                           [Feature(type="fixed", selection="F/A"),
                            Feature(type="fixed", selection="F/D"), push])  # fmt: skip
    run = _solve(tmp_path, p, study)
    from puzzle import sections

    i = sections.get("aisc", "W12X26").I1
    expected = _portal_base(h, b, i, i, H)
    ma, md = (abs(_at(run, "beam_forces", pt, "My")) for pt in ((0, 0, 0), (b, 0, 0)))
    rx = _at(run, "reaction_force", (0, 0, 0), "x") + _at(run, "reaction_force", (b, 0, 0), "x")
    print(f"{solver} portal: M_A {ma:.6g}, M_D {md:.6g} N m (closed form {expected:.6g}), "
          f"sum R_x {rx:.6g} N (-H {-H:g})")  # fmt: skip
    assert rx == pytest.approx(-H, rel=1e-6)
    # The beam carries H/2 in compression and shortens, which the closed form neglects:
    # the two base moments part by about ±0.6 % (PyNite 12583 and 12429 N m); their mean
    # holds to 0.05 %.
    assert (ma + md) / 2 == pytest.approx(expected, rel=2e-3 if solver == "pynite" else 1e-2)
    for m in (ma, md):
        assert m == pytest.approx(expected, rel=1e-2)


@SOLVERS
def test_released_joint(tmp_path, solver):
    nodes = {"A": [0, 0, 0], "H": [L, 0, 0], "M": [1.5 * L, 0, 0], "C": [2 * L, 0, 0]}
    members = [["AH", "A", "H"], ["HM", "H", "M"], ["MC", "M", "C"]]
    hinge = MemberRelease(selection="F/HM", at="F/H", released=["ry", "rz"])
    p, study, _ = _project(solver, nodes, members,
                           [Feature(type="fixed", selection="F/A"), _pinned("F/C"),
                            _down("F/M")], releases=[hinge])  # fmt: skip
    if solver != "pynite":
        from puzzle.adapters import available_adapters

        problems = available_adapters()[solver]().check(p, study)
        assert any("releases" in m for m in problems), problems
        return
    run = _solve(tmp_path, p, study)
    ma = abs(_at(run, "beam_forces", (0, 0, 0), "My"))
    rc = _at(run, "reaction_force", (2 * L, 0, 0), "z")
    hinge_moment = abs(_at(run, "beam_forces", (L, 0, 0), "My"))
    print(f"{solver} hinge: M_A {ma:.6g} (PL/2 {P * L / 2:.6g}), R_C {rc:.6g} (P/2 {P / 2:g}), "
          f"averaged moment at the hinge {hinge_moment:.4g}")  # fmt: skip
    assert ma == pytest.approx(P * L / 2, rel=1e-6)
    assert rc == pytest.approx(P / 2, rel=1e-6)
    assert hinge_moment < 1e-9 * P * L  # both members' ends there carry none


@over("pynite")
def test_self_weight(tmp_path, solver):
    nodes = {"A": [0, 0, 0], "B": [L, 0, 0]}
    gravity = Feature(type="gravity", selection="F", values={"gz": q(-9.81, "m/s^2")})
    p, study, steel = _project(solver, nodes, [["AB", "A", "B"]],
                               [Feature(type="fixed", selection="F/A"), gravity])  # fmt: skip
    run = _solve(tmp_path, p, study)
    from puzzle import sections

    rho = steel.properties["density"].to_si()
    expected = rho * sections.get("aisc", "W12X26").A * 9.81 * L**2 / 2
    ma = abs(_at(run, "beam_forces", (0, 0, 0), "My"))
    print(f"{solver} self weight: M_A {ma:.6g} N m (rho A g L^2/2 {expected:.6g})")
    assert ma == pytest.approx(expected, rel=1e-6)


@SOLVERS
def test_member_force_signs(tmp_path, solver):
    """`beam_forces` means the same on every solver (Code_Aster's EFGE signs): a pulled
    cantilever's N is +P; a lever (a crossbar at the tip, pushed down at its end) twists
    the cantilever by T = −P a and bends its root by My = +P L."""
    from puzzle.model.schema import BeamSection

    a, span, force = 1.0, 4.0, 1000.0
    nodes = {"A": [0, 0, 0], "B": [span, 0, 0], "C": [span, a, 0]}
    p, study, _ = _project(solver, nodes, [["AB", "A", "B"], ["BC", "B", "C"]],
                           [Feature(type="fixed", selection="F/A"), _down("F/C", force),
                            Feature(type="force", selection="F/B",
                                    values={"x": q(force, "N")})])  # fmt: skip
    comp = next(o for o in p.objects.values() if o.kind == "component")
    comp.beam_sections = [BeamSection(selection="F/AB", **W12),
                          BeamSection(selection="F/BC",
                                      **{**W12, "orientation": (1, 0, 0)})]  # fmt: skip
    run = _solve(tmp_path, p, study)
    got = {c: _at(run, "beam_forces", (0, 0, 0), c) for c in ("N", "T", "My")}  # the root
    print(f"{solver} signs: {got}")
    assert got["N"] == pytest.approx(force, rel=1e-6)
    assert got["T"] == pytest.approx(-force * a, rel=1e-6)
    assert got["My"] == pytest.approx(force * span, rel=1e-6)
