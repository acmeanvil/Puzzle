"""Thermal -> structural through a transfer (ADR 0009): thermal bending of a cantilever.

The 200 x 10 x 10 mm steel bar is held at 120 C on its bottom face and 20 C on its top,
so the temperature is linear through the thickness. Relative to a 20 C reference, the
thermal strain alpha (T - 20 C) bends the clamped bar with curvature alpha dT / h and
stretches it by alpha 50 K on average:

    tip lift      w = alpha dT L^2 / (2 h)
    tip stretch   u = alpha (dT / 2) L

The thermal study runs first (on its own solver and mesh), its temperature field is
mapped onto the structural mesh, and the structural study applies it as a load.
"""

import gmsh
import numpy as np
import pytest
from solvers import available

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
    Transfer,
)
from puzzle.results.store import ResultStore
from puzzle.solve import run_study

L, B, H = 0.2, 0.01, 0.01
HOT, COLD = 120.0, 20.0  # C
ALPHA = library_material("structural_steel").properties["thermal_expansion"].to_si()
LIFT = ALPHA * (HOT - COLD) * L**2 / (2 * H)
STRETCH = ALPHA * (HOT - COLD) / 2 * L


def _bar(path):
    with gmsh_session("bar"):
        gmsh.model.occ.addBox(0, 0, 0, L, B, H)
        gmsh.model.occ.synchronize()
        gmsh.write(str(path))
    m = import_step(path)
    return [
        NamedSelection(name="Body", entity="body", members=["body-1"]),
        NamedSelection(name="Root", entity="face", members=m.faces_on_plane("x", 0.0)),
        NamedSelection(name="Tip", entity="face", members=m.faces_on_plane("x", L)),
        NamedSelection(name="Bottom", entity="face", members=m.faces_on_plane("z", 0.0)),
        NamedSelection(name="Top", entity="face", members=m.faces_on_plane("z", H)),
    ]


def _project(tmp_path, thermal_solver, structural_solver, thermal_mesh_mm=None):
    p = Project(name="thermal bending")
    geo = p.add(Geometry(name="Bar", source="bar.step", selections=_bar(tmp_path / "bar.step")))
    steel = add_library_material(p, "structural_steel")
    fine = p.add(MeshRecipe(name="Fine", global_size=Quantity(value=2.5, unit="mm")))

    def component(name, mesh, physics):
        return p.add(
            Component(
                name=name,
                parts=[PartInstance(name="Bar", geometry=geo.id)],
                mesh=mesh.id,
                materials=[MaterialAssignment(material=steel.id, selection="Bar/Body")],
                physics=physics,
            )
        )

    heat = Physics(
        type="heat_transfer",
        features=[
            Feature(
                type="temperature",
                selection="Bar/Bottom",
                values={"T": Quantity(value=HOT, unit="degC")},
            ),
            Feature(
                type="temperature",
                selection="Bar/Top",
                values={"T": Quantity(value=COLD, unit="degC")},
            ),
        ],
        settings={"temperature_dependent_materials": False},
    )
    solid = Physics(
        type="solid_mechanics",
        features=[Feature(type="fixed", selection="Bar/Root")],
        settings={"reference_temperature": Quantity(value=COLD, unit="degC")},
    )
    if thermal_mesh_mm is None:  # one component, one mesh, both physics
        both = component("Bar", fine, [heat, solid])
        thermal_comp = structural_comp = both
    else:  # the thermal study on its own, coarser mesh: the field is interpolated
        coarse = p.add(
            MeshRecipe(name="Coarse", global_size=Quantity(value=thermal_mesh_mm, unit="mm"))
        )
        thermal_comp = component("Bar thermal", coarse, [heat])
        structural_comp = component("Bar structure", fine, [solid])
    thermal = p.add(
        Study(
            name="Thermal",
            component=thermal_comp.id,
            solver=thermal_solver,
            steps=[SolveStep(type="stationary", physics=["heat_transfer"])],
        )
    )
    static = p.add(
        Study(
            name="Static",
            component=structural_comp.id,
            solver=structural_solver,
            steps=[SolveStep(type="stationary", physics=["solid_mechanics"])],
        )
    )
    p.add(
        Transfer(
            name="Temperature",
            source=thermal.id,
            field="temperature",
            target=static.id,
            role="load",
        )
    )
    return p, static


def _tip(run):
    u = run.results.last("displacement")
    x = run.mesh.node_coords(u.ids)[:, 0]
    tip = u.values[x > L - 1e-9]
    return tip[:, 0].mean(), tip[:, 2].mean()


CHAINS = [
    ("calculix", "calculix", None),
    ("elmer", "calculix", None),  # the bracket example's pairing (ADR 0009)
    ("calculix", "fenicsx", None),
    ("calculix", "code_aster", None),
    ("calculix", "elmer", None),
    ("calculix", "calculix", 5.0),  # coarser thermal mesh: interpolated
]


@pytest.mark.parametrize(
    ("thermal", "structural", "thermal_mesh"),
    [
        pytest.param(
            *chain,
            id=f"{chain[0]}-to-{chain[1]}" + ("-interpolated" if chain[2] else ""),
            marks=pytest.mark.skipif(
                not (available(chain[0]) and available(chain[1])), reason="solver not available"
            ),
        )
        for chain in CHAINS
    ],
)
def test_thermal_bending(tmp_path, thermal, structural, thermal_mesh):
    project, static = _project(tmp_path, thermal, structural, thermal_mesh)
    store = ResultStore(tmp_path / "results")
    run = run_study(project, static.id, tmp_path, tmp_path / "run", store=store, timeout_s=900)
    stretch, lift = _tip(run)
    print(
        f"{thermal} -> {structural}: tip lift {lift:.5e} (exact {LIFT:.5e}), "
        f"stretch {stretch:.5e} (exact {STRETCH:.5e})"
    )
    assert lift == pytest.approx(LIFT, rel=0.02)
    assert stretch == pytest.approx(STRETCH, rel=0.02)

    # Free thermal bending is stress-free away from the clamp.
    stress = run.results.last("stress")
    x = run.mesh.node_coords(stress.ids)[:, 0]
    middle = (x > 0.25 * L) & (x < 0.75 * L)
    scale = (
        library_material("structural_steel").properties["youngs_modulus"].to_si()
        * ALPHA
        * (HOT - COLD)
    )
    print(f"  |sxx| at mid-span up to {np.abs(stress.values[middle, 0]).max() / scale:.1e} E a dT")
    assert np.abs(stress.values[middle, 0]).max() < 0.01 * scale

    # The chain is cached: an unchanged rerun reuses both studies' stored results.
    again = run_study(project, static.id, tmp_path, tmp_path / "again", store=store)
    assert again.cached and len(store.runs()) == 2


@pytest.mark.parametrize("solver", ["calculix", "code_aster", "fenicsx", "elmer"])
def test_a_temperature_on_one_body_leaves_the_other_cold(tmp_path, solver):
    """ADR 0090 package 7, to-check 5: a body temperature 100 K up on the left half of
    the split bar only, held at its start and bonded to the right half: the left grows
    by α ΔT L, the right is carried along unstrained, so both ends move alike. FEniCSx
    had filled the right half's nodes from the nearest heated ones and stretched it as
    well (Right/End 2.445e-4 m for 1.232e-4); Elmer refuses a load not on every node.
    Measured: CalculiX, Code_Aster and FEniCSx 1.23522e-4 and 1.23223e-4 m (α ΔT L
    1.216e-4: the held start's restraint)."""
    from conftest_geometry import HALF, split_bar_project

    if not available(solver):
        pytest.skip(f"{solver} not available")
    alpha = library_material("structural_steel").properties["thermal_expansion"].to_si()
    features = [Feature(type="fixed", selection="Left/Start"),
                Feature(type="body_temperature", selection="Left/Body",
                        values={"T": Quantity(value=120, unit="degC")})]  # fmt: skip
    bond = Contact(name="Bond", type="bonded", source="Right/Start", target="Left/End")
    p, study = split_bar_project(tmp_path, solver, features, bond,
                                 settings={"temperature_dependent_materials": False})  # fmt: skip
    if solver == "elmer":
        with pytest.raises(Exception, match="temperature load on every node"):
            run_study(p, study.id, tmp_path, tmp_path / "run")
        return
    run = run_study(p, study.id, tmp_path, tmp_path / "run", timeout_s=900)
    u = run.results.last("displacement")
    left = u.at(run.mesh.groups["Left/End"].node_ids)[:, 0].mean()
    right = u.at(run.mesh.groups["Right/End"].node_ids)[:, 0].mean()
    print(f"{solver}: Left/End {left:.6g} m, Right/End {right:.6g} m ({alpha * 100 * HALF:.6g})")
    assert left == pytest.approx(alpha * 100 * HALF, rel=0.03)
    assert right == pytest.approx(left, rel=0.005)  # the right half unstrained
