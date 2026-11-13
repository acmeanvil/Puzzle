"""Light welds (ADR 0078 decision 2) against statics and the elastic method.

Lap joint: a 10 mm plate lapped 40 mm on another, joined only by a 6 mm transverse
fillet across its end (50 mm long); the lower plate held at its far end, the upper
pulled along by P. The weld carries all of P (equilibrium: to the solver's precision),
spread along it at about P / L.

Bracket welded all round: a 20 × 40 mm block, 60 mm tall, on a stiff base, a 6 mm
fillet round its foot; a side load P at its top. The weld group carries the shear P and
the moment P h (h = 60 mm, the top over the weld) by statics, exactly; about 7 % of the
moment is the fillets' own resistance to turning about their lines (`twist`: the ±y
sides run along x), which the elastic method leaves out. The elastic method (weld
as lines, AISC Manual Part 8) gives the line force normal to the base at the ±y sides,
f = M (d/2) / I with I = b d²/2 + d³/6, and the shear P / (2 (b + d)); the FE weld's
mean normal force along those sides is within 10 % (the parts aren't rigid, so the
force gathers toward the corners).
"""

import numpy as np
import pytest
from solvers import over

from puzzle.model.schema import (
    Component,
    Feature,
    Geometry,
    HistoryStep,
    MaterialAssignment,
    MeshRecipe,
    NamedSelection,
    PartInstance,
    Physics,
    Project,
    Quantity,
    SolveStep,
    Study,
    Weld,
)
from puzzle.project import save_project
from puzzle.solve import run_study

q = lambda v, u: Quantity(value=v, unit=u)  # noqa: E731
P = 10e3  # N
LEG = 0.006


def _box(p, name, dx, dy, dz, edges=()):
    faces = ("-x", "+x", "-y", "+y", "-z", "+z")
    return p.add(Geometry(
        name=name,
        history=[HistoryStep(kind="box", name="B", args={"dx": dx, "dy": dy, "dz": dz})],
        selections=[
            NamedSelection(name="Body", entity="body", members=["Body"]),
            *(NamedSelection(name=f, entity="face", members=[f"B/{f}"]) for f in faces),
            *(NamedSelection(name=n, entity=kind, members=m) for n, kind, m in edges),
        ],
    ))  # fmt: skip


def _project(solver, parts, features, weld, mesh_mm):
    p = Project(name="welded")
    from puzzle.materials import add_library_material

    steel = add_library_material(p, "structural_steel")
    geos = {name: _box(p, name, *size, edges=edges) for name, (size, _at, edges) in parts.items()}
    mesh = p.add(MeshRecipe(name="M", global_size=q(mesh_mm, "mm")))
    comp = p.add(Component(
        name="Joint",
        parts=[PartInstance(name=n, geometry=geos[n].id, translation=at)
               for n, (_size, at, _e) in parts.items()],
        mesh=mesh.id,
        materials=[MaterialAssignment(material=steel.id, selection=f"{n}/Body") for n in parts],
        physics=[Physics(type="solid_mechanics", features=features,
                         settings={"temperature_dependent_materials": False})],
        welds=[weld],
    ))  # fmt: skip
    study = p.add(Study(name="Load", component=comp.id, solver=solver,
                        steps=[SolveStep(type="stationary")]))  # fmt: skip
    return p, study


def _run(tmp_path, p, study):
    root = tmp_path / "joint"
    save_project(p, root)
    return run_study(p, study.id, root, tmp_path / "w")


def _line(run):
    f = run.results.last("weld_line_force")
    c = {k: i for i, k in enumerate(f.components)}
    return f, c, run.mesh.node_coords(f.ids)


@over("calculix", "code_aster", "fenicsx")
def test_lap_joint_carries_the_pull(tmp_path, solver):
    pytest.importorskip("OCP")
    length = 0.05
    parts = {
        "Lower": ((0.1, length, 0.01), (0.0, 0.0, 0.0), ()),
        "Upper": ((0.1, length, 0.01), (0.06, 0.0, 0.01), (("Foot", "edge", ["B/-x|B/-z"]),)),
    }
    features = [
        Feature(type="fixed", selection="Lower/-x"),
        Feature(type="force", selection="Upper/+x", values={"x": q(P, "N")}),
    ]
    weld = Weld(name="End fillet", leg=q(6, "mm"), path="Upper/Foot",
                faces=["Upper/-x", "Lower/+z"])  # fmt: skip
    p, study = _project(solver, parts, features, weld, mesh_mm=3.0)
    run = _run(tmp_path, p, study)
    total = run.results.last("weld").values[0]
    f, c, _xyz = _line(run)
    mean = float(np.mean(f.values[:, c["magnitude"]]))
    print(f"{solver} lap joint: weld force {total[0]:.6g} N (P {P:g}), y {total[1]:.3g}, "
          f"z {total[2]:.3g}; mean line force {mean / 1e3:.4g} N/mm (P/L "
          f"{P / length / 1e3:.4g})")  # fmt: skip
    assert total[0] == pytest.approx(P, rel=1e-4)
    assert abs(total[1]) < 1e-3 * P
    assert mean == pytest.approx(P / length, rel=0.05)
    # ADR 0078 step 2: AISC 360 J2 at the governing station, the structural steel's
    # matching E70 filler: utilisation = peak line force / (0.75 x 0.60 x 70 ksi x 0.707 w).
    from puzzle.model.schema import Component
    from puzzle.post.weld_fields import results

    comp = next(o for o in p.objects.values() if isinstance(o, Component))
    (got,) = results(p, comp, run.mesh, run.results)["welds"]
    strength = got["strength"]
    design = 0.75 * 0.60 * 482.6e6 * 0.006 / np.sqrt(2)
    print(f"AISC 360 J2: {strength['filler']}, utilisation {strength['utilisation_lrfd']:.4g} "
          f"(peak {got['peak_line_force_N_per_m'] / 1e3:.4g} N/mm of "
          f"{design / 1e3:.4g})")  # fmt: skip
    assert strength["checked"] and strength["filler"] == "E70XX"
    assert strength["design_strength_lrfd_N_per_m"] == pytest.approx(design, rel=1e-6)
    assert strength["utilisation_lrfd"] == pytest.approx(
        got["peak_line_force_N_per_m"] / design, rel=1e-6
    )


@over("calculix", "code_aster", "fenicsx")
def test_bracket_welded_all_round(tmp_path, solver):
    pytest.importorskip("OCP")
    b, d, h = 0.02, 0.04, 0.06
    foot = ["B/+x|B/-z", "B/-x|B/-z", "B/+y|B/-z", "B/-y|B/-z"]
    parts = {
        "Base": ((0.1, 0.1, 0.02), (0.0, 0.0, -0.02), ()),
        "Block": (
            (b, d, h),
            (0.04, 0.03, 0.0),
            (("Foot", "edge", foot), ("Sides", "face", ["B/+x", "B/-x", "B/+y", "B/-y"])),
        ),
    }
    features = [
        Feature(type="fixed", selection="Base/-z"),
        Feature(type="force", selection="Block/+z", values={"y": q(P, "N")}),
    ]
    weld = Weld(name="All round", leg=q(6, "mm"), path="Block/Foot",
                faces=["Block/Sides", "Base/+z"])  # fmt: skip
    p, study = _project(solver, parts, features, weld, mesh_mm=3.0)
    run = _run(tmp_path, p, study)
    g = run.results.last("weld")
    names = {k: i for i, k in enumerate(g.components)}
    total = g.values[0]
    f, c, xyz = _line(run)
    # The weld's moment about its centroid in the base's plane, from its forces.
    centre = np.array([0.05, 0.05, 0.0])
    force = total[[names[a] for a in "xyz"]]
    moment = total[[names[a] for a in ("mx", "my", "mz")]] - np.cross(centre, force)
    twisting = float(np.mean(np.abs(f.values[:, c["twist"]])))
    inertia = b * d**2 / 2 + d**3 / 6
    elastic = P * h * (d / 2) / inertia
    sides = np.abs(np.abs(xyz[:, 1] - centre[1]) - d / 2) < 1e-6
    normal = float(np.mean(np.abs(f.values[sides, c["z"]])))
    print(f"{solver} bracket: shear {total[1]:.6g} N (P {P:g}); moment about x "
          f"{moment[0]:.5g} N·m (P h {P * h:.5g}); normal line force on the ±y sides "
          f"{normal / 1e3:.4g} N/mm, elastic method {elastic / 1e3:.4g} N/mm; "
          f"shear line force elastic {P / (2 * (b + d)) / 1e3:.4g} N/mm; twist "
          f"{twisting:.4g} N·m/m")  # fmt: skip
    assert total[1] == pytest.approx(P, rel=1e-4)
    assert abs(moment[0]) == pytest.approx(P * h, rel=1e-4)
    assert normal == pytest.approx(elastic, rel=0.10)
    # The instantaneous centre (in the base's plane): the shear passes over the centroid,
    # so the group is concentric there; by hand, the transverse sides (±y, along x, b
    # long) at their rupture Δ_u(90°), the longitudinal ones at p = Δ_u(90°)/Δ_m(0°).
    from puzzle.model.schema import Component
    from puzzle.post.weld_fields import results
    from puzzle.weld_strength import _fp

    ic = results(p, p.get(study.component, Component), run.mesh,
                 run.results)["welds"][0]["strength"]["instantaneous_centre"]  # fmt: skip
    w, fexx = 0.006, 482.6e6
    te = 0.707 * w
    du90 = min(1.087 * 96**-0.65, 0.17) * w
    longitudinal = 0.6 * fexx * te * 2 * d * float(_fp(du90 / (0.209 * 2**-0.32 * w)))
    transverse = 0.6 * fexx * 1.5 * te * 2 * b * float(_fp(du90 / (0.209 * 92**-0.32 * w)))
    assert ic["checked"], ic
    print(
        f"instantaneous centre: P_n {ic['Pn_N'] / 1e3:.4g} kN ({ic['method']}), by hand "
        f"{(longitudinal + transverse) / 1e3:.4g} kN; out of plane "
        f"{ic['out_of_plane_moment_N_m']:.4g} N m"
    )
    assert ic["method"].startswith("concentric")
    assert ic["Pn_N"] == pytest.approx(longitudinal + transverse, rel=0.03)
    # the line forces' share of P h: the rest is the fillets' twist about their lines (~7 %)
    assert ic["out_of_plane_moment_N_m"] == pytest.approx(P * h, rel=0.1) and "note" in ic


def test_the_generator_finds_the_joint(tmp_path):
    """Picking the upper plate's end edge: the generator finds the lower plate, puts the
    first leg on the end face and the second on the plate below, in one batch."""
    pytest.importorskip("OCP")
    from puzzle import weld_generator
    from puzzle.commands import Session

    parts = {
        "Lower": ((0.1, 0.05, 0.01), (0.0, 0.0, 0.0), ()),
        "Upper": ((0.1, 0.05, 0.01), (0.06, 0.0, 0.01), ()),
    }
    weld = Weld(name="placeholder", leg=q(6, "mm"), path="Upper/-x", faces=["a/b", "c/d"])
    p, _study = _project("calculix", parts, [], weld, mesh_mm=5.0)
    comp = p.of_type(Component)[0]
    comp.welds = []
    root = tmp_path / "joint"
    save_project(p, root)
    planned = weld_generator.plan(p, comp, root, "Upper", ["B/-x|B/-z"])
    assert (planned.other, planned.leg_faces, planned.base_faces) == ("Lower", ["B/-x"], ["B/+z"])
    session = Session(root)
    batch = weld_generator.commands(session.project, session.project.get(comp.id, Component),
                                    planned, name="End fillet", leg=q(6, "mm"))  # fmt: skip
    session.submit_batch(batch)
    made = session.project.get(comp.id, Component).welds[0]
    assert made.path == "Upper/End fillet path"
    assert made.faces == ["Upper/-x", "Lower/+z"]  # the existing face selections reused


@over("calculix")
def test_the_welds_panel(tmp_path, solver):
    """Window → Welds lists a stored run's welds with their J2 and IC utilisations, and a
    selected weld's stations."""
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    pytest.importorskip("OCP")
    from PySide6.QtWidgets import QApplication

    from puzzle.gui.window import MainWindow
    from puzzle.project.layout import RESULTS_DIR
    from puzzle.results.store import ResultStore

    QApplication.instance() or QApplication([])
    parts = {
        "Lower": ((0.1, 0.05, 0.01), (0.0, 0.0, 0.0), ()),
        "Upper": ((0.1, 0.05, 0.01), (0.06, 0.0, 0.01), (("Foot", "edge", ["B/-x|B/-z"]),)),
    }
    features = [Feature(type="fixed", selection="Lower/-x"),
                Feature(type="force", selection="Upper/+x", values={"x": q(P, "N")})]  # fmt: skip
    weld = Weld(name="End fillet", leg=q(6, "mm"), path="Upper/Foot",
                faces=["Upper/-x", "Lower/+z"])  # fmt: skip
    p, study = _project(solver, parts, features, weld, mesh_mm=3.0)
    root = tmp_path / "welds"
    save_project(p, root)
    run_study(p, study.id, root, tmp_path / "w", store=ResultStore(root / RESULTS_DIR))
    window = MainWindow(root)
    try:
        window.docks.show("welds")
        window.welds.refresh()
        table = window.welds.table
        assert table.rowCount() == 1, window.welds.note.text()
        assert table.item(0, 0).text() == "End fillet" and table.item(0, 7).text() == "E70XX"
        assert float(table.item(0, 4).text()) == pytest.approx(0.412, abs=0.01)
        assert window.welds.stations.rowCount() > 5
        out = window.welds.export(str(tmp_path / "w.csv"))
        with open(out, encoding="utf-8") as f:
            assert "End fillet" in f.read()
    finally:
        window.close()


@over("calculix")
def test_weld_fatigue_on_the_lap_joint(tmp_path, solver):
    """AISC 360 Appendix 3 on the lap joint cycled from zero to P: category F's range is
    the largest line force over the throat (379.7 N/mm / (0.707 × 6 mm) = 89.5 MPa),
    against 690 (1.5/n)^0.167 MPa; a toe category reads the plates' stress range."""
    from puzzle.model.schema import Component
    from puzzle.post.weld_fields import fatigue

    parts = {
        "Lower": ((0.1, 0.05, 0.01), (0.0, 0.0, 0.0), ()),
        "Upper": ((0.1, 0.05, 0.01), (0.06, 0.0, 0.01), (("Foot", "edge", ["B/-x|B/-z"]),)),
    }
    features = [Feature(type="fixed", selection="Lower/-x"),
                Feature(type="force", selection="Upper/+x", values={"x": q(P, "N")})]  # fmt: skip
    weld = Weld(name="End fillet", leg=q(6, "mm"), path="Upper/Foot",
                faces=["Upper/-x", "Lower/+z"])  # fmt: skip
    p, study = _project(solver, parts, features, weld, mesh_mm=3.0)
    run = _run(tmp_path, p, study)
    comp = p.get(study.component, Component)
    f, c, _xyz = _line(run)
    peak = float(np.max(f.values[:, c["magnitude"]]))
    out = fatigue(p, comp, run.mesh, run.results, "End fillet", "F", 2e6)
    allowed = 690e6 * (1.5 / 2e6) ** 0.167
    print(f"fatigue F: {out['stress_range_Pa'] / 1e6:.4g} MPa against "
          f"{allowed / 1e6:.4g} MPa, ratio {out['ratio']:.3f}")  # fmt: skip
    assert out["stress_range_Pa"] == pytest.approx(peak / (0.707 * 0.006), rel=2e-3)
    assert out["allowable_range_Pa"] == pytest.approx(allowed)
    toe = fatigue(p, comp, run.mesh, run.results, "End fillet", "C", 2e6)
    assert toe["stress_range_Pa"] > 0 and "mesh-dependent" in toe["where"]


def _tee(tmp_path, solver):
    """A T of two 10 mm sheets: the web (100 × 80 mm, upright) welded by 6 mm fillets
    along its foot to the flange (100 × 100 mm), the flange held along its two far edges
    and the web pulled up by P along its top edge."""
    pytest.importorskip("OCP")
    from puzzle.model.schema import ShellSection

    length, height, width, mid = 0.1, 0.08, 0.1, 0.05
    p = Project(name="tee")
    from puzzle.materials import add_library_material

    steel = add_library_material(p, "structural_steel")
    plane = lambda axis, at: {"plane": {"axis": axis, "at": at}}  # noqa: E731
    flange = p.add(Geometry(name="Flange", history=[HistoryStep(kind="sheet", name="F", args={
        "points": [[0, 0, 0], [length, 0, 0], [length, width, 0], [0, width, 0]]})],
        selections=[NamedSelection(name="Face", entity="face", members=["F/face"]),
                    NamedSelection(name="Near", entity="edge", rule=plane("y", 0.0)),
                    NamedSelection(name="Far", entity="edge",
                                   rule=plane("y", width))]))  # fmt: skip
    web = p.add(Geometry(name="Web", history=[HistoryStep(kind="sheet", name="W", args={
        "points": [[0, mid, 0], [length, mid, 0], [length, mid, height], [0, mid, height]]})],
        selections=[NamedSelection(name="Face", entity="face", members=["W/face"]),
                    NamedSelection(name="Foot", entity="edge", rule=plane("z", 0.0)),
                    NamedSelection(name="Top", entity="edge",
                                   rule=plane("z", height))]))  # fmt: skip
    order = 1 if solver == "code_aster" else 2
    mesh = p.add(MeshRecipe(name="M", global_size=q(5, "mm"), element_order=order))
    thick = q(10, "mm")
    comp = p.add(Component(
        name="Tee", mesh=mesh.id,
        parts=[PartInstance(name="F", geometry=flange.id), PartInstance(name="W", geometry=web.id)],
        materials=[MaterialAssignment(material=steel.id, selection=n) for n in ("F", "W")],
        shell_sections=[ShellSection(selection="F", thickness=thick),
                        ShellSection(selection="W", thickness=thick)],
        physics=[Physics(type="solid_mechanics",
                         settings={"temperature_dependent_materials": False},
                         features=[Feature(type="fixed", selection="F/Near"),
                                   Feature(type="fixed", selection="F/Far"),
                                   Feature(type="force", selection="W/Top",
                                           values={"z": q(P, "N")})])],
        welds=[Weld(name="Foot", leg=q(6, "mm"), path="W/Foot", faces=["W/Face", "F/Face"])],
    ))  # fmt: skip
    study = p.add(Study(name="Load", component=comp.id, solver=solver,
                        steps=[SolveStep(type="stationary")]))  # fmt: skip
    return p, comp, _run(tmp_path, p, study)


@over("calculix", "code_aster")
def test_a_light_weld_between_shells(tmp_path, solver):
    """Light welds along a shell's edge (ADR 0078 step 6), on the tee (`_tee`): the weld
    carries P (equilibrium), about P / L along it."""
    length = 0.1
    _p, _comp, run = _tee(tmp_path, solver)
    total = run.results.last("weld").values[0]
    f, c, _xyz = _line(run)
    mean = float(np.mean(f.values[:, c["magnitude"]]))
    print(f"{solver} shell tee: weld force {total[:3]} N (P {P:g}); mean line force "
          f"{mean / 1e3:.4g} N/mm (P/L {P / length / 1e3:.4g})")  # fmt: skip
    assert total[2] == pytest.approx(P, rel=1e-4)
    assert abs(total[0]) < 1e-3 * P and abs(total[1]) < 1e-3 * P
    assert mean == pytest.approx(P / length, rel=0.1)


@over("calculix")
def test_asme_structural_stress_at_a_shell_toe(tmp_path, solver):
    """ASME VIII-2 5.5.5 on the shell tee's web toe: the web is pulled straight, so the
    structural stress is about membrane P / (L t) (the flange's bending turns the web's
    foot a little), and the cycles follow from it by the hand formula."""
    from puzzle.post.weld_fields import structural_stress
    from puzzle.weld_strength import asme_cycles

    p, comp, run = _tee(tmp_path, solver)
    out = structural_stress(p, comp, run.mesh, run.results, "Foot")
    membrane = np.mean([s["membrane_range_Pa"] for s in out["stations"]])
    print(f"web toe: mean membrane {membrane / 1e6:.4f} MPa (P/Lt {P / 0.1 / 0.01 / 1e6:.4f}), "
          f"worst N {out['worst']['cycles']:.4g}")  # fmt: skip
    assert membrane == pytest.approx(P / 0.1 / 0.01, rel=0.05)
    worst = out["worst"]
    again = asme_cycles(worst["membrane_range_Pa"], worst["bending_range_Pa"], 0.01)
    assert worst["cycles"] == pytest.approx(again["cycles"], rel=1e-12)


@over("calculix", "code_aster")
def test_a_modelled_fillet_carries_the_lap_joints_pull(tmp_path, solver):
    """The modelled weld (ADR 0078 step 4) on the lap joint: the light weld's bead drawn
    as a solid (`weld_bead`, legs from its pairs), tied to both plates by its legs, its
    root cut back so the plates meet only through it. The throat's section carries P
    to the section's integration through the root's notch (98.8 % in x, 10 % of P
    across, six elements over the leg), about P / L along it, as the light weld does."""
    pytest.importorskip("OCP")
    from puzzle.commands import Session
    from puzzle.weld_generator import modelled_commands

    length = 0.05
    parts = {
        "Lower": ((0.1, length, 0.01), (0.0, 0.0, 0.0), ()),
        "Upper": ((0.1, length, 0.01), (0.06, 0.0, 0.01), (("Foot", "edge", ["B/-x|B/-z"]),)),
    }
    features = [
        Feature(type="fixed", selection="Lower/-x"),
        Feature(type="force", selection="Upper/+x", values={"x": q(P, "N")}),
    ]
    weld = Weld(name="End fillet", leg=q(6, "mm"), path="Upper/Foot",
                faces=["Upper/-x", "Lower/+z"])  # fmt: skip
    p, study = _project(solver, parts, features, weld, mesh_mm=3.0)
    light = _run(tmp_path, p, study)
    s = Session(tmp_path / "joint")
    comp = s.project.get(next(iter(s.project.of_type(Component))).id, Component)
    s.submit_batch(modelled_commands(s.project, comp, light.mesh, "End fillet"))
    run = run_study(s.project, study.id, tmp_path / "joint", tmp_path / "w-bead")
    total = run.results.last("weld").values[0]
    f, c, _xyz = _line(run)
    mean = float(np.mean(f.values[:, c["magnitude"]]))
    print(f"{solver} modelled fillet: throat force {total[:3]} N (P {P:g}); mean line "
          f"force {mean / 1e3:.4g} N/mm (P/L {P / length / 1e3:.4g})")  # fmt: skip
    assert total[0] == pytest.approx(P, rel=0.03)  # 98.8 %: the section crosses a notch
    assert abs(total[1]) < 0.01 * P and abs(total[2]) < 0.15 * P
    assert mean == pytest.approx(P / length, rel=0.05)
    from puzzle.weld_generator import line_commands

    comp = s.project.get(comp.id, Component)
    s.submit_batch(line_commands(s.project, comp, "End fillet"))  # back to a light weld
    comp = s.project.get(comp.id, Component)
    assert [p.name for p in comp.parts] == ["Lower", "Upper"]
    assert comp.welds[0].formulation == "line" and not comp.contacts


def _modelled(tmp_path, solver, parts, features, weld, mesh_mm):
    """The joint solved with a light weld, then with it drawn as a modelled bead."""
    from puzzle.commands import Session
    from puzzle.weld_generator import modelled_commands

    p, study = _project(solver, parts, features, weld, mesh_mm=mesh_mm)
    light = _run(tmp_path, p, study)
    s = Session(tmp_path / "joint")
    comp = s.project.get(next(iter(s.project.of_type(Component))).id, Component)
    s.submit_batch(modelled_commands(s.project, comp, light.mesh, weld.name))
    return light, run_study(s.project, study.id, tmp_path / "joint", tmp_path / "w-bead")


@over("calculix")
def test_a_modelled_bead_all_round(tmp_path, solver):
    """The bracket welded all round, its fillet drawn as a closed bead (four straight
    segments, mitred at the corners): its throat sections carry the side load P and the
    moment P h, as the light weld does by statics; within the sections' integration."""
    pytest.importorskip("OCP")
    b, d, h = 0.02, 0.04, 0.06
    foot = ["B/+x|B/-z", "B/-x|B/-z", "B/+y|B/-z", "B/-y|B/-z"]
    parts = {
        "Base": ((0.1, 0.1, 0.02), (0.0, 0.0, -0.02), ()),
        "Block": ((b, d, h), (0.04, 0.03, 0.0),
                  (("Foot", "edge", foot), ("Sides", "face", ["B/+x", "B/-x", "B/+y", "B/-y"]))),
    }  # fmt: skip
    features = [Feature(type="fixed", selection="Base/-z"),
                Feature(type="force", selection="Block/+z", values={"y": q(P, "N")})]  # fmt: skip
    weld = Weld(name="All round", leg=q(6, "mm"), path="Block/Foot",
                faces=["Block/Sides", "Base/+z"])  # fmt: skip
    _light, run = _modelled(tmp_path, solver, parts, features, weld, 3.0)
    g = run.results.last("weld")
    names = {k: i for i, k in enumerate(g.components)}
    total = g.values[0]
    centre = np.array([0.05, 0.05, 0.0])
    force = total[[names[a] for a in "xyz"]]
    moment = total[[names[a] for a in ("mx", "my", "mz")]] - np.cross(centre, force)
    print(f"{solver} modelled all round: shear {force} N (P {P:g}); moment about x "
          f"{moment[0]:.5g} N·m (P h {P * h:.5g})")  # fmt: skip
    assert force[1] == pytest.approx(P, rel=0.05)
    assert abs(moment[0]) == pytest.approx(P * h, rel=0.08)


@over("calculix")
def test_an_intermittent_modelled_bead(tmp_path, solver):
    """The lap joint's end fillet intermittent, 10 mm in every 20 mm, drawn as three
    beads: together they carry P."""
    pytest.importorskip("OCP")
    length = 0.05
    parts = {
        "Lower": ((0.1, length, 0.01), (0.0, 0.0, 0.0), ()),
        "Upper": ((0.1, length, 0.01), (0.06, 0.0, 0.01), (("Foot", "edge", ["B/-x|B/-z"]),)),
    }
    features = [Feature(type="fixed", selection="Lower/-x"),
                Feature(type="force", selection="Upper/+x", values={"x": q(P, "N")})]  # fmt: skip
    weld = Weld(name="Stitch", leg=q(6, "mm"), path="Upper/Foot", faces=["Upper/-x", "Lower/+z"],
                pattern="intermittent", segment=q(10, "mm"), pitch=q(20, "mm"))  # fmt: skip
    _light, run = _modelled(tmp_path, solver, parts, features, weld, 2.0)
    total = run.results.last("weld").values[0]
    print(f"{solver} intermittent bead: {total[:3]} N (P {P:g})")
    assert total[0] == pytest.approx(P, rel=0.05)


@over("calculix", "code_aster")
@pytest.mark.parametrize("depth", [10.0, 5.0], ids=["CJP", "PJP"])
def test_a_modelled_groove_weld(tmp_path, solver, depth):
    """A butt joint of two 10 mm plates 50 mm wide, a groove weld along the top of the
    joint, modelled: the faces tied over the groove's depth (all of it for CJP, the top
    half for PJP), the rest open. The section inside the path's plate carries P, about
    P / L along the weld. The force is the one on B, the pulled plate, from A: -P."""
    pytest.importorskip("OCP")
    length = 0.05
    parts = {
        "A": ((0.05, length, 0.01), (0.0, 0.0, 0.0), (("Top edge", "edge", ["B/+x|B/+z"]),)),
        "B": ((0.05, length, 0.01), (0.05, 0.0, 0.0), ()),
    }
    features = [Feature(type="fixed", selection="A/-x"),
                Feature(type="force", selection="B/+x", values={"x": q(P, "N")})]  # fmt: skip
    weld = Weld(name="Butt", type="groove", groove="cjp" if depth == 10.0 else "pjp",
                leg=q(depth, "mm"), path="A/Top edge", faces=["A/+x", "B/-x"],
                formulation="modelled")  # fmt: skip
    p, study = _project(solver, parts, features, weld, mesh_mm=2.0)
    run = _run(tmp_path, p, study)
    total = run.results.last("weld").values[0]
    f, c, _xyz = _line(run)
    mean = float(np.mean(f.values[:, c["x"]]))
    print(f"{solver} {weld.groove} groove: {total[:3]} N (P {P:g}); mean line force "
          f"{mean / 1e3:.4g} N/mm (P/L {P / length / 1e3:.4g})")  # fmt: skip
    assert total[0] == pytest.approx(-P, rel=0.02)
    assert mean == pytest.approx(-P / length, rel=0.1)
    if depth == 10.0:  # ASME VIII-2 5.5.5 at the toe: the plates pulled straight
        from puzzle.post.weld_fields import structural_stress

        comp = next(o for o in p.objects.values() if isinstance(o, Component))
        out = structural_stress(p, comp, run.mesh, run.results, "Butt", thickness=0.01)
        membrane = np.mean([s["membrane_range_Pa"] for s in out["stations"]])
        bending = np.mean([s["bending_range_Pa"] for s in out["stations"]])
        print(f"groove toe: membrane {membrane / 1e6:.4f} MPa (P/(L t) "
              f"{P / length / 0.01 / 1e6:.4f}), bending {bending / 1e6:.4f} MPa")  # fmt: skip
        assert membrane == pytest.approx(P / length / 0.01, rel=0.05)
        assert abs(bending) < 0.1 * membrane
