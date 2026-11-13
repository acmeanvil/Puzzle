"""Contacts between the bodies of one part (ADR 0039): an imported STEP assembly is one
part holding every body, so the search and the mesher work body by body too.

A 100 × 100 × 10 mm plate carries two 20 mm cubes side by side, touching each other,
all three bodies of one STEP file. The search finds the three touching pairs, named by
the bodies' selections. Meshed with Share faces, touching bodies share their nodes;
a frictional contact between the first cube and the plate meshes those two apart while
the other two interfaces stay shared (the split is made after meshing, Gmsh's Crack
plugin, so the line where cube B meets both stays joined); with Share faces off, a
bonded contact joins.
"""

import numpy as np
import pytest

from puzzle.model.schema import (
    Component,
    Contact,
    Geometry,
    MeshRecipe,
    NamedSelection,
    PartInstance,
    Project,
    Quantity,
)

pytest.importorskip("OCP")

from puzzle.geometry.contacts import (  # noqa: E402
    body_labels,  # noqa: E402
    contact_commands,
    find_contacts,
    proposals,
)


def _write_step(path):
    """Plate, then cube A at x 20..40 mm, cube B at x 40..60 mm, both on the plate (mm)."""
    from OCP.BRep import BRep_Builder
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
    from OCP.TopoDS import TopoDS_Compound

    from puzzle.geometry.prep import _set_unit

    builder, compound = BRep_Builder(), TopoDS_Compound()
    builder.MakeCompound(compound)
    for corner, size in (((0, 0, 0), (100, 100, 10)), ((20, 40, 10), (20, 20, 20)),
                         ((40, 40, 10), (20, 20, 20))):  # fmt: skip
        builder.Add(compound, BRepPrimAPI_MakeBox(gp_Pnt(*corner), *size).Shape())
    writer = STEPControl_Writer()
    _set_unit("MM")
    writer.Transfer(compound, STEPControl_AsIs)
    writer.Write(str(path))


def _project(root, share=True):
    root.mkdir(parents=True, exist_ok=True)
    _write_step(root / "stack.step")
    p = Project(name="stack")
    geo = p.add(Geometry(name="Stack", source="stack.step", source_unit="mm", share_faces=share))
    geo.selections += [
        NamedSelection(name="Plate", entity="body", members=["body-1"]),
        NamedSelection(name="Cube A", entity="body", members=["body-2"]),
        NamedSelection(name="Cube B", entity="body", members=["body-3"]),
    ]
    mesh = p.add(MeshRecipe(name="Tets", global_size=Quantity(value=8, unit="mm"), element_order=1))
    comp = p.add(Component(name="Stack", mesh=mesh.id,
                           parts=[PartInstance(name="Stack", geometry=geo.id)]))  # fmt: skip
    return p, comp


def _found(p, comp, root):
    return {frozenset(x.name.split(" to ")): x for x in proposals(
        find_contacts(p, comp, root), body_labels(p, comp))}  # fmt: skip


def test_the_search_finds_bodies_of_one_part(tmp_path):
    p, comp = _project(tmp_path)
    found = _found(p, comp, tmp_path)
    assert set(found) == {frozenset({"Plate", "Cube A"}), frozenset({"Plate", "Cube B"}),
                          frozenset({"Cube A", "Cube B"})}  # fmt: skip
    base = found[frozenset({"Plate", "Cube A"})]
    assert base.within_part and base.source_part == base.target_part == "Stack"
    assert base.area == pytest.approx(4e-4, rel=1e-6)
    assert found[frozenset({"Cube A", "Cube B"})].area == pytest.approx(4e-4, rel=1e-6)


def test_accepting_names_the_bodies(tmp_path):
    p, comp = _project(tmp_path)
    prop = _found(p, comp, tmp_path)[frozenset({"Plate", "Cube A"})]
    commands = contact_commands(p, comp, [prop])
    contacts = commands[-1].ops[0].value
    sides = {contacts["source"], contacts["target"]}
    assert sides == {"Stack/Touches Cube A", "Stack/Touches Plate"}
    assert contacts["name"] in ("Plate to Cube A", "Cube A to Plate")


def _made(p, comp, root, *pairs, types=None):
    """The search's contacts for the given body pairs, made as the dialog makes them
    (`contact_commands`, applied here by hand); returns each pair's contact."""
    from puzzle.model.schema import Contact as C

    found = _found(p, comp, root)
    chosen = [found[frozenset(pair)] for pair in pairs]
    for prop, kind in zip(chosen, types or ["bonded"] * len(chosen), strict=True):
        prop.type, prop.friction = kind, 0.2 if kind == "frictional" else None
    geo = p.of_type(Geometry)[0]
    for command in contact_commands(p, comp, chosen):
        for op in command.ops:
            if op.path == "selections":
                geo.selections.append(NamedSelection(**op.value))
            else:
                comp.contacts.append(C(**op.value))
    return {frozenset(pair): c for pair, c in zip(pairs, comp.contacts[-len(pairs) :], strict=True)}


def _nodes(p, comp, root, contact, where=False):
    """The node ids on each side of a contact (and, with `where`, every node's place)."""
    from puzzle.mesh.generate import generate_mesh

    mesh = generate_mesh(p, comp.id, root, root / "m").mesh
    sides = (contact.source, contact.target)
    out = [set(np.asarray(mesh.groups[s].node_ids).tolist()) for s in sides]
    if where:
        out.append(dict(zip(np.asarray(mesh.node_ids).tolist(), mesh.coords, strict=True)))
    return out


def test_share_faces_joins_touching_bodies_and_a_sliding_contact_parts_two(tmp_path):
    p, comp = _project(tmp_path)
    made = _made(p, comp, tmp_path, ("Plate", "Cube A"), ("Plate", "Cube B"),
                 ("Cube A", "Cube B"), types=["frictional", "bonded", "bonded"])  # fmt: skip
    slide = made[frozenset({"Plate", "Cube A"})]
    a, b, at = _nodes(p, comp, tmp_path, slide, where=True)
    # Apart: the contact's two sides have their own nodes, but for the line where cube B
    # meets both (x = 40 mm), which holds the three bodies together.
    shared = a & b
    assert a and b and len(shared) < 0.2 * len(a)
    assert all(abs(at[n][0] - 0.04) < 1e-9 and abs(at[n][2] - 0.01) < 1e-9 for n in shared)
    for pair in ({"Plate", "Cube B"}, {"Cube A", "Cube B"}):  # the others stay joined
        a, b = _nodes(p, comp, tmp_path, made[frozenset(pair)])
        small, large = sorted((a, b), key=len)
        assert small and small <= large
    comp.contacts.clear()  # no contacts: every touching pair shares its nodes
    a, b = _nodes(p, comp, tmp_path, slide)
    small, large = sorted((a, b), key=len)
    assert small and small <= large


def test_without_share_faces_a_bond_joins_two_bodies(tmp_path):
    p, comp = _project(tmp_path, share=False)
    (bond,) = _made(p, comp, tmp_path, ("Plate", "Cube A")).values()
    a, b = _nodes(p, comp, tmp_path, bond)
    small, large = sorted((a, b), key=len)
    assert small and small <= large
    comp.contacts[0] = bond.model_copy(update={"type": "frictionless"})
    a, b = _nodes(p, comp, tmp_path, comp.contacts[0])
    assert a and b and not (a & b)  # each body meshed apart


def _write_pin(path):
    """A 40 mm block with a 10 mm bore and a pin filling it, 30 mm long (mm)."""
    from OCP.BRep import BRep_Builder
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
    from OCP.TopoDS import TopoDS_Compound

    from puzzle.geometry.prep import _set_unit

    pin = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(20, 20, -5), gp_Dir(0, 0, 1)), 5, 30).Shape()
    block = BRepAlgoAPI_Cut(BRepPrimAPI_MakeBox(40, 40, 20).Shape(), pin).Shape()
    builder, compound = BRep_Builder(), TopoDS_Compound()
    builder.MakeCompound(compound)
    builder.Add(compound, block)
    builder.Add(compound, pin)
    writer = STEPControl_Writer()
    _set_unit("MM")
    writer.Transfer(compound, STEPControl_AsIs)
    writer.Write(str(path))


def test_a_pin_slides_in_its_bore_on_second_order_tets(tmp_path):
    """A curved interface, quadratic elements: the bore and the pin get their own nodes
    everywhere (no third body meets them), and the mesh's faces follow its volumes."""
    from puzzle.mesh.generate import generate_mesh

    _write_pin(tmp_path / "pin.step")
    p = Project(name="pin")
    geo = p.add(Geometry(name="Pin", source="pin.step", source_unit="mm"))
    geo.selections += [NamedSelection(name="Block", entity="body", members=["body-1"]),
                       NamedSelection(name="Pin", entity="body", members=["body-2"])]  # fmt: skip
    mesh = p.add(MeshRecipe(name="Tets", global_size=Quantity(value=5, unit="mm")))
    comp = p.add(Component(name="Pin", mesh=mesh.id,
                           parts=[PartInstance(name="Pin", geometry=geo.id)]))  # fmt: skip
    (prop,) = proposals(find_contacts(p, comp, tmp_path), body_labels(p, comp))
    assert {*prop.labels} == {"Block", "Pin"}
    assert prop.area == pytest.approx(np.pi * 0.01 * 0.02, rel=1e-3)  # the bore's wall
    prop.type, prop.friction = "frictional", 0.1
    for command in contact_commands(p, comp, [prop]):
        for op in command.ops:
            if op.path == "selections":
                geo.selections.append(NamedSelection(**op.value))
            else:
                comp.contacts.append(Contact(**op.value))
    result = generate_mesh(p, comp.id, tmp_path, tmp_path / "m")
    (c,) = comp.contacts
    a, b = (set(np.asarray(result.mesh.groups[s].node_ids).tolist()) for s in (c.source, c.target))
    assert a and b and not (a & b)
    # Every node of a face element is a node of a volume element.
    used = set()
    faces = set()
    for block in result.mesh.blocks:
        ids = set(np.asarray(block.connectivity).ravel().tolist())
        (used if block.dim == 3 else faces).update(ids)
    assert faces <= used


def test_three_parts_bonded_two_ways_and_sliding_the_third(tmp_path):
    """Plate and two cubes as three parts: bonds plate–B and A–B fragment all three
    together, as before, and the frictional plate–A is parted after meshing."""
    from puzzle.mesh.generate import generate_mesh

    p = Project(name="three")
    from puzzle.model.schema import HistoryStep

    boxes = {"Plate": ((0, 0, 0), (0.1, 0.1, 0.01)), "A": ((0.02, 0.04, 0.01), (0.02, 0.02, 0.02)),
             "B": ((0.04, 0.04, 0.01), (0.02, 0.02, 0.02))}  # fmt: skip
    parts = []
    for name, ((x, y, z), (dx, dy, dz)) in boxes.items():
        g = p.add(Geometry(name=name, history=[HistoryStep(kind="box", name=name[0], args={
            "dx": dx, "dy": dy, "dz": dz, "x0": x, "y0": y, "z0": z})]))  # fmt: skip
        parts.append(PartInstance(name=name, geometry=g.id))
    mesh = p.add(MeshRecipe(name="Tets", global_size=Quantity(value=8, unit="mm"), element_order=1))
    comp = p.add(Component(name="Three", mesh=mesh.id, parts=parts))
    found = {frozenset((x.source_part, x.target_part)): x
             for x in proposals(find_contacts(p, comp, tmp_path))}  # fmt: skip
    chosen = []
    for pair, kind in (({"Plate", "A"}, "frictional"), ({"Plate", "B"}, "bonded"),
                       ({"A", "B"}, "bonded")):  # fmt: skip
        prop = found[frozenset(pair)]
        prop.type, prop.friction = kind, 0.2 if kind == "frictional" else None
        chosen.append(prop)
    geos = {g.id: g for g in p.of_type(Geometry)}
    for command in contact_commands(p, comp, chosen):
        for op in command.ops:
            if op.path == "selections":
                geos[__import__("uuid").UUID(command.object)].selections.append(
                    NamedSelection(**op.value)
                )
            else:
                comp.contacts.append(Contact(**op.value))
    result = generate_mesh(p, comp.id, tmp_path, tmp_path / "m")
    nodes = {c.name: [set(np.asarray(result.mesh.groups[s].node_ids).tolist())
                      for s in (c.source, c.target)] for c in comp.contacts}  # fmt: skip
    at = dict(zip(np.asarray(result.mesh.node_ids).tolist(), result.mesh.coords, strict=True))
    slide = next(c for c in comp.contacts if c.type == "frictional")
    a, b = nodes[slide.name]
    assert a and b and all(abs(at[n][0] - 0.04) < 1e-9 for n in a & b)  # B's line only
    for c in comp.contacts:
        if c.type == "bonded":
            x, y = nodes[c.name]
            small, large = sorted((x, y), key=len)
            assert small and small <= large
