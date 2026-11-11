"""Contact between separately meshed parts (CalculiX and Code_Aster).

The 200 x 10 x 10 mm steel bar is two halves joined at x = 100 mm. Each half is meshed
on its own with a different element size, so the interface nodes do not coincide and
the solver must connect them.

- Uniaxial patch test: symmetry planes and an axial load. A bond must pass the
  uniform stress F/A across the interface; the end displacement is F L / (E A).
- Split cantilever: fixed root, tip load. A bond must reproduce the beam deflection.
"""

import gmsh
import numpy as np
import pytest
from conftest_geometry import split_bar_project
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
from puzzle.solve import run_study

HALF, B, H = 0.1, 0.01, 0.01
A, INERTIA = B * H, B * H**3 / 12
STEEL = library_material("structural_steel").properties
E, NU = STEEL["youngs_modulus"].to_si(), STEEL["poissons_ratio"].to_si()


def _bond(meshing="nonconformal"):
    # The finer half is the source (slave) side.
    return Contact(
        name="Joint", type="bonded", source="Right/Start", target="Left/End", meshing=meshing
    )


def _force(selection, **xyz):
    return Feature(
        type="force",
        selection=selection,
        values={k: Quantity(value=v, unit="N") for k, v in xyz.items()},
    )


def _symmetry():
    # Symmetry planes on the left half only: the right half is held through the bond,
    # and its interface nodes must not carry constraints of their own (CalculiX ties
    # dependent nodes, which an SPC on the same node contradicts).
    planes = [
        Feature(
            type="displacement",
            selection=f"Left/{face}",
            values={axis: Quantity(value=0, unit="m")},
        )
        for face, axis in (("Side", "y"), ("Bottom", "z"))
    ]
    start = Feature(
        type="displacement", selection="Left/Start", values={"x": Quantity(value=0, unit="m")}
    )
    return [*planes, start]


@over("calculix", "code_aster")
def test_tie_passes_uniform_stress(tmp_path, solver):
    force = 2000.0  # N
    project, study = split_bar_project(
        tmp_path, solver, [*_symmetry(), _force("Right/End", x=force)], _bond()
    )
    run = run_study(project, study.id, tmp_path, tmp_path / "run", timeout_s=900)
    u = run.results.last("displacement")
    x = run.mesh.node_coords(u.ids)[:, 0]
    exact = force / (E * A) * x
    print(
        f"{solver}: end displacement {u.values[x > 2 * HALF - 1e-9, 0].mean():.6e} m "
        f"(exact {force * 2 * HALF / (E * A):.6e})"
    )
    assert np.abs(u.values[:, 0] - exact).max() < 1e-3 * exact.max()
    stress = run.results.last("stress")
    error = np.abs(stress.values[:, 0] - force / A) / (force / A)
    near = np.abs(run.mesh.node_coords(stress.ids)[:, 0] - HALF) < 5e-3
    print(
        f"{solver}: stress error {error[~near].max():.1e} away from the joint, "
        f"{error[near].mean():.1e} mean / {error[near].max():.1e} max within 5 mm"
    )
    # Exact away from the joint; a node-to-surface tie disturbs stress locally, at the
    # worst node by how the two meshes meet there (17 % on Linux's Gmsh mesh, 1 % mean).
    assert error[~near].max() < 2e-3
    assert error[near].mean() < 0.02 and error[near].max() < 0.25


@over("calculix", "code_aster")
def test_tie_carries_bending(tmp_path, solver):
    load = -100.0  # N at the tip
    fixed = Feature(type="fixed", selection="Left/Start")
    deflections = {}
    for meshing in ("nonconformal", "conformal"):
        work = tmp_path / meshing
        work.mkdir()
        project, study = split_bar_project(
            work, solver, [fixed, _force("Right/End", y=load)], _bond(meshing)
        )
        run = run_study(project, study.id, work, work / "run", timeout_s=900)
        u = run.results.last("displacement")
        x = run.mesh.node_coords(u.ids)[:, 0]
        deflections[meshing] = u.values[x > 2 * HALF - 1e-9, 1].mean()
    length = 2 * HALF
    beam = load * length**3 / (3 * E * INERTIA) + load * length / ((5 / 6) * E / (2 * (1 + NU)) * A)
    print(
        f"{solver}: tied {deflections['nonconformal']:.6e}, conformal "
        f"{deflections['conformal']:.6e}, beam {beam:.6e} m"
    )
    assert deflections["nonconformal"] == pytest.approx(beam, rel=0.02)
    assert deflections["nonconformal"] == pytest.approx(deflections["conformal"], rel=5e-3)


@over("calculix")
def test_tie_conducts_heat(tmp_path, solver):
    """Steady conduction 100 -> 20 C across the tie: the profile must stay linear."""
    hot = Feature(
        type="temperature", selection="Left/Start", values={"T": Quantity(value=100, unit="degC")}
    )
    cold = Feature(
        type="temperature", selection="Right/End", values={"T": Quantity(value=20, unit="degC")}
    )
    project, study = split_bar_project(
        tmp_path,
        solver,
        [hot, cold],
        _bond(),
        physics="heat_transfer",
        settings={"temperature_dependent_materials": False},
    )
    run = run_study(project, study.id, tmp_path, tmp_path / "run", timeout_s=900)
    T = run.results.last("temperature")
    x = run.mesh.node_coords(T.ids)[:, 0]
    exact = 373.15 - 80.0 * x / (2 * HALF)
    # The tie disturbs the profile locally by a few hundredths of a kelvin (of 80 K).
    assert np.abs(T.values[:, 0] - exact).max() < 0.05  # K


# --- frictionless contact: a stack of two materials -------------------------------------

SIDE = 0.01  # m, both blocks are 10 mm cubes
PRESSURE = 20e6  # Pa on the top


def _cube(path, overhang=0.0):
    with gmsh_session("cube"):
        gmsh.model.occ.addBox(-overhang, 0, 0, SIDE + 2 * overhang, SIDE + overhang, SIDE)
        gmsh.model.occ.synchronize()
        gmsh.write(str(path))
    model = import_step(path)
    return [
        NamedSelection(name="Body", entity="body", members=["body-1"]),
        NamedSelection(name="Bottom", entity="face", members=model.faces_on_plane("z", 0.0)),
        NamedSelection(name="Top", entity="face", members=model.faces_on_plane("z", SIDE)),
        NamedSelection(name="SymX", entity="face", members=model.faces_on_plane("x", 0.0)),
        NamedSelection(name="SymY", entity="face", members=model.faces_on_plane("y", 0.0)),
    ]


def _stack(tmp_path, solver, contact, features=None, *, overhang=0.0, order=2):
    """Base cube (steel) under a cap cube (aluminium), meshed separately. With
    `overhang`, the base is wider than the cap by that much on +x, -x and +y."""
    p = Project(name="stack")
    geo = p.add(Geometry(name="Cube", source="cube.step", selections=_cube(tmp_path / "cube.step")))
    base = geo
    if overhang:
        base = p.add(
            Geometry(
                name="Wide base",
                source="base.step",
                selections=_cube(tmp_path / "base.step", overhang=overhang),
            )
        )
    steel = add_library_material(p, "structural_steel")
    aluminium = add_library_material(p, "aluminium_6061_t6")
    mesh = p.add(
        MeshRecipe(
            name="Unmatched",
            element_order=order,
            global_size=Quantity(value=2.5, unit="mm"),
            local_sizes={"Cap/Body": Quantity(value=1.7, unit="mm")},
        )
    )
    if features is None:
        symmetry = [
            Feature(
                type="displacement",
                selection=f"{part}/{face}",
                values={axis: Quantity(value=0, unit="m")},
            )
            for part in ("Base", "Cap")
            for face, axis in (("SymX", "x"), ("SymY", "y"))
        ]
        features = [
            *symmetry,
            Feature(
                type="displacement",
                selection="Base/Bottom",
                values={"z": Quantity(value=0, unit="m")},
            ),
            Feature(
                type="pressure",
                selection="Cap/Top",
                values={"p": Quantity(value=PRESSURE, unit="Pa")},
            ),
        ]

    comp = p.add(
        Component(
            name="Stack",
            parts=[
                PartInstance(name="Base", geometry=base.id),
                PartInstance(name="Cap", geometry=geo.id, translation=(0.0, 0.0, SIDE)),
            ],
            mesh=mesh.id,
            materials=[
                MaterialAssignment(material=steel.id, selection="Base/Body"),
                MaterialAssignment(material=aluminium.id, selection="Cap/Body"),
            ],
            contacts=[contact],
            physics=[Physics(type="solid_mechanics", features=features)],
        )
    )
    study = p.add(
        Study(name="Press", component=comp.id, solver=solver, steps=[SolveStep(type="stationary")])
    )
    return p, study


@over("calculix", "code_aster")
@pytest.mark.parametrize("kind", ["frictionless", "no_separation"])
def test_frictionless_stack_is_uniaxial(tmp_path, solver, kind):
    """Pressed: frictionless and no-separation interfaces both let each block grow
    sideways by its own Poisson strain (no shear across), the stack shortening as two
    springs in series."""
    # Accurate: the stack is loaded through the interface, where a penalty's give adds to
    # the blocks' own (balanced, the default, is 0.8 % over here; see PENALTY_FACTORS).
    contact = Contact(name="Interface", type=kind, source="Cap/Bottom",
                      target="Base/Top", stiffness="accurate")  # fmt: skip
    project, study = _stack(tmp_path, solver, contact)
    run = run_study(project, study.id, tmp_path, tmp_path / "run", timeout_s=900)

    props = {k: library_material(k).properties for k in ("structural_steel", "aluminium_6061_t6")}
    (e_s, nu_s), (e_a, nu_a) = (
        (m["youngs_modulus"].to_si(), m["poissons_ratio"].to_si()) for m in props.values()
    )
    u = run.results.last("displacement")
    xyz = run.mesh.node_coords(u.ids)
    top = np.isclose(xyz[:, 2], 2 * SIDE)
    expected_top = -PRESSURE * SIDE * (1 / e_s + 1 / e_a)
    # Lateral growth at x = SIDE just either side of the interface: each block's own nu p / E.
    cap_edge = np.isin(u.ids, run.mesh.groups["Cap/Bottom"].node_ids) & np.isclose(xyz[:, 0], SIDE)
    base_edge = np.isin(u.ids, run.mesh.groups["Base/Top"].node_ids) & np.isclose(xyz[:, 0], SIDE)
    cap_growth, base_growth = u.values[cap_edge, 0].mean(), u.values[base_edge, 0].mean()
    print(
        f"{solver}: top {u.values[top, 2].mean():.5e} (exact {expected_top:.5e}); lateral cap "
        f"{cap_growth:.4e} (exact {nu_a * PRESSURE / e_a * SIDE:.4e}), base {base_growth:.4e} "
        f"(exact {nu_s * PRESSURE / e_s * SIDE:.4e})"
    )
    assert u.values[top, 2].mean() == pytest.approx(expected_top, rel=2e-3)
    assert cap_growth == pytest.approx(nu_a * PRESSURE / e_a * SIDE, rel=0.01)
    assert base_growth == pytest.approx(nu_s * PRESSURE / e_s * SIDE, rel=0.01)

    stress = run.results.last("stress")
    z = run.mesh.node_coords(stress.ids)[:, 2]
    near = np.abs(z - SIDE) < 2.5e-3  # within an element of the interface
    szz = np.abs(stress.values[:, 2] + PRESSURE) / PRESSURE
    sxx = np.abs(stress.values[:, 0]) / PRESSURE
    print(
        f"{solver}: szz error {szz[~near].max():.1e} away / {szz[near].mean():.1e} mean "
        f"{szz[near].max():.1e} max near; |sxx|/p {sxx[~near].max():.1e} away, "
        f"{sxx[near].max():.1e} near"
    )
    # Uniaxial away from the interface; contact disturbs nodal stress next to it. Code_Aster's
    # no separation is a kinematic relation (LIAISON_MAIL DNOR) interpolating the master
    # face's quadratic field, whose nodal stresses are noisier there (measured 1.2 % away,
    # 33 % at a node next to it) while its displacements stay within 0.3 % of exact.
    loose = kind == "no_separation" and solver == "code_aster"
    away, near_max, sxx_near = (0.015, 0.4, 0.15) if loose else (0.01, 0.1, 0.05)
    assert szz[~near].max() < away and sxx[~near].max() < away
    assert szz[near].mean() < 0.02 and szz[near].max() < near_max and sxx[near].max() < sxx_near


@over("calculix", "code_aster")
def test_sliding_friction_obeys_coulomb(tmp_path, solver):
    """Press the cap down 1 um and drag its top 10 um sideways: the whole interface
    slides, so the tangential reaction is mu times the normal one.

    The cap is held only at its top (no condition on its interface nodes). For Code_Aster
    the base overhangs the cap, so the slave face lies inside the master one, and the
    mesh is first-order (see its adapter)."""
    mu = 0.3
    zero = Quantity(value=0, unit="m")
    features = [
        Feature(type="fixed", selection="Base/Bottom"),
        Feature(type="displacement", selection="Base/SymY", values={"y": zero}),
        Feature(
            type="displacement",
            selection="Cap/Top",
            values={
                "x": Quantity(value=10, unit="um"),
                "y": zero,
                "z": Quantity(value=-1, unit="um"),
            },
        ),
    ]
    contact = Contact(
        name="Interface", type="frictional", friction=mu, source="Cap/Bottom", target="Base/Top"
    )
    order = 1 if solver == "code_aster" else 2
    # Code_Aster's friction needs the slave face inside the master one; CalculiX's
    # surface-to-surface penalty inverted an element with that overhang, and needs none.
    overhang = 0.002 if solver == "code_aster" else 0.0
    project, study = _stack(tmp_path, solver, contact, features, overhang=overhang, order=order)
    run = run_study(project, study.id, tmp_path, tmp_path / "run", timeout_s=900)

    from puzzle.results.query import total

    tx, _, tz = total(run.results, run.mesh, "reaction_force", "Cap/Top")["total"]
    print(f"{solver}: normal {tz:.4g} N, tangential {tx:.4g} N, ratio {abs(tx / tz):.4f}")
    assert abs(tz) > 50  # really pressed
    assert abs(tx / tz) == pytest.approx(mu, rel=0.01)


# --- no separation and rough (ADR 0039's two other types) ---------------------------------

DELTA = 1e-6  # m, the cap's top pulled up


def _pulled(contact, *, symmetric: bool):
    """The cap's top pulled up by DELTA. Symmetric: the default stack's symmetry planes
    (each block free to shrink sideways, so a closed interface is uniaxial); else the
    base's bottom fixed and the cap held at its top only (Code_Aster's rough contact
    takes no condition on its slave nodes)."""
    zero = Quantity(value=0, unit="m")
    up = Quantity(value=DELTA, unit="m")
    if symmetric:
        return [
            *(Feature(type="displacement", selection=f"{part}/{face}", values={axis: zero})
              for part in ("Base", "Cap") for face, axis in (("SymX", "x"), ("SymY", "y"))),
            Feature(type="displacement", selection="Base/Bottom", values={"z": zero}),
            Feature(type="displacement", selection="Cap/Top", values={"z": up}),
        ]  # fmt: skip
    return [Feature(type="fixed", selection="Base/Bottom"),
            Feature(type="displacement", selection="Cap/Top",
                    values={"x": zero, "y": zero, "z": up})]  # fmt: skip


def _springs() -> float:
    """The stack's pull for DELTA, its blocks in series (uniaxial: free sideways)."""
    e_s = library_material("structural_steel").properties["youngs_modulus"].to_si()
    e_a = library_material("aluminium_6061_t6").properties["youngs_modulus"].to_si()
    return DELTA * SIDE * SIDE / (SIDE / e_s + SIDE / e_a)


@over("calculix", "code_aster")
def test_no_separation_holds_a_pull(tmp_path, solver):
    """No separation: pulled apart, the interface holds, so the stack carries its
    uniaxial pull, F = delta A / (L/E_steel + L/E_aluminium); frictionless would open."""
    from puzzle.results.query import total

    contact = Contact(name="Interface", type="no_separation", source="Cap/Bottom",
                      target="Base/Top", stiffness="accurate")  # fmt: skip
    project, study = _stack(tmp_path, solver, contact, _pulled(contact, symmetric=True))
    run = run_study(project, study.id, tmp_path, tmp_path / "run", timeout_s=900)
    fz = total(run.results, run.mesh, "reaction_force", "Cap/Top")["total"][2]
    print(f"{solver}: pull {fz:.6g} N (series springs {_springs():.6g} N)")
    assert fz == pytest.approx(_springs(), rel=5e-3)


@over("calculix", "code_aster")
@pytest.mark.parametrize("kind", ["rough", "frictionless"])
def test_rough_opens_under_a_pull(tmp_path, solver, kind):
    """Rough (and frictionless): pulled apart, the interface opens, so the cap carries
    next to nothing (a penalty's or a multiplier's remainder) against no separation's
    pull."""
    from puzzle.results.query import total

    contact = Contact(name="Interface", type=kind, source="Cap/Bottom", target="Base/Top")
    order = 1 if solver == "code_aster" else 2
    overhang = 0.002 if solver == "code_aster" else 0.0
    project, study = _stack(tmp_path, solver, contact, _pulled(contact, symmetric=False),
                            overhang=overhang, order=order)  # fmt: skip
    run = run_study(project, study.id, tmp_path, tmp_path / "run", timeout_s=900)
    fz = total(run.results, run.mesh, "reaction_force", "Cap/Top")["total"][2]
    print(f"{solver}: rough pull {fz:.4g} N (a closed interface would carry ~{_springs():.4g})")
    assert abs(fz) < 1e-3 * _springs()


@over("calculix", "code_aster")
def test_rough_sticks_as_a_bond_when_pressed(tmp_path, solver):
    """Rough: pressed 1 um and dragged 0.3 um sideways, no point slips, so the cap pulls
    the base along as a bonded interface does: the same tangential and normal reactions
    (within 2 %)."""
    from puzzle.results.query import total

    zero = Quantity(value=0, unit="m")
    features = [
        Feature(type="fixed", selection="Base/Bottom"),
        Feature(type="displacement", selection="Base/SymY", values={"y": zero}),
        Feature(type="displacement", selection="Cap/Top", values={
            "x": Quantity(value=0.3, unit="um"), "y": zero,
            "z": Quantity(value=-1, unit="um")}),
    ]  # fmt: skip
    order = 1 if solver == "code_aster" else 2
    overhang = 0.002 if solver == "code_aster" else 0.0
    got = {}
    for kind in ("rough", "bonded"):
        contact = Contact(name="Interface", type=kind, source="Cap/Bottom", target="Base/Top",
                          meshing="nonconformal" if kind == "bonded" else None)  # fmt: skip
        (tmp_path / kind).mkdir()
        project, study = _stack(tmp_path / kind, solver, contact, features,
                                overhang=overhang, order=order)  # fmt: skip
        run = run_study(project, study.id, tmp_path / kind, tmp_path / kind / "run",
                        timeout_s=900)  # fmt: skip
        got[kind] = total(run.results, run.mesh, "reaction_force", "Cap/Top")["total"]
    (rx, _, rz), (bx, _, bz) = got["rough"], got["bonded"]
    print(f"{solver}: rough {rx:.4g}/{rz:.4g} N, bonded {bx:.4g}/{bz:.4g} N")
    assert rx == pytest.approx(bx, rel=0.02) and rz == pytest.approx(bz, rel=0.02)
    assert abs(rx) > 0.02 * abs(rz)  # really dragged
