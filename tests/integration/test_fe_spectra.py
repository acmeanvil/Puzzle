"""Response spectra on CalculiX and Code_Aster (ADR 0079 step 4, `puzzle.spectra`).

A W14X90 cantilever column 6 m tall, its own mass only, against closed forms:

- each bending mode's frequency f_n = (β_n² / 2π) √(EI / ρA L⁴) (β_1 L = 1.8751,
  β_2 L = 4.6941) and effective mass 0.6131 and 0.1883 of ρAL (Chopra), from the
  solvers' own participation factors (modes normalised to unit generalised mass);
- the modal base shear in x: the CQC over the modes bending about the strong axis of
  each m_eff S_a(T) g I_e / R (12.9.1.3, 5 % damping), with S_a from Figure 11.4-1;
- the ELF base shear C_s W it is scaled up to (12.9.1.4.1), W = ρ A L g, at the
  fundamental period capped at C_u T_a.
"""

import math

import numpy as np
import pytest
from solvers import over
from test_member_checks import KSI, _frame

from puzzle.frames.seismic import G, approximate_period, correlation, cu, spectrum
from puzzle.model.schema import Feature, Quantity, SeismicDesign, SolveStep
from puzzle.project import save_project
from puzzle.solve import run_study

E, RHO, H = 29000 * KSI, 7850.0, 6.0
DESIGN = dict(sds=1.0, sd1=0.6, s1=0.4, tl=8.0, r=8.0, cd=5.5, ie=1.0)
BETA = (1.87510, 4.69409, 7.85476)
MASS = (0.6131, 0.1883, 0.0647)  # effective masses of the first three, of rho A L


def _w14x90():
    from puzzle.sections import _table

    return _table("aisc")["W14X90"]


def _column(tmp_path, solver, settings):
    fixed = [Feature(type="fixed", selection="F/A")]
    p, study, comp = _frame({"A": [0, 0, 0], "B": [0, 0, H]}, [["AB", "A", "B"]], fixed,
                            "W14X90", size=H / 24)  # fmt: skip
    study.solver = solver
    study.seismic = SeismicDesign(**DESIGN)
    study.steps = [SolveStep(type="modal", settings=settings)]
    root = tmp_path / "frame"
    save_project(p, root)
    return run_study(p, study.id, root, tmp_path / "w", timeout_s=1800)


def _strong_modes(run):
    """The modes bending about the strong axis (x motion), lowest first."""
    modes = [s for s in run.results.steps if s.kind == "modal"]
    return [s for s in modes if s.fields["mass_fraction"].values[0][0] > 0.01]


@over("calculix", "code_aster")
def test_modes_and_effective_masses_from_the_solver(tmp_path, solver):
    run = _column(tmp_path, solver, {"modes": 12})
    row = _w14x90()
    base = math.sqrt(E * row["Ix"] / (RHO * row["A"] * H**4)) / (2 * math.pi)
    strong = _strong_modes(run)
    for k, s in enumerate(strong[:2]):
        fraction = float(s.fields["mass_fraction"].values[0][0])
        meff = float(s.fields["effective_mass"].values[0][0]) / (RHO * row["A"] * H)
        print(f"{solver} mode {k + 1}: {s.value:.5f} Hz ({BETA[k] ** 2 * base:.5f}), effective "
              f"mass {meff:.4f} of rho A L ({MASS[k]}), fraction {fraction:.4f}")  # fmt: skip
        # Euler-Bernoulli closed forms; both solvers' beams are Timoshenko's (shear and
        # rotary inertia: the I-section's shear area is its web), 1.8 % lower on the
        # first mode, 11 % on the second, whose wavelength is a third of the first's
        assert s.value == pytest.approx(BETA[k] ** 2 * base, rel=0.025 if k == 0 else 0.12)
        assert s.value < BETA[k] ** 2 * base
        assert meff == pytest.approx(MASS[k], abs=0.015)


@over("calculix", "code_aster")
def test_a_columns_spectrum_against_its_closed_form_modes(tmp_path, solver):
    run = _column(tmp_path, solver, {"modes": 12, "response_spectrum": True,
                                     "spectrum_directions": "x"})  # fmt: skip
    row = _w14x90()
    m = RHO * row["A"] * H
    # the CQC of each mode's m_eff S_a g I_e / R, at the solver's own modes (their
    # frequencies and effective masses are checked against closed forms above)
    modes = [s for s in run.results.steps if s.kind == "modal"]
    freqs = [s.value for s in modes]
    meff = np.array([s.fields["effective_mass"].values[0][0] for s in modes])
    sa = np.array([spectrum(1 / f, 1.0, 0.6, 8.0) * G / 8.0 for f in freqs])
    shears = meff * sa
    w = [2 * math.pi * f for f in freqs]
    rho = np.array([[correlation(a, b) for b in w] for a in w])
    vt = float(np.sqrt(shears @ rho @ shears))
    first = _strong_modes(run)[0].value
    (sp,) = [s for s in run.results.steps if s.kind == "spectrum"]
    got = dict(zip(sp.fields["spectrum_base_shear"].components,
                   sp.fields["spectrum_base_shear"].values[0], strict=True))  # fmt: skip
    t_used = min(1 / first, cu(0.6) * approximate_period("other", H))
    cs = min(1.0 / 8.0, 0.6 / (t_used * 8.0))
    v_elf = cs * m * G
    print(
        f"{solver}: V_t {got['modal_V']:.1f} N ({vt:.1f}), ELF V {got['elf_V']:.1f} "
        f"({v_elf:.1f}), scale {got['scale']:.4f}, T {got['period']:.4f} s ({t_used:.4f})"
    )
    assert got["modal_V"] == pytest.approx(vt, rel=1e-9)
    assert got["period"] == pytest.approx(t_used, rel=1e-9)
    # W: the mass the modes move; CalculiX's lumped mass leaves the support node's share
    # (1/48 of it) out, Code_Aster's POST_ELEM counts it all
    assert got["elf_V"] == pytest.approx(v_elf, rel=0.03)
    assert got["scale"] == pytest.approx(max(1.0, v_elf / vt), rel=0.03)  # W, as above
    assert sp.label() == "Ex response spectrum (CQC)"
    assert float(np.max(np.abs(sp.fields["displacement"].values))) > 0


def test_elmer_refuses_a_spectrum():
    from conftest_geometry import bar_project, write_bar_step  # noqa: F401

    from puzzle.adapters.elmer import ElmerAdapter
    from puzzle.model import Physics, Study
    from puzzle.model.schema import Project

    p = Project(name="p")
    from uuid import uuid4

    from puzzle.model.schema import Component, PartInstance

    part = PartInstance(name="P", geometry=uuid4())
    comp = p.add(Component(name="C", parts=[part], mesh=uuid4(),
                           physics=[Physics(type="solid_mechanics")]))  # fmt: skip
    modal = SolveStep(type="modal", settings={"response_spectrum": True})
    study = p.add(Study(name="S", component=comp.id, solver="elmer",
                        seismic=SeismicDesign(**DESIGN), steps=[modal]))  # fmt: skip
    problems = ElmerAdapter().check(p, study)
    assert any("response spectrum" in x for x in problems)


@over("calculix", "code_aster")
def test_combinations_take_the_spectrum_both_ways(tmp_path, solver):
    """1.2D + Ex: the static 1.2 D plus and minus the x spectrum's CQC envelope, as
    PyNite's (+E) and (−E) steps; 0.9D − Ex is its twin and goes."""
    from puzzle.model.schema import LoadCombination

    fixed = [Feature(type="fixed", selection="F/A"),
             Feature(type="force", selection="F/B", case="D",
                     values={"z": Quantity(value=-50e3, unit="N")})]  # fmt: skip
    p, study, comp = _frame({"A": [0, 0, 0], "B": [0, 0, H]}, [["AB", "A", "B"]], fixed,
                            "W14X90", size=H / 24)  # fmt: skip
    study.solver = solver
    study.seismic = SeismicDesign(**DESIGN)
    study.steps = [SolveStep(type="stationary"),
                   SolveStep(type="modal", settings={"modes": 12, "response_spectrum": True,
                                                     "spectrum_directions": "x"})]  # fmt: skip
    study.combinations = [
        LoadCombination(name="1.2D + Ex", factors={"D": 1.2, "Ex": 1.0}),
        LoadCombination(name="0.9D - Ex", factors={"D": 0.9, "Ex": -1.0}),
    ]
    root = tmp_path / "frame"
    save_project(p, root)
    run = run_study(p, study.id, root, tmp_path / "w", timeout_s=1800)
    steps = {s.label(): s for s in run.results.steps if s.kind == "combination"}
    assert set(steps) == {"1.2D + Ex (+E)", "1.2D + Ex (−E)"}
    (sp,) = [s for s in run.results.steps if s.kind == "spectrum"]
    env = sp.fields["displacement"]
    up, down = (steps[f"1.2D + Ex ({m}E)"].fields["displacement"] for m in "+−")
    tip = int(np.argmax(np.abs(env.values[:, 0])))
    node = env.ids[tip : tip + 1]
    ux_up, ux_down = up.at(node)[0, 0], down.at(node)[0, 0]
    print(f"{solver}: tip ux {ux_up:.6g} / {ux_down:.6g} m, envelope {env.values[tip, 0]:.6g}")
    assert (ux_up - ux_down) / 2 == pytest.approx(env.values[tip, 0], rel=1e-9)
    static_z = (up.at(node)[0, 2] + down.at(node)[0, 2]) / 2
    assert static_z < 0  # 1.2 D pushes the top down in both
