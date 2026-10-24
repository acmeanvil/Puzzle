"""Remote loads (ADR 0037) on a round steel shaft in torsion, against Saint-Venant.

A shaft of radius R = 10 mm and length L = 100 mm is fixed at z = 0. At z = L:
- a deformable remote moment T (an RBE3-equivalent traction): the surface shear stress
  at mid-length is τ = 2T/(πR³);
- a rigid remote moment T (CalculiX *RIGID BODY, Code_Aster LIAISON_SOLIDE): the end
  turns by θ = TL/(GJ), J = πR⁴/2;
- a remote displacement turning the end face by θ: τ = GθR/L at mid-length.
Nodal stresses on the surface of quadratic tets read about 1 % low; within 3 %.
"""

import numpy as np
import pytest
from solvers import over

from puzzle.model.schema import (
    Component,
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

R, L, T = 0.01, 0.1, 100.0  # m, m, N·m
SOLVERS = ("calculix", "code_aster", "elmer", "fenicsx")


def _shaft(solver: str, load: Feature) -> tuple[Project, Study]:
    p = Project(name="shaft")
    geo = p.add(
        Geometry(
            name="Shaft",
            history=[HistoryStep(kind="cylinder", name="Shaft", args={"r": R, "h": L})],
            selections=[
                NamedSelection(name="Body", entity="body", members=["body-1"]),
                *(
                    NamedSelection(name=n, entity="face", rule={"plane": {"axis": "z", "at": z}})
                    for n, z in (("Start", 0.0), ("End", L))
                ),
            ],
        )
    )  # fmt: skip
    from puzzle.materials import add_library_material

    steel = add_library_material(p, "structural_steel")
    mesh = p.add(MeshRecipe(name="Tets", global_size=Quantity(value=2.5, unit="mm")))
    comp = p.add(
        Component(
            name="Shaft",
            parts=[PartInstance(name="S", geometry=geo.id)],
            mesh=mesh.id,
            materials=[MaterialAssignment(material=steel.id, selection="S/Body")],
            physics=[
                Physics(
                    type="solid_mechanics",
                    features=[Feature(type="fixed", selection="S/Start"), load],
                )
            ],
        )
    )
    study = p.add(
        Study(name="Twist", component=comp.id, solver=solver, steps=[SolveStep(type="stationary")])
    )
    return p, study


def _shear_at_mid(run) -> float:
    s = run.results.last("stress")
    xyz = run.mesh.node_coords(s.ids)
    r = np.hypot(xyz[:, 0], xyz[:, 1])
    band = (r > 0.995 * R) & (np.abs(xyz[:, 2] - L / 2) < 0.01)
    c = {k: i for i, k in enumerate(s.components)}
    return float(np.hypot(s.values[band, c["yz"]], s.values[band, c["zx"]]).mean())


def _end_twist(run) -> float:
    u = run.results.last("displacement")
    xyz = run.mesh.node_coords(u.ids)
    r2 = xyz[:, 0] ** 2 + xyz[:, 1] ** 2
    end = np.isclose(xyz[:, 2], L, atol=1e-7) & (r2 > (0.5 * R) ** 2)
    return float(
        ((xyz[end, 0] * u.values[end, 1] - xyz[end, 1] * u.values[end, 0]) / r2[end]).mean()
    )


def _report(solver: str, what: str, got: float, expected: float) -> None:
    print(f"{solver} {what}: {got:.6g}, theory {expected:.6g}, {100 * (got / expected - 1):+.2f} %")


def _steel(p):
    m = next(o for o in p.objects.values() if o.kind == "material")
    E, nu = m.properties["youngs_modulus"].to_si(), m.properties["poissons_ratio"].to_si()
    return E / (2 * (1 + nu))


def _run(tmp_path, p, study):
    root = tmp_path / "shaft"
    save_project(p, root)
    return run_study(p, study.id, root, tmp_path / "w")


@over(*SOLVERS)
def test_deformable_remote_moment(tmp_path, solver):
    pytest.importorskip("OCP")
    load = Feature(
        type="remote_force", selection="S/End", values={"Mz": Quantity(value=T, unit="N*m")}
    )
    p, study = _shaft(solver, load)
    tau = _shear_at_mid(_run(tmp_path, p, study))
    expected = 2 * T / (np.pi * R**3)
    _report(solver, "remote moment", tau, expected)
    assert tau == pytest.approx(expected, rel=3e-2)


@over(*SOLVERS)
def test_remote_rotation(tmp_path, solver):
    pytest.importorskip("OCP")
    theta = 0.01  # rad
    load = Feature(
        type="remote_displacement",
        selection="S/End",
        values={"rz": Quantity(value=theta, unit="rad")},
    )
    p, study = _shaft(solver, load)
    run = _run(tmp_path, p, study)
    tau, expected = _shear_at_mid(run), _steel(p) * theta * R / L
    _report(solver, "remote rotation", tau, expected)
    assert tau == pytest.approx(expected, rel=3e-2)
    assert _end_twist(run) == pytest.approx(theta, rel=1e-6)  # prescribed exactly


@over("calculix", "code_aster")
def test_rigid_remote_moment(tmp_path, solver):
    pytest.importorskip("OCP")
    load = Feature(
        type="remote_force_rigid", selection="S/End", values={"Mz": Quantity(value=T, unit="N*m")}
    )
    p, study = _shaft(solver, load)
    twist = _end_twist(_run(tmp_path, p, study))
    expected = T * L / (_steel(p) * np.pi * R**4 / 2)
    _report(solver, "rigid moment", twist, expected)
    assert twist == pytest.approx(expected, rel=2e-2)
