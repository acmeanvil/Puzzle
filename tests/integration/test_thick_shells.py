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


def _strip(tmp_path, solver, thick: bool):
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
    mesh = p.add(MeshRecipe(name="M", global_size=q(10, "mm"), element_order=1))
    features = [Feature(type="fixed", selection="S/Root"),
                Feature(type="pressure", selection="S/Face", values={"p": q(P, "Pa")})]  # fmt: skip
    comp = p.add(Component(
        name="C", parts=[PartInstance(name="S", geometry=geo.id)], mesh=mesh.id,
        materials=[MaterialAssignment(material=mat.id, selection="S")],
        shell_sections=[ShellSection(selection="S", thickness=q(H * 1e3, "mm"))],
        elements=[ElementChoice(selection="S", formulation="thick_shell")] if thick else [],
        physics=[Physics(type="solid_mechanics", features=features,
                         settings={"temperature_dependent_materials": False})]))  # fmt: skip
    study = p.add(Study(name="S", component=comp.id, solver=solver,
                        steps=[SolveStep(type="stationary")]))  # fmt: skip
    root = tmp_path / ("thick" if thick else "thin")
    save_project(p, root)
    run = run_study(p, study.id, root, tmp_path / ("w-thick" if thick else "w-thin"))
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
