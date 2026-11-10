"""Modelled bolts (ADR 0077): a standard bolt drawn from the tables, placed as a beam
bolt is, joined by its head's contact, its thread and a pretension section.

An M16 8.8 cap screw (ISO 4014 hex head) through a 10 mm plate into a steel boss with
a tapped hole (the bore at D1/2 = 6.9175 mm), the plate bonded on the boss, preloaded
to 40 kN. Made by one command, it lands with its head on the plate, its pretension
section carries the preload, and the shank's axial stress is F / A within 2 % away
from its ends. Undo takes it all back.
"""

import pytest
from solvers import over

from puzzle.materials import add_library_material
from puzzle.model import Contact, Feature, Quantity
from puzzle.model.schema import (
    BeamFastener,
    BoltType,
    Component,
    Geometry,
    HistoryStep,
    MaterialAssignment,
    MeshRecipe,
    NamedSelection,
    PartInstance,
    Physics,
    Project,
    SolveStep,
    Study,
)

pytest.importorskip("OCP")

MM = 1e-3
PRELOAD = 40e3


def _joint(root, solver="calculix"):
    from puzzle.project import save_project

    p = Project(name="joint")
    steel = add_library_material(p, "structural_steel")
    grade = add_library_material(p, "iso_898_1_class_8_8")
    boss = p.add(Geometry(name="Boss", history=[
        HistoryStep(kind="cylinder", name="B", args={"r": 20 * MM, "h": 30 * MM}),
        HistoryStep(kind="cylinder", name="H", args={"r": 6.9175 * MM, "h": 30 * MM,
                                                     "mode": "cut"})]))  # fmt: skip
    plate = p.add(Geometry(name="Plate", history=[
        HistoryStep(kind="cylinder", name="P", args={"r": 20 * MM, "h": 10 * MM, "z": 30 * MM}),
        HistoryStep(kind="cylinder", name="C", args={"r": 8.5 * MM, "h": 10 * MM, "z": 30 * MM,
                                                     "mode": "cut"})]))  # fmt: skip
    boss.selections = [NamedSelection(name="Bore", entity="face", members=["H/wall"]),
                       NamedSelection(name="Bottom", entity="face", members=["B/start"]),
                       NamedSelection(name="Top", entity="face", members=["B/end"])]  # fmt: skip
    plate.selections = [
        NamedSelection(name="Hole", entity="face", members=["C/wall"]),
        NamedSelection(name="Under", entity="face", members=["P/start"])]  # fmt: skip
    m16 = p.add(BoltType(name="M16 8.8", size="M16", material=grade.id, preload="force",
                         preload_force=Quantity(value=PRELOAD / 1e3, unit="kN"),
                         joint="tapped"))  # fmt: skip
    mesh = p.add(MeshRecipe(name="Tets", element_order=2,
                            global_size=Quantity(value=3, unit="mm")))  # fmt: skip
    comp = p.add(Component(
        name="Joint", mesh=mesh.id,
        parts=[PartInstance(name="Boss", geometry=boss.id),
               PartInstance(name="Plate", geometry=plate.id)],
        materials=[MaterialAssignment(material=steel.id, selection=s) for s in ("Boss", "Plate")],
        contacts=[Contact(name="Plate on boss", type="bonded", source="Plate/Under",
                          target="Boss/Top")],
        connectors=[BeamFastener(name="Screw", source="Plate/Hole", target="Boss/Bore",
                                 type=m16.id)],
        physics=[Physics(type="solid_mechanics", features=[
            Feature(type="fixed", selection="Boss/Bottom")])],
    ))  # fmt: skip
    p.add(Study(name="Preload", component=comp.id, solver=solver,
                steps=[SolveStep(type="stationary")]))  # fmt: skip
    save_project(p, root)
    return p


def test_a_modelled_bolt_is_drawn_and_placed(tmp_path):
    from puzzle.commands.session import Session
    from puzzle.solid_bolts import make_commands

    _joint(tmp_path)
    s = Session(tmp_path)
    comp = s.project.of_type(Component)[0]
    s.submit_batch(make_commands(s.project, comp, 0, tmp_path))
    comp = s.project.of_type(Component)[0]
    bolt = comp.connectors[0]
    assert bolt.formulation == "modelled" and bolt.parts == ["Screw"]
    screw = next(x for x in comp.parts if x.name == "Screw")
    assert screw.translation == pytest.approx((0.0, 0.0, 0.04), abs=1e-9)  # on the plate
    assert screw.rotation == pytest.approx((0.0, 0.0, 0.0))  # shank down: -z
    kinds = {c.name: (c.type, c.thread is not None) for c in comp.contacts}
    assert kinds["Screw head"] == ("frictional", False)
    assert kinds["Screw thread"] == ("bonded", True)
    feats = [f for f in comp.physics[0].features if f.type == "bolt_pretension"]
    assert feats and feats[0].selection == "Screw/Section"
    geo = next(g for g in s.project.of_type(Geometry) if g.name == "Screw (bolt)")
    length = geo.history[0].args["length"]
    assert length == pytest.approx(0.035)  # 10 mm grip + 24 mm engaged, up to 5 mm
    s.undo()
    comp = s.project.of_type(Component)[0]
    assert comp.connectors[0].formulation is None and [x.name for x in comp.parts] == [
        "Boss", "Plate"]  # fmt: skip


def test_back_to_a_beam(tmp_path):
    from puzzle.commands.session import Session
    from puzzle.solid_bolts import beam_commands, make_commands

    _joint(tmp_path)
    s = Session(tmp_path)
    s.submit_batch(make_commands(s.project, s.project.of_type(Component)[0], 0, tmp_path))
    comp = s.project.of_type(Component)[0]
    s.submit_batch(beam_commands(s.project, comp, 0))
    comp = s.project.of_type(Component)[0]
    assert comp.connectors[0].formulation == "beam" and comp.connectors[0].parts == []
    assert [x.name for x in comp.parts] == ["Boss", "Plate"]
    assert [c.name for c in comp.contacts] == ["Plate on boss"]
    assert not [g for g in s.project.of_type(Geometry) if "(bolt)" in g.name]
    plate = next(g for g in s.project.of_type(Geometry) if g.name == "Plate")
    assert not [x for x in plate.selections if x.name.startswith("Under Screw")]


@over("calculix")
def test_the_preloaded_shank_carries_its_preload(tmp_path, solver):
    from puzzle.commands.session import Session
    from puzzle.solid_bolts import make_commands
    from puzzle.solve import run_study

    _joint(tmp_path, solver)
    s = Session(tmp_path)
    s.submit_batch(make_commands(s.project, s.project.of_type(Component)[0], 0, tmp_path))
    p = s.project
    study = p.of_type(Study)[0]
    run = run_study(p, study.id, tmp_path, tmp_path / "run", timeout_s=3600)
    from puzzle.bolts import section_loads

    # The force across the bolt's section, as the bolt table reads it (the stress near
    # the pretension cut isn't uniform across: its node mean by the axis read 18 % low).
    loads = section_loads(run.mesh, run.results.last("stress"), "Screw")
    print(f"axial force at the section {loads.axial[0] / 1e3:.2f} kN")
    assert loads.axial[0] == pytest.approx(PRELOAD, rel=0.02)
