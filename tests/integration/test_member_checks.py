"""Frame member checks by AISC 360-16 (ADR 0079 step 3) on PyNite solves.

Each expected value is worked here in kips and inches, as the Manual does, from the
specification's equations and the AISC Shapes Database v16.0 properties, so the
checks are tested against the hand calculation, not against themselves:

- a simply supported W18X50 under a uniform load, unbraced over its 30 ft span:
  M_u = wL²/8 at midspan, C_b = 12.5/11 = 1.136 for the parabola (F1-1), and F2-4's
  elastic lateral-torsional buckling, since L_b passes L_r = 16.9 ft;
- the same beam braced at its third points: three segments, the middle one's C_b from
  its own diagram (1.014), F2-2 inelastic;
- a W14X90 column, L_c = 20 ft about both axes: E3 flexural buckling about y,
  E3-2 with the tabulated r_y, as the Manual's Table 4-1a;
- the column with a lateral load at mid-height: H1-1a from the solved P and M;
- the beam's deflection, 5wL⁴/384EI, against L/360.
"""

import math

import numpy as np
import pytest
from solvers import over

from puzzle import frames
from puzzle.frames.design import member_checks
from puzzle.model.schema import (
    BeamSection,
    Component,
    Feature,
    Material,
    MaterialAssignment,
    MemberDesign,
    MeshRecipe,
    PartInstance,
    Physics,
    Project,
    Quantity,
    SolveStep,
    Study,
)
from puzzle.project import save_project
from puzzle.solve import run_study

q = lambda v, u: Quantity(value=v, unit=u)  # noqa: E731
KSI, IN, FT, KIP = 6.894757e6, 0.0254, 0.3048, 4448.222
E_KSI, FY_KSI = 29000.0, 50.0


def _a992():
    return Material(name="A992", info={"group": "Carbon Steel"}, properties={
        "youngs_modulus": q(E_KSI, "ksi"), "poissons_ratio": q(0.3, ""),
        "yield_strength": q(FY_KSI, "ksi"), "tensile_strength": q(65.0, "ksi"),
        "density": q(7850, "kg/m^3")})  # fmt: skip


def _frame(nodes, members, features, designation, design=(), size=0.5, standard="aisc"):
    pytest.importorskip("OCP")
    p = Project(name="frame")
    geo = p.add(frames.geometry("Frame", nodes, members))
    steel = p.add(_a992())
    mesh = p.add(MeshRecipe(name="M", global_size=q(size, "m"), element_order=1))
    comp = p.add(Component(
        name="Frame", parts=[PartInstance(name="F", geometry=geo.id)], mesh=mesh.id,
        materials=[MaterialAssignment(material=steel.id, selection="F")],
        beam_sections=[BeamSection(selection="F", orientation=(0.0, 1.0, 0.0), shape="library",
                                   standard=standard, designation=designation)],
        member_design=list(design),
        physics=[Physics(type="solid_mechanics", features=features,
                         settings={"temperature_dependent_materials": False})]))  # fmt: skip
    study = p.add(Study(name="S", component=comp.id, solver="pynite",
                        steps=[SolveStep(type="stationary")]))  # fmt: skip
    return p, study, comp


def _checks(tmp_path, p, study, comp):
    root = tmp_path / "frame"
    save_project(p, root)
    run = run_study(p, study.id, root, tmp_path / "w")
    return member_checks(p, comp, run.mesh, run.results)


def _held(sel, axes="xyz"):
    return Feature(type="displacement", selection=sel, values={a: q(0.0, "m") for a in axes})


def _check(member, name):
    checks = member["steps"][0]["checks"]
    return next(c for c in checks if c["check"].startswith(name))


SPAN_FT, W_KLF = 30.0, 1.0  # a 30 ft span, 1 kip/ft


def _beam(tmp_path, design=()):
    span = SPAN_FT * FT
    features = [
        _held("F/A"), _held("F/B", "yz"),
        Feature(type="node_spring", selection="F/A", values={"krx": q(1e9, "N*m/rad")}),
        Feature(type="line_load", selection="F/AB", values={"z": q(-W_KLF * KIP / FT, "N/m")}),
    ]  # fmt: skip
    p, study, comp = _frame({"A": [0, 0, 0], "B": [span, 0, 0]}, [["AB", "A", "B"]], features,
                            "W18X50", design, size=span / 24)  # a node at midspan  # fmt: skip
    return _checks(tmp_path, p, study, comp)


# W18X50 (v16.0): Zx 101, Sx 88.9, Iy 40.1, ry 1.65, J 1.24, Cw 3040, rts 1.98, ho 17.4,
# from the shipped table in inches.
def _w18x50():
    from puzzle.sections import _table

    row = _table("aisc")["W18X50"]
    return {k: row[k] / IN ** {"Zx": 3, "Sx": 3, "Iy": 4, "Ix": 4, "J": 4, "Cw": 6}.get(k, 1)
            for k in ("Zx", "Sx", "Iy", "Ix", "J", "Cw", "rts", "ho", "ry")}  # fmt: skip


def _f2(lb_in, cb, s):
    """F2 in kip-in, by hand."""
    mp = FY_KSI * s["Zx"]
    lp = 1.76 * s["ry"] * math.sqrt(E_KSI / FY_KSI)
    jc = s["J"] / (s["Sx"] * s["ho"])
    lr = 1.95 * s["rts"] * E_KSI / (0.7 * FY_KSI) * math.sqrt(
        jc + math.sqrt(jc**2 + 6.76 * (0.7 * FY_KSI / E_KSI) ** 2))  # fmt: skip
    if lb_in <= lp:
        return mp
    if lb_in <= lr:
        return min(cb * (mp - (mp - 0.7 * FY_KSI * s["Sx"]) * (lb_in - lp) / (lr - lp)), mp)
    fcr = cb * math.pi**2 * E_KSI / (lb_in / s["rts"]) ** 2 * math.sqrt(
        1 + 0.078 * jc * (lb_in / s["rts"]) ** 2)  # fmt: skip
    return min(fcr * s["Sx"], mp)


@over("pynite")
def test_an_unbraced_beam_by_elastic_lateral_torsional_buckling(tmp_path, solver):
    out = _beam(tmp_path)
    (m,) = out["members"]
    flex = _check(m, "flexure about x")
    mu = W_KLF * SPAN_FT**2 / 8  # kip-ft
    cb = 12.5 / 11
    phi_mn = 0.9 * _f2(SPAN_FT * 12, cb, _w18x50()) / 12  # kip-ft
    print(f"W18X50, 30 ft unbraced: M_u {flex['demand'] / (KIP * FT):.2f} kip-ft "
          f"({mu:.2f}), phiM_n {flex['available'] / (KIP * FT):.2f} ({phi_mn:.2f}), "
          f"{flex['clause']}, {flex['notes']}")  # fmt: skip
    assert flex["demand"] / (KIP * FT) == pytest.approx(mu, rel=2e-3)
    assert flex["clause"] == "F2.2(c)"
    assert "C_b 1.136" in flex["notes"][0]
    assert flex["available"] / (KIP * FT) == pytest.approx(phi_mn, rel=2e-3)
    assert m["governing"]["ratio"] == pytest.approx(mu / phi_mn, rel=3e-3)


@over("pynite")
def test_a_beam_braced_at_its_third_points(tmp_path, solver):
    design = [MemberDesign(unbraced_length=q(SPAN_FT / 3, "ft"))]
    out = _beam(tmp_path, design)
    (m,) = out["members"]
    middle = _check(m, "flexure about x (3.05-6.1 m)")
    # the middle third of a parabola: M_max at midspan (wL²/8 = 1), quarter points of
    # the segment at x = 12.5 and 17.5 ft of 30 (in units of wL²/8: 4 x (L - x) / L²)
    shape = lambda x: 4 * x * (SPAN_FT - x) / SPAN_FT**2  # noqa: E731
    ma, mb, mc = shape(12.5), shape(15.0), shape(17.5)
    cb = 12.5 / (2.5 + 3 * ma + 4 * mb + 3 * mc)
    phi_mn = 0.9 * _f2(SPAN_FT / 3 * 12, cb, _w18x50()) / 12
    got = middle["available"] / (KIP * FT)
    print(
        f"W18X50 braced at thirds: middle C_b {cb:.4f}, phiM_n {got:.2f} ({phi_mn:.2f}) "
        f"kip-ft, {middle['clause']}"
    )
    assert middle["clause"] == "F2.2(b)"
    assert f"C_b {cb:.3f}" in middle["notes"][0]
    assert middle["available"] / (KIP * FT) == pytest.approx(phi_mn, rel=2e-3)


@over("pynite")
def test_a_beam_against_its_deflection_limit(tmp_path, solver):
    out = _beam(tmp_path, [MemberDesign(deflection_limit=360)])
    (m,) = out["members"]
    d = _check(m, "deflection")
    s = _w18x50()
    exact = 5 * (W_KLF / 12) * (SPAN_FT * 12) ** 4 / (384 * E_KSI * s["Ix"]) * IN  # m
    print(f"W18X50 deflection {d['demand'] * 1e3:.3f} mm (5wL^4/384EI {exact * 1e3:.3f}), "
          f"ratio to L/360 {d['ratio']:.3f}")  # fmt: skip
    assert d["demand"] == pytest.approx(exact, rel=5e-3)
    assert d["ratio"] == pytest.approx(exact / (SPAN_FT * FT / 360), rel=5e-3)


def _column(tmp_path, axial_kips):
    h = 20 * FT
    features = [
        _held("F/A"),
        Feature(type="node_spring", selection="F/A", values={"krz": q(1e9, "N*m/rad")}),
        _held("F/B", "xy"),
        Feature(type="force", selection="F/B", values={"z": q(-axial_kips * KIP, "N")}),
    ]
    p, study, comp = _frame({"A": [0, 0, 0], "B": [0, 0, h]}, [["AB", "A", "B"]], features,
                            "W14X90")  # fmt: skip
    return p, study, comp


@over("pynite")
def test_a_column_by_flexural_buckling(tmp_path, solver):
    p, study, comp = _column(tmp_path, 500.0)
    out = _checks(tmp_path, p, study, comp)
    (m,) = out["members"]
    c = _check(m, "compression")
    # W14X90: A 26.5 in², ry 3.70 in
    from puzzle.sections import _table

    row = _table("aisc")["W14X90"]
    a, ry = row["A"] / IN**2, row["ry"] / IN  # the tabulated ry, as the Manual uses
    fe = math.pi**2 * E_KSI / (240 / ry) ** 2
    fcr = FY_KSI * 0.658 ** (FY_KSI / fe)
    print(f"W14X90, L_c 20 ft: phiP_n {c['available'] / KIP:.1f} kips (hand {0.9 * fcr * a:.1f}), "
          f"{c['limit']}, ratio {c['ratio']:.4f}")  # fmt: skip
    assert c["limit"] == "flexural buckling about y"
    assert c["available"] / KIP == pytest.approx(0.9 * fcr * a, rel=1e-4)
    assert c["demand"] / KIP == pytest.approx(500.0, rel=1e-6)
    combined = _check(m, "combined")
    assert combined["ratio"] == pytest.approx(500 / (0.9 * fcr * a), rel=1e-4)  # H1-1a, M = 0


@over("pynite")
def test_a_beam_column_by_the_interaction(tmp_path, solver):
    """The W14X90 column with a lateral load at mid-height: H1-1a at the midspan
    station from the solved P and M_x, the strengths as the hand calculation."""
    h, axial, lateral = 20 * FT, 300.0, 20.0  # kips
    features = [
        _held("F/A"),
        Feature(type="node_spring", selection="F/A", values={"krz": q(1e9, "N*m/rad")}),
        _held("F/B", "xy"),
        Feature(type="force", selection="F/B", values={"z": q(-axial * KIP, "N")}),
        Feature(type="force", selection="F/M", values={"x": q(lateral * KIP, "N")}),
    ]
    p, study, comp = _frame({"A": [0, 0, 0], "M": [0, 0, h / 2], "B": [0, 0, h]},
                            [["AM", "A", "M", "C"], ["MB", "M", "B", "C"]], features,
                            "W14X90",
                            [MemberDesign(selection="F/C", length_x=q(20, "ft"),
                                          length_y=q(20, "ft"),
                                          unbraced_length=q(20, "ft"))])  # fmt: skip
    out = _checks(tmp_path, p, study, comp)
    worst = max(out["members"], key=lambda r: r["ratio"])
    combined = _check(worst, "combined")
    flex, comp_ = _check(worst, "flexure about x"), _check(worst, "compression")
    mu = lateral * 20 / 4  # kip-ft, PL/4
    expected = axial * KIP / comp_["available"] + 8 / 9 * mu * KIP * FT / flex["available"]
    print(f"W14X90 beam-column: P {axial} kips, M {mu} kip-ft, H1-1a {combined['ratio']:.4f} "
          f"(by hand from the strengths {expected:.4f})")  # fmt: skip
    assert combined["ratio"] == pytest.approx(expected, rel=2e-3)
    assert np.isclose(worst["length_m"], h / 2)


@over("pynite")
def test_the_agent_sets_the_design_and_reads_the_checks(tmp_path, solver):
    """MCP member_design (one edit) then member_checks on the latest run."""
    pytest.importorskip("mcp")
    import asyncio

    from puzzle.agent.mcp import build_server
    from puzzle.project import load_project

    span = SPAN_FT * FT
    features = [
        _held("F/A"), _held("F/B", "yz"),
        Feature(type="node_spring", selection="F/A", values={"krx": q(1e9, "N*m/rad")}),
        Feature(type="line_load", selection="F/AB", values={"z": q(-W_KLF * KIP / FT, "N/m")}),
    ]  # fmt: skip
    p, study, comp = _frame({"A": [0, 0, 0], "B": [span, 0, 0]}, [["AB", "A", "B"]], features,
                            "W18X50", size=span / 24)  # fmt: skip
    root = tmp_path / "frame"
    save_project(p, root)
    from puzzle.commands import Session

    Session(root).submit({"command": "set_autonomy", "level": "act_in_budget"})
    server = build_server(root)
    call = lambda name, args: asyncio.run(server.call_tool(name, args)).structured_content  # noqa: E731
    out = call("member_design", {"component": "Frame", "unbraced_length": "10 ft",
                                 "deflection_limit": 360})  # fmt: skip
    assert out.get("status") != "refused", out
    edited = load_project(root)
    assert next(o for o in edited.objects.values() if isinstance(o, Component)).member_design
    from puzzle.project.layout import RESULTS_DIR
    from puzzle.results.store import ResultStore

    run_study(edited, study.id, root, tmp_path / "w", store=ResultStore(root / RESULTS_DIR))
    checks = call("member_checks", {"study": "S"})
    (m,) = checks["members"]
    assert m["section"] == "W18X50" and m["code"] == "AISC 360-16" and m["passes"]
    assert m["governing"]["check"].startswith(("flexure about x", "deflection"))
    assert "steps" not in m


@over("pynite")
def test_the_members_panel_and_form(tmp_path, solver):
    """Model → Beams and shells → Member design… sets an entry (one Edit); the Members
    panel lists the run's members worst first with their checks below."""
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from forms import fill
    from PySide6.QtWidgets import QApplication

    from puzzle.gui.member_design import edit_design
    from puzzle.gui.window import MainWindow
    from puzzle.project.layout import RESULTS_DIR
    from puzzle.results.store import ResultStore

    app = QApplication.instance() or QApplication([])
    span = SPAN_FT * FT
    features = [
        _held("F/A"), _held("F/B", "yz"),
        Feature(type="node_spring", selection="F/A", values={"krx": q(1e9, "N*m/rad")}),
        Feature(type="line_load", selection="F/AB", values={"z": q(-W_KLF * KIP / FT, "N/m")}),
    ]  # fmt: skip
    p, study, comp = _frame({"A": [0, 0, 0], "B": [span, 0, 0]}, [["AB", "A", "B"]], features,
                            "W18X50", size=span / 24)  # fmt: skip
    root = tmp_path / "frame"
    save_project(p, root)
    run_study(p, study.id, root, tmp_path / "w", store=ResultStore(root / RESULTS_DIR))
    window = MainWindow(root)
    try:
        fill(window, selection="F/AB", method="ASD", unbraced_length="10 ft",
             deflection_limit="240")  # fmt: skip
        edit_design(window, window.session.project.get(comp.id, Component))
        (entry,) = window.session.project.get(comp.id, Component).member_design
        assert entry.method == "ASD" and entry.deflection_limit == 240
        assert entry.unbraced_length.value == 10 and entry.unbraced_length.unit == "ft"
        window.docks.show("members")
        app.processEvents()
        window.members.refresh()
        table = window.members.table
        assert table.rowCount() == 1, window.members.note.text()
        assert table.item(0, 0).text() == "AB" and table.item(0, 1).text() == "W18X50"
        checks = [window.members.details.item(r, 0).text()
                  for r in range(window.members.details.rowCount())]  # fmt: skip
        assert "deflection" in checks and "compression" not in checks  # no axial load
        assert any(c.startswith("flexure about x (") for c in checks)  # three segments
        out = window.members.export(str(tmp_path / "m.csv"))
        with open(out, encoding="utf-8") as f:
            assert "W18X50" in f.read()
    finally:
        window.close()


@over("pynite")
def test_a_stainless_beam_is_checked_by_aisc_370(tmp_path, solver):
    """The same 30 ft beam in the generic stainless steel 304: the code follows the
    material (AISC 370-21), and its strengths are 370's (F2 with Table F2.1's
    austenitic coefficients), not 360's."""
    from puzzle.frames import aisc370
    from puzzle.frames.aisc360 import _library
    from puzzle.materials import add_library_material

    span = SPAN_FT * FT
    features = [
        _held("F/A"), _held("F/B", "yz"),
        Feature(type="node_spring", selection="F/A", values={"krx": q(1e9, "N*m/rad")}),
        Feature(type="line_load", selection="F/AB", values={"z": q(-0.5 * KIP / FT, "N/m")}),
    ]  # fmt: skip
    p, study, comp = _frame({"A": [0, 0, 0], "B": [span, 0, 0]}, [["AB", "A", "B"]], features,
                            "W18X50", size=span / 24)  # fmt: skip
    stainless = add_library_material(p, "stainless_steel_304")
    comp.materials[0] = comp.materials[0].model_copy(update={"material": stainless.id})
    out = _checks(tmp_path, p, study, comp)
    (m,) = out["members"]
    assert m["code"] == "AISC 370-21"
    flex = _check(m, "flexure about x")
    s = aisc360_steel(stainless)
    expected = aisc370.flexure_x(_library("aisc", "W18X50"), s, span, 12.5 / 11)
    print(
        f"stainless W18X50: phiM_n {flex['available'] / (KIP * FT):.2f} kip-ft by "
        f"{flex['clause']} (370 by hand {expected.available('LRFD') / (KIP * FT):.2f})"
    )
    assert flex["available"] == pytest.approx(expected.available("LRFD"), rel=2e-3)
    assert flex["clause"] == expected.clause


def aisc360_steel(material):
    from puzzle.frames.design import _steel

    return _steel(material)


@over("pynite")
def test_an_aluminium_beam_is_checked_by_the_adm(tmp_path, solver):
    """An Aluminum Association I 8 × 6.18 in generic 6061-T6, braced at its ends, a
    uniform load: the code is the Aluminum Design Manual's, and its flexure the least of
    F.2, F.3.1 and F.4 (C_b 1.136 from the diagram), as `adm` gives it for the same
    length and C_b."""
    from puzzle.frames import adm
    from puzzle.frames.aisc360 import _library
    from puzzle.materials import add_library_material

    span = 8 * FT
    features = [
        _held("F/A"), _held("F/B", "yz"),
        Feature(type="node_spring", selection="F/A", values={"krx": q(1e9, "N*m/rad")}),
        Feature(type="line_load", selection="F/AB", values={"z": q(-0.5 * KIP / FT, "N/m")}),
    ]  # fmt: skip
    p, study, comp = _frame({"A": [0, 0, 0], "B": [span, 0, 0]}, [["AB", "A", "B"]], features,
                            "I8X6.18", size=span / 24, standard="aa")  # fmt: skip
    aluminium = add_library_material(p, "aluminium_6061_t6")
    comp.materials[0] = comp.materials[0].model_copy(update={"material": aluminium.id})
    out = _checks(tmp_path, p, study, comp)
    (m,) = out["members"]
    assert m["code"] == "Aluminum Design Manual 2015"
    flex = _check(m, "flexure about x")
    expected = adm.flexure_x(_library("aa", "I8X6.18"), adm.material(aluminium), span,
                             12.5 / 11)  # fmt: skip
    print(
        f"aluminium I8X6.18: phiM_n {flex['available'] / (KIP * 0.0254):.1f} in-k, "
        f"{flex['limit']} ({flex['clause']})"
    )
    assert flex["available"] == pytest.approx(expected.available("LRFD"), rel=2e-3)
    assert _check(m, "combined")["clause"].startswith("H1-1")
