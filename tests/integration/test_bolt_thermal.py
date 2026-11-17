"""Preloaded bolts under temperature (ADR 0090 package 6; ADR 0091 holds the harder
cases), on CalculiX and Code_Aster, against the two-spring joint.

The joint (`bolt_thermal_joint.py`): an aluminium 6061-T6 sleeve, 40 mm long, clamped
by an ASTM A36 bolt. A free length mismatch delta between bolt and sleeve changes the
bolt force by delta / C, C = 1/k_b + 1/k_c the joint's compliance; heated uniformly by
dT, delta = (alpha_c - alpha_b) dT L. Where the stiffnesses come from:

- beam bolts: k_b = E_b A_b / L, exact (CalculiX's Timoshenko springs and Code_Aster's
  POU_D_T are E A / L axially); k_c from the model itself, the preload over the sleeve's
  end-face shortening in the preload step (the spiders spread the force each solver's
  own way: Code_Aster's LIAISON_RBE3 made the sleeve 2.2 % softer than E A / L);
- modelled bolts: C from the model itself, a `bolt_adjustment` run (F = d / C). Their
  heads and nuts bear on the sleeve through frictionless contact: a tie would add the
  steel's and the aluminium's different radial growth as a shear on the bearing faces,
  which no two-spring joint holds (10 % off with ties, measured).

The bolt force: a beam bolt's `fastener` field (from its spiders' reactions, exact); a
modelled bolt's mean axial stress times its area, sampled 2 to 8 mm either side of the
pretension section (the stress is uniform there: 10000.2 N read for a 10 kN preload).
"""

import numpy as np
import pytest
from bolt_thermal_joint import (
    D_BEAM,
    R_BOLT,
    T_REF,
    L,
    heat,
    joint,
    q,
)
from solvers import over

from puzzle.model.schema import Feature, SolveStep
from puzzle.project import save_project
from puzzle.solve import run_study

ALPHA_AL, ALPHA_ST = 23.6e-6, 11.7e-6  # 6061-T6 and A36, 1/K (generic.toml)
E_ST = 29000 * 6.894757293168e6  # A36, Pa
F0 = 10e3  # N, the preload


def _run(tmp_path, name, p, study):
    root = tmp_path / name
    save_project(p, root)
    return run_study(p, study.id, root, tmp_path / f"{name}-w", timeout_s=3600)


def _beam_force(step) -> float:
    return float(step.fields["fastener"].values[0][0])


def _modelled_force(run, step) -> float:
    stress = step.fields["stress"]
    xyz = run.mesh.node_coords(stress.ids)
    r = np.hypot(xyz[:, 0], xyz[:, 1])
    dz = np.abs(xyz[:, 2] - L / 2)
    shank = (dz > 2e-3) & (dz < 8e-3) & (r < R_BOLT - 1e-6)
    return float(stress.values[shank, 2].mean() * np.pi * R_BOLT**2)


def _stretch(run, step) -> float:
    """The sleeve's length change: its end faces' mean axial displacements apart."""
    u = step.fields["displacement"]
    top = u.at(run.mesh.groups["S/Top"].node_ids)[:, 2].mean()
    bottom = u.at(run.mesh.groups["S/Bottom"].node_ids)[:, 2].mean()
    return float(top - bottom)


def _by_step(run):
    """The last result of each solve step, in order."""
    out = {}
    for s in run.results.steps:
        out[s.index] = s
    return [out[k] for k in sorted(out)]


K_BEAM = E_ST * np.pi * D_BEAM**2 / 4 / L  # the beam bolt's axial stiffness, exact


def _sleeve_stiffness(run, preload_step) -> float:
    return F0 / -_stretch(run, preload_step)


@over("calculix", "code_aster")
def test_a_beam_bolt_heated_after_its_preload(tmp_path, solver):
    """a, c and f on a beam bolt: preloaded to 10 kN (Puzzle puts the preload step in
    front), then the joint heated 100 K. (a) The force rises by delta / C; (c) the same
    joint of one material (A36 sleeve) keeps its preload, the null test of a missing
    expansion term; (f) snug (1 N) the force is the same rise alone."""
    dt = 100.0
    results = {}
    for name, sleeve, preload in (("a", "aluminium_6061_t6", F0), ("c", "astm_a36", F0),
                                  ("f", "aluminium_6061_t6", 1.0)):  # fmt: skip
        p, study, comp = joint(solver, "beam", sleeve=sleeve, preload=preload)
        comp.physics[0].features.append(heat("S/Body", T_REF + dt))
        results[name] = _run(tmp_path, name, p, study)
    first, hot = _by_step(results["a"])
    k_c = _sleeve_stiffness(results["a"], first)
    rise = (ALPHA_AL - ALPHA_ST) * dt * L / (1 / K_BEAM + 1 / k_c)
    got = _beam_force(hot) - _beam_force(first)
    same = _beam_force(_by_step(results["c"])[-1]) - F0
    alone = _beam_force(_by_step(results["f"])[-1]) - 1.0  # snug: 1 N
    print(f"{solver} beam bolt: preload {_beam_force(first):.6g} N, rise {got:.6g} N (closed "
          f"form {rise:.6g}, k_c {k_c:.5g} N/m against E A / L "
          f"{68.9e9 * np.pi * (12e-3**2 - 6e-3**2) / L:.5g}); one material {same:+.4g} N; "
          f"snug {alone:.6g} N")  # fmt: skip
    assert _beam_force(first) == pytest.approx(F0, rel=1e-6)
    assert got == pytest.approx(rise, rel=1e-3)
    assert abs(same) < 1e-5 * F0
    assert alone == pytest.approx(got, rel=1e-4)


@over("calculix", "code_aster")
def test_a_beam_bolt_preloaded_heated_and_loaded(tmp_path, solver):
    """e: three steps, the preload locked throughout: the preload; 100 K; then 5 kN
    pulling the sleeve's end faces apart, of which the bolt takes Phi F_A, Phi =
    k_b / (k_b + k_c) (the load enters at the bolt's own bearing planes; 0.1 %, and 3 %
    on Code_Aster, whose spider's weights differ from the load's)."""
    dt, pull = 100.0, 5e3
    p, study, comp = joint(solver, "beam")
    feats = comp.physics[0].features
    feats.append(heat("S/Body", T_REF + dt, steps=[2, 3]))
    feats += [Feature(type="force", selection="S/Top", values={"z": q(pull, "N")}, steps=[3]),
              Feature(type="force", selection="S/Bottom", values={"z": q(-pull, "N")},
                      steps=[3])]  # fmt: skip
    study.steps = [SolveStep(type="stationary") for _ in range(3)]
    run = _run(tmp_path, "e", p, study)
    first, hot, loaded = _by_step(run)
    k_c = _sleeve_stiffness(run, first)
    rise = (ALPHA_AL - ALPHA_ST) * dt * L / (1 / K_BEAM + 1 / k_c)
    share = K_BEAM / (K_BEAM + k_c) * pull
    forces = [_beam_force(s) for s in (first, hot, loaded)]
    print(f"{solver} preload, heat, load: {[round(f, 2) for f in forces]} N (closed form "
          f"{F0:g}, {F0 + rise:.6g}, {F0 + rise + share:.6g})")  # fmt: skip
    assert forces[0] == pytest.approx(F0, rel=1e-6)
    assert forces[1] == pytest.approx(F0 + rise, rel=1e-3)
    # Code_Aster's LIAISON_RBE3 weights the bolt's spider unlike the force's area
    # spread, so its sleeve takes the load a little differently: 2.2 % (3 %)
    assert forces[2] - forces[1] == pytest.approx(share, rel=1e-3 if solver == "calculix"
                                                  else 3e-2)  # fmt: skip


@over("calculix", "code_aster")
def test_a_bolt_to_ground_grows_from_its_held_end(tmp_path, solver):
    """A beam bolt to ground from the sleeve's bore (its far end held 2 d along the
    axis) heated 100 K with the sleeve: nothing resists its growth, so it carries
    nothing and the bore's spider moves back by alpha_b dT 2 d (the bore's displacement
    averaged with its spider's own weights; 1 %: Code_Aster's weights differ)."""
    from puzzle.adapters.common import fastener_end

    dt = 100.0
    p, study, comp = joint(solver, "beam", preload=0.0)
    c = comp.connectors[0]
    c.source, c.target, c.ground, c.ground_direction = "S/Bore", None, True, (0.0, 0.0, 1.0)
    comp.physics[0].features.append(heat("S/Body", T_REF + dt))
    run = _run(tmp_path, "g", p, study)
    step = run.results.steps[-1]
    end = fastener_end("S/Bore", run.mesh, "bore")
    moved = float(end.weights @ step.fields["displacement"].at(end.nodes)[:, 2])
    want = -ALPHA_ST * dt * 2 * D_BEAM
    print(f"{solver} bolt to ground: force {_beam_force(step):.3g} N, bore {moved:.6g} m "
          f"({want:.6g})")  # fmt: skip
    assert abs(_beam_force(step)) < 1.0
    assert moved == pytest.approx(want, rel=1e-2)


@over("calculix")
def test_a_modal_step_after_preload_and_heat(tmp_path, solver):
    """h: preload, heat, then a modal step: it solves, the pretension is locked (the
    bolt whole) and the heat is not in it (a modal step has no thermal load), so its
    elastic modes equal those of the joint neither preloaded nor heated, to 1e-5."""
    modes = {}
    for name, preload in (("loaded", F0), ("plain", 0.0)):
        p, study, comp = joint(solver, "beam", preload=preload)
        if preload:
            comp.physics[0].features.append(heat("S/Body", T_REF + 100.0, steps=[1]))
            study.steps = [SolveStep(type="stationary"),
                           SolveStep(type="modal", settings={"modes": 10})]  # fmt: skip
        else:
            study.steps = [SolveStep(type="modal", settings={"modes": 10})]
        run = _run(tmp_path, name, p, study)
        modes[name] = [s.value for s in run.results.steps if s.kind == "modal" and s.value > 100]
    print(f"{solver} elastic modes after preload and heat {np.round(modes['loaded'], 1)} Hz, "
          f"plain {np.round(modes['plain'], 1)} Hz")  # fmt: skip
    assert len(modes["plain"]) >= 3
    n = min(len(modes["loaded"]), len(modes["plain"]))
    assert modes["loaded"][:n] == pytest.approx(modes["plain"][:n], rel=1e-5)


def _compliance(tmp_path, solver, contact="frictionless") -> float:
    """The modelled joint's compliance seen from its section: an adjustment d gives the
    bolt d / C (no heat)."""
    d = 6e-5
    p, study, _ = joint(solver, "modelled", adjustment=d, contact=contact)
    run = _run(tmp_path, "compliance", p, study)
    return d / _modelled_force(run, run.results.steps[-1])


def _heated_modelled(tmp_path, solver, name, temperatures, contact="frictionless",
                     sleeve="aluminium_6061_t6", extra=(), adjustment=None):  # fmt: skip
    """A modelled bolt preloaded in step 1 (or shortened by `adjustment`), then step k at
    `temperatures[k - 2]` = (bolt dT, sleeve dT). -> (run, steps' results)."""
    p, study, comp = joint(solver, "modelled", contact=contact, sleeve=sleeve,
                           adjustment=adjustment)  # fmt: skip
    feats = comp.physics[0].features
    for k, (bolt, sleeve_dt) in enumerate(temperatures, start=2):
        feats += [heat("B/Body", T_REF + bolt, steps=[k]), heat("S/Body", T_REF + sleeve_dt,
                                                              steps=[k])]  # fmt: skip
    feats += list(extra)
    study.steps = [SolveStep(type="stationary") for _ in range(len(temperatures) + 1)]
    run = _run(tmp_path, name, p, study)
    return run, _by_step(run)


@over("calculix")
def test_a_modelled_bolt_heated_cooled_and_loaded(tmp_path, solver):
    """A modelled bolt (frictionless heads; C from an adjustment run):
    (a) heated 100 K after its preload: delta / C, 0.5 % (with heads 4 mm thick the
        bearing pressure moved with the load, 7 % off at a 3.5 kN clamp; 10 mm keeps it);
    (d) the bolt at +150 K, the sleeve at +50 K: delta = (alpha_c 50 - alpha_b 150) L;
    (b) cooled 40 K and 80 K, the force falling linearly, then 250 K, past the
        -104 K at which the joint opens: no force, and never a negative one;
    (e) heated 100 K, then 5 kN pulling the head and nut apart: the bolt takes the same
        share Phi F_A as in the joint cold (0.5 %);
    and a pretension by adjustment d, then 100 K: (d + delta) / C.
    """
    compliance = _compliance(tmp_path, solver)
    mismatch = (ALPHA_AL - ALPHA_ST) * L
    run, steps = _heated_modelled(tmp_path, solver, "a", [(100.0, 100.0), (150.0, 50.0),
                                                          (-40.0, -40.0), (-80.0, -80.0),
                                                          (-250.0, -250.0)])  # fmt: skip
    f = [_modelled_force(run, s) for s in steps]
    opens = -f[0] * compliance / mismatch
    want = {
        "a": f[0] + mismatch * 100 / compliance,
        "d": f[0] + (ALPHA_AL * 50 - ALPHA_ST * 150) * L / compliance,
        "b40": f[0] - mismatch * 40 / compliance,
        "b80": f[0] - mismatch * 80 / compliance,
    }
    print(f"{solver} modelled bolt: C {compliance:.5g} m/N, preload {f[0]:.6g} N; heated "
          f"{f[1]:.6g} ({want['a']:.6g}); bolt hotter {f[2]:.6g} ({want['d']:.6g}); cooled "
          f"{f[3]:.6g} ({want['b40']:.6g}), {f[4]:.6g} ({want['b80']:.6g}); past {opens:.4g} "
          f"K: {f[5]:.4g} N")  # fmt: skip
    assert f[0] == pytest.approx(F0, rel=1e-4)
    assert f[1] == pytest.approx(want["a"], rel=5e-3)
    assert f[2] == pytest.approx(want["d"], rel=5e-3)
    assert f[3] == pytest.approx(want["b40"], rel=5e-3)
    assert f[4] == pytest.approx(want["b80"], rel=5e-3)
    assert -250 < opens < -40 and abs(f[5]) < 1e-3 * F0 and f[5] > -1e-3 * F0

    pull = 5e3
    loads = [Feature(type="force", selection="B/Top of head", values={"z": q(pull, "N")},
                     steps=[3]),
             Feature(type="force", selection="B/Bottom of nut", values={"z": q(-pull, "N")},
                     steps=[3])]  # fmt: skip
    run, steps = _heated_modelled(tmp_path, solver, "e", [(100.0, 100.0), (100.0, 100.0)],
                                  extra=loads)  # fmt: skip
    f = [_modelled_force(run, s) for s in steps]
    # the load's share Phi F_A from the joint itself, cold: preload, then the load
    run, cold = _heated_modelled(tmp_path, solver, "e0", [(0.0, 0.0), (0.0, 0.0)], extra=loads)
    share = _modelled_force(run, cold[2]) - _modelled_force(run, cold[1])
    print(f"{solver} modelled preload, heat, load: {[round(x, 1) for x in f]} N; the load's "
          f"share {f[2] - f[1]:.5g} N (cold {share:.5g} N, Phi {share / pull:.4f})")  # fmt: skip
    assert f[1] == pytest.approx(f[0] + mismatch * 100 / compliance, rel=5e-3)
    assert f[2] - f[1] == pytest.approx(share, rel=5e-3)  # heat barely moves Phi (contact)
    assert 0 < share < pull

    # pretension by adjustment: shortened by d, then heated 100 K: (d + delta) / C
    d = 6e-5
    run, steps = _heated_modelled(tmp_path, solver, "adj", [(100.0, 100.0)], adjustment=d)
    f = [_modelled_force(run, s) for s in steps]
    print(f"{solver} adjusted {d:g} m: {f[0]:.6g} N ({d / compliance:.6g}), heated {f[1]:.6g} "
          f"N ({(d + mismatch * 100) / compliance:.6g})")  # fmt: skip
    assert f[0] == pytest.approx(d / compliance, rel=1e-4)
    assert f[1] == pytest.approx((d + mismatch * 100) / compliance, rel=5e-3)


@over("calculix", "code_aster")
def test_a_modelled_bolt_of_one_material_keeps_its_preload(tmp_path, solver):
    """c on a modelled bolt (heads tied, which both solvers converge): bolt and sleeve
    of A36 heated 100 K keep the preload (0.1 %, the stress sampling's own scatter)."""
    run, steps = _heated_modelled(tmp_path, solver, "c", [(100.0, 100.0)], contact="bonded",
                                  sleeve="astm_a36")  # fmt: skip
    before, after = (_modelled_force(run, s) for s in steps)
    print(f"{solver} modelled bolt, one material: {before:.6g} then {after:.6g} N")
    assert after == pytest.approx(before, rel=1e-3)


def test_calculix_and_code_aster_heat_the_same_joint_alike(tmp_path):
    """g across solvers: the modelled bolt (heads tied) and the beam bolt, preloaded and
    heated 100 K, on CalculiX and Code_Aster. The modelled bolts agree to 1e-4 (the same
    mesh, ties, cut and temperatures). The beam bolts each match their own closed form
    (`test_a_beam_bolt_heated_after_its_preload`) and differ by their spiders: Code_Aster's
    LIAISON_RBE3 leaves the sleeve 2.2 % softer, so its rise is 0.9 % lower (1.5 %)."""
    from solvers import available

    if not all(available(s) for s in ("calculix", "code_aster")):
        pytest.skip("needs CalculiX and Code_Aster")
    modelled, beam = {}, {}
    for solver in ("calculix", "code_aster"):
        run, steps = _heated_modelled(tmp_path / solver, solver, "g", [(100.0, 100.0)],
                                      contact="bonded")  # fmt: skip
        modelled[solver] = _modelled_force(run, steps[-1])
        p, study, comp = joint(solver, "beam")
        comp.physics[0].features.append(heat("S/Body", T_REF + 100.0))
        first, hot = _by_step(_run(tmp_path / solver, "beam", p, study))
        beam[solver] = _beam_force(hot) - _beam_force(first)
    print(f"modelled bolt heated: {modelled}; beam bolt rise: {beam}")
    assert modelled["code_aster"] == pytest.approx(modelled["calculix"], rel=1e-4)
    assert beam["code_aster"] == pytest.approx(beam["calculix"], rel=1.5e-2)


@over("calculix", "code_aster")
def test_rigid_spiders_under_temperature_are_refused(tmp_path, solver):
    """Rigid spiders don't grow with the faces they hold: refused (`unsupported`), never
    solved wrong (+13.6 % of the preload in a joint of one material, measured)."""
    from puzzle.errors import PuzzleError

    for change in ({"ends": "rigid"}, {"formulation": "solid"}):
        p, study, comp = joint(solver, "beam")
        for k, v in change.items():
            setattr(comp.connectors[0], k, v)
        comp.physics[0].features.append(heat("S/Body", T_REF + 100.0))
        with pytest.raises(PuzzleError) as err:
            _run(tmp_path, f"refused-{next(iter(change))}", p, study)
        assert err.value.code == "unsupported"


@over("calculix", "code_aster")
def test_a_transferred_temperature_heats_a_beam_bolt(tmp_path, solver):
    """A thermal study's temperature (a transfer, role load) heats the snug beam bolt's
    joint as the body temperature does: the sleeve held at 100 K above the reference on
    both end faces solves to that temperature everywhere, and the bolt's force is the
    same rise to 1e-4 (in every step: a transfer has no steps of its own)."""
    from uuid import uuid4

    from puzzle.model.schema import Physics, Study, Transfer

    forces = {}
    for name in ("body", "transfer"):
        p, study, comp = joint(solver, "beam", preload=0.0, springs=1e-4)
        study.steps = [SolveStep(type="stationary", physics=["solid_mechanics"])]
        if name == "body":
            comp.physics[0].features.append(heat("S/Body", T_REF + 100.0))
        else:  # the sleeve alone, its own component (no bolts in a thermal study)
            hot = p.add(comp.model_copy(update={"id": uuid4(), "name": "Sleeve", "connectors": [],
                                                "physics": [Physics(type="heat_transfer", features=[
                Feature(type="temperature", selection=s, values={"T": q(T_REF + 100.0, "K")})
                for s in ("S/Top", "S/Bottom")],
                settings={"temperature_dependent_materials": False})]}))  # fmt: skip
            thermal = p.add(Study(name="Thermal", component=hot.id, solver=solver,
                                  steps=[SolveStep(type="stationary",
                                                   physics=["heat_transfer"])]))  # fmt: skip
            p.add(Transfer(name="T", source=thermal.id, field="temperature", target=study.id,
                           role="load"))  # fmt: skip
        forces[name] = _beam_force(_run(tmp_path, name, p, study).results.steps[-1])
    print(f"{solver} snug beam bolt: body temperature {forces['body']:.6g} N, transferred "
          f"{forces['transfer']:.6g} N")  # fmt: skip
    assert forces["body"] > 1e4
    assert forces["transfer"] == pytest.approx(forces["body"], rel=1e-4)
