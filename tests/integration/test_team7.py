"""TEAM Problem 7 (ADR 0047): an asymmetrical aluminium conductor with a hole.

An aluminium plate, 294 × 294 × 19 mm with a 108 mm square hole, under a racetrack
winding of 2742 turns carrying 1 A at 50 Hz (Fujiwara and Nakata, COMPEL 9 (1990)
137; https://www.compumag.org/wp/wp-content/uploads/2018/06/problem7.pdf). The measured
Bz (10⁻⁴ T) along A1–B1 (y = 72 mm, z = 34 mm, x from 0 to 288 mm) is as tabulated in
NGSolve's TEAM-7 notebook (https://ngsolve.github.io/TEAM-problems/TEAM-7/team7.html),
(its own plot negates the real part: its winding runs the other way). Here, with the
winding's current right-handed about +z and the e^{jωt} convention, Bz × 10⁴ conjugated
is the measurement.

The winding is a closed stranded coil (no terminal faces): Elmer's CoilSolver and
FEniCSx's circulating source.
"""

import math

import gmsh
import numpy as np
import pytest
from solvers import over

from puzzle import machine
from puzzle.geometry import gmsh_session, import_step
from puzzle.materials import add_library_material
from puzzle.model import (
    Component,
    Feature,
    Geometry,
    Material,
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

pytest.importorskip("OCP")

# FEniCSx peaked at 18.4 GB here (32 GB Mac); GitHub's 16 GB Linux and Windows machines
# ran out (the Linux one was shut down mid-run, SciPy's SuperLU failed on Windows).
_memory = machine.memory_bytes()
pytestmark = pytest.mark.skipif(
    _memory is not None and _memory < 24 * 2**30, reason="needs about 20 GB of memory"
)

X_MEASURED = np.arange(0, 289, 18) * 1e-3  # m
BZ_50HZ = np.array([
    -4.9 - 1.16j, -17.88 + 2.48j, -22.13 + 4.15j, -20.19 + 4.0j, -15.67 + 3.07j,
    0.36 + 2.31j, 43.64 + 1.89j, 78.11 + 4.97j, 71.55 + 12.61j, 60.44 + 14.15j,
    53.91 + 13.04j, 52.62 + 12.4j, 53.81 + 12.05j, 56.91 + 12.27j, 59.24 + 12.66j,
    52.78 + 9.96j, 27.61 + 2.26j,
])  # fmt: skip  # 10⁻⁴ T, measured


def _team7(tmp_path, solver):
    path = tmp_path / "team7.step"
    with gmsh_session("team7"):
        occ = gmsh.model.occ
        plate = occ.addBox(0, 0, 0, 0.294, 0.294, 0.019)
        hole = occ.addBox(0.018, 0.018, -0.001, 0.108, 0.108, 0.021)
        plate_cut, _ = occ.cut([(3, plate)], [(3, hole)])
        cx, cy = 0.194, 0.100  # the winding's centre
        outer = occ.addRectangle(cx - 0.100, cy - 0.100, 0.049, 0.200, 0.200, roundedRadius=0.050)
        inner = occ.addRectangle(cx - 0.075, cy - 0.075, 0.049, 0.150, 0.150, roundedRadius=0.025)
        ring, _ = occ.cut([(2, outer)], [(2, inner)])
        coil = occ.extrude(ring, 0, 0, 0.100)
        air = occ.addBox(-0.2, -0.2, -0.2, 0.7, 0.7, 0.7)
        occ.fragment([(3, air)], plate_cut + [e for e in coil if e[0] == 3])
        occ.synchronize()
        gmsh.write(str(path))
    model = import_step(path)
    bodies = sorted(model.of_dim(3), key=lambda e: e.measure)  # plate, coil, air
    plate_b, coil_b, air_b = (b.name for b in bodies)
    outside = [
        f
        for axis, v in (("x", -0.2), ("x", 0.5), ("y", -0.2), ("y", 0.5), ("z", -0.2), ("z", 0.5))
        for f in model.faces_on_plane(axis, v)
    ]
    p = Project(name="team7")
    geo = p.add(
        Geometry(
            name="T7",
            source=path.name,
            selections=[
                NamedSelection(name="Plate", entity="body", members=[plate_b]),
                NamedSelection(name="Coil", entity="body", members=[coil_b]),
                NamedSelection(name="Air", entity="body", members=[air_b]),
                NamedSelection(name="Outside", entity="face", members=outside),
            ],
        )
    )
    aluminium = p.add(
        Material(
            name="Aluminium (TEAM 7)",
            properties={
                "electrical_conductivity": Quantity(value=35.26e6, unit="S/m"),
                "relative_permeability": Quantity(value=1),
            },
        )
    )
    copper, air = add_library_material(p, "copper"), add_library_material(p, "air")
    mesh = p.add(
        MeshRecipe(
            name="Mesh",
            element_order=1,
            global_size=Quantity(value=40, unit="mm"),
            local_sizes={
                "T7/Plate": Quantity(value=6, unit="mm"),
                "T7/Coil": Quantity(value=12, unit="mm"),
            },
        )
    )
    winding = Feature(
        type="stranded_coil",
        selection="T7/Coil",
        values={
            "I": Quantity(value=1, unit="A"),
            "phase": Quantity(value=0, unit="deg"),
            "turns": Quantity(value=2742, unit="dimensionless"),
            "fill": Quantity(value=1, unit="dimensionless"),
        },
    )
    comp = p.add(
        Component(
            name="TEAM 7",
            parts=[PartInstance(name="T7", geometry=geo.id)],
            mesh=mesh.id,
            materials=[
                MaterialAssignment(material=aluminium.id, selection="T7/Plate"),
                MaterialAssignment(material=copper.id, selection="T7/Coil"),
                MaterialAssignment(material=air.id, selection="T7/Air"),
            ],
            physics=[
                Physics(
                    type="magnetic_fields",
                    features=[winding, Feature(type="magnetic_insulation", selection="T7/Outside")],
                )
            ],
        )
    )
    study = p.add(
        Study(
            name="50 Hz",
            component=comp.id,
            solver=solver,
            steps=[SolveStep(type="frequency_domain", settings={"frequencies": "50"})],
        )
    )
    return p, study


def _probe(run, points):
    """Bz (complex) at points, linear between the nodes near them."""
    from scipy.interpolate import LinearNDInterpolator

    step = run.results.steps[0]
    amp, phase = (step.fields[f"magnetic_flux_density_{k}"] for k in ("amplitude", "phase"))
    xyz = run.mesh.node_coords(amp.ids)
    near = np.linalg.norm(xyz[:, None, :2] - points[None, :, :2], axis=2).min(axis=1) < 0.03
    near &= np.abs(xyz[:, 2] - points[0, 2]) < 0.03
    bz = amp.values[near, 2] * np.exp(1j * np.radians(phase.at(amp.ids[near])[:, 2]))
    return LinearNDInterpolator(xyz[near], bz)(points)


@over("elmer", "fenicsx")
def test_team7_bz_along_a1_b1(tmp_path, solver):
    project, study = _team7(tmp_path, solver)
    run = run_study(project, study.id, tmp_path, tmp_path / "run", timeout_s=3600)
    points = np.column_stack([X_MEASURED, np.full(17, 0.072), np.full(17, 0.034)])
    # The measurements' convention is e^{-jωt}: their Bz is conj(Bz) here, for the
    # winding's current right-handed about +z (the up-going z axis).
    bz = np.conj(_probe(run, points)) * 1e4
    error = np.abs(bz - BZ_50HZ)
    scale = np.abs(BZ_50HZ).max()
    for x, got, want in zip(X_MEASURED, bz, BZ_50HZ, strict=True):
        print(
            f"{solver}: x = {x * 1e3:3.0f} mm  Bz {got.real:7.2f} {got.imag:+6.2f}j  "
            f"(measured {want.real:7.2f} {want.imag:+6.2f}j)"
        )
    rms = math.sqrt(np.mean(error**2)) / scale
    print(f"{solver}: rms difference {rms:.1%} of the largest measured |Bz|")
    assert rms < 0.06
    assert np.abs(bz.real - BZ_50HZ.real).max() < 0.12 * scale
