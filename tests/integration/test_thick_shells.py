"""Thick shells (ADR 0082 step 3): Code_Aster's DST (Mindlin-Reissner) against DKT
(Kirchhoff) on a plate strip in cylindrical bending.

A strip L = 300 mm long, b = 100 mm wide and h = 100 mm thick (L/h = 3), ν = 0 (so no
anticlastic curvature: in Mindlin's theory the strip is exactly a Timoshenko beam),
clamped at one end, under a uniform pressure p (w = p b per length). Its tip deflects
w L⁴/(8EI) by Kirchhoff (DKT) and w L⁴/(8EI) + w L²/(2κGA) by Mindlin (DST), κ = 5/6,
G = E/2: the shear part is 8.9 % here, so the elements are told apart by their own
theories.
"""

import numpy as np
import pytest
from solvers import over

from puzzle.model.schema import (
    Component,
    ElementChoice,
    Feature,
    Geometry,
    HistoryStep,
    Material,
    MaterialAssignment,
    MeshRecipe,
    NamedSelection,
    PartInstance,
    Physics,
    Project,
    Quantity,
    ShellSection,
    SolveStep,
    Study,
)
from puzzle.project import save_project
from puzzle.solve import run_study

q = lambda v, u: Quantity(value=v, unit=u)  # noqa: E731
L, B, H, E, P = 0.3, 0.1, 0.1, 200e9, 1e6  # P: the pressure (Pa)


def _strip(
    tmp_path,
    solver,
    thick: bool,
    quads: bool = False,
    order: int = 1,
    formulation=None,
    nodes: int = 20,
    types: dict | None = None,
):
    pytest.importorskip("OCP")
    p = Project(name="strip")
    rectangle = [[0, 0, 0], [L, 0, 0], [L, B, 0], [0, B, 0]]
    edge = lambda at: {"plane": {"axis": "x", "at": at}}  # noqa: E731
    selections = [
        NamedSelection(name="Root", entity="edge", rule=edge(0.0)),
        NamedSelection(name="Face", entity="face", members=["P/face"]),
    ]
    geo = p.add(Geometry(name="Strip", history=[HistoryStep(kind="sheet", name="P",
                                                            args={"points": rectangle})],
                         selections=selections))  # fmt: skip
    mat = p.add(Material(name="Steel, ν 0", properties={
        "youngs_modulus": q(E, "Pa"), "poissons_ratio": q(0.0, "dimensionless"),
        "density": q(7850.0, "kg/m^3")}))  # fmt: skip
    mesh = p.add(MeshRecipe(name="M", global_size=q(10, "mm"), element_order=order,
                            element_shape="hexahedra" if quads else "tetrahedra",
                            hexahedron_nodes=nodes))  # fmt: skip
    formulation = formulation or ("thick_shell" if thick else None)
    features = [Feature(type="fixed", selection="S/Root"),
                Feature(type="pressure", selection="S/Face", values={"p": q(P, "Pa")})]  # fmt: skip
    comp = p.add(Component(
        name="C", parts=[PartInstance(name="S", geometry=geo.id)], mesh=mesh.id,
        materials=[MaterialAssignment(material=mat.id, selection="S")],
        shell_sections=[ShellSection(selection="S", thickness=q(H * 1e3, "mm"))],
        elements=[ElementChoice(selection="S", formulation=formulation, solver_types=types or {})]
        if formulation
        else [],
        physics=[Physics(type="solid_mechanics", features=features,
                         settings={"temperature_dependent_materials": False})]))  # fmt: skip
    study = p.add(Study(name="S", component=comp.id, solver=solver,
                        steps=[SolveStep(type="stationary")]))  # fmt: skip
    name = f"{formulation}-{'quad' if quads else 'tri'}{order}-{nodes}" + (
        "-" + "-".join(types.values()) if types else ""
    )
    root = tmp_path / name
    save_project(p, root)
    run = run_study(p, study.id, root, tmp_path / f"w-{name}")
    u = run.results.last("displacement")
    xyz = run.mesh.node_coords(u.ids)
    tip = np.abs(xyz[:, 0] - L) < 1e-9
    return float(np.mean(u.values[tip, 2]))


@over("code_aster")
def test_dst_takes_the_shear_deflection_dkt_leaves_out(tmp_path, solver):
    inertia, w = B * H**3 / 12, P * B
    bending = w * L**4 / (8 * E * inertia)
    shear = w * L**2 / (2 * 5 / 6 * (E / 2) * B * H)
    thin, thick = -_strip(tmp_path, solver, False), -_strip(tmp_path, solver, True)
    print(
        f"DKT {thin * 1e6:.4f} µm (Kirchhoff {bending * 1e6:.4f}), DST {thick * 1e6:.4f} µm "
        f"(Mindlin {(bending + shear) * 1e6:.4f})"
    )
    assert thin == pytest.approx(bending, rel=0.01)
    assert thick == pytest.approx(bending + shear, rel=0.01)


@over("code_aster", "calculix", "pynite")
def test_quadrilateral_shells(tmp_path, solver):
    """The same strip meshed into quadrilaterals (the recipe's hexahedra): Code_Aster's DKT
    and DST on QUAD4 (its DKQ and DSQ) against Kirchhoff and Mindlin, and COQUE_3D on
    QUAD9 (27-node bricks' faces) against Mindlin; PyNite's MITC4 plates against Mindlin;
    CalculiX's S4 (C3D8I inside) and S8R
    (C3D20R) against Mindlin, as its triangles' S6 (-1.3 %: they are expanded into
    bricks)."""
    inertia, w = B * H**3 / 12, P * B
    bending = w * L**4 / (8 * E * inertia)
    mindlin = bending + w * L**2 / (2 * 5 / 6 * (E / 2) * B * H)
    if solver == "code_aster":
        thin = -_strip(tmp_path, solver, False, quads=True)
        thick = -_strip(tmp_path, solver, True, quads=True)
        curved = -_strip(tmp_path, solver, False, quads=True, order=2, nodes=27)
        q4g = -_strip(tmp_path, solver, False, quads=True, formulation="thick_shell",
                      types={"code_aster": "Q4G"})  # fmt: skip
        assert q4g == pytest.approx(mindlin, rel=0.005)
        print(
            f"DKQ {thin / bending:.5f} of Kirchhoff, DSQ {thick / mindlin:.5f} and "
            f"COQUE_3D {curved / mindlin:.5f} of Mindlin"
        )
        assert thin == pytest.approx(bending, rel=0.005)
        assert thick == pytest.approx(mindlin, rel=0.005)
        assert curved == pytest.approx(mindlin, rel=0.005)
        comm = next((tmp_path / "w-None-quad2-27").rglob("*.comm")).read_text(encoding="utf-8")
        assert "MODELISATION='COQUE_3D'" in comm
        return
    if solver == "pynite":  # its MITC4 quadrilaterals (plates, ADR 0082)
        plate = -_strip(tmp_path, solver, False, quads=True)
        print(f"PyNite MITC4 {plate / mindlin:.5f} of Mindlin")
        assert plate == pytest.approx(mindlin, rel=0.005)
        return
    linear = -_strip(tmp_path, solver, False, quads=True)
    reduced = -_strip(tmp_path, solver, False, quads=True, order=2, formulation="reduced")
    print(f"S4 {linear / mindlin:.5f}, S8R {reduced / mindlin:.5f} of Mindlin")
    for got in (linear, reduced):
        assert got == pytest.approx(mindlin, rel=0.02)
    deck = next((tmp_path / "w-reduced-quad2-20").rglob("*.inp")).read_text(encoding="utf-8")
    assert "TYPE=S8R" in deck


@over("code_aster", "calculix")
@pytest.mark.parametrize("dynamic", [False, True], ids=["static", "damped transient"])
def test_the_elastica_on_shells(tmp_path, solver, dynamic):
    """Large deflection of shells (ADR 0082 decision 6), and a force along a shell's edge:
    a 200 × 10 mm strip 2 mm thick, ν = 0, clamped, PL²/EI = 1 spread along its tip edge.
    Code_Aster writes DKT with GROT_GDEP, CalculiX its S6 shells under NLGEOM: the
    elastica's tip, 0.3017 L down and 0.0566 L in (Bisshopp & Drucker). `dynamic`: the
    load applied at once in a transient step, mass-proportional damping near 0.7 of
    critical in the first mode; once still it must be the same shape."""
    pytest.importorskip("OCP")
    length, width, depth = 0.2, 0.01, 0.002
    p = Project(name="strip")
    rectangle = [[0, 0, 0], [length, 0, 0], [length, width, 0], [0, width, 0]]
    edge = lambda at: {"plane": {"axis": "x", "at": at}}  # noqa: E731
    geo = p.add(Geometry(name="Strip", history=[HistoryStep(kind="sheet", name="P",
                                                            args={"points": rectangle})],
                         selections=[NamedSelection(name="Root", entity="edge", rule=edge(0.0)),
                                     NamedSelection(name="Tip", entity="edge",
                                                    rule=edge(length))]))  # fmt: skip
    mat = p.add(Material(name="Steel, ν 0", properties={
        "youngs_modulus": q(E, "Pa"), "poissons_ratio": q(0.0, "dimensionless"),
        "density": q(7850.0, "kg/m^3")}))  # fmt: skip
    order = 1 if solver == "code_aster" else 2
    mesh = p.add(MeshRecipe(name="M", global_size=q(2.5, "mm"), element_order=order))
    force = E * width * depth**3 / 12 / length**2
    features = [Feature(type="fixed", selection="S/Root"),
                Feature(type="force", selection="S/Tip", values={"z": q(-force, "N")})]  # fmt: skip
    comp = p.add(Component(
        name="C", parts=[PartInstance(name="S", geometry=geo.id)], mesh=mesh.id,
        materials=[MaterialAssignment(material=mat.id, selection="S")],
        shell_sections=[ShellSection(selection="S", thickness=q(depth * 1e3, "mm"))],
        physics=[Physics(type="solid_mechanics", features=features,
                         settings={"temperature_dependent_materials": False})]))  # fmt: skip
    step = SolveStep(type="stationary", settings={"geometric_nonlinearity": True,
                                                  "increments": 20})  # fmt: skip
    if dynamic:
        omega = 1.875**2 * np.sqrt(E * depth**2 / 12 / (7850.0 * length**4))
        period = 2 * np.pi / omega
        step = SolveStep(type="transient", settings={
            "geometric_nonlinearity": True, "rayleigh_alpha": float(2 * 0.7 * omega),
            "time_step": period / 40, "end_time": 6 * period})  # fmt: skip
    study = p.add(Study(name="S", component=comp.id, solver=solver, steps=[step]))
    root = tmp_path / "strip"
    save_project(p, root)
    if dynamic and solver == "code_aster":  # no shell for large rotations in time there
        with pytest.raises(Exception, match="no shell for large rotations"):
            run_study(p, study.id, root, tmp_path / "w")
        return
    run = run_study(p, study.id, root, tmp_path / "w", timeout_s=1800)
    u = [s for s in run.results.steps if "displacement" in s.fields][-1].fields["displacement"]
    xyz = run.mesh.node_coords(u.ids)
    tip = np.abs(xyz[:, 0] - length) < 1e-9
    down, drawn = (
        -float(np.mean(u.values[tip, 2])) / length,
        -float(np.mean(u.values[tip, 0])) / length,
    )
    print(f"{solver} shell elastica{' (transient)' if dynamic else ''}: down {down:.4f} L "
          f"(0.3017), in {drawn:.4f} L (0.0566)")  # fmt: skip
    assert down == pytest.approx(0.3017, rel=0.015)
    assert drawn == pytest.approx(0.0566, rel=0.04)
