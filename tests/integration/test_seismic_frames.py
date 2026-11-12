"""ASCE 7-16 seismic analysis of frames on PyNite (ADR 0079 step 4), against closed forms:

- a cantilever W14X90's own modes: f_n = (β_n² / 2π) √(EI / ρA L⁴), β_1 L = 1.8751,
  β_2 L = 4.6941, and the effective masses 0.6131 and 0.1883 of ρAL (Chopra, Table
  18.x; Clough & Penzien), about both axes;
- a single mass W on a massless column: T = 2π √(W h³ / (3 g EI)), the modal base shear
  V_t = W S_a(T) I_e / R, scaled up to the ELF V = C_s W at C_u T_a (12.9.1.4.1), so the
  base moment is V h; the combinations with Ex carry it both ways (± E);
- the ELF procedure on a two-level column (12.8-11, 12.8-12) written as loads, and the
  drifts of a combination named Ex at the levels (12.8.6): C_d δ / I_e with δ from the
  cantilever's two point loads, P a² (3x − a) / 6EI and P x² (3a − x) / 6EI.
"""

import math

import numpy as np
import pytest
from solvers import over
from test_member_checks import KSI, _frame

from puzzle.frames.seismic import G, approximate_period, cu, spectrum
from puzzle.model.schema import (
    Feature,
    LoadCombination,
    Material,
    Quantity,
    SeismicDesign,
    SolveStep,
)
from puzzle.project import save_project
from puzzle.solve import run_study

q = lambda v, u: Quantity(value=v, unit=u)  # noqa: E731
E = 29000 * KSI


def _w14x90():
    from puzzle.sections import _table

    return _table("aisc")["W14X90"]


def _run(tmp_path, p, study):
    root = tmp_path / "frame"
    save_project(p, root)
    return run_study(p, study.id, root, tmp_path / "w")


@over("pynite")
def test_a_cantilevers_modes_and_effective_masses(tmp_path, solver):
    h, rho = 6.0, 7850.0
    fixed = [Feature(type="fixed", selection="F/A")]
    p, study, comp = _frame({"A": [0, 0, 0], "B": [0, 0, h]}, [["AB", "A", "B"]], fixed, "W14X90",
                            size=h / 24)  # fmt: skip
    study.steps = [SolveStep(type="modal", settings={"modes": 6})]
    run = _run(tmp_path, p, study)
    row = _w14x90()
    m = rho * row["A"] * h
    modes = [s for s in run.results.steps if s.kind == "modal"]
    by_axis = {}
    for s in modes:
        fraction = s.fields["effective_mass"].values[0] / m
        axis = int(np.argmax(fraction))
        by_axis.setdefault(axis, []).append((s.value, fraction[axis]))
    for axis, inertia in ((0, row["Ix"]), (1, row["Iy"])):  # x bends the strong axis
        base = math.sqrt(E * inertia / (rho * row["A"] * h**4)) / (2 * math.pi)
        (f1, m1), (f2, m2) = by_axis[axis][:2]
        print(f"bending about {'xy'[axis]}: f1 {f1:.5f} Hz ({1.87510**2 * base:.5f}), "
              f"f2 {f2:.5f} ({4.69409**2 * base:.5f}), effective mass {m1:.4f} (0.6131), "
              f"{m2:.4f} (0.1883)")  # fmt: skip
        assert f1 == pytest.approx(1.87510**2 * base, rel=1e-4)
        assert f2 == pytest.approx(4.69409**2 * base, rel=1e-3)
        assert m1 == pytest.approx(0.6131, abs=2e-4)
        assert m2 == pytest.approx(0.1883, abs=2e-4)


def _massless(p):
    """A steel without self weight: the mass is the loads' only."""
    steel = next(o for o in p.objects.values() if isinstance(o, Material))
    steel.properties["density"] = q(1e-9, "kg/m^3")


DESIGN = dict(sds=1.0, sd1=0.6, s1=0.4, tl=8.0, r=8.0, cd=5.5, ie=1.0)


@over("pynite")
def test_a_single_mass_by_response_spectrum_scaled_to_elf(tmp_path, solver):
    h, weight = 4.0, 500e3
    tip = Feature(type="force", selection="F/B", case="D", values={"z": q(-weight, "N")})
    features = [Feature(type="fixed", selection="F/A"), tip]
    p, study, comp = _frame({"A": [0, 0, 0], "B": [0, 0, h]}, [["AB", "A", "B"]], features,
                            "W14X90", size=h / 8)  # fmt: skip
    _massless(p)
    study.seismic = SeismicDesign(**DESIGN)
    study.steps = [SolveStep(type="modal", settings={"modes": 3, "response_spectrum": True,
                                                     "spectrum_directions": "x"})]  # fmt: skip
    study.combinations = [LoadCombination(name="0.9D + Ex", factors={"D": 0.9, "Ex": 1.0})]
    run = _run(tmp_path, p, study)
    row = _w14x90()
    t = 2 * math.pi * math.sqrt(weight / G * h**3 / (3 * E * row["Ix"]))
    vt = weight * spectrum(t, 1.0, 0.6, 8.0) / 8.0
    t_used = cu(0.6) * approximate_period("other", h)
    assert t > t_used
    v_elf = min(1.0 / 8.0, 0.6 / (t_used * 8.0)) * weight
    (sp,) = [s for s in run.results.steps if s.kind == "spectrum"]
    shear = dict(zip(sp.fields["spectrum_base_shear"].components,
                     sp.fields["spectrum_base_shear"].values[0], strict=True))  # fmt: skip
    print(
        f"T {t:.4f} s (modal {shear['period']:.4f}), V_t {shear['modal_V']:.1f} N "
        f"({vt:.1f}), ELF V {shear['elf_V']:.1f} ({v_elf:.1f}), scale {shear['scale']:.4f}"
    )
    assert shear["modal_V"] == pytest.approx(vt, rel=2e-3)
    assert shear["period"] == pytest.approx(t_used, rel=1e-9)
    assert shear["elf_V"] == pytest.approx(v_elf, rel=1e-6)
    assert shear["scale"] == pytest.approx(v_elf / vt, rel=2e-3)
    base = sp.fields["reaction_moment"]
    assert float(np.max(np.abs(base.values))) == pytest.approx(v_elf * h, rel=2e-3)
    steps = {s.label(): s for s in run.results.steps if s.kind == "combination"}
    assert set(steps) == {"0.9D + Ex (+E)", "0.9D + Ex (−E)"}
    for label in ("0.9D + Ex (+E)", "0.9D + Ex (−E)"):
        m = steps[label].fields["reaction_moment"].values
        assert float(np.max(np.abs(m))) == pytest.approx(v_elf * h, rel=2e-3)
        n = steps[label].fields["reaction_force"].values[:, 2]
        assert float(n.sum()) == pytest.approx(0.9 * weight, rel=1e-6)  # no vertical E


@over("pynite")
def test_elf_loads_and_drifts(tmp_path, solver):
    from puzzle.frames.seismic_frame import elf_commands, report

    h1, h2, w1, w2 = 3.0, 6.0, 300e3, 200e3
    down = lambda sel, w: Feature(type="force", selection=sel, case="D",  # noqa: E731
                                  values={"z": q(-w, "N")})  # fmt: skip
    features = [Feature(type="fixed", selection="F/A"), down("F/B", w1), down("F/C", w2)]
    p, study, comp = _frame({"A": [0, 0, 0], "B": [0, 0, h1], "C": [0, 0, h2]},
                            [["AB", "A", "B"], ["BC", "B", "C"]], features, "W14X90",
                            size=0.5)  # fmt: skip
    _massless(p)
    study.seismic = SeismicDesign(**DESIGN, risk_category="II")
    edit, elf, _notes = elf_commands(p, comp, study, "x")
    (op,) = edit.ops  # the one Edit: the physics' features, ELF loads added
    assert op.path == "physics.0.features"
    comp.physics[0].features = [Feature.model_validate(v) for v in op.value]
    loads = {f.selection: f.values["x"].to_si() for f in comp.physics[0].features if f.case == "Ex"}
    ta = approximate_period("other", h2)
    cs = min(1.0 / 8.0, 0.6 / (ta * 8.0))
    v = cs * (w1 + w2)
    k = 1.0  # T_a under 0.5 s
    f1 = v * w1 * h1**k / (w1 * h1**k + w2 * h2**k)
    print(f"ELF: T_a {ta:.4f} s, C_s {cs:.4f}, V {v:.1f} N, F at B {loads['F/B']:.1f} "
          f"({f1:.1f}), at C {loads['F/C']:.1f} ({v - f1:.1f})")  # fmt: skip
    assert elf.base_shear == pytest.approx(v)
    assert loads["F/B"] == pytest.approx(f1) and loads["F/C"] == pytest.approx(v - f1)
    study.combinations = [LoadCombination(name="Ex", factors={"Ex": 1.0}),
                          LoadCombination(name="D", factors={"D": 1.0})]  # fmt: skip
    run = _run(tmp_path, p, study)
    out = report(p, comp, study, run.mesh, run.results)
    ei = E * _w14x90()["Ix"]
    fb, fc = f1, v - f1

    def deflection(x):
        d = 0.0
        for force, a in ((fb, h1), (fc, h2)):
            d += force * (a**2 * (3 * x - a) if x >= a else x**2 * (3 * a - x)) / (6 * ei)
        return d

    d1, d2 = deflection(h1), deflection(h2)
    drift = out["drifts"]["Ex"]
    print(f"drifts {drift[0]['drift_m'] * 1e3:.3f} mm ({5.5 * d1 * 1e3:.3f}), "
          f"{drift[1]['drift_m'] * 1e3:.3f} ({5.5 * (d2 - d1) * 1e3:.3f})")  # fmt: skip
    assert drift[0]["drift_m"] == pytest.approx(5.5 * d1, rel=1e-4)
    assert drift[1]["drift_m"] == pytest.approx(5.5 * (d2 - d1), rel=1e-4)
    assert drift[0]["allowed_m"] == pytest.approx(0.020 * h1)
    assert out["elf_x"]["base_shear_N"] == pytest.approx(v)


@over("pynite")
def test_the_agent_runs_elf_and_reads_the_report(tmp_path, solver):
    """MCP seismic_design, seismic_elf (the loads written), then seismic_report."""
    pytest.importorskip("mcp")
    import asyncio

    from puzzle.agent.mcp import build_server
    from puzzle.commands import Session
    from puzzle.project import load_project

    h, weight = 4.0, 500e3
    tip = Feature(type="force", selection="F/B", case="D", values={"z": q(-weight, "N")})
    held = Feature(type="fixed", selection="F/A")
    p, study, comp = _frame({"A": [0, 0, 0], "B": [0, 0, h]}, [["AB", "A", "B"]], [held, tip],
                            "W14X90", size=0.5)  # fmt: skip
    _massless(p)
    root = tmp_path / "frame"
    save_project(p, root)
    Session(root).submit({"command": "set_autonomy", "level": "act_in_budget"})
    server = build_server(root)
    call = lambda name, args: asyncio.run(server.call_tool(name, args)).structured_content  # noqa: E731
    call("seismic_design", {"study": "S", **DESIGN})
    out = call("seismic_elf", {"study": "S", "direction": "x"})
    assert out["base_shear_N"] == pytest.approx(0.125 * weight)
    assert out["edit"].get("status") != "refused", out["edit"]
    edited = load_project(root)
    loads = [f for c in edited.objects.values() if isinstance(c, type(comp))
             for f in c.physics[0].features if f.case == "Ex"]  # fmt: skip
    assert len(loads) == 1 and loads[0].values["x"].to_si() == pytest.approx(0.125 * weight)
    report = call("seismic_report", {"study": "S"})
    assert report["run_id"] is None and report["elf_x"]["cs_clause"] == "12.8-2"


@over("pynite")
def test_the_seismic_forms(tmp_path, solver):
    """Model → Studies → Seismic design… then ELF loads…, each one Edit."""
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from forms import fill
    from PySide6.QtWidgets import QApplication

    from puzzle.gui.seismic_dialog import edit_seismic, write_elf
    from puzzle.gui.window import MainWindow

    QApplication.instance() or QApplication([])
    tip = Feature(type="force", selection="F/B", case="D", values={"z": q(-500e3, "N")})
    held = Feature(type="fixed", selection="F/A")
    p, study, comp = _frame({"A": [0, 0, 0], "B": [0, 0, 4.0]}, [["AB", "A", "B"]], [held, tip],
                            "W14X90", size=0.5)  # fmt: skip
    _massless(p)
    root = tmp_path / "frame"
    save_project(p, root)
    window = MainWindow(root)
    try:
        fill(window, sds="1.0", sd1="0.6", r="8", cd="5.5", risk_category="III")
        edit_seismic(window, window.session.project.get(study.id, type(study)))
        design = window.session.project.get(study.id, type(study)).seismic
        assert design.sds == 1.0 and design.cd == 5.5 and design.risk_category == "III"
        fill(window, direction="y")
        write_elf(window, window.session.project.get(study.id, type(study)))
        features = window.session.project.get(comp.id, type(comp)).physics[0].features
        (ey,) = [f for f in features if f.case == "Ey"]
        assert ey.values["y"].to_si() == pytest.approx(0.125 * 500e3)
    finally:
        window.close()
