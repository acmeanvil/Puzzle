"""Beam fasteners (ADR 0027) against statics and beam theory.

Hanging block: a 20 mm steel cube hangs 50 mm below a fixed plate on one 6 mm
fastener, under a heavy gravity. The fastener's axial force is the cube's weight
(statics, exact) and it stretches by F L / (E A) within 2 % (CalculiX expands the beam
into solids with a slightly small round section).

Shear: two 5 mm plates 5 mm apart, one fixed on its far face, the other pulled along by P at
its far end; one fastener joins them. It carries P in shear, and next to no axial
force. Both with rigid and with distributing ends.
"""

import numpy as np
import pytest
from solvers import over

from puzzle.model.schema import (
    BeamFastener,
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

G = 9.81e4  # m/s², a heavy gravity, so the fastener force is well above round-off
q = lambda v, u: Quantity(value=v, unit=u)  # noqa: E731


def _box(p, name, dx, dy, dz):
    return p.add(
        Geometry(
            name=name,
            history=[HistoryStep(kind="box", name="B", args={"dx": dx, "dy": dy, "dz": dz})],
            selections=[
                NamedSelection(name="Body", entity="body", members=["Body"]),
                *(
                    NamedSelection(name=side, entity="face", members=[f"B/{side}"])
                    for side in ("-x", "+x", "-z", "+z")
                ),
            ],
        )
    )


def _project(solver, parts, features, fastener, mesh_mm=4.0):
    p = Project(name="fastened")
    from puzzle.materials import add_library_material

    steel = add_library_material(p, "structural_steel")
    geos = {name: _box(p, name, *size) for name, (size, _at) in parts.items()}
    mesh = p.add(MeshRecipe(name="M", global_size=q(mesh_mm, "mm")))
    comp = p.add(
        Component(
            name="Joint",
            parts=[
                PartInstance(name=name, geometry=geos[name].id, translation=at)
                for name, (_size, at) in parts.items()
            ],
            mesh=mesh.id,
            materials=[
                MaterialAssignment(material=steel.id, selection=f"{name}/Body") for name in parts
            ],
            physics=[
                Physics(
                    type="solid_mechanics",
                    features=features,
                    settings={"temperature_dependent_materials": False},
                )
            ],
            connectors=[fastener.model_copy(update={"material": steel.id})],
        )
    )
    study = p.add(
        Study(name="Load", component=comp.id, solver=solver, steps=[SolveStep(type="stationary")])
    )
    return p, study, steel


def _run(tmp_path, p, study):
    root = tmp_path / "joint"
    save_project(p, root)
    return run_study(p, study.id, root, tmp_path / "w")


def _fastener(ends):
    return BeamFastener(
        name="Bolt",
        source="Block/+z",
        target="Ceiling/-z",
        diameter=q(6, "mm"),
        material="00000000-0000-0000-0000-000000000000",
        ends=ends,
    )


@over("calculix", "code_aster")
@pytest.mark.parametrize("ends", ["rigid", "distributing"])
def test_hanging_block(tmp_path, solver, ends):
    pytest.importorskip("OCP")
    parts = {
        "Ceiling": ((0.04, 0.04, 0.01), (-0.01, -0.01, 0.07)),
        "Block": ((0.02, 0.02, 0.02), (0.0, 0.0, 0.0)),
    }
    features = [
        Feature(type="fixed", selection="Ceiling/+z"),
        Feature(type="gravity", selection="Block/Body", values={"gz": q(-G, "m/s^2")}),
    ]
    p, study, steel = _project(solver, parts, features, _fastener(ends))
    run = _run(tmp_path, p, study)
    f = run.results.last("fastener")
    c = {k: i for i, k in enumerate(f.components)}
    weight = steel.properties["density"].to_si() * 0.02**3 * G
    axial = float(f.values[0, c["axial"]])
    print(f"{solver} {ends} hanging block: fastener {axial:.6g} N, weight {weight:.6g} N")
    assert axial == pytest.approx(weight, rel=1e-4)
    assert abs(f.values[0, c["shear"]]) < 1e-6 * weight
    if ends == "rigid":  # a rigid end moves as one: the stretch is the beam's
        u = run.results.last("displacement")
        xyz = run.mesh.node_coords(u.ids)
        top = np.isclose(xyz[:, 2], 0.02, atol=1e-9) & (np.abs(xyz[:, 0] - 0.01) < 0.011)
        bottom = np.isclose(xyz[:, 2], 0.07, atol=1e-9) & (np.abs(xyz[:, 0] - 0.01) < 0.021)
        stretch = float(u.values[bottom, 2].mean() - u.values[top, 2].mean())
        E = steel.properties["youngs_modulus"].to_si()
        expected = weight * 0.05 / (E * np.pi * 0.003**2)
        print(f"{solver} stretch {stretch:.6g} m, F L / E A {expected:.6g} m")
        assert stretch == pytest.approx(expected, rel=2e-2)


@over("calculix", "code_aster")
@pytest.mark.parametrize("ends", ["rigid", "distributing"])
def test_fastener_in_shear(tmp_path, solver, ends):
    pytest.importorskip("OCP")
    P = 1000.0
    parts = {
        "Block": ((0.04, 0.02, 0.005), (0.0, 0.0, 0.0)),
        "Ceiling": ((0.04, 0.02, 0.005), (0.0, 0.0, 0.01)),
    }
    features = [
        Feature(type="fixed", selection="Block/-z"),
        Feature(type="force", selection="Ceiling/+x", values={"x": q(P, "N")}),
    ]
    fastener = _fastener(ends).model_copy(update={"source": "Block/+z", "target": "Ceiling/-z"})
    p, study, _ = _project(solver, parts, features, fastener, mesh_mm=3.0)
    run = _run(tmp_path, p, study)
    f = run.results.last("fastener")
    c = {k: i for i, k in enumerate(f.components)}
    shear, axial = float(f.values[0, c["shear"]]), float(f.values[0, c["axial"]])
    print(f"{solver} {ends} shear: {shear:.6g} N (P {P:g}), axial {axial:.4g} N")
    assert shear == pytest.approx(P, rel=1e-4)
    assert abs(axial) < 0.05 * P


@over("calculix")
def test_fastener_pretension(tmp_path, solver):
    """The block hangs from the fixed plate on a fastener tightened to F = 5 kN, its
    bottom on a support as stiff as the bolt (k_s = k_b = E A / L). Step 1: the bolt
    carries F (a set force across its mid-plane cut: exact). Step 2: locked, a force P
    pulls the block down; bolt and support share it by stiffness, so the bolt carries
    F + P k_b / (k_b + k_s) = F + P/2, within 3 % (the plates' own give)."""
    pytest.importorskip("OCP")
    F, P = 5000.0, 2000.0
    parts = {
        "Ceiling": ((0.04, 0.04, 0.01), (-0.01, -0.01, 0.07)),
        "Block": ((0.02, 0.02, 0.02), (0.0, 0.0, 0.0)),
    }
    E = 210e9
    kb = E * np.pi * 0.003**2 / 0.05
    features = [
        Feature(type="fixed", selection="Ceiling/+z"),
        Feature(
            type="elastic_support", selection="Block/-z", values={"kn": q(kb / 0.02**2, "N/m^3")}
        ),
        Feature(type="force", selection="Block/-z", values={"z": q(-P, "N")}, steps=[2]),
    ]
    fastener = _fastener("rigid").model_copy(update={"pretension": q(F, "N")})
    p, study, _ = _project(solver, parts, features, fastener)
    study.steps = [SolveStep(type="stationary"), SolveStep(type="stationary")]
    run = _run(tmp_path, p, study)
    axial = []
    for index in (1, 2):
        step = [s for s in run.results.steps if s.index == index and "fastener" in s.fields][-1]
        f = step.fields["fastener"]
        axial.append(float(f.values[0, f.components.index("axial")]))
    print(f"{solver} fastener pretension: step 1 {axial[0]:.6g} N (F {F:g}), "
          f"step 2 {axial[1]:.6g} N (F + P/2 {F + P / 2:g})")  # fmt: skip
    assert axial[0] == pytest.approx(F, rel=1e-4)
    assert axial[1] == pytest.approx(F + P / 2, rel=3e-2)


@over("calculix", "code_aster")
def test_fastener_pretension_one_step(tmp_path, solver):
    """One step: the tightened fastener carries its pretension F, the block held off by
    the support (exact: a set force across the bolt's mid-plane cut)."""
    pytest.importorskip("OCP")
    F = 5000.0
    parts = {
        "Ceiling": ((0.04, 0.04, 0.01), (-0.01, -0.01, 0.07)),
        "Block": ((0.02, 0.02, 0.02), (0.0, 0.0, 0.0)),
    }
    features = [
        Feature(type="fixed", selection="Ceiling/+z"),
        Feature(type="elastic_support", selection="Block/-z", values={"kn": q(1e12, "N/m^3")}),
    ]
    fastener = _fastener("rigid").model_copy(update={"pretension": q(F, "N")})
    p, study, _ = _project(solver, parts, features, fastener)
    f = _run(tmp_path, p, study).results.last("fastener")
    axial = float(f.values[0, f.components.index("axial")])
    print(f"{solver} one-step fastener pretension: {axial:.6g} N (F {F:g})")
    assert axial == pytest.approx(F, rel=1e-4)
