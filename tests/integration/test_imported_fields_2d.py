"""Imported fields on 2D models (ADR 0044 on ADR 0048), against closed forms.

Steel (constant properties), meshed in the XY plane; CalculiX and Code_Aster, the
heat cases on Elmer and FEniCSx too:

- **a radial body force f = c r** (a field on a grid of points, linear, so the mapping
  is exact) is a spinning ring's load, ρω² = c: the plane-stress disk (a 2 mm thick
  quarter ring), the plane-strain ring and the axisymmetric cylinder with its ends
  held (plane strain along the axis). With m = (3 + ν) / 8 (plane stress) or
  (3 - 2ν) / (8 (1 - ν)) (plane strain) and s = (1 + 3ν) / (3 + ν) or
  (1 + 2ν) / (3 - 2ν), free bore and rim:
  σr = m c (a² + b² - a² b² / r² - r²), σθ = m c (a² + b² + a² b² / r² - s r²);
  u = r (σθ - ν σr) / E (plane stress) or r ((1 - ν²) σθ - ν (1 + ν) σr) / E;
- **a traction on the bore** t = P x̂ (pushing outward, as a pressure P does), uniform
  and from a field, on the axisymmetric cylinder: Lamé, as the pressure benchmark;
- **a parabolic shear traction** on a plane-stress cantilever's free end, τ(y) =
  τmax (1 - (2y'/H)²), from a field: Timoshenko's tip deflection
  F L³ / (3 E I) + F L / (κ G A), κ = 5/6, F = 2/3 τmax H t (the clamped root's local
  give is the gap), and the uniform shear of the same total alike;
- **a linear heat flux** q(y) = 2 q̄ y / H on a planar slab's far edge, its near edge
  held at T0: the far edge's mean is T0 + q̄ L / k (the energy balance) and its spread
  follows the cosine series of the y-varying part (as the 3D parity test); an inner
  flux q on an axisymmetric wall at Tb outside: T(a) = Tb + q a ln(b/a) / k;
- **films from fields** (h and T_inf): the slab's far edge T_inf + (T0 - T_inf) R_h /
  (R_k + R_h), and an axisymmetric wall's outside, conduction ln(b/a) / (2π k) in
  series with 1 / (2π b h).

CalculiX writes tractions and body forces as whole nodal forces (times the thickness,
or 2π r), Code_Aster per metre of depth or per radian; Code_Aster's flux and film are
FORMULEs over the edges' shape functions (`OnEdges`).
"""

import math

import numpy as np
import pytest
from solvers import over

from puzzle.fields import new_field_data
from puzzle.materials import add_library_material, library_material
from puzzle.model import (
    Component,
    Feature,
    FieldUse,
    Geometry,
    ImportedField,
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
from puzzle.model.schema import HistoryStep, ShellSection
from puzzle.solve import run_study

pytest.importorskip("OCP")

STEEL = library_material("structural_steel").properties
E, NU, K = (STEEL[k].to_si() for k in ("youngs_modulus", "poissons_ratio", "thermal_conductivity"))
A_IN, B_OUT = 0.010, 0.020
CONSTANT = {"temperature_dependent_materials": False}
THICK = ShellSection(selection="Part", thickness=Quantity(value=2, unit="mm"))  # plane stress
STRUCTURES = ("calculix", "code_aster")
HEAT = ("calculix", "code_aster", "elmer", "fenicsx")
# Sides of a rectangle face: side-1 bottom (y = y0), side-2 right, side-3 top, side-4 left.
SIDES = {"Bottom": ["S/side-1"], "Right": ["S/side-2"], "Top": ["S/side-3"], "Left": ["S/side-4"]}
RING = {"Bore": ["Bore/side-1"], "Outer": ["Ring/side-1"], "Xaxis": ["Q/side-1"],
        "Yaxis": ["Q/side-4"]}  # fmt: skip


def qty(value, unit):
    return Quantity(value=value, unit=unit)


def mm(v):
    return qty(v, "mm")


def _rectangle(name, x0, y0, w, h):
    rect = [[x0, y0], [x0 + w, y0], [x0 + w, y0 + h], [x0, y0 + h]]
    return HistoryStep(kind="face", name=name, args={"profile": {"points": rect}})


def _ring_steps():
    """A quarter ring a..b in the first quadrant."""
    return [
        HistoryStep(kind="face", name="Ring", args={"profile": {"circle": [0, 0, B_OUT]}}),
        HistoryStep(kind="face", name="Bore", args={"profile": {"circle": [0, 0, A_IN]},
                                                    "mode": "cut"}),
        HistoryStep(kind="face", name="Q", args={"profile": {"points": [[0, 0], [1, 0], [1, 1],
                                                                        [0, 1]]},
                                                 "mode": "intersect"}),
    ]  # fmt: skip


def _project(steps, edges, dimension, physics, size=1.0, **comp):
    p = Project(name="2d")
    selections = [NamedSelection(name="Region", entity="face", members=[f"{steps[0].name}/face"])]
    selections += [NamedSelection(name=n, entity="edge", members=m) for n, m in edges.items()]
    geo = p.add(Geometry(name="Part", history=steps, selections=selections))
    steel = add_library_material(p, "structural_steel")
    mesh = p.add(MeshRecipe(name="Mesh", global_size=mm(size)))
    c = p.add(Component(name="Part", dimension=dimension, mesh=mesh.id,
                        parts=[PartInstance(name="Part", geometry=geo.id)],
                        materials=[MaterialAssignment(material=steel.id, selection="Part/Region")],
                        physics=physics, **comp))  # fmt: skip
    return p, c


def _field(p, where, name, points, columns: dict[str, tuple[np.ndarray, str]]):
    """An imported field of `points` (n, 3) with value columns name -> (values, unit)."""
    header = ",".join(["x [m]", "y [m]", "z [m]"] + [f"{n} [{u}]" for n, (_, u) in columns.items()])
    data = np.column_stack([points] + [v for v, _ in columns.values()])
    path = where / f"{name}.csv"
    path.write_text("\n".join([header] + [",".join(f"{c:.15g}" for c in r) for r in data]) + "\n")
    return p.add(ImportedField(**new_field_data(where, path, name=name))).id


def _solve(where, p, c, solver, physics=("solid_mechanics",)):
    steps = [SolveStep(type="stationary", physics=list(physics))]
    study = p.add(Study(name="s", component=c.id, solver=solver, steps=steps))
    return run_study(p, study.id, where, where / "run", timeout_s=900)


def _at(run, name, group):
    ids = run.mesh.groups[group].node_ids
    return run.results.last(name).at(ids), run.mesh.node_coords(ids)


def _structure(features):
    return [Physics(type="solid_mechanics", features=features, settings=CONSTANT)]


def _heat(features):
    return [Physics(type="heat_transfer", features=features, settings=CONSTANT)]


def _edge_points(x0, y0, x1, y1, n=41):
    """Points along a line past both ends, so the linear mapping spans the edge."""
    s = np.linspace(-0.1, 1.1, n)
    return np.column_stack([x0 + s * (x1 - x0), y0 + s * (y1 - y0), 0 * s])


# --- a radial body force from a field ---------------------------------------------------


def _spinning(c, r, plane_stress):
    a2, b2 = A_IN**2, B_OUT**2
    if plane_stress:
        m, s = (3 + NU) / 8, (1 + 3 * NU) / (3 + NU)
    else:
        m, s = (3 - 2 * NU) / (8 * (1 - NU)), (1 + 2 * NU) / (3 - 2 * NU)
    sr = m * c * (a2 + b2 - a2 * b2 / r**2 - r**2)
    st = m * c * (a2 + b2 + a2 * b2 / r**2 - s * r**2)
    if plane_stress:
        return st, r * (st - NU * sr) / E
    return st, r * ((1 - NU**2) * st - NU * (1 + NU) * sr) / E


@over(*STRUCTURES)
@pytest.mark.parametrize("model", ["plane_stress", "plane_strain", "axisymmetric"])
def test_a_radial_body_force_field_spins_the_ring(tmp_path, solver, model):
    c = 1e12  # N/m⁴: f = c r, about 350 MPa of hoop stress at the bore
    g = np.linspace(-0.002, 0.022, 25)
    xx, yy = np.meshgrid(g, g)
    grid = np.column_stack([xx.ravel(), yy.ravel(), 0 * xx.ravel()])
    axi = model == "axisymmetric"
    if axi:  # the cylinder's section: r along x, its ends held along the axis (y)
        steps, edges, h = [_rectangle("S", A_IN, 0, B_OUT - A_IN, 0.01)], SIDES, 0.01
        held = [Feature(type="displacement", selection=f"Part/{s}", values={"y": mm(0)})
                for s in ("Bottom", "Top")]  # fmt: skip
        g = np.linspace(0.008, 0.022, 15)
        xx, yy = np.meshgrid(g, np.linspace(-0.002, h + 0.002, 7))
        grid = np.column_stack([xx.ravel(), yy.ravel(), 0 * xx.ravel()])
    else:
        steps, edges = _ring_steps(), RING
        held = [
            Feature(type="displacement", selection="Part/Xaxis", values={"y": mm(0)}),
            Feature(type="displacement", selection="Part/Yaxis", values={"x": mm(0)}),
        ]
    force = Feature(type="body_force", selection="Part/Region", values={})
    extra = {"dimension": "2d_axisymmetric"} if axi else {"dimension": "2d_planar",
                                                          "planar_model": model}  # fmt: skip
    if model == "plane_stress":
        extra["shell_sections"] = [THICK]
    p, comp = _project(steps, edges, extra.pop("dimension"), _structure([*held, force]), **extra)
    columns = {"fx": (c * grid[:, 0], "N/m^3"), "fy": (0 * grid[:, 0] if axi else c * grid[:, 1],
                                                       "N/m^3")}  # fmt: skip
    field = _field(p, tmp_path, "spin", grid, columns)
    force.fields = {n: FieldUse(field=field, column=n) for n in ("fx", "fy")}
    run = _solve(tmp_path, p, comp, solver)
    hoop_bore, _ = _spinning(c, A_IN, model == "plane_stress")
    _, u_rim = _spinning(c, B_OUT, model == "plane_stress")
    rim = "Right" if axi else "Outer"
    u, xyz = _at(run, "displacement", f"Part/{rim}")
    radial = (u[:, 0] * xyz[:, 0] + (0 if axi else u[:, 1] * xyz[:, 1])) / np.hypot(
        xyz[:, 0], 0 if axi else xyz[:, 1]
    )
    s, xyz = _at(run, "stress", f"Part/{'Left' if axi else 'Bore'}")
    hoop = s[:, 2].mean() if axi else s[int(np.argmax(xyz[:, 1])), 0]  # (0, a): σx is hoop
    print(f"{solver} {model}: rim u {radial.mean():.6g} m (exact {u_rim:.6g}, "
          f"{radial.mean() / u_rim - 1:+.3%}), bore hoop {hoop / 1e6:.4f} MPa "
          f"(exact {hoop_bore / 1e6:.4f}, {hoop / hoop_bore - 1:+.3%})")  # fmt: skip
    assert radial.mean() == pytest.approx(u_rim, rel=5e-3)
    assert hoop == pytest.approx(hoop_bore, rel=0.02)


# --- tractions ---------------------------------------------------------------------------


def _lame(r, p):
    a2, b2 = A_IN**2, B_OUT**2
    A, B = p * a2 / (b2 - a2), p * a2 * b2 / (b2 - a2)
    return A + B / r**2, (1 + NU) / E * ((1 - 2 * NU) * A * r + B / r)


@over(*STRUCTURES)
@pytest.mark.parametrize("source", ["uniform", "field"])
def test_a_traction_on_the_bore_is_lames_pressure(tmp_path, solver, source):
    P, h = 100e6, 0.01
    held = [Feature(type="displacement", selection=f"Part/{s}", values={"y": mm(0)})
            for s in ("Bottom", "Top")]  # fmt: skip
    pull = Feature(type="traction", selection="Part/Left",
                   values={"tx": qty(P / 1e6 if source == "uniform" else 0, "MPa")})  # fmt: skip
    p, comp = _project([_rectangle("S", A_IN, 0, B_OUT - A_IN, h)], SIDES, "2d_axisymmetric",
                       _structure([*held, pull]))  # fmt: skip
    if source == "field":
        points = _edge_points(A_IN, 0, A_IN, h)
        field = _field(p, tmp_path, "bore", points, {"tx": (np.full(len(points), P), "Pa")})
        pull.fields = {"tx": FieldUse(field=field, column="tx")}
    run = _solve(tmp_path, p, comp, solver)
    u, _ = _at(run, "displacement", "Part/Right")
    s, _ = _at(run, "stress", "Part/Left")
    exact_u, exact_hoop = _lame(B_OUT, P)[1], _lame(A_IN, P)[0]
    print(f"{solver} {source}: rim u {u[:, 0].mean():.6g} m (exact {exact_u:.6g}, "
          f"{u[:, 0].mean() / exact_u - 1:+.3%}), bore hoop {s[:, 2].mean() / 1e6:.3f} MPa "
          f"(exact {exact_hoop / 1e6:.3f})")  # fmt: skip
    assert u[:, 0].mean() == pytest.approx(exact_u, rel=5e-3)
    assert s[:, 2].mean() == pytest.approx(exact_hoop, rel=0.02)


@over(*STRUCTURES)
@pytest.mark.parametrize("source", ["uniform", "field"])
def test_a_shear_traction_bends_a_plane_stress_cantilever(tmp_path, solver, source):
    L, H, t, tau = 0.1, 0.01, 0.002, 1e6
    force = 2 / 3 * tau * H * t  # the parabola's total; the uniform shear carries the same
    clamp = Feature(type="fixed", selection="Part/Left")
    shear = Feature(
        type="traction",
        selection="Part/Right",
        values={"ty": qty(force / (H * t) if source == "uniform" else 0, "Pa")},
    )
    p, comp = _project([_rectangle("S", 0, 0, L, H)], SIDES, "2d_planar",
                       _structure([clamp, shear]), size=1.0,
                       shell_sections=[THICK])  # fmt: skip
    if source == "field":
        points = _edge_points(L, 0, L, H, n=81)
        y = np.clip(points[:, 1], 0, H) - H / 2
        field = _field(p, tmp_path, "shear", points,
                       {"ty": (tau * (1 - (2 * y / H) ** 2), "Pa")})  # fmt: skip
        shear.fields = {"ty": FieldUse(field=field, column="ty")}
    run = _solve(tmp_path, p, comp, solver)
    u, _ = _at(run, "displacement", "Part/Right")
    inertia, g_mod = t * H**3 / 12, E / (2 * (1 + NU))
    exact = force * L**3 / (3 * E * inertia) + force * L / (5 / 6 * g_mod * H * t)
    w = u[:, 1].mean()
    print(f"{solver} {source}: tip uy {w:.6g} m, Timoshenko {exact:.6g} m ({w / exact - 1:+.3%})")
    assert w == pytest.approx(exact, rel=0.01)


# --- heat flux and films ------------------------------------------------------------------

T0 = 400.0


def _series(q_slope, w, length, terms=401):
    """T(L, y) - T̄(L) at y = 0 and y = w for a flux's y-varying part q' (y - w/2)."""
    n = np.arange(1, terms + 1, 2)
    a = -4 * q_slope * w / (n * np.pi) ** 2
    amp = a * w / (n * np.pi * K) * np.tanh(n * np.pi * length / w)
    return float(amp.sum()), float((amp * np.cos(n * np.pi)).sum())


def _slab(where, solver, feature, fields):
    """The 40 x 20 mm, 2 mm thick slab, its left edge at T0 and `feature` on the right;
    `fields` value name -> values along the right edge (from y = -2 to 22 mm)."""
    L, H = 0.04, 0.02
    held = Feature(type="temperature", selection="Part/Left", values={"T": qty(T0, "K")})
    p, comp = _project([_rectangle("S", 0, 0, L, H)], SIDES, "2d_planar", _heat([held, feature]),
                       shell_sections=[THICK])  # fmt: skip
    points = _edge_points(L, 0, L, H)
    for name, (values, unit) in fields.items():
        field = _field(p, where, name, points, {name: (values(points[:, 1]), unit)})
        feature.fields[name] = FieldUse(field=field, column=name)
    return _solve(where, p, comp, solver, ("heat_transfer",))


@over(*HEAT)
def test_a_linear_heat_flux_field_on_a_slab_balances_and_spreads(tmp_path, solver):
    L, H, q_mean = 0.04, 0.02, 1e5
    flux = Feature(type="heat_flux", selection="Part/Right", values={"q": qty(0, "W/m^2")})
    run = _slab(tmp_path, solver, flux, {"q": (lambda y: 2 * q_mean * y / H, "W/m^2")})
    T, xyz = _at(run, "temperature", "Part/Right")
    mean = T[:, 0].mean()  # the nodes are spread evenly enough for a check of the mean
    low, high = T[xyz[:, 1] < 1e-9, 0].mean(), T[xyz[:, 1] > H - 1e-9, 0].mean()
    exact_mean = T0 + q_mean * L / K
    d_low, d_high = _series(2 * q_mean / H, H, L)
    print(f"{solver}: far edge mean {mean:.6g} K (energy balance {exact_mean:.6g}), spread "
          f"{high - low:.5g} K (series {d_high - d_low:.5g})")  # fmt: skip
    assert mean == pytest.approx(exact_mean, abs=0.005 * (exact_mean - T0))
    assert high - low == pytest.approx(d_high - d_low, rel=0.01)


@over(*HEAT)
def test_imported_films_on_a_slab_give_the_1d_answer(tmp_path, solver):
    L, h, t_inf = 0.04, 250.0, 300.0
    film = Feature(type="convection", selection="Part/Right",
                   values={"h": qty(0, "W/(m^2*K)"), "T_inf": qty(0, "K")})  # fmt: skip
    given = {"h": (lambda y: np.full(len(y), h), "W/(m^2*K)"),
             "T_inf": (lambda y: np.full(len(y), t_inf), "K")}  # fmt: skip
    run = _slab(tmp_path, solver, film, given)
    T, _ = _at(run, "temperature", "Part/Right")
    r_k, r_h = L / K, 1 / h
    exact = t_inf + (T0 - t_inf) * r_h / (r_k + r_h)
    print(f"{solver}: far edge {T[:, 0].mean():.7g} K (1D {exact:.7g})")
    assert T[:, 0].mean() == pytest.approx(exact, abs=1e-4 * (T0 - t_inf))


@over(*HEAT)
@pytest.mark.parametrize("load", ["flux", "film"])
def test_imported_flux_and_film_on_an_axisymmetric_wall(tmp_path, solver, load):
    h_wall, q, h, t_inf, tb = 0.01, 2e5, 500.0, 300.0, 300.0
    if load == "flux":  # into the bore; the outside held at Tb
        held = Feature(type="temperature", selection="Part/Right", values={"T": qty(tb, "K")})
        given = Feature(type="heat_flux", selection="Part/Left", values={"q": qty(0, "W/m^2")})
        edge, columns = (A_IN, 0, A_IN, h_wall), {"q": (q, "W/m^2")}
    else:  # the bore held at T0, a film outside
        held = Feature(type="temperature", selection="Part/Left", values={"T": qty(T0, "K")})
        given = Feature(type="convection", selection="Part/Right",
                        values={"h": qty(0, "W/(m^2*K)"), "T_inf": qty(0, "K")})  # fmt: skip
        edge, columns = (B_OUT, 0, B_OUT, h_wall), {"h": (h, "W/(m^2*K)"), "T_inf": (t_inf, "K")}
    p, comp = _project([_rectangle("S", A_IN, 0, B_OUT - A_IN, h_wall)], SIDES, "2d_axisymmetric",
                       _heat([held, given]))  # fmt: skip
    points = _edge_points(*edge)
    for name, (value, unit) in columns.items():
        field = _field(p, tmp_path, name, points, {name: (np.full(len(points), value), unit)})
        given.fields[name] = FieldUse(field=field, column=name)
    run = _solve(tmp_path, p, comp, solver, ("heat_transfer",))
    if load == "flux":
        T, _ = _at(run, "temperature", "Part/Left")
        exact = tb + q * A_IN * math.log(B_OUT / A_IN) / K
    else:
        T, _ = _at(run, "temperature", "Part/Right")
        flow = (T0 - t_inf) / (math.log(B_OUT / A_IN) / (2 * math.pi * K)
                               + 1 / (2 * math.pi * B_OUT * h))  # fmt: skip
        exact = t_inf + flow / (2 * math.pi * B_OUT * h)
    print(f"{solver} {load}: {T[:, 0].mean():.7g} K (exact {exact:.7g})")
    assert T[:, 0].mean() == pytest.approx(exact, abs=1e-3 * abs(exact - 300.0))
