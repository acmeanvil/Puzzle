"""Imported fields, stage 3 (ADR 0044): an initial stress, a shell's thickness and a
graded material from fields, on CalculiX and Code_Aster, against closed forms.

- **Initial stress:** the 200 x 10 x 10 mm steel bar, its root held along x (and across
  it), starting from σxx = σ0. Not in equilibrium with its free tip, it relaxes until
  σ = 0: u(L) = -σ0 L / E (a tension shortens it). An imported uniform field equals the
  uniform feature, and so does a copy written turned 90° about z (σ0 then along y) and
  placed back: the tensor turns with the points. A self-equilibrated residual stress
  (σxx = σ0 (1 - 12 s²) across the height, no net force or moment) barely moves the tip.
- **Shell thickness:** a cantilever strip of sheet (L 200 mm, b 20 mm, E of steel, ν = 0
  so it bends as a beam) with t from 2 mm at the root to 1 mm at the tip, under a
  pressure q: δ = ∫ M(x) (L - x) / (E I(x)) dx, M = q b (L - x)² / 2, I = b t³ / 12. A
  uniform thickness from a field equals the plain section.
- **Graded material:** the bar with E(x) from 200 to 100 GPa along it, an end force F:
  u(L) = ∫ F / (E(x) A) dx = F L ln(E0 / E1) / ((E0 - E1) A), within the binning
  tolerance (0.5 %) and the mesh's error; a uniform E from a field equals the material's.
"""

import numpy as np
import pytest
from conftest_geometry import BAR, bar_project, write_bar_step
from solvers import over

from puzzle.fields import new_field_data
from puzzle.materials import library_material
from puzzle.model import Feature, FieldUse, ImportedField, Physics, Quantity, SolveStep, Study
from puzzle.solve import run_study

pytest.importorskip("OCP")

L, B, H = BAR
STEEL = library_material("structural_steel").properties
E = STEEL["youngs_modulus"].to_si()
CONSTANT = {"temperature_dependent_materials": False}
SIGMA0 = 100e6  # Pa


def qty(value, unit):
    return Quantity(value=value, unit=unit)


def _csv(path, points, columns: dict[str, np.ndarray], coordinates="x,y,z"):
    header = ",".join([coordinates, *columns])
    rows = [header]
    for i, p in enumerate(points):
        rows.append(",".join(f"{c:.12g}" for c in (*p, *(v[i] for v in columns.values()))))
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def _add_field(project, root, path, **extra):
    return project.add(ImportedField(**new_field_data(root, path, **extra)))


def _solve(where, project, comp, solver, physics=("solid_mechanics",), settings=None):
    steps = [SolveStep(type="stationary", physics=list(physics), settings=settings or {})]
    study = project.add(Study(name="s", component=comp.id, solver=solver, steps=steps))
    return run_study(project, study.id, where, where / "run", timeout_s=900)


def _tip(run, name="displacement", component=0):
    tip = run.mesh.groups["Bar/Tip"].node_ids
    return float(run.results.last(name).at(tip)[:, component].mean())


def _bar_supports():
    return [
        Feature(type="displacement", selection="Bar/Root", values={"x": qty(0, "mm")}),
        Feature(type="displacement", selection="Bar/Root",
                values={"y": qty(0, "mm"), "z": qty(0, "mm")}),
    ]  # fmt: skip


def _grid(n=(11, 3, 3), margin=0.002):
    """Points over the bar and `margin` past it."""
    x = np.linspace(-0.01, L + 0.01, n[0])
    y = np.linspace(-margin, B + margin, n[1])
    z = np.linspace(-margin, H + margin, n[2])
    return np.array([(a, b, c) for a in x for b in y for c in z])


# --- initial stress --------------------------------------------------------------------------


def _stress_case(where, solver, *, field=None, placement=None, values=None, settings=None):
    """The free bar from an initial stress: uniform `values` (MPa), or a field file's
    columns (`field`: (points, {name: MPa})) taken for every component."""
    where.mkdir(parents=True, exist_ok=True)
    stress = Feature(type="initial_stress", selection="Bar/Body",
                     values={k: qty(v, "MPa") for k, v in (values or {}).items()})  # fmt: skip
    physics = Physics(type="solid_mechanics", features=[*_bar_supports(), stress],
                      settings=CONSTANT)  # fmt: skip
    project, comp = bar_project(write_bar_step(where / "bar.step"), [physics], mesh_size_mm=5)
    if field is not None:
        points, columns = field
        path = _csv(where / "stress.csv", points,
                    {f"{k} [MPa]": v for k, v in columns.items()})  # fmt: skip
        f = _add_field(project, where, path, name="Residual", placement=placement)
        for k in columns:
            stress.fields[k] = FieldUse(field=f.id, column=k)
    run = _solve(where, project, comp, solver, settings=settings)
    tip = run.mesh.groups["Bar/Tip"].node_ids
    return run.results.last("displacement").at(tip).mean(axis=0)


ZERO6 = ("sxx", "syy", "szz", "sxy", "syz", "szx")


@over("calculix", "code_aster")
def test_a_free_bar_relaxes_from_an_initial_stress(tmp_path, solver):
    """σxx, σxy and σxz relax to a stress-free state the root allows: u(L) = -σxx L / E
    along x, -σxy L / G along y and -σxz L / G along z (the shears tilt the bar; the
    root, held across, stops the other half of each shear strain)."""
    nu = STEEL["poissons_ratio"].to_si()
    G = E / (2 * (1 + nu))
    given = {"sxx": 100.0, "sxy": 20.0, "szx": -30.0}  # MPa
    exact = -np.array([given["sxx"] / E, given["sxy"] / G, given["szx"] / G]) * 1e6 * L
    u = _stress_case(tmp_path / "uniform", solver, values=given)
    points = _grid()
    sigma = np.array([given.get(k, 0.0) for k in ZERO6])
    columns = {k: np.full(len(points), v) for k, v in zip(ZERO6, sigma, strict=True)}
    u_field = _stress_case(tmp_path / "field", solver, field=(points, columns))
    # The same state written in a frame turned 30°, 40°, 50° (about x, y, z) and moved
    # 1 m along y, placed back by that rotation and a move: every component is nonzero
    # in the file, so the tensor must turn (R σ Rᵀ) to give the same answer.
    from puzzle.fields import rotate_tensors
    from puzzle.geometry.model import rotation_matrix

    r = rotation_matrix((30.0, 40.0, 50.0))
    away = np.array([0.0, 1.0, 0.0])
    in_file = rotate_tensors(sigma[None], r.T)[0]  # Rᵀ σ R
    turned = {k: np.full(len(points), v) for k, v in zip(ZERO6, in_file, strict=True)}
    back = {"rotation": (30.0, 40.0, 50.0), "translation": tuple(-(r @ away))}
    u_turned = _stress_case(tmp_path / "turned", solver,
                            field=(points @ r + away, turned), placement=back)  # fmt: skip
    print(f"{solver}: tip {u} m (closed form {exact}); field {u_field}; turned field "
          f"{u_turned}; file holds {np.round(in_file, 2)} MPa")  # fmt: skip
    assert u == pytest.approx(exact, rel=0.01)
    assert u_field == pytest.approx(u, rel=1e-6, abs=1e-12)
    assert u_turned == pytest.approx(u, rel=1e-6, abs=1e-12)


@over("calculix", "code_aster")
def test_an_initial_stress_relaxes_the_same_with_large_deflection(tmp_path, solver):
    """With geometric nonlinearity (CalculiX NLGEOM, Code_Aster GREEN_LAGRANGE) the
    initial stress is the same starting state: the strain is small, so the same tip."""
    exact = -SIGMA0 * L / E
    u = _stress_case(tmp_path, solver, values={"sxx": SIGMA0 / 1e6},
                     settings={"geometric_nonlinearity": True})[0]  # fmt: skip
    print(f"{solver}: large deflection, tip {u:.6g} m (closed form {exact:.6g}, "
          f"{100 * (u / exact - 1):+.2f} %)")  # fmt: skip
    assert u == pytest.approx(exact, rel=0.01)


@over("calculix", "code_aster")
def test_a_self_equilibrated_residual_stress_barely_moves_the_bar(tmp_path, solver):
    # Fine across the height: linear interpolation between rows of a parabola leaves a
    # net force of about h² σ0 / B² (9 rows: 6 % of σ0 L / E at the tip).
    points = _grid((11, 41, 3), margin=0.0)
    s = (points[:, 1] - B / 2) / B
    columns = {k: np.zeros(len(points)) for k in ZERO6}
    columns["sxx"] = SIGMA0 / 1e6 * (1 - 12 * s**2)  # no net force, no moment
    u = _stress_case(tmp_path, solver, field=(points, columns))[0]
    scale = SIGMA0 * L / E
    print(f"{solver}: self-equilibrated residual stress moves the tip {u:.3g} m "
          f"({100 * abs(u) / scale:.2f} % of σ0 L / E)")  # fmt: skip
    assert abs(u) < 0.02 * scale


# --- shell thickness --------------------------------------------------------------------------

STRIP_L, STRIP_B, T_ROOT, T_TIP, Q = 0.2, 0.02, 2e-3, 1e-3, 100.0  # m, m, m, m, Pa
SHELL_ORDER = {"calculix": (2, 2.5), "code_aster": (1, 2.0)}  # element order, size in mm


def _strip_deflection(thickness) -> float:
    """The tip's deflection under a pressure q: ∫ M(x) (L - x) / (E I(x)) dx, with
    M = q b (L - x)² / 2 and I = b t³ / 12 (ν = 0: no plate stiffening)."""
    from scipy.integrate import quad

    def integrand(x):
        moment = Q * STRIP_B * (STRIP_L - x) ** 2 / 2
        return moment * (STRIP_L - x) / (E * STRIP_B * thickness(x) ** 3 / 12)

    return quad(integrand, 0.0, STRIP_L)[0]


def _strip(where, solver, *, field=None, thickness=1.5e-3):
    """A cantilever strip of sheet, its root edge clamped, a pressure pushing it down
    (against the face's normal, +z); the tip edge's mean deflection."""
    from puzzle.model.schema import (
        Component,
        Geometry,
        HistoryStep,
        Material,
        MaterialAssignment,
        MeshRecipe,
        NamedSelection,
        PartInstance,
        Project,
        ShellSection,
    )

    where.mkdir(parents=True, exist_ok=True)
    order, size = SHELL_ORDER[solver]
    p = Project(name="strip")
    corners = [[0, 0, 0], [STRIP_L, 0, 0], [STRIP_L, STRIP_B, 0], [0, STRIP_B, 0]]
    plane = lambda at: {"plane": {"axis": "x", "at": at}}  # noqa: E731
    geo = p.add(Geometry(name="Strip", history=[
        HistoryStep(kind="sheet", name="P", args={"points": corners})], selections=[
        NamedSelection(name="Root", entity="edge", rule=plane(0.0)),
        NamedSelection(name="Tip", entity="edge", rule=plane(STRIP_L)),
        NamedSelection(name="Top", entity="face", members=["P/face"])]))  # fmt: skip
    steel = p.add(Material(name="Steel, ν = 0", properties={
        "youngs_modulus": qty(E, "Pa"), "poissons_ratio": qty(0.0, "dimensionless"),
        "density": qty(7850.0, "kg/m^3")}))  # fmt: skip
    mesh = p.add(MeshRecipe(name="M", global_size=qty(size, "mm"), element_order=order))
    section = ShellSection(selection="S", thickness=qty(thickness * 1e3, "mm"))
    comp = p.add(Component(
        name="C", parts=[PartInstance(name="S", geometry=geo.id)], mesh=mesh.id,
        materials=[MaterialAssignment(material=steel.id, selection="S")],
        shell_sections=[section],
        physics=[Physics(type="solid_mechanics", settings=CONSTANT, features=[
            Feature(type="fixed", selection="S/Root"),
            Feature(type="pressure", selection="S/Top", values={"p": qty(Q, "Pa")}),
        ])]))  # fmt: skip
    if field is not None:
        x = np.linspace(-0.005, STRIP_L + 0.005, 43)
        y = np.linspace(-0.005, STRIP_B + 0.005, 3)
        points = np.array([(a, b, 0.0) for a in x for b in y])
        path = _csv(where / "thickness.csv", points, {"t [mm]": field(points[:, 0]) * 1e3})
        f = _add_field(p, where, path, name="Thickness")
        section.fields["thickness"] = FieldUse(field=f.id, column="t")
    run = _solve(where, p, comp, solver)
    tip = run.mesh.groups["S/Tip"].node_ids
    return -float(run.results.last("displacement").at(tip)[:, 2].mean())


@over("calculix", "code_aster")
def test_a_tapered_strip_takes_its_thickness_from_a_field(tmp_path, solver):
    def taper(x):
        return T_ROOT + (T_TIP - T_ROOT) * np.asarray(x) / STRIP_L

    exact = _strip_deflection(taper)
    w = _strip(tmp_path / "taper", solver, field=taper)
    plain = _strip(tmp_path / "plain", solver)
    uniform = _strip(tmp_path / "uniform", solver, field=lambda x: np.full(len(x), 1.5e-3))
    flat = _strip_deflection(lambda x: 1.5e-3)
    print(f"{solver}: tapered strip tip {w:.6g} m (∫ M x / E I dx {exact:.6g}, "
          f"{100 * (w / exact - 1):+.2f} %); uniform 1.5 mm {plain:.6g} m (beam "
          f"{flat:.6g}, {100 * (plain / flat - 1):+.2f} %), from a field "
          f"{uniform:.6g} m")  # fmt: skip
    assert w == pytest.approx(exact, rel=0.015)
    assert uniform == pytest.approx(plain, rel=1e-9)


# --- graded materials ---------------------------------------------------------------------------


def _graded_bar(where, solver, prop, values, unit, physics, features, result, column):
    """The bar with `prop` from a field varying along x (`values(x)` in `unit`), or the
    material's own when `values` is None; the tip's mean `result` component."""
    where.mkdir(parents=True, exist_ok=True)
    ph = Physics(type=physics, features=features, settings=CONSTANT)
    project, comp = bar_project(write_bar_step(where / "bar.step"), [ph], mesh_size_mm=5)
    if values is not None:
        points = _grid((41, 3, 3))
        path = _csv(where / "graded.csv", points, {f"v [{unit}]": values(points[:, 0])})
        f = _add_field(project, where, path, name="Graded")
        comp.materials[0].fields[prop] = FieldUse(field=f.id, column="v")
    run = _solve(where, project, comp, solver, physics=(physics,))
    return _tip(run, result, column)


@over("calculix", "code_aster")
def test_a_graded_bar_takes_its_stiffness_from_a_field(tmp_path, solver):
    E0, E1, F = 200e9, 100e9, 10e3  # Pa, Pa, N

    def modulus(x):  # GPa
        return (E0 + (E1 - E0) * np.asarray(x) / L) / 1e9

    pull = Feature(type="force", selection="Bar/Tip", values={"x": qty(F, "N")})
    case = ("youngs_modulus", "GPa", "solid_mechanics", [*_bar_supports(), pull],
            "displacement", 0)  # fmt: skip
    u = _graded_bar(tmp_path / "graded", solver, case[0], modulus, *case[1:])
    exact = F * L * np.log(E0 / E1) / ((E0 - E1) * B * H)  # ∫ F / (E(x) A) dx
    plain = _graded_bar(tmp_path / "plain", solver, case[0], None, *case[1:])
    same = _graded_bar(tmp_path / "same", solver, case[0],
                       lambda x: np.full(len(x), E / 1e9), *case[1:])  # fmt: skip
    print(f"{solver}: graded bar tip {u:.6g} m (∫ F / E A dx {exact:.6g}, "
          f"{100 * (u / exact - 1):+.2f} %); steel {plain:.6g} m, from a uniform field "
          f"{same:.6g} m (F L / E A {F * L / (E * B * H):.6g})")  # fmt: skip
    assert u == pytest.approx(exact, rel=0.01)
    assert same == pytest.approx(plain, rel=1e-9)


@over("calculix", "code_aster")
def test_a_graded_bar_takes_its_conductivity_from_a_field(tmp_path, solver):
    k0, k1, q, T0 = 50.0, 25.0, 1e5, 400.0  # W/(m K), W/(m K), W/m², K

    def conductivity(x):
        return k0 + (k1 - k0) * np.asarray(x) / L

    features = [
        Feature(type="temperature", selection="Bar/Root", values={"T": qty(T0, "K")}),
        Feature(type="heat_flux", selection="Bar/Tip", values={"q": qty(q, "W/m^2")}),
    ]
    T = _graded_bar(tmp_path, solver, "thermal_conductivity", conductivity, "W/(m*K)",
                    "heat_transfer", features, "temperature", 0)  # fmt: skip
    exact = T0 + q * L * np.log(k0 / k1) / (k0 - k1)  # T0 + q ∫ dx / k(x)
    print(f"{solver}: graded conduction, tip {T:.6g} K (T0 + q ∫ dx / k {exact:.6g} K, "
          f"rise {100 * ((T - T0) / (exact - T0) - 1):+.2f} %)")  # fmt: skip
    assert pytest.approx(exact - T0, rel=0.01) == T - T0
