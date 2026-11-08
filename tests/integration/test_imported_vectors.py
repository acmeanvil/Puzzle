"""Imported fields, stage 2 (ADR 0044), on the 200 x 10 x 10 mm steel bar held at its
root, on all four FE solvers (CalculiX, Code_Aster, Elmer, FEniCSx):

- **a body force density is gravity without the density:** a uniform fz = −ρ g gives
  gravity's displacements, to the solvers' digits;
- **a traction:** normal to the tip and inward it is a pressure (t = −p n); along z it
  bends the bar as a cantilever with a tip load τ b h, Timoshenko's deflection
  F L³ / (3 E I) + F L / (κ G A) within the mesh's error;
- **imported vectors:** a traction field gives the uniform feature's answer, and so does a
  copy written turned 90° about z and moved, points and vectors, placed back by the
  use's placement (the vectors turn with the points);
- **an imported body force density:** a field of −ρ g gives gravity's displacements, and
  the cantilever's under its weight, q L⁴ / (8 E I) + q L² / (2 κ G A);
- **an imported linear traction:** tx = σ (2 z / h − 1) on the tip has no resultant and a
  moment M = σ (2 / h) I: pure bending, the tip's mean uz = −M L² / (2 E I) = −σ L² / (E h);
- **an imported displacement:** a linear field on the tip is imposed at its nodes exactly;
- **conservative mapping (EM → structural):** a coarse Lorentz-force cloud, as the
  ADR 0043 export writes it (f = J × B and each node's volume dV), mapped
  conservatively onto the finer bar: the root's reaction equals −∑ dV f, the source's
  total force ∫ J × B dV; and a coarse wall pressure with its faces' areas likewise.

CalculiX writes tractions and body forces as consistent nodal forces (∫N t dA, ∫N f dV)
and field displacements per node; Code_Aster the uniform ones as FORCE_FACE and
FORCE_INTERNE and the field ones as nodal forces and DDL_IMPO on one-node groups; Elmer
the field values as restart variables (Force i, Stress Bodyforce i and Displacement i =
Equals them) and FEniCSx as Lagrange functions: the last two integrate the field's
interpolant, which is the consistent nodal forces' load. Elmer and FEniCSx store no
reaction forces, so the conservative Lorentz cloud is checked there by the tip's
displacement against CalculiX's, the same discrete load on the same mesh.
"""

import numpy as np
import pytest
from conftest_geometry import BAR, bar_project, write_bar_step
from solvers import over

from puzzle.fields import new_field_data
from puzzle.materials import library_material
from puzzle.model import (
    Component,
    Feature,
    FieldPlacement,
    FieldUse,
    ImportedField,
    Physics,
    Quantity,
    SolveStep,
    Study,
)
from puzzle.results.query import total
from puzzle.solve import run_study

pytest.importorskip("OCP")

L, B, H = BAR
STEEL = library_material("structural_steel").properties
E = STEEL["youngs_modulus"].to_si()
NU = STEEL["poissons_ratio"].to_si()
RHO = STEEL["density"].to_si()
G = 9.81
REACTIONS = ("calculix", "code_aster")  # the solvers that store reaction forces
SOLVERS = ("calculix", "code_aster", "elmer", "fenicsx")
TURN = np.array([[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])  # +90° about z
AWAY = np.array([0.0, 1.0, 0.0])  # and 1 m along y
BACK = FieldPlacement(
    rotation=(0.0, 0.0, -90.0), about=(0.0, 1.0, 0.0), translation=(0.0, -1.0, 0.0)
)


def qty(value, unit):
    return Quantity(value=value, unit=unit)


def _csv(path, points, columns, header):
    rows = [header] + [
        ",".join(f"{c:.12g}" for c in (*p, *v)) for p, v in zip(points, columns, strict=True)
    ]
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def _run(where, features, solver, fields=()):
    """The bar held at its root with `features`; `fields` are (path, name) to import
    first, their ids handed to `features(ids)` when it is a function."""
    where.mkdir(parents=True, exist_ok=True)
    physics = Physics(type="solid_mechanics", features=[])
    project, comp = bar_project(write_bar_step(where / "bar.step"), [physics], mesh_size_mm=5)
    ids = [project.add(ImportedField(**new_field_data(where, p, name=n))).id for p, n in fields]
    physics.features[:] = [Feature(type="fixed", selection="Bar/Root"), *features(ids)]
    study = project.add(
        Study(name="s", component=comp.id, solver=solver, steps=[SolveStep(type="stationary")])
    )
    run = run_study(project, study.id, where, where / "run", timeout_s=900)
    run.context = (project, study, where, solver)  # for _expected_reaction
    return run


def _tip(run, component):
    tip = run.mesh.groups["Bar/Tip"].node_ids
    return run.results.last("displacement").at(tip)[:, component].mean()


def _expected_reaction(run, body_load):
    """What the root reports for a body load totalling `body_load`: −body_load on
    Code_Aster (REAC_NODA: internal minus external forces); CalculiX's RF is the internal
    force at the held nodes only, so the share of the body load applied straight to them
    (their ∫N f dV) goes into the support unreported."""
    project, study, root, solver = run.context
    if solver != "calculix":
        return -np.asarray(body_load)
    from puzzle.adapters.common import body_forces, feature_vector
    from puzzle.fields import field_inputs

    given = {i.source: i.field for i in field_inputs(project, root, study, run.mesh)}
    comp = project.get(study.component, Component)
    f = next(f for f in comp.physics[0].features if f.type == "body_force")

    def at(ids):
        return feature_vector(f, ("fx", "fy", "fz"), given, ids)[0]

    forces = body_forces(run.mesh.groups["Bar/Body"], run.mesh, at)
    held = sum(forces[int(n)] for n in run.mesh.groups["Bar/Root"].node_ids)
    return -(np.asarray(body_load) - held)


def _reaction(run):
    return np.asarray(total(run.results, run.mesh, "reaction_force", "Bar/Root")["total"])


@over(*SOLVERS)
def test_a_body_force_density_is_gravity_without_the_density(tmp_path, solver):
    gravity = _run(
        tmp_path / "g",
        lambda _: [Feature(type="gravity", selection="Bar/Body", values={"gz": qty(-G, "m/s^2")})],
        solver,
    )
    force = _run(
        tmp_path / "f",
        lambda _: [
            Feature(type="body_force", selection="Bar/Body", values={"fz": qty(-RHO * G, "N/m^3")})
        ],
        solver,
    )
    a, b = _tip(gravity, 2), _tip(force, 2)
    print(f"{solver}: tip uz under gravity {a:.9g} m, under fz = -rho g {b:.9g} m")
    assert b == pytest.approx(a, rel=1e-6)
    weight = [0, 0, -RHO * G * L * B * H]
    if solver in REACTIONS:
        expected = _expected_reaction(force, weight)
        np.testing.assert_allclose(_reaction(force), expected, rtol=1e-5, atol=1e-5 * -weight[2])


@over(*SOLVERS)
def test_a_traction_is_a_pressure_inward_and_bends_the_bar_along_z(tmp_path, solver):
    p, tau = 10e6, 1e6  # Pa
    pressed = _run(
        tmp_path / "p",
        lambda _: [Feature(type="pressure", selection="Bar/Tip", values={"p": qty(p, "Pa")})],
        solver,
    )
    pulled = _run(
        tmp_path / "t",
        lambda _: [
            Feature(  # the tip's outward normal is +x
                type="traction", selection="Bar/Tip", values={"tx": qty(-p, "Pa")}
            )
        ],
        solver,
    )
    a, b = _tip(pressed, 0), _tip(pulled, 0)
    print(f"{solver}: tip ux under pressure {a:.9g} m, under traction -p n {b:.9g} m")
    assert b == pytest.approx(a, rel=1e-6)
    sheared = _run(
        tmp_path / "s",
        lambda _: [Feature(type="traction", selection="Bar/Tip", values={"tz": qty(tau, "Pa")})],
        solver,
    )
    force, inertia, shear = tau * B * H, B * H**3 / 12, E / (2 * (1 + NU))
    exact = force * L**3 / (3 * E * inertia) + force * L / (5 / 6 * shear * B * H)
    w = _tip(sheared, 2)
    print(f"{solver}: tip uz {w:.6g} m, Timoshenko {exact:.6g} m ({w / exact - 1:+.2%})")
    assert w == pytest.approx(exact, rel=0.03)
    if solver in REACTIONS:
        np.testing.assert_allclose(_reaction(sheared), [0, 0, -force], rtol=1e-4, atol=1e-3)


def _tip_points():
    g = np.linspace(-0.002, 0.012, 8)
    yy, zz = np.meshgrid(g, g)
    return np.column_stack([np.full(yy.size, L), yy.ravel(), zz.ravel()])


@over(*SOLVERS)
def test_an_imported_traction_is_the_uniform_one_and_turns_with_its_points(tmp_path, solver):
    t = np.array([0.0, 2e5, 1e6])  # Pa: shear in y and z
    points = _tip_points()
    header = "x [m],y [m],z [m],tx [MPa],ty [MPa],tz [MPa]"
    plain = _csv(tmp_path / "walls.csv", points, np.tile(t / 1e6, (len(points), 1)), header)
    # The same data with its points and vectors turned +90° about z and moved 1 m along y.
    turned = _csv(
        tmp_path / "turned.csv",
        points @ TURN.T + AWAY,
        np.tile(TURN @ t / 1e6, (len(points), 1)),
        header,
    )

    def uses(ids, placement=None):
        extra = {"placement": placement} if placement is not None else {}
        return [
            Feature(
                type="traction",
                selection="Bar/Tip",
                values={},
                fields={c: FieldUse(field=ids[0], column=c, **extra) for c in ("tx", "ty", "tz")},
            )
        ]

    uniform = _run(
        tmp_path / "u",
        lambda _: [
            Feature(
                type="traction",
                selection="Bar/Tip",
                values={k: qty(v, "Pa") for k, v in zip(("tx", "ty", "tz"), t, strict=True)},
            )
        ],
        solver,
    )
    imported = _run(tmp_path / "i", uses, solver, [(plain, "Walls")])
    moved = _run(tmp_path / "m", lambda ids: uses(ids, BACK), solver, [(turned, "Turned")])
    for d in (1, 2):
        a, b, c = _tip(uniform, d), _tip(imported, d), _tip(moved, d)
        print(f"{solver}: tip u{'xyz'[d]} uniform {a:.9g}, imported {b:.9g}, turned back {c:.9g}")
        assert b == pytest.approx(a, rel=1e-7)
        assert c == pytest.approx(a, rel=1e-7)


@over(*SOLVERS)
def test_an_imported_body_force_is_gravity_and_the_cantilever_under_its_weight(tmp_path, solver):
    points = _body_points()
    path = _csv(tmp_path / "weight.csv", points, np.tile([0.0, 0.0, -RHO * G], (len(points), 1)),
                "x [m],y [m],z [m],fx [N/m^3],fy [N/m^3],fz [N/m^3]")  # fmt: skip
    gravity = _run(
        tmp_path / "g",
        lambda _: [Feature(type="gravity", selection="Bar/Body", values={"gz": qty(-G, "m/s^2")})],
        solver,
    )
    imported = _run(
        tmp_path / "f",
        lambda ids: [
            Feature(
                type="body_force",
                selection="Bar/Body",
                values={},
                fields={c: FieldUse(field=ids[0], column=c) for c in ("fx", "fy", "fz")},
            )
        ],
        solver,
        [(path, "Weight")],
    )
    a, b = _tip(gravity, 2), _tip(imported, 2)
    q, inertia, shear = RHO * G * B * H, B * H**3 / 12, E / (2 * (1 + NU))
    exact = -(q * L**4 / (8 * E * inertia) + q * L**2 / (2 * 5 / 6 * shear * B * H))
    print(f"{solver}: tip uz under gravity {a:.9g} m, imported fz = -rho g {b:.9g} m, "
          f"Timoshenko {exact:.6g} m ({b / exact - 1:+.2%})")  # fmt: skip
    assert b == pytest.approx(a, rel=1e-6)
    assert b == pytest.approx(exact, rel=0.03)


@over(*SOLVERS)
def test_an_imported_linear_traction_bends_the_bar(tmp_path, solver):
    sigma = 10e6  # Pa, the traction at the tip's top and bottom
    points = _tip_points()
    tx = sigma * (2 * points[:, 2] / H - 1)
    zero = np.zeros(len(points))
    path = _csv(tmp_path / "bend.csv", points, np.column_stack([tx, zero, zero]),
                "x [m],y [m],z [m],tx [Pa],ty [Pa],tz [Pa]")  # fmt: skip
    run = _run(
        tmp_path / "b",
        lambda ids: [
            Feature(
                type="traction",
                selection="Bar/Tip",
                values={},
                fields={c: FieldUse(field=ids[0], column=c) for c in ("tx", "ty", "tz")},
            )
        ],
        solver,
        [(path, "Bending")],
    )
    w, exact = _tip(run, 2), -sigma * L**2 / (E * H)
    stretch = _tip(run, 0)
    print(f"{solver}: tip uz {w:.6g} m, pure bending {exact:.6g} m ({w / exact - 1:+.2%}), "
          f"mean ux {stretch:.3g} m")  # fmt: skip
    assert w == pytest.approx(exact, rel=0.02)
    assert abs(stretch) < 1e-3 * abs(exact)  # no resultant: the tip does not stretch


def _body_points():
    """A grid past the bar's ends and sides, so the linear mapping spans the body."""
    xs, gs = np.linspace(-0.01, L + 0.01, 6), np.linspace(-0.002, 0.012, 4)
    xx, yy, zz = np.meshgrid(xs, gs, gs, indexing="ij")
    return np.column_stack([xx.ravel(), yy.ravel(), zz.ravel()])


@over(*SOLVERS)
def test_an_imported_linear_displacement_is_imposed_exactly(tmp_path, solver):
    points = _tip_points()
    u = np.column_stack([1e-5 + 2e-3 * points[:, 1], -3e-3 * points[:, 2], 0 * points[:, 0]])
    path = _csv(tmp_path / "shape.csv", points, u * 1e3, "x,y,z,ux [mm],uy [mm],uz [mm]")
    run = _run(
        tmp_path / "d",
        lambda ids: [
            Feature(
                type="displacement",
                selection="Bar/Tip",
                values={"z": qty(0, "mm")},
                fields={a: FieldUse(field=ids[0], column=f"u{a}") for a in "xy"},
            )
        ],
        solver,
        [(path, "Scan")],
    )
    tip = run.mesh.groups["Bar/Tip"].node_ids
    xyz = run.mesh.node_coords(tip)
    got = run.results.last("displacement").at(tip)
    want = np.column_stack([1e-5 + 2e-3 * xyz[:, 1], -3e-3 * xyz[:, 2], 0 * xyz[:, 0]])
    print(
        f"{solver}: largest difference at the tip {np.abs(got - want).max():.3g} m "
        f"(of {np.abs(want).max():.3g} m)"
    )
    # to the results' digits: CalculiX's .frd holds 6 significant digits
    np.testing.assert_allclose(got, want, rtol=1e-5, atol=1e-9 * np.abs(want).max())


@over("calculix", "code_aster")
def test_a_coarse_lorentz_cloud_mapped_conservatively_keeps_its_total(tmp_path, solver):
    """The EM → structural chain: f = J × B on a coarse 4 x 2 x 2 grid of cells (their
    centres and volumes), varying along the bar, onto the bar's finer mesh."""
    run, source = _lorentz(tmp_path, solver)
    reaction, expected = _reaction(run), _expected_reaction(run, source)
    print(f"{solver}: source total {source} N, root reaction {reaction} N ({expected} N)")
    # CalculiX prints each node's reaction to 6 digits; summed over the root they came
    # within 0.8e-5 to 1.5e-5 of the largest component on different machines
    np.testing.assert_allclose(reaction, expected, rtol=1e-5, atol=5e-5 * np.abs(source).max())


@over("code_aster", "elmer", "fenicsx")
def test_a_coarse_lorentz_cloud_moves_the_bar_as_on_calculix(tmp_path, solver):
    """The same conserved cloud: the same nodal load on the same mesh, so the tip moves
    as CalculiX's does, to the solvers' agreement (Elmer and FEniCSx store no
    reactions, so this is where their totals show)."""
    reference, _ = _lorentz(tmp_path / "calculix", "calculix")
    run, _ = _lorentz(tmp_path / solver, solver)
    for d in (0, 2):
        a, b = _tip(reference, d), _tip(run, d)
        print(f"{solver}: tip u{'xyz'[d]} {b:.9g} m, CalculiX {a:.9g} m ({b / a - 1:+.2e})")
        assert b == pytest.approx(a, rel=1e-4)


def _lorentz(where, solver):
    """The bar under a coarse conserved Lorentz cloud, and the cloud's total force."""
    n = (4, 2, 2)
    h = np.array([L, B, H]) / n
    centres = np.array(
        [
            ((i + 0.5) * h[0], (j + 0.5) * h[1], (k + 0.5) * h[2])
            for i in range(n[0])
            for j in range(n[1])
            for k in range(n[2])
        ]
    )
    s = centres[:, 0] / L
    f = np.column_stack([2e4 * s**2, 0 * s, -5e4 * (1 + s)])  # N/m^3
    dv = np.full(len(centres), np.prod(h))
    header = "x [m],y [m],z [m],f_x [N/m^3],f_y [N/m^3],f_z [N/m^3],|f| [N/m^3],dV [m^3]"
    where.mkdir(parents=True, exist_ok=True)
    path = _csv(
        where / "lorentz.csv",
        centres,
        np.column_stack([f, np.linalg.norm(f, axis=1), dv]),
        header,
    )
    source = dv @ f  # ∫ J × B dV, N
    run = _run(
        where / "c",
        lambda ids: [
            Feature(
                type="body_force",
                selection="Bar/Body",
                values={},
                fields={
                    c: FieldUse(
                        field=ids[0], column=f"f_{c[1]}", mapping="conservative", weight="dV"
                    )
                    for c in ("fx", "fy", "fz")
                },
            )
        ],
        solver,
        [(path, "Lorentz")],
    )
    return run, source


@over("calculix", "code_aster")
def test_a_coarse_wall_pressure_mapped_conservatively_keeps_its_force(tmp_path, solver):
    """A coarse 3 x 3 wall pressure on the tip, peaked off centre, with its faces'
    areas (as the ADR 0043 wall cloud writes them)."""
    c = (np.arange(3) + 0.5) * B / 3
    yy, zz = np.meshgrid(c, c)
    points = np.column_stack([np.full(yy.size, L), yy.ravel(), zz.ravel()])
    p = 1e6 * (1 + 40 * points[:, 1] + 3e3 * points[:, 2] ** 2)  # Pa
    area = np.full(len(points), (B / 3) * (H / 3) * 1e6)  # mm²
    path = _csv(
        tmp_path / "wall.csv",
        points,
        np.column_stack([p, area]),
        "x [m],y [m],z [m],wall pressure [Pa],area [mm^2]",
    )
    run = _run(
        tmp_path / "w",
        lambda ids: [
            Feature(
                type="pressure",
                selection="Bar/Tip",
                values={"p": qty(0, "Pa")},
                fields={
                    "p": FieldUse(
                        field=ids[0], column="wall pressure", mapping="conservative", weight="area"
                    )
                },
            )
        ],
        solver,
        [(path, "Wall")],
    )
    force = p @ (area / 1e6)  # pushes the tip toward the root: −x
    reaction = _reaction(run)
    print(f"{solver}: source force {force:.9g} N, root reaction {reaction[0]:.9g} N")
    assert reaction[0] == pytest.approx(force, rel=1e-5)
