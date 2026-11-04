"""Magnetodynamics inputs and results without a solver (ADR 0047)."""

import json
import math

import numpy as np
import pytest
from conftest_geometry import BAR, bar_project, write_bar_step

from puzzle.adapters import eddy
from puzzle.adapters.common import InputError
from puzzle.adapters.elmer import ElmerAdapter
from puzzle.adapters.fenicsx import FenicsxAdapter
from puzzle.mesh import generate_mesh
from puzzle.model import Feature, Material, Physics, Quantity, SolveStep, Study

AC = SolveStep(type="frequency_domain", settings={"frequencies": "50, 1000"})


def _coil(kind="coil_current", current=2000.0, phase=30.0, **extra):
    values = {"I": Quantity(value=current, unit="A"), "phase": Quantity(value=phase, unit="deg")}
    values |= {k: Quantity(value=v, unit="dimensionless") for k, v in extra.items()}
    return Feature(type=kind, selection="Bar/Body", values=values)


TERMINALS = [Feature(type="coil_start", selection="Bar/Root"),
             Feature(type="coil_end", selection="Bar/Tip")]  # fmt: skip


def _setup(tmp_path, features, step=AC):
    tmp_path.mkdir(parents=True, exist_ok=True)
    stepfile = write_bar_step(tmp_path / "bar.step")
    project, comp = bar_project(
        stepfile, [Physics(type="magnetic_fields", features=features)], mesh_size_mm=5
    )
    steel = project.of_type(Material)[0]
    steel.properties["relative_permeability"] = Quantity(value=1)
    steel.properties["electrical_conductivity"] = Quantity(value=5.8e7, unit="S/m")
    study = project.add(Study(name="m", component=comp.id, solver="elmer", steps=[step]))
    mesh = generate_mesh(project, comp.id, tmp_path, tmp_path / "m").mesh
    return project, comp, study, mesh


def test_elmer_writes_a_harmonic_massive_conductor(tmp_path):
    project, _, study, mesh = _setup(tmp_path, [_coil(), *TERMINALS])
    sif = ElmerAdapter().write_input(project, study, mesh, tmp_path / "case")
    text = sif.read_text()
    assert '"MagnetoDynamics" "WhitneyAVHarmonicSolver"' in text
    assert "Simulation Type = Scanning" in text and "Timestep Intervals(1) = 2" in text
    assert f"2 {2 * math.pi * 1000:.12g}" in text  # ω of the second scan
    area = BAR[1] * BAR[2]
    current = 2000 * complex(math.cos(math.radians(30)), math.sin(math.radians(30)))
    assert f"Electric Current Density = Real {current.real / area:.10g}" in text
    assert f"Electric Current Density im = Real {current.imag / area:.10g}" in text
    assert "AV re = Real 0" in text  # V = 0 on the end face
    assert "CoilSolver" not in text  # a massive conductor needs none
    assert "BiCGStabl" in text and "Umfpack" not in text  # the harmonic A–V solve
    meta = json.loads((sif.parent / "mesh" / eddy.META).read_text())
    assert meta["frequencies"] == [50.0, 1000.0]
    assert meta["coils"][0]["kind"] == "massive" and meta["coils"][0]["start"] == "Bar/Root"


def test_elmer_writes_a_winding(tmp_path):
    coil = _coil("stranded_coil", current=3.0, phase=0.0, turns=40, fill=0.5)
    project, _, study, mesh = _setup(tmp_path, [coil, *TERMINALS])
    text = ElmerAdapter().write_input(project, study, mesh, tmp_path / "case").read_text()
    assert '"CoilSolver" "CoilSolver"' in text and "Use Unity Conductivity" in text
    assert 'Coil Type = String "stranded"' in text  # its own Component, scaled alone
    assert 'Current Density 1 = Variable "CoilCurrent 1"' in text
    assert 'Real MATC "tx*(-120)"' in text  # −N I: CoilCurrent flows end → start
    assert "Electric Conductivity = 0" in text  # no eddy currents in a winding
    assert "Fix Input Current Density = Logical True" in text


def test_fenicsx_case_carries_the_coils(tmp_path):
    project, _, study, mesh = _setup(tmp_path, [_coil(), *TERMINALS])
    study.solver = "fenicsx"
    FenicsxAdapter().write_input(project, study, mesh, tmp_path / "case")
    case = json.loads((tmp_path / "case" / "case.json").read_text())
    mag = case["magnetics"]
    assert mag["frequencies"] == [50.0, 1000.0]
    (coil,) = mag["coils"]
    assert coil["kind"] == "massive" and coil["area"] == pytest.approx(BAR[1] * BAR[2])
    assert coil["current"][1] == pytest.approx(1000.0)  # 2000 A at 30°
    assert (tmp_path / "case" / eddy.ELEMENTS).exists()


def test_coil_checks(tmp_path):
    project, comp, study, mesh = _setup(tmp_path, [_coil()])
    features = comp.physics[0].features
    with pytest.raises(InputError, match="enters"):  # a massive conductor needs terminals
        eddy.coils(project, comp, features, mesh, solver="Elmer")
    half = [_coil(), TERMINALS[0]]
    with pytest.raises(InputError, match="both"):
        eddy.coils(project, comp, half, mesh, solver="Elmer")
    with pytest.raises(InputError, match="share of the section"):
        eddy.coils(project, comp, [_coil("stranded_coil", fill=1.5)], mesh, solver="Elmer")
    closed = eddy.coils(project, comp, [_coil("stranded_coil", fill=0.5)], mesh, solver="Elmer")
    assert closed[0].closed  # a winding without terminals is a loop


def test_a_static_phase_is_refused(tmp_path):
    coil = _coil("stranded_coil", phase=45.0, turns=1, fill=1)
    project, _, study, mesh = _setup(tmp_path, [coil, *TERMINALS], SolveStep(type="stationary"))
    with pytest.raises(InputError, match="phase"):
        ElmerAdapter().write_input(project, study, mesh, tmp_path / "case")


def test_skin_depth():
    # Copper at 50 Hz: about 9.3 mm.
    assert eddy.skin_depth(50, 1.0, 5.8e7) == pytest.approx(9.35e-3, rel=0.01)
    assert eddy.skin_depth(0, 1.0, 5.8e7) == math.inf


def test_globals_from_element_values():
    """A uniform current in a conductor, and a winding: loss, energy, R and L."""
    meta = {
        "bodies": [{"name": "Bar", "mu_r": 1.0, "sigma": 1e6},
                   {"name": "Coil", "mu_r": 1.0, "sigma": 0.0}],
        "coils": [eddy.Coil("Bar", "massive", (2.0, 0.0), conductivity=1e6),
                  eddy.Coil("Coil", "stranded", (1.0, 0.0), turns=10, fill=0.5,
                            conductivity=1e6)],
        "frequencies": [100.0],
    }  # fmt: skip
    omega = 2 * math.pi * 100
    body = np.array([0, 0, 1])
    volume = np.array([1e-6, 1e-6, 2e-6])
    J = np.array([[0, 0, 4e6], [0, 0, 4e6], [0, 0, 1e6]], complex)
    B = np.array([[0.1, 0, 0]] * 3, complex)
    A = np.array([[0, 0, 0], [0, 0, 0], [0, 0, 2e-4j]], complex)
    density, out = eddy.globals_(meta, omega, body, volume, B, J, A, {"Bar": 3.0 + 4.0j})
    assert density[0] == pytest.approx(0.5 * 16e12 / 1e6)  # ½|J|²/σ
    assert density[2] == pytest.approx(0.5 * 1e12 / (1e6 * 0.5))  # the winding's copper
    loss = out["joule_loss"]
    assert loss.components == ["total", "Bar", "Coil"]
    assert loss.values[0, 1] == pytest.approx(2 * 8e6 * 1e-6)
    energy = 0.25 * 0.01 / eddy.MU0 * 4e-6
    assert out["magnetic_energy"].values[0, 0] == pytest.approx(energy)
    r, ind = out["coil_resistance"].values[0], out["coil_inductance"].values[0]
    assert r[0] == pytest.approx(1.5) and ind[0] == pytest.approx(2.0 / omega)  # V / I
    r_dc = 1e12 * 2e-6 / (1e6 * 0.5)  # ∫|J|²/(σ f) / |I|²
    assert r[1] == pytest.approx(r_dc - omega * 2e-4 * 1e6 * 2e-6)  # jω·(j ∫A·J)


def test_skin_depth_warnings_and_refinement(tmp_path):
    """5 mm elements in copper-like steel at 1 kHz (δ ≈ 2.1 mm): warned, then refined."""
    from puzzle.advisor import advise
    from puzzle.magnetics import mesh_warnings, skin_refinement, warnings

    project, comp, study, mesh = _setup(tmp_path, [_coil(), *TERMINALS])
    (w,) = warnings(project)
    assert w.code == "skin_depth" and w.object == "m" and "Bar/Body" in w.message
    found = {a.step: a for a in advise(project, component=comp, study=study)}
    assert found["skin_depth"].action == f"skin_depth:{study.id}"
    (control,) = skin_refinement(project, study)
    delta = eddy.skin_depth(1000, 1.0, 5.8e7)
    assert control.kind == "refine_near" and control.selection == "Bar/Body"
    assert control.size.to_si() == pytest.approx(delta / 3, rel=0.01)
    assert control.far.to_si() == pytest.approx(4 * delta, rel=0.01)
    assert [x.code for x in mesh_warnings(project, study, mesh)][:1] == ["skin_depth"]
    from puzzle.model import MeshRecipe

    recipe = project.of_type(MeshRecipe)[0]
    recipe.controls.append(control)
    assert warnings(project) == [] and skin_refinement(project, study) == []


def test_a_loss_transfer_keeps_its_total(tmp_path):
    """A loss density on one mesh heats the body on a coarser one with the same power."""
    from types import SimpleNamespace

    from puzzle.results.model import Field
    from puzzle.transfer import _integral, heat_from_losses

    _, _, _, fine = _setup(tmp_path / "fine", [_coil(), *TERMINALS])
    _, _, _, coarse = _setup(tmp_path / "coarse", [_coil(), *TERMINALS])
    blocks = [b for b in fine.blocks if b.dim == 3]
    ids = np.concatenate([b.ids for b in blocks])
    centres = np.vstack([fine.node_coords(b.connectivity[:, :4].ravel()).reshape(-1, 4, 3)
                         .mean(axis=1) for b in blocks])  # fmt: skip
    q = 1e6 * (1 + centres[:, 0] / BAR[0])  # W/m³, rising along the bar
    losses = Field("loss_density", "element", [""], ids, q[:, None])
    transfer = SimpleNamespace(name="Losses", target_selection="Bar/Body", mapping="interpolate",
                               id=None, source="ac")  # fmt: skip
    from uuid import uuid4

    transfer.id = uuid4()
    given, feature = heat_from_losses(transfer, losses, fine, coarse)
    assert feature.type == "heat_generation" and "q" in feature.fields
    assert given.role == "feature" and given.source == "heat_generation:Bar/Body:q"
    total = 1e6 * 1.5 * BAR[0] * BAR[1] * BAR[2]  # the mean q times the volume
    taken = _integral(coarse, "Bar/Body",
                      lambda conn: given.field.at(conn.ravel()).reshape(conn.shape))  # fmt: skip
    assert taken == pytest.approx(total, rel=1e-3)


def test_cfd_solvers_refuse_heat_generation(tmp_path):
    from puzzle.adapters import available_adapters

    source = Feature(type="heat_generation", selection="Bar/Body",
                     values={"q": Quantity(value=1e5, unit="W/m^3")})  # fmt: skip
    stepfile = write_bar_step(tmp_path / "bar.step")
    project, comp = bar_project(stepfile, [Physics(type="heat_transfer", features=[source])])
    study = project.add(Study(name="s", component=comp.id, solver="code_saturne",
                              steps=[SolveStep(type="stationary")]))  # fmt: skip
    for solver in ("code_saturne", "openfoam"):
        adapter = available_adapters()[solver]()
        problems = adapter.check(project, study.model_copy(update={"solver": solver}))
        assert any("heat_generation" in p or "heat generation" in p.lower() for p in problems)
