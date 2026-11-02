"""Standard sections for beams (ADR 0055), against beam theory.

Cantilever: 1 m long, clamped at one end, P at the other, along the web (strong axis)
and across it (weak axis): δ = PL³/(3EI) + PL/(G A_shear), I and the shear area of
that axis (Timoshenko, St Venant torsion; no warping).

L-frame: two 1 m members at right angles in the ground plane, clamped at one end, P
down at the free end. The first member bends and twists by P L2, so the tip falls
P L2³/(3EI) + P L1³/(3EI) + P L2² L1/(GJ) + shear: for an open section the twist,
through J, is nearly all of it. The same frame oriented four ways (a vector, a
coordinate system's z turned 90° about each member, toward a far point, and per
member), which all put the web upright, falls the same.

Code_Aster takes the tables' values (A, I, J, shear areas); CalculiX takes a
rectangular HSS as a BOX of its design wall (square corners), so it is checked against
that box's own properties, and open sections as exact Timoshenko springs (the tables'
values, `adapters/calculix/spring_beams.py`), on first- and second-order meshes.

Shoot-out: a three-member space frame of IPE 300 (members along z, x and y, oriented
toward a point), clamped at its foot, a force along each axis at the tip: CalculiX's
springs and Code_Aster's POU_D_T are both exact for nodal loads, so the displacements
and section forces (`beam_forces`) agree to 1e-4 of their largest.

Modal: a 4 m W12X26 cantilever's first bending mode (about the weak axis) against
Euler-Bernoulli, f = (1.8751² / 2π) √(EI / (ρ A L⁴)): CalculiX's springs lump the mass
at the nodes (40 elements) and leave rotary inertia out, within 1 % here.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from solvers import available, over

from puzzle import sections
from puzzle.model.schema import (
    BeamSection,
    Component,
    CoordinateSystem,
    Feature,
    Geometry,
    HistoryStep,
    MaterialAssignment,
    MeshRecipe,
    NamedSelection,
    PartInstance,
    Physics,
    Project,
    Quantity,
    SolveStep,
    Study,
)
from puzzle.project import save_project
from puzzle.solve import run_study

E, NU = 210e9, 0.3
G = E / (2 * (1 + NU))
P = 1000.0
q = lambda v, u: Quantity(value=v, unit=u)  # noqa: E731


def _frame(tmp_path, solver, points, section_kw, load_at, load, order=None, steps=None, more=()):
    p = Project(name="frame")
    plane = lambda axis, at: {"plane": {"axis": axis, "at": at}}  # noqa: E731
    geo = p.add(Geometry(
        name="Frame",
        history=[HistoryStep(kind="polyline", name="L", args={"points": points})],
        selections=[NamedSelection(name="Root", entity="vertex", rule=plane("x", 0.0)),
                    NamedSelection(name="Tip", entity="vertex", rule=plane(*load_at))],
    ))  # fmt: skip
    from puzzle.materials import add_library_material

    steel = add_library_material(p, "structural_steel")
    steel.properties["youngs_modulus"] = q(E, "Pa")
    steel.properties["poissons_ratio"] = q(NU, "dimensionless")
    # Code_Aster's POU_D_T takes two-node beams; CalculiX's BOX and PIPE take B32R.
    if order is None:
        order = 2 if solver == "calculix" else 1
    mesh = p.add(MeshRecipe(name="M", global_size=q(100, "mm"), element_order=order))
    axes = p.add(CoordinateSystem(name="World", type="cartesian"))
    section = BeamSection(selection="F", **section_kw)
    if section.orient == "axes":
        section = section.model_copy(update={"axes": axes.id})
    comp = p.add(Component(
        name="Frame",
        parts=[PartInstance(name="F", geometry=geo.id)],
        mesh=mesh.id,
        materials=[MaterialAssignment(material=steel.id, selection="F")],
        beam_sections=[section],
        physics=[Physics(type="solid_mechanics", features=[
            Feature(type="fixed", selection="F/Root"),
            Feature(type="force", selection="F/Tip", values=load), *more,
        ], settings={"temperature_dependent_materials": False})],
    ))  # fmt: skip
    study = p.add(Study(name="S", component=comp.id, solver=solver,
                        steps=steps or [SolveStep(type="stationary")]))  # fmt: skip
    root = tmp_path / "frame"
    save_project(p, root)
    run = run_study(p, study.id, root, tmp_path / "w")
    u = run.results.last("displacement")
    return run, u


def _tip(run, u, at):
    xyz = run.mesh.node_coords(u.ids)
    return u.values[np.argmin(np.linalg.norm(xyz - np.asarray(at), axis=1))]


def _box(sec):
    """The square-cornered box CalculiX expands an HSS into: A, I1, I2, J (Bredt)."""
    b, h, t = sec.sizes["B"], sec.sizes["Ht"], sec.sizes["tdes"]
    bi, hi = b - 2 * t, h - 2 * t
    return {"A": b * h - bi * hi, "I1": (b * h**3 - bi * hi**3) / 12,
            "I2": (h * b**3 - hi * bi**3) / 12,
            "J": 2 * t * (b - t) ** 2 * (h - t) ** 2 / (b + h - 2 * t),
            "A1": 2 * b * t, "A2": 2 * h * t}  # fmt: skip


CASES = [
    pytest.param(solver, std, name, id=f"{solver}-{name}",
                 marks=pytest.mark.skipif(not available(solver), reason=f"no {solver}"))
    for solver, std, name in [("code_aster", "aisc", "W12X26"), ("code_aster", "en", "IPE 300"),
                              ("code_aster", "aisc", "HSS6X4X1/4"),
                              ("calculix", "aisc", "HSS6X4X1/4"),
                              ("calculix", "aisc", "W12X26"), ("calculix", "en", "IPE 300")]
]  # fmt: skip


@pytest.mark.parametrize("solver, standard, designation", CASES)
@pytest.mark.parametrize("axis", ["strong", "weak"])
def test_cantilever(tmp_path, solver, standard, designation, axis):
    pytest.importorskip("OCP")
    sec = sections.get(standard, designation)
    table = {"A": sec.A, "I1": sec.I1, "I2": sec.I2, "J": sec.J, "A1": sec.A1, "A2": sec.A2}
    props = _box(sec) if solver == "calculix" and sec.shape == "RHS" else table
    # Along x, first axis y: the web upright (z). Strong: P along z; weak: along y.
    load = {"z": q(-P, "N")} if axis == "strong" else {"y": q(-P, "N")}
    kw = {"shape": "library", "standard": standard, "designation": designation,
          "orientation": (0.0, 1.0, 0.0)}  # fmt: skip
    run, u = _frame(tmp_path, solver, [[0, 0, 0], [1, 0, 0]], kw, ("x", 1.0), load)
    i, shear = (props["I1"], props["A2"]) if axis == "strong" else (props["I2"], props["A1"])
    expected = P / (3 * E * i) + P / (G * shear)
    got = float(-_tip(run, u, (1, 0, 0))[2 if axis == "strong" else 1])
    print(f"{solver} {designation} {axis}: {got:.6g} m, expected {expected:.6g} m, "
          f"{100 * (got / expected - 1):+.2f} %")  # fmt: skip
    # CalculiX's expanded box bends as theory says but takes next to no shear strain
    # (the thin-wall shear term here is 3 % of the total), so it is checked to 4 %;
    # its springs for open sections are exact.
    boxed = solver == "calculix" and sec.shape == "RHS"
    assert got == pytest.approx(expected, rel=4e-2 if boxed else 1e-2)


@pytest.mark.skipif(not available("calculix"), reason="no calculix")
@pytest.mark.parametrize("order", [1, 2])
def test_calculix_open_section_springs_are_exact(tmp_path, order):
    """A W12X26 cantilever on CalculiX's springs, both mesh orders: Timoshenko's tip
    deflection to 1e-4, and the root's section forces the statics (Vz = -P, My = P L:
    the force and moment the part ahead puts on the part behind, in the member's axes)."""
    pytest.importorskip("OCP")
    sec = sections.get("aisc", "W12X26")
    kw = {"shape": "library", "standard": "aisc", "designation": "W12X26",
          "orientation": (0.0, 1.0, 0.0)}  # fmt: skip
    run, u = _frame(tmp_path, "calculix", [[0, 0, 0], [1, 0, 0]], kw, ("x", 1.0),
                    {"z": q(-P, "N")}, order=order)  # fmt: skip
    expected = P / (3 * E * sec.I1) + P / (G * sec.A2)
    got = float(-_tip(run, u, (1, 0, 0))[2])
    print(f"calculix springs, order {order}: {got:.7g} m, expected {expected:.7g} m, "
          f"{100 * (got / expected - 1):+.4f} %")  # fmt: skip
    assert got == pytest.approx(expected, rel=1e-4)
    f = run.results.last("beam_forces")
    assert f.ids.max() <= run.mesh.node_ids.max()  # the helper nodes are dropped
    c = {k: i for i, k in enumerate(f.components)}
    root = f.values[np.argmin(np.linalg.norm(run.mesh.node_coords(f.ids), axis=1))]
    print(f"root: Vz {root[c['Vz']]:.7g} N, My {root[c['My']]:.7g} N m")
    assert root[c["Vz"]] == pytest.approx(-P, rel=1e-5)
    assert root[c["My"]] == pytest.approx(P * 1.0, rel=1e-5)
    rf = run.results.last("reaction_force")  # carried through the arms to the root
    held = np.argmin(np.linalg.norm(run.mesh.node_coords(rf.ids), axis=1))
    assert rf.values[held] == pytest.approx([0.0, 0.0, P], abs=1e-3)


@pytest.mark.skipif(not available("calculix"), reason="no calculix")
def test_calculix_open_section_under_gravity(tmp_path):
    """A 3 m W12X26 cantilever under its own weight, w = ρ A g per metre, along the web:
    the tip falls w L⁴/(8 E I) + w L²/(2 G A_shear) (Timoshenko). The springs take each
    segment's consistent loads (wL/2 and wL²/12), for which their nodes are exact."""
    pytest.importorskip("OCP")
    from puzzle.materials import library_material

    sec = sections.get("aisc", "W12X26")
    kw = {"shape": "library", "standard": "aisc", "designation": "W12X26",
          "orientation": (0.0, 1.0, 0.0)}  # fmt: skip
    length, g = 3.0, 9.81
    gravity = Feature(type="gravity", selection="F", values={"gz": q(-g, "m/s^2")})
    run, u = _frame(tmp_path, "calculix", [[0, 0, 0], [length, 0, 0]], kw, ("x", length),
                    {"z": q(0.0, "N")}, order=1, more=[gravity])  # fmt: skip
    rho = library_material("structural_steel").properties["density"].to_si()
    w = rho * sec.A * g
    expected = w * length**4 / (8 * E * sec.I1) + w * length**2 / (2 * G * sec.A2)
    got = float(-_tip(run, u, (length, 0, 0))[2])
    print(f"W12X26 under its weight: {got:.6g} m, expected {expected:.6g} m, "
          f"{100 * (got / expected - 1):+.4f} %")  # fmt: skip
    assert got == pytest.approx(expected, rel=1e-4)


ORIENTS = {
    "vector": {"orient": "vector", "orientation": (1.0, 1.0, 0.0)},
    "axes": {"orient": "axes", "axes_axis": "z", "roll": 90.0},
    "toward": {"orient": "toward", "toward_point": (11.0, -10.0, 0.0)},
}


@over("code_aster", "calculix")
@pytest.mark.parametrize("orient", list(ORIENTS))
def test_l_frame_twists_by_j(tmp_path, solver, orient):
    pytest.importorskip("OCP")
    sec = sections.get("aisc", "W12X26")
    kw = {"shape": "library", "standard": "aisc", "designation": "W12X26", **ORIENTS[orient]}
    run, u = _frame(tmp_path, solver, [[0, 0, 0], [1, 0, 0], [1, 1, 0]], kw,
                    ("y", 1.0), {"z": q(-P, "N")})  # fmt: skip
    expected = 2 * P / (3 * E * sec.I1) + P / (G * sec.J) + 2 * P / (G * sec.A2)
    got = float(-_tip(run, u, (1, 1, 0))[2])
    print(f"L-frame W12X26 {solver} ({orient}): {got:.6g} m, expected {expected:.6g} m "
          f"(twist {P / (G * sec.J):.4g}), {100 * (got / expected - 1):+.2f} %")  # fmt: skip
    assert got == pytest.approx(expected, rel=1e-2)


@pytest.mark.skipif(
    not (available("calculix") and available("code_aster")), reason="needs both solvers"
)
def test_space_frame_shoot_out(tmp_path):
    """CalculiX's springs against Code_Aster's POU_D_T on one frame (both exact for
    nodal loads): displacements and section forces at every node to 1e-4 of the
    largest, component by component."""
    pytest.importorskip("OCP")
    points = [[0, 0, 0], [1.2, 0, 0], [1.2, 0, 1.5], [1.2, 0.8, 1.5]]
    kw = {"shape": "library", "standard": "en", "designation": "IPE 300",
          "orient": "toward", "toward_point": (3.0, 2.0, -4.0)}  # fmt: skip
    load = {"x": q(400.0, "N"), "y": q(-700.0, "N"), "z": q(-1000.0, "N")}
    out = {}
    for solver in ("calculix", "code_aster"):
        run, u = _frame(tmp_path / solver, solver, points, kw, ("y", 0.8), load, order=1)
        out[solver] = (run.mesh, u, run.results.last("beam_forces"))
    mesh, ua, fa = out["calculix"]
    _mesh, ub, fb = out["code_aster"]
    ids = np.intersect1d(ua.ids, ub.ids)
    assert len(ids) == len(mesh.beam_nodes())
    du = np.abs(ua.at(ids) - ub.at(ids)).max() / np.abs(ub.at(ids)).max()
    print(f"displacement: largest {np.abs(ub.values).max():.4g} m, differing by {du:.2e} of it")
    assert du < 1e-4
    assert fa.components == fb.components
    ids = np.intersect1d(fa.ids, fb.ids)
    assert len(ids) == len(mesh.beam_nodes())
    for k, name in enumerate(fb.components):
        a, b = fa.at(ids)[:, k], fb.at(ids)[:, k]
        diff = np.abs(a - b).max() / np.abs(b).max()
        print(f"{name}: largest {np.abs(b).max():.5g}, differing by {diff:.2e} of it")
        assert diff < 1e-4, name


@pytest.mark.skipif(not available("calculix"), reason="no calculix")
def test_open_section_modes(tmp_path):
    pytest.importorskip("OCP")
    from puzzle.materials import library_material

    sec = sections.get("aisc", "W12X26")
    kw = {"shape": "library", "standard": "aisc", "designation": "W12X26",
          "orientation": (0.0, 1.0, 0.0)}  # fmt: skip
    length = 4.0
    run, _u = _frame(tmp_path, "calculix", [[0, 0, 0], [length, 0, 0]], kw, ("x", length),
                     {"z": q(0.0, "N")}, order=1,
                     steps=[SolveStep(type="modal", settings={"modes": 3})])  # fmt: skip
    rho = library_material("structural_steel").properties["density"].to_si()
    expected = 1.8751**2 / (2 * math.pi) * math.sqrt(E * sec.I2 / (rho * sec.A * length**4))
    got = min(s.value for s in run.results.steps)
    print(f"W12X26 cantilever, first mode: {got:.5g} Hz, Euler-Bernoulli {expected:.5g} Hz, "
          f"{100 * (got / expected - 1):+.2f} %")  # fmt: skip
    assert got == pytest.approx(expected, rel=1e-2)


@pytest.mark.skipif(not available("calculix"), reason="no calculix")
def test_calculix_refuses_buckling_of_open_sections(tmp_path):
    """Springs have no geometric stiffness: buckling goes to Code_Aster."""
    pytest.importorskip("OCP")
    kw = {"shape": "library", "standard": "aisc", "designation": "W12X26"}
    with pytest.raises(Exception, match="Code_Aster"):
        _frame(tmp_path, "calculix", [[0, 0, 0], [1, 0, 0]], kw, ("x", 1.0),
               {"x": q(-P, "N")}, steps=[SolveStep(type="stationary"),
                                         SolveStep(type="buckling")])  # fmt: skip


def test_the_tables_hold_the_published_values():
    in4, in2 = 0.0254**4, 0.0254**2
    w = sections.get("aisc", "W12X26")  # AISC Manual, 16th ed., Table 1-1
    published = (7.65 * in2, 204 * in4, 17.3 * in4, 0.300 * in4)
    assert pytest.approx(published, rel=1e-3) == (w.A, w.I1, w.I2, w.J)
    ipe = sections.get("en", "IPE 300")  # EN 10365 / ArcelorMittal: 53.8 cm², 8356, 603.8 cm⁴
    assert pytest.approx((53.8e-4, 8356e-8, 603.8e-8), rel=2e-3) == (ipe.A, ipe.I1, ipe.I2)
    assert pytest.approx(25.68e-4) == ipe.A2  # EN's Avz, as tabulated
    assert math.isclose(sections.get("aisc", "HSS6X4X1/4").I1, 20.9 * in4, rel_tol=2e-3)
