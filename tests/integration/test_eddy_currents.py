"""Time-harmonic magnetic fields (ADR 0047) against closed forms.

A straight copper wire of radius a inside a coaxial tube of air of radius R, both
length L, the air's ends and outer face magnetically insulated (n × A = 0), so the field
is that of an infinite wire with its return current on the tube: B circles the wire.

- **Skin effect** (a massive conductor, the total current I imposed): with k = (1 − j)/δ,
  J(r) = k I J0(kr) / (2π a J1(ka)), and per length the internal impedance
  Z' = k J0(ka) / (2π a σ J1(ka)); the field outside adds jω μ0 ln(R/a) / 2π. So
  R = Re Z' L and L = (Im Z' / ω + μ0 ln(R/a) / 2π) L.
- **A winding** (stranded: N turns, fill f): the current stays uniform at any frequency,
  R = N² L / (σ f π a²) and L = N² μ0 L (1/4 + ln(R/a)) / 2π, as at DC.
- **Two windings** side by side, each with its own current and phase: the current
  through each wire's section is its own N I at its own phase.
"""

import math

import gmsh
import numpy as np
import pytest
from scipy.special import jv
from solvers import over

from puzzle.geometry import gmsh_session, import_step
from puzzle.materials import add_library_material
from puzzle.model import (
    Component,
    Contact,
    Feature,
    Geometry,
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
from puzzle.solve import run_study

MU0 = 4e-7 * math.pi
A, R, L = 0.001, 0.004, 0.003  # m: wire radius, air tube radius, length
CURRENT = 10.0  # A, peak

solver = over("elmer", "fenicsx")


def _cylinder(path, radius, length, bores=()):
    """A cylinder along z (or a block of air with bores at (x, r)), its faces named."""
    with gmsh_session("cylinder"):
        outer = gmsh.model.occ.addCylinder(0, 0, 0, 0, 0, length, radius)
        if bores:
            holes = [(3, gmsh.model.occ.addCylinder(x, 0, 0, 0, 0, length, r)) for x, r in bores]
            gmsh.model.occ.cut([(3, outer)], holes)
        gmsh.model.occ.synchronize()
        gmsh.write(str(path))
    model = import_step(path)
    lateral = model.faces_of_kind("Cylinder")
    outer_face = max(lateral, key=lambda f: model[f].bbox[3] - model[f].bbox[0])
    return [
        NamedSelection(name="Body", entity="body", members=["body-1"]),
        NamedSelection(name="Start", entity="face", members=model.faces_on_plane("z", 0.0)),
        NamedSelection(name="End", entity="face", members=model.faces_on_plane("z", length)),
        NamedSelection(name="Outer", entity="face", members=[outer_face]),
        *(
            [
                NamedSelection(
                    name="Inner", entity="face", members=[f for f in lateral if f != outer_face]
                )
            ]
            if bores
            else []
        ),
    ]


def _coil(kind: str, body: str, current: float, phase: float = 0.0, turns=1.0, fill=1.0):
    values = {"I": Quantity(value=current, unit="A"), "phase": Quantity(value=phase, unit="deg")}
    if kind == "stranded_coil":
        values |= {
            "turns": Quantity(value=turns, unit="dimensionless"),
            "fill": Quantity(value=fill, unit="dimensionless"),
        }
    return Feature(type=kind, selection=body, values=values)


def names_of(wires) -> list[str]:
    return [f"Wire{k}" for k in range(1, len(wires) + 1)]


def _project(tmp_path, solver_name, wires, frequencies, *, wire_size, coils, terminals=True):
    """Wires (x, radius) in one air tube of radius R; `coils(names)` gives the coil
    features, one per wire part named Wire1, Wire2..."""
    p = Project(name="eddy")
    copper, air = add_library_material(p, "copper"), add_library_material(p, "air")
    air_geo = p.add(
        Geometry(
            name="Air",
            source="air.step",
            selections=_cylinder(tmp_path / "air.step", R, L, bores=wires),
        )
    )
    parts, materials, contacts, features = (
        [PartInstance(name="Gap", geometry=air_geo.id)],
        [],
        [],
        [],
    )
    names = []
    for k, (x, r) in enumerate(wires, start=1):
        geo = p.add(
            Geometry(
                name=f"Wire{k}",
                source=f"wire{k}.step",
                selections=_cylinder(tmp_path / f"wire{k}.step", r, L),
            )
        )
        name = f"Wire{k}"
        names.append(name)
        parts.append(PartInstance(name=name, geometry=geo.id, translation=(x, 0.0, 0.0)))
        materials.append(MaterialAssignment(material=copper.id, selection=f"{name}/Body"))
        driven = any(c.selection == f"{name}/Body" for c in coils(names_of(wires)))
        if terminals and driven:
            features += [
                Feature(type="coil_start", selection=f"{name}/Start"),
                Feature(type="coil_end", selection=f"{name}/End"),
            ]
        elif not driven:  # a passive conductor: its ends are part of the insulated planes
            features += [
                Feature(type="magnetic_insulation", selection=f"{name}/{s}")
                for s in ("Start", "End")
            ]
        contacts.append(
            Contact(name=f"Surface {k}", type="bonded", source=f"{name}/Outer", target="Gap/Inner")
        )
    materials.append(MaterialAssignment(material=air.id, selection="Gap/Body"))
    features += [
        Feature(type="magnetic_insulation", selection=f"Gap/{s}") for s in ("Start", "End", "Outer")
    ]
    mesh = p.add(
        MeshRecipe(
            name="Tets",
            element_order=1,
            global_size=Quantity(value=0.5, unit="mm"),
            local_sizes={f"{n}/Body": Quantity(value=wire_size, unit="mm") for n in names},
        )
    )
    comp = p.add(
        Component(
            name="Wires",
            parts=parts,
            mesh=mesh.id,
            materials=materials,
            contacts=contacts,
            physics=[Physics(type="magnetic_fields", features=[*coils(names), *features])],
        )
    )
    text = ", ".join(f"{f:g}" for f in frequencies)
    step = (
        SolveStep(type="frequency_domain", settings={"frequencies": text})
        if frequencies
        else SolveStep(type="stationary")
    )
    study = p.add(Study(name="AC", component=comp.id, solver=solver_name, steps=[step]))
    return p, study


def _sigma(project):
    return (
        next(m for m in project.objects.values() if getattr(m, "name", "") == "Copper")
        .properties["electrical_conductivity"]
        .to_si()
    )


def _centres(mesh, ids):
    """Element centres (corner averages) of volume elements `ids`."""
    found = {}
    for b in mesh.blocks:
        if b.dim == 3:
            xyz = mesh.node_coords(b.connectivity[:, :4].ravel()).reshape(-1, 4, 3).mean(axis=1)
            found.update(zip(b.ids.tolist(), xyz, strict=True))
    return np.array([found[int(i)] for i in ids])


def _global(step, name, component):
    f = step.fields[name]
    return float(f.values[0, f.components.index(component)])


@solver
def test_skin_effect_in_a_round_wire(tmp_path, solver):
    hz = 20e3
    project, study = _project(
        tmp_path,
        solver,
        [(0.0, A)],
        [1e3, hz],
        wire_size=0.12,
        coils=lambda n: [_coil("coil_current", f"{n[0]}/Body", CURRENT)],
    )
    run = run_study(project, study.id, tmp_path, tmp_path / "run", timeout_s=1800)
    sigma = _sigma(project)
    for step, f in zip(run.results.steps, (1e3, hz), strict=True):
        assert step.value == pytest.approx(f)
        w = 2 * math.pi * f
        delta = math.sqrt(2 / (w * MU0 * sigma))
        k = (1 - 1j) / delta
        z_int = k * jv(0, k * A) / (2 * math.pi * A * sigma * jv(1, k * A))
        r_exact = z_int.real * L
        l_exact = (z_int.imag / w + MU0 * math.log(R / A) / (2 * math.pi)) * L
        r = _global(step, "coil_resistance", "Wire1/Body")
        ind = _global(step, "coil_inductance", "Wire1/Body")
        loss = _global(step, "joule_loss", "total")
        print(
            f"{f:g} Hz, a/δ = {A / delta:.2f}: R {r / r_exact - 1:+.2%}, "
            f"L {ind / l_exact - 1:+.2%}, loss {loss / (0.5 * CURRENT**2 * r_exact) - 1:+.2%}"
        )
        assert r == pytest.approx(r_exact, rel=0.03)
        assert ind == pytest.approx(l_exact, rel=0.03)
        assert loss == pytest.approx(0.5 * CURRENT**2 * r, rel=0.01)  # the same power twice

    # The current crowds to the surface: |J| across the wire against the Bessel profile.
    step = run.results.steps[-1]
    j = step.fields["current_density_amplitude"]
    centre = _centres(run.mesh, j.ids)
    r = np.hypot(centre[:, 0], centre[:, 1])
    inside = r < A * 0.97
    k = (1 - 1j) / math.sqrt(2 / (2 * math.pi * hz * MU0 * sigma))
    exact = np.abs(k * CURRENT * jv(0, k * r[inside]) / (2 * math.pi * A * jv(1, k * A)))
    error = np.abs(j.values[inside, 2] - exact) / exact.max()
    print(f"|J| error: mean {error.mean():.2%}, max {error.max():.2%}")
    assert error.mean() < 0.03
    assert (
        j.values[inside, 2][r[inside] > 0.8 * A].mean()
        > 1.5 * j.values[inside, 2][r[inside] < 0.3 * A].mean()
    )


def _volumes(mesh, ids):
    found = {}
    for b in mesh.blocks:
        if b.dim == 3:
            p = mesh.node_coords(b.connectivity[:, :4].ravel()).reshape(-1, 4, 3)
            v = (
                np.abs(
                    np.einsum(
                        "ij,ij->i",
                        p[:, 1] - p[:, 0],
                        np.cross(p[:, 2] - p[:, 0], p[:, 3] - p[:, 0]),
                    )
                )
                / 6
            )
            found.update(zip(b.ids.tolist(), v, strict=True))
    return np.array([found[int(i)] for i in ids])


TURNS, FILL = 5.0, 0.6


@solver
@pytest.mark.parametrize("hz", [0.0, 5e3])
def test_a_winding_keeps_its_current_uniform(tmp_path, solver, hz):
    """A stranded coil: DC resistance and inductance at any frequency, B as at DC."""
    project, study = _project(
        tmp_path,
        solver,
        [(0.0, A)],
        [hz] if hz else [],
        wire_size=0.25,
        coils=lambda n: [
            _coil("stranded_coil", f"{n[0]}/Body", CURRENT / TURNS, turns=TURNS, fill=FILL)
        ],
    )
    run = run_study(project, study.id, tmp_path, tmp_path / "run", timeout_s=1800)
    (step,) = run.results.steps
    sigma = _sigma(project)
    r_exact = TURNS**2 * L / (sigma * FILL * math.pi * A**2)
    l_exact = TURNS**2 * MU0 * L * (0.25 + math.log(R / A)) / (2 * math.pi)
    r = _global(step, "coil_resistance", "Wire1/Body")
    ind = _global(step, "coil_inductance", "Wire1/Body")
    print(f"{hz:g} Hz: R {r / r_exact - 1:+.2%}, L {ind / l_exact - 1:+.2%}")
    assert r == pytest.approx(r_exact, rel=0.02)  # the meshed section is a little smaller
    assert ind == pytest.approx(l_exact, rel=0.02)
    name = "current_density_amplitude" if hz else "current_density"
    j = np.abs(step.fields[name].values[:, 2])
    inside = j > 0
    assert np.std(j[inside]) / j[inside].mean() < 0.002  # uniform: no eddy currents in it

    b = step.fields["magnetic_flux_density_amplitude" if hz else "magnetic_flux_density"]
    xyz = run.mesh.node_coords(b.ids)
    rr = np.hypot(xyz[:, 0], xyz[:, 1])
    gap = (rr > 1.5 * A) & (rr < 0.8 * R) & (np.abs(xyz[:, 2] - L / 2) < L / 4)
    exact = MU0 * CURRENT / (2 * math.pi * rr[gap])
    assert np.mean(np.abs(np.linalg.norm(b.values[gap], axis=1) / exact - 1)) < 0.03


@solver
def test_two_windings_each_carry_their_own_current(tmp_path, solver):
    """Two wires side by side, 10 A at 0° and 6 A at 90°: each section carries its own."""
    currents = {"Wire1/Body": (10.0, 0.0), "Wire2/Body": (6.0, 90.0)}
    project, study = _project(
        tmp_path,
        solver,
        [(-1.5e-3, 0.6e-3), (1.5e-3, 0.6e-3)],
        [2e3],
        wire_size=0.3,
        coils=lambda n: [_coil("stranded_coil", f"{w}/Body", *currents[f"{w}/Body"]) for w in n],
    )
    run = run_study(project, study.id, tmp_path, tmp_path / "run", timeout_s=1800)
    (step,) = run.results.steps
    amp, phase = step.fields["current_density_amplitude"], step.fields["current_density_phase"]
    jz = amp.values[:, 2] * np.exp(1j * np.radians(phase.values[:, 2]))
    volume = _volumes(run.mesh, amp.ids)
    for body, (i, deg) in currents.items():
        mine = np.isin(amp.ids, run.mesh.groups[body].blocks(run.mesh)[0].ids)
        through = (jz[mine] * volume[mine]).sum() / L
        print(f"{body}: {abs(through):.4g} A at {np.degrees(np.angle(through)):.2f}°")
        assert abs(through) == pytest.approx(i, rel=1e-3)
        assert np.degrees(np.angle(through)) == pytest.approx(deg, abs=0.05)
    for body in currents:  # both have a resistance and an inductance
        assert _global(step, "coil_resistance", body) > 0
        assert _global(step, "coil_inductance", body) != 0


def test_shootout_elmer_and_fenicsx(tmp_path):
    """One mesh, both solvers: a driven wire beside a passive copper wire, whose eddy
    currents Elmer solves in A–V and FEniCSx in A (V = 0 there)."""
    from solvers import available

    if not (available("elmer") and available("fenicsx")):
        pytest.skip("needs Elmer and FEniCSx")
    from puzzle.model import ComparisonMetric, Shootout
    from puzzle.shootout import run_shootout

    project, study = _project(
        tmp_path,
        "elmer",
        [(-1.4e-3, 0.8e-3), (1.4e-3, 0.8e-3)],
        [10e3],
        wire_size=0.2,
        coils=lambda n: [_coil("coil_current", f"{n[0]}/Body", CURRENT)],
    )
    metrics = [
        ComparisonMetric(quantity=q, norm="relative_value", tolerance=0.02)
        for q in ("joule_loss", "magnetic_energy", "coil_resistance", "coil_inductance")
    ]
    study.shootout = Shootout(solvers=["fenicsx"], metrics=metrics)
    out = run_shootout(project, study.id, tmp_path, tmp_path / "runs", timeout_s=1800)
    for c in out.comparisons:
        print(f"{c.metric.quantity}: {c.value:.3%}")
        assert c.value < c.metric.tolerance
    passive = {
        s: _global(r.results.steps[0], "joule_loss", "Wire2/Body") for s, r in out.runs.items()
    }
    print(f"passive wire's loss: {passive}")
    assert passive["fenicsx"] == pytest.approx(passive["elmer"], rel=0.03)
    assert passive["elmer"] > 0.01 * _global(
        out.runs["elmer"].results.steps[0], "joule_loss", "total"
    )


def test_losses_heat_the_wire(tmp_path):
    """Elmer's losses at 1 kHz (a/δ = 0.48: nearly uniform) into a CalculiX heat study of
    the wire alone, its ends held at T0: T = T0 + q̄ z (L − z) / (2k), q̄ = loss / volume."""
    from solvers import available

    if not (available("elmer") and available("calculix")):
        pytest.skip("needs Elmer and CalculiX")
    from puzzle.model import Transfer
    from puzzle.transfer import _integral

    project, ac = _project(
        tmp_path,
        "elmer",
        [(0.0, A)],
        [1e3],
        wire_size=0.25,
        coils=lambda n: [_coil("coil_current", f"{n[0]}/Body", 100.0)],
    )
    wire = next(g for g in project.of_type(Geometry) if g.name == "Wire1")
    copper = next(m for m in project.objects.values() if getattr(m, "name", "") == "Copper")
    t0 = 300.0
    ends = [
        Feature(
            type="temperature", selection=f"Wire1/{s}", values={"T": Quantity(value=t0, unit="K")}
        )
        for s in ("Start", "End")
    ]
    mesh = project.add(
        MeshRecipe(name="Wire only", element_order=2, global_size=Quantity(value=0.3, unit="mm"))
    )
    comp = project.add(
        Component(
            name="Wire heat",
            parts=[PartInstance(name="Wire1", geometry=wire.id)],
            mesh=mesh.id,
            materials=[MaterialAssignment(material=copper.id, selection="Wire1/Body")],
            physics=[
                Physics(
                    type="heat_transfer",
                    features=ends,
                    settings={"temperature_dependent_materials": False},
                )
            ],
        )
    )
    heat = project.add(
        Study(
            name="Heat", component=comp.id, solver="calculix", steps=[SolveStep(type="stationary")]
        )
    )
    project.add(
        Transfer(
            name="Losses",
            source=ac.id,
            field="loss_density",
            target=heat.id,
            role="load",
            target_selection="Wire1/Body",
        )
    )
    run = run_study(project, heat.id, tmp_path, tmp_path / "heat", timeout_s=1800)
    t = run.results.last("temperature")
    z = run.mesh.node_coords(t.ids)[:, 2]
    k = copper.properties["thermal_conductivity"].to_si()
    volume = _integral(run.mesh, "Wire1/Body", lambda conn: np.ones(conn.shape))
    ac_run = run_study(project, ac.id, tmp_path, tmp_path / "ac", timeout_s=1800)
    lost = _global(ac_run.results.steps[0], "joule_loss", "Wire1/Body")
    q = lost / volume
    exact = t0 + q * z * (L - z) / (2 * k)
    rise = q * L**2 / (8 * k)
    print(f"wire heats {t.values.max() - t0:.4g} K above its ends (1D: {rise:.4g} K)")
    assert np.abs(t.values[:, 0] - exact).max() < 0.02 * rise
