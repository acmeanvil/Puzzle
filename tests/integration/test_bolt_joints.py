"""Bolt joint styles (ADR 0050): what a bolt's ends bear on.

Two 10 mm plates, glued at their interface, with a 9 mm clearance hole through both;
an M8 bolt joins them. On the hole walls its ends are at the walls' centres, 10 mm
apart; a bolt and nut bears on the annuli under its head and nut (the plates' outer
faces, out to the M8 bearing diameter of 11.63 mm), 20 mm apart; a cap screw bears on
the head's annulus and the lower plate's thread over 1.5 d from the interface. Tightened
to F = 5 kN, the bolt carries F whatever its ends (the force across its cut, exact).
"""

from __future__ import annotations

import numpy as np
import pytest
from solvers import over

from puzzle import bolts
from puzzle.materials import add_library_material
from puzzle.model.schema import (
    BeamFastener,
    BoltType,
    Component,
    Contact,
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
)
from puzzle.project import save_project

q = lambda v, u="mm": Quantity(value=v, unit=u)  # noqa: E731
F = 5000.0


def _joint(tmp_path, joint, solver="calculix"):
    p = Project(name="joint")
    steel = add_library_material(p, "structural_steel")
    grade = add_library_material(p, "iso_898_1_class_8_8")
    plate = p.add(Geometry(
        name="Plate",
        history=[HistoryStep(kind="box", name="P", args={"dx": 0.04, "dy": 0.04, "dz": 0.01}),
                 HistoryStep(kind="hole", name="H", args={"r": 0.0045, "x": 0.02, "y": 0.02})],
        selections=[NamedSelection(name="Body", entity="body", members=["Body"]),
                    NamedSelection(name="Hole", entity="face", members=["H/wall"]),
                    NamedSelection(name="Top", entity="face", members=["P/+z"]),
                    NamedSelection(name="Bottom", entity="face", members=["P/-z"]),
                    NamedSelection(name="Side", entity="face", members=["P/-x"])],
    ))  # fmt: skip
    btype = p.add(BoltType(name="M8 8.8", size="M8", material=grade.id, preload="force",
                           preload_force=q(F, "N"), joint=joint))  # fmt: skip
    mesh = p.add(MeshRecipe(name="M", global_size=q(2.5)))
    comp = p.add(Component(
        name="Joint",
        parts=[PartInstance(name="Lower", geometry=plate.id),
               PartInstance(name="Upper", geometry=plate.id, translation=(0.0, 0.0, 0.01))],
        mesh=mesh.id,
        materials=[MaterialAssignment(material=steel.id, selection=f"{n}/Body")
                   for n in ("Lower", "Upper")],
        contacts=[Contact(name="Glue", type="bonded", source="Upper/Bottom", target="Lower/Top",
                          meshing="nonconformal")],
        physics=[Physics(type="solid_mechanics",
                         features=[Feature(type="fixed", selection="Lower/Side")],
                         settings={"temperature_dependent_materials": False})],
        connectors=[BeamFastener(name="Bolt", source="Lower/Hole", target="Upper/Hole",
                                 type=btype.id)],
    ))  # fmt: skip
    study = p.add(Study(name="Tighten", component=comp.id, solver=solver,
                        steps=[SolveStep(type="stationary")]))  # fmt: skip
    root = tmp_path / "joint"
    save_project(p, root)
    return p, comp, study, root


def _meshed(tmp_path, joint):
    pytest.importorskip("OCP")
    from puzzle.mesh import generate_mesh

    p, comp, study, root = _joint(tmp_path, joint)
    meshed = generate_mesh(p, comp.id, root, tmp_path / "mesh")
    return p, comp, meshed.mesh


def _ends(p, comp, mesh):
    from puzzle.adapters.common import fastener_end

    written, solved = bolts.with_joints(p, comp.id, mesh)
    (bolt,) = solved.get(comp.id, Component).connectors
    a, b = (fastener_end(s, written, "Bolt") for s in (bolt.source, bolt.target))
    return written, bolt, a, b


def test_hole_walls_are_left_alone(tmp_path):
    p, comp, mesh = _meshed(tmp_path, "hole_walls")
    written, bolt, a, b = _ends(p, comp, mesh)
    assert written is mesh and (bolt.source, bolt.target) == ("Lower/Hole", "Upper/Hole")
    assert np.linalg.norm(b.centre - a.centre) == pytest.approx(0.010, rel=1e-5)


def test_a_bolt_and_nut_bear_on_annuli(tmp_path):
    p, comp, mesh = _meshed(tmp_path, "through_nut")
    written, bolt, a, b = _ends(p, comp, mesh)
    assert bolt.source == "Lower/Hole (under the head)"
    assert bolt.target == "Upper/Hole (under the nut)"
    for end, z in ((a, 0.0), (b, 0.02)):
        xyz = written.node_coords(end.nodes)
        assert np.allclose(xyz[:, 2], z, atol=1e-9)  # the plates' outer faces
        r = np.linalg.norm(xyz[:, :2] - 0.02, axis=1)
        assert r.min() >= 0.0045 - 1e-9 and r.max() <= 0.0125  # out to about 11.63 mm
        assert end.weights.sum() == pytest.approx(1.0)
    assert np.linalg.norm(b.centre - a.centre) == pytest.approx(0.020, rel=1e-3)  # the grip
    assert written.shell_blocks() == mesh.shell_blocks()  # the new faces aren't elements


def test_a_cap_screw_bears_on_the_head_and_the_thread(tmp_path):
    p, comp, mesh = _meshed(tmp_path, "stud_tapped")
    written, bolt, a, b = _ends(p, comp, mesh)
    assert bolt.source == "Lower/Hole (thread)" and bolt.target == "Upper/Hole (under the nut)"
    z = written.node_coords(a.nodes)[:, 2]
    # The thread from the interface (z = 10 mm) down 1.5 d = 12 mm: the whole lower hole.
    assert z.max() == pytest.approx(0.010, abs=1e-9) and z.min() == pytest.approx(0.0, abs=1e-9)


@over("calculix", "code_aster")
@pytest.mark.parametrize("joint", ["hole_walls", "through_nut", "tapped"])
def test_tightened_bolts_carry_their_preload(tmp_path, solver, joint):
    pytest.importorskip("OCP")
    from puzzle.solve import run_study

    p, comp, study, root = _joint(tmp_path, joint, solver)
    run = run_study(p, study.id, root, tmp_path / "w")
    f = run.results.steps[0].fields["fastener"]
    axial = float(f.values[0, list(f.components).index("axial")])
    print(f"{solver} {joint}: axial {axial:.6g} N (preload {F:g})")
    assert axial == pytest.approx(F, rel=1e-3)


def _anchored(tmp_path, solver, load):
    """One 10 mm plate held only by an M8 bolt to ground (an anchor 16 mm below the
    hole's centre), loaded on its top face or its side."""
    p = Project(name="anchor")
    steel = add_library_material(p, "structural_steel")
    grade = add_library_material(p, "iso_898_1_class_8_8")
    plate = p.add(Geometry(
        name="Plate",
        history=[HistoryStep(kind="box", name="P", args={"dx": 0.04, "dy": 0.04, "dz": 0.01}),
                 HistoryStep(kind="hole", name="H", args={"r": 0.0045, "x": 0.02, "y": 0.02})],
        selections=[NamedSelection(name="Body", entity="body", members=["Body"]),
                    NamedSelection(name="Hole", entity="face", members=["H/wall"]),
                    NamedSelection(name="Top", entity="face", members=["P/+z"]),
                    NamedSelection(name="Side", entity="face", members=["P/+x"])],
    ))  # fmt: skip
    btype = p.add(BoltType(name="M8 8.8", size="M8", material=grade.id, preload="none"))
    mesh = p.add(MeshRecipe(name="M", global_size=q(2.5)))
    selection, values = load
    comp = p.add(Component(
        name="Anchored",
        parts=[PartInstance(name="Base", geometry=plate.id)],
        mesh=mesh.id,
        materials=[MaterialAssignment(material=steel.id, selection="Base/Body")],
        physics=[Physics(type="solid_mechanics",
                         features=[Feature(type="force", selection=selection, values=values)],
                         settings={"temperature_dependent_materials": False})],
        connectors=[BeamFastener(name="Anchor", source="Base/Hole", ground=True,
                                 ground_length=q(16), type=btype.id)],
    ))  # fmt: skip
    study = p.add(Study(name="Pull", component=comp.id, solver=solver,
                        steps=[SolveStep(type="stationary")]))  # fmt: skip
    root = tmp_path / "anchor"
    save_project(p, root)
    return p, comp, study, root


def test_a_bolt_to_ground_ends_below_its_hole(tmp_path):
    pytest.importorskip("OCP")
    from puzzle.adapters.common import fastener_ends
    from puzzle.mesh import generate_mesh

    p, comp, _study, root = _anchored(tmp_path, "calculix", ("Base/Top", {"z": q(1, "N")}))
    mesh = generate_mesh(p, comp.id, root, tmp_path / "mesh").mesh
    (bolt,) = bolts.resolved(p).get(comp.id, Component).connectors
    a, b = fastener_ends(bolt, mesh)
    # (the mesh's hole wall puts its centre a few µm off the true axis)
    assert a.centre == pytest.approx([0.02, 0.02, 0.005], abs=1e-5)
    assert b.centre == pytest.approx([0.02, 0.02, 0.005 - 0.016], abs=1e-5)  # down, 16 mm
    assert len(b.nodes) == 0


@over("calculix", "code_aster")
@pytest.mark.parametrize("pull", ["up", "sideways"])
def test_an_anchored_plate_by_statics(tmp_path, solver, pull):
    """Pulled up by P, the anchor carries P in tension; pushed sideways by P on its side
    (whose centre is level with the hole's), it carries P in shear, with no moment at
    the plate and P × 16 mm at the ground."""
    pytest.importorskip("OCP")
    from puzzle.adapters.common import fastener_ends
    from puzzle.solve import run_study

    P = 1000.0
    load = ("Base/Top", {"z": q(P, "N")}) if pull == "up" else ("Base/Side", {"x": q(P, "N")})
    p, comp, study, root = _anchored(tmp_path, solver, load)
    run = run_study(p, study.id, root, tmp_path / "w")
    f = run.results.steps[0].fields["fastener"]
    c = {k: i for i, k in enumerate(f.components)}
    axial, shear = float(f.values[0, c["axial"]]), float(f.values[0, c["shear"]])
    print(f"{solver} {pull}: axial {axial:.6g} N, shear {shear:.6g} N")
    if pull == "up":
        assert axial == pytest.approx(P, rel=1e-4) and shear < 1e-3 * P
        return
    assert shear == pytest.approx(P, rel=1e-4) and abs(axial) < 1e-3 * P
    (bolt,) = bolts.resolved(p).get(comp.id, Component).connectors
    a, b = (e.centre for e in fastener_ends(bolt, run.mesh))
    loads = bolts.along(f.values[0], a, b)
    print(f"{solver} sideways bending {loads.bending[0]:.4g} -> {loads.bending[-1]:.4g} N·m")
    assert loads.bending[0] == pytest.approx(0.0, abs=1e-3 * P * 0.016)
    assert loads.bending[-1] == pytest.approx(P * 0.016, rel=1e-3)


@pytest.mark.parametrize(("joint", "drawn"), [("hole_walls", ["head", "nut"]),
                                             ("through_nut", ["head", "nut"]),
                                             ("tapped", ["head"]),
                                             ("stud_tapped", ["nut"])])  # fmt: skip
def test_heads_and_nuts_are_drawn_on_the_outer_faces(tmp_path, joint, drawn):
    """Drawing only (ADR 0050): the bolt in the bolts' colour, a hex head under the
    first plate (z = 0, below) and a nut on the second (z = 20 mm, above), none where
    the joint has a thread."""
    from puzzle.post.symbols import BOLT, _fastener_symbols

    p, comp, mesh = _meshed(tmp_path, joint)
    written, solved = bolts.with_joints(p, comp.id, mesh)
    glyphs = _fastener_symbols(solved.get(comp.id, Component), written, solved)
    assert {g.colour for g in glyphs} == {BOLT}
    parts = {g.feature.split()[-1]: g for g in glyphs if g.kind == "hex"}
    assert sorted(parts) == sorted(drawn)
    if "head" in parts:  # from the lower plate's bottom face, 0.7 d = 5.6 mm down
        assert parts["head"].points[:, 2] == pytest.approx([0.0, -0.0056], abs=1e-6)
    if "nut" in parts:  # from the upper plate's top face, 0.85 d = 6.8 mm up
        assert parts["nut"].points[:, 2] == pytest.approx([0.02, 0.0268], abs=1e-6)


@over("calculix", "code_aster")
def test_the_generator_imprints_bearing_rings(tmp_path, solver):
    """`bearing_faces`: the generator imprints each head's and nut's ring in the plate
    (one imprint for the shared plate), names it, and the bolt bears on the exact ring:
    its nodes lie between the hole and the M8 bearing radius, the ring's area is the
    annulus's, and the 5 kN preload is carried."""
    pytest.importorskip("OCP")
    import math

    from puzzle import bolt_generator as gen
    from puzzle.adapters.common import fastener_ends, surface_area
    from puzzle.commands import Session
    from puzzle.mesh import generate_mesh
    from puzzle.model.schema import BoltRule
    from puzzle.project import load_project
    from puzzle.solve import run_study

    _p, _comp, _study, root = _joint(tmp_path, "through_nut", solver)
    p = load_project(root)
    comp = p.of_type(Component)[0]
    comp.connectors = []
    save_project(p, root)
    s = Session(root)
    comp = s.project.of_type(Component)[0]
    rule = BoltRule(name="Ringed", bearing_faces=True)
    planned = gen.plan(s.project, comp, gen.by_rule(s.project, comp, root, rule), rule)
    s.submit_batch(gen.commands(s.project, comp, planned, rule=rule))
    p = s.project
    plate = p.of_type(Geometry)[0]
    assert [h.kind for h in plate.history].count("imprint") == 1  # the plate is shared
    (bolt,) = p.of_type(Component)[0].connectors
    assert bolt.bearing == ["Lower/Bearing 1", "Upper/Bearing 1"]
    mesh = generate_mesh(p, comp.id, root, tmp_path / "mesh").mesh
    written, solved = bolts.with_joints(p, comp.id, mesh)
    a, b = fastener_ends(solved.get(comp.id, Component).connectors[0], written)
    dw = bolts.size("iso_coarse", "M8").bearing
    for end, z in ((a, 0.0), (b, 0.02)):
        xyz = written.node_coords(end.nodes)
        r = np.linalg.norm(xyz[:, :2] - 0.02, axis=1)
        assert np.allclose(xyz[:, 2], z, atol=1e-9)
        assert r.min() == pytest.approx(0.0045, abs=1e-6) and r.max() == pytest.approx(
            dw / 2, abs=1e-6)  # fmt: skip
    ring = surface_area(written.groups["Lower/Hole (under the head)"], written)
    assert ring == pytest.approx(math.pi * ((dw / 2) ** 2 - 0.0045**2), rel=2e-3)
    run = run_study(p, s.project.of_type(Study)[0].id, root, tmp_path / "w")
    f = run.results.steps[0].fields["fastener"]
    axial = float(f.values[0, list(f.components).index("axial")])
    print(f"{solver} ringed: axial {axial:.6g} N")
    assert axial == pytest.approx(F, rel=1e-3)


def test_the_bolt_colour_is_a_preference(tmp_path):
    """Settings > Geometry: Bolt colour paints every bolt's shank, head and nut; Bolt
    opacity (50 % by default) and Bolt edges (outlined by default) draw them."""
    from puzzle import settings
    from puzzle.post.symbols import _fastener_symbols

    p, comp, mesh = _meshed(tmp_path, "through_nut")
    written, solved = bolts.with_joints(p, comp.id, mesh)
    drawn = _fastener_symbols(solved.get(comp.id, Component), written, solved)
    assert {(g.opacity, g.edges) for g in drawn} == {(0.5, True)}  # half see-through, outlined
    settings.current().set("geometry.bolt_colour", "#123456")
    settings.current().set("geometry.bolt_opacity", 1.0)
    settings.current().set("geometry.bolt_edges", False)
    glyphs = _fastener_symbols(solved.get(comp.id, Component), written, solved)
    assert len(glyphs) == 3 and {g.colour for g in glyphs} == {"#123456"}
    assert {(g.opacity, g.edges) for g in glyphs} == {(1.0, False)}
    pref = next(x for x in settings.PREFERENCES if x.key == "geometry.bolt_colour")
    assert (pref.group, pref.kind) == ("Geometry", "colour")
