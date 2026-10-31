"""A press fit (ADR 0056: the offset interface) against Lamé.

A steel shaft of radius b = 10 mm in a steel ring b..c = 20 mm, both 2 mm thick and
held in plane strain (z = 0 on both faces), a quarter by symmetry. The faces are
meshed coincident and the contact is given an offset of -δ, δ = 20 µm of radial
interference. Lamé's interface pressure for one material in plane strain:

    p = E' δ (c² - b²) / (2 b c²),   E' = E / (1 - ν²)

and the ring's hoop stress at its bore σθ = p (c² + b²) / (c² - b²)."""

import math

import gmsh
import numpy as np
import pytest
from solvers import over

from puzzle.geometry import gmsh_session, import_step
from puzzle.materials import add_library_material, library_material
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
from puzzle.post.contact import contact_results
from puzzle.solve import run_study

B, C, H, DELTA = 0.010, 0.020, 0.002, 20e-6


def _quarter(path, inner, outer):
    with gmsh_session("quarter"):
        occ = gmsh.model.occ
        disc = occ.addCylinder(0, 0, 0, 0, 0, H, outer, angle=math.pi / 2)
        if inner:
            hole = occ.addCylinder(0, 0, -H, 0, 0, 3 * H, inner, angle=math.pi / 2)
            occ.cut([(3, disc)], [(3, hole)])
        occ.synchronize()
        gmsh.write(str(path))
    model = import_step(path)
    cylinders = sorted(model.faces_of_kind("Cylinder"), key=lambda f: model[f].bbox[3])
    return [
        NamedSelection(name="Body", entity="body", members=["body-1"]),
        NamedSelection(name="Bottom", entity="face", members=model.faces_on_plane("z", 0.0)),
        NamedSelection(name="Top", entity="face", members=model.faces_on_plane("z", H)),
        NamedSelection(name="SymX", entity="face", members=model.faces_on_plane("x", 0.0)),
        NamedSelection(name="SymY", entity="face", members=model.faces_on_plane("y", 0.0)),
        NamedSelection(name="Bore" if inner else "Outer", entity="face", members=cylinders[:1]),
    ]


def _press_fit(tmp_path, solver):
    p = Project(name="press fit")
    shaft = p.add(Geometry(name="Shaft", source="shaft.step",
                           selections=_quarter(tmp_path / "shaft.step", 0.0, B)))  # fmt: skip
    ring = p.add(Geometry(name="Ring", source="ring.step",
                          selections=_quarter(tmp_path / "ring.step", B, C)))  # fmt: skip
    steel = add_library_material(p, "structural_steel")
    mesh = p.add(
        MeshRecipe(
            name="Mesh",
            global_size=Quantity(value=1.0, unit="mm"),
            local_sizes={"Shaft/Body": Quantity(value=0.8, unit="mm")},
        )
    )
    zero = Quantity(value=0, unit="m")
    features = [
        Feature(type="displacement", selection=f"{part}/{face}", values={axis: zero})
        for part in ("Shaft", "Ring")
        for face, axis in (("Bottom", "z"), ("Top", "z"), ("SymX", "x"), ("SymY", "y"))
    ]
    comp = p.add(Component(
        name="Fit",
        parts=[PartInstance(name="Shaft", geometry=shaft.id),
               PartInstance(name="Ring", geometry=ring.id)],
        mesh=mesh.id,
        materials=[MaterialAssignment(material=steel.id, selection=f"{n}/Body")
                   for n in ("Shaft", "Ring")],
        contacts=[Contact(name="Fit", type="frictionless", source="Shaft/Outer",
                          target="Ring/Bore", interface="offset",
                          offset=Quantity(value=-DELTA * 1e6, unit="um"))],
        physics=[Physics(type="solid_mechanics", features=features)],
    ))  # fmt: skip
    study = p.add(Study(name="Press", component=comp.id, solver=solver,
                        steps=[SolveStep(type="stationary")]))  # fmt: skip
    return p, study, comp


@over("calculix", "code_aster")
def test_press_fit_pressure_is_lames(tmp_path, solver):
    p, study, comp = _press_fit(tmp_path, solver)
    run = run_study(p, study.id, tmp_path, tmp_path / "run", timeout_s=1800)
    props = library_material("structural_steel").properties
    e, nu = props["youngs_modulus"].to_si(), props["poissons_ratio"].to_si()
    exact = e / (1 - nu**2) * DELTA * (C**2 - B**2) / (2 * B * C**2)
    step = run.results.steps[-1]
    [r] = contact_results(step, comp, run.mesh)
    area = math.pi / 2 * B * H
    mean = r.normal_force / area
    pressure = step.fields["contact_pressure"].values[:, 0]
    print(f"{solver}: mean pressure {mean:.5g} Pa (Lamé {exact:.5g}), nodes "
          f"{pressure.min():.4g}..{pressure.max():.4g}, in contact {r.area_share:.3f}")  # fmt: skip
    assert r.area_share == pytest.approx(1.0, abs=0.02)
    assert mean == pytest.approx(exact, rel=0.03)
    # The ring's hoop stress at its bore, at 45° (away from the symmetry planes).
    stress = step.fields["stress"]
    xyz = run.mesh.node_coords(stress.ids)
    r_xy = np.hypot(xyz[:, 0], xyz[:, 1])
    ring = np.isin(stress.ids, run.mesh.groups["Ring/Bore"].node_ids)
    near = ring & (np.abs(np.arctan2(xyz[:, 1], xyz[:, 0]) - math.pi / 4) < 0.2)
    s = stress.values[near]
    c, s_ = np.cos(math.pi / 4), np.sin(math.pi / 4)
    hoop = s[:, 0] * s_**2 + s[:, 1] * c**2 - 2 * s[:, 3] * s_ * c
    expected = exact * (C**2 + B**2) / (C**2 - B**2)
    print(f"{solver}: hoop at the bore {hoop.mean():.5g} Pa (Lamé {expected:.5g}), r "
          f"{r_xy[near].mean():.5g}")  # fmt: skip
    assert hoop.mean() == pytest.approx(expected, rel=0.05)
