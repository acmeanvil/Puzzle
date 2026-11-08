"""The tree and the schematic are equal ways to build a project (ADR 0071 decision 8):
whatever the tree adds the schematic adds, and every reference is a wire that can be
drawn and removed, by people and by agents (MCP) alike."""

from __future__ import annotations

import asyncio
import os
import typing
import uuid

import pytest
from pydantic import BaseModel

from puzzle.model import schema as S
from puzzle.schematic import links

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _project():
    from puzzle.model.schema import (
        Component,
        CoordinateSystem,
        Feature,
        Geometry,
        Material,
        MeshRecipe,
        PartInstance,
        Physics,
        Project,
        Quantity,
        ResultProbe,
        Study,
    )

    p = Project(name="p")
    from puzzle.model.schema import NamedSelection

    geo = p.add(Geometry(name="Bracket", source="bracket.step", selections=[
        NamedSelection(name="Inner", entity="face", members=["face-1"]),
        NamedSelection(name="Outer", entity="face", members=["face-2"])]))  # fmt: skip
    steel = p.add(Material(name="Steel"))
    mesh = p.add(MeshRecipe(name="Fine", global_size=Quantity(value=2, unit="mm")))
    axes = p.add(CoordinateSystem(name="Pipe axis", type="cylindrical"))
    load = Feature(type="pressure", selection="Bracket/Inner",
                   values={"p": Quantity(value=1, unit="MPa")})  # fmt: skip
    coarse = p.add(MeshRecipe(name="Coarse", global_size=Quantity(value=5, unit="mm")))
    comp = p.add(Component(name="Assembly", parts=[PartInstance(name="Bracket", geometry=geo.id)],
                           mesh=coarse.id,
                           physics=[Physics(type="solid_mechanics", features=[load])]))  # fmt: skip
    study = p.add(Study(name="Static", component=comp.id, solver="calculix"))
    other = p.add(Study(name="Other", component=comp.id, solver="calculix"))
    probe = p.add(ResultProbe(name="Tip", type="point", source=study.id, point=(0.1, 0, 0)))
    return p, dict(geo=geo, steel=steel, mesh=mesh, axes=axes, comp=comp, study=study,
                   other=other, probe=probe)  # fmt: skip


def _apply(p, cmds):
    from puzzle.commands.model import parse_command
    from puzzle.commands.session import _execute

    for c in cmds:
        _execute(p, parse_command(c))


def test_wires_set_what_the_trees_properties_set():
    p, o = _project()
    # a material onto a component without one: the part's material, a new assignment
    found = links.choices(p, o["steel"], o["comp"])
    assert [c.label for c in found] == ["Material of Bracket (new)"]
    _apply(p, links.commands(found[0], o["steel"], o["comp"]))
    assert p.objects[o["comp"].id].materials[0].material == o["steel"].id
    # a mesh recipe onto a component, a coordinate system onto its load
    _apply(p, links.commands(links.choices(p, o["mesh"], o["comp"])[0], o["mesh"], o["comp"]))
    assert p.objects[o["comp"].id].mesh == o["mesh"].id
    axes = links.choices(p, o["axes"], p.objects[o["comp"].id])
    labels = [c.label for c in axes]
    assert "Coordinate system of Pressure on Bracket/Inner" in labels
    assert "Material directions of Bracket/Body" not in labels  # no such assignment
    pick = next(c for c in axes if c.label.startswith("Coordinate system of Pressure"))
    _apply(p, links.commands(pick, o["axes"], p.objects[o["comp"].id]))
    assert p.objects[o["comp"].id].physics[0].features[0].axes == o["axes"].id
    # a study onto a result tool: what it reads
    pick = links.choices(p, o["other"], o["probe"])[0]
    _apply(p, links.commands(pick, o["other"], o["probe"]))
    assert p.objects[o["probe"].id].source == o["other"].id
    # a geometry onto a component: a new part first
    assert links.choices(p, o["geo"], o["comp"])[0].op == "add_part"


def test_a_wire_that_fits_nowhere_says_what_the_target_takes():
    p, o = _project()
    assert links.choices(p, o["mesh"], o["study"]) == []
    expected = "Static takes Component, Operator, Result tool, not Mesh recipe"
    assert links.why_not(p, o["mesh"], o["study"]) == expected


def test_removing_a_wire_clears_it_and_a_required_one_is_refused():
    p, o = _project()
    comp = p.objects[o["comp"].id]
    pick = next(c for c in links.choices(p, o["axes"], comp) if "Pressure" in c.label)
    _apply(p, links.commands(pick, o["axes"], comp))
    comp = p.objects[o["comp"].id]
    made = links.existing(p, o["axes"].id, comp)
    _apply(p, links.clear_commands(made[0], comp))
    assert p.objects[o["comp"].id].physics[0].features[0].axes is None
    for source, target in ((o["comp"], o["study"]), (o["geo"], comp)):  # can't be without
        required = links.existing(p, source.id, p.objects[target.id])
        assert required and required[0].link.required
    mesh = links.existing(p, comp.mesh, p.objects[o["comp"].id])
    assert mesh and links.clear_commands(mesh[0], comp) == []  # replaced, never cleared


# Reference fields not drawn as these links, and why: each is wired another way.
WIRED_ELSEWHERE = {
    "operator.inputs{}.source": "an operator's input sockets",
    "transfer.source": "a wire from one study to another", "transfer.target": "the same",
    "optimisation.objective.source": "the optimisation's Objective socket",
    "optimisation.constraints[].source": "its Constraint sockets",
    "mesh_study.quantities[].source": "the mesh study's sockets",
    "component.physics[].features[].fields{}.field": "the feature's field sockets (ADR 0044)",
    "component.physics[].features[].fields{}.placement.axes": "set in the field's use form",
    "component.shell_sections[].fields{}.field": "the shell section's field sockets",
    "component.materials[].fields{}.field": "a graded material's field sockets (ADR 0044)",
    "component.materials[].fields{}.placement.axes": "set in the field's use form",
    "component.shell_sections[].fields{}.placement.axes": "set in the field's use form",
    "field.placement.axes": "set in the field wizard's placement",
    "axes.component": "the coordinate system panel's Attached to",
    "axes.geometry": "the same", "geometry.owner": "a component's local geometry",
    "cut.construction": "saved section cuts aren't schematic nodes",
    "study.steps[].parameters[].source": "the study's sweep sockets (ADR 0062)",
    "study.steps[].parameters[].materials[]": "the study's Materials… dialog (ADR 0018)",
    "result.fatigue.blocks[].combination": "the fatigue form",
    "result.fatigue.blocks[].case.study": "the fatigue form",
}  # fmt: skip


def _reference_paths() -> set[str]:
    found: set[str] = set()

    def walk(cls, prefix, seen):
        for name, f in cls.model_fields.items():
            if name == "id":
                continue

            def go(a, p):
                o, args = typing.get_origin(a), typing.get_args(a)
                if a is uuid.UUID:
                    found.add(p)
                elif o in (list, tuple) and args:
                    go(args[0], p + "[]")
                elif o is dict and len(args) == 2:
                    go(args[1], p + "{}")
                elif args and o is not None:
                    for x in args:
                        if x is not type(None):
                            go(x, p)
                elif isinstance(a, type) and issubclass(a, BaseModel) and a not in seen:
                    walk(a, p + ".", seen | {a})

            go(f.annotation, prefix + name)

    for c in vars(S).values():
        if isinstance(c, type) and issubclass(c, S.ProjectObject) and c is not S.ProjectObject:
            kind = getattr(c, "kind", None)
            if isinstance(kind, str):
                walk(c, kind + ".", frozenset({c}))
    return found


def test_every_reference_in_the_model_can_be_wired():
    drawable = {f"{link.target}.{link.path}" for link in links.LINKS}
    missing = sorted(_reference_paths() - drawable - set(WIRED_ELSEWHERE))
    assert not missing, f"references the schematic can't wire: {missing}"


def test_the_new_menu_holds_what_the_tree_adds(tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QMenu

    from puzzle.gui.menus import build_menu
    from puzzle.gui.tree import SECTIONS
    from puzzle.gui.tree import Node as TreeNode
    from puzzle.gui.window import MainWindow
    from puzzle.project import save_project

    QApplication.instance() or QApplication([])
    p, _ = _project()
    save_project(p, tmp_path / "p")
    w = MainWindow(tmp_path / "p")
    menu = w.schematic.fill_new_menu(QMenu())
    shown = {m.menu().title(): {a.text() for a in m.menu().actions()}
             for m in menu.actions() if m.menu()}  # fmt: skip
    for heading in [t for t, _ in SECTIONS] + ["Results"]:
        tree = build_menu(w, TreeNode("section", label=heading))
        if tree is not None and tree.actions():
            assert shown.get(heading) == {a.text() for a in tree.actions()}, heading
    assert {"Geometry", "Materials", "Components", "Studies", "Coordinate systems"} <= set(shown)
    w.close()


def test_agents_wire_and_unwire_over_mcp(tmp_path):
    pytest.importorskip("mcp")
    from puzzle.agent.mcp import build_server
    from puzzle.commands import Session
    from puzzle.project import save_project

    p, o = _project()
    save_project(p, tmp_path / "p")
    Session(tmp_path / "p").submit({"command": "set_autonomy", "level": "act_in_budget"})
    server = build_server(tmp_path / "p", "test")

    def call(tool, **arguments):
        result = asyncio.run(server.call_tool(tool, arguments))
        return result.structured_content.get("result", result.structured_content)

    out = call("link", source="Steel", target="Assembly")
    assert out["status"] == "applied"
    listed = call("link", source="Bracket", target="Assembly")  # geometry onto component
    assert listed["status"] == "choose" and listed["choices"][0]["label"] == "New part from Bracket"
    assert call("link", source="Fine", target="Assembly")["status"] == "applied"
    both = call("link", source="Pipe axis", target="Assembly")  # a load, or the material
    assert both["status"] == "choose"
    assert (
        call(
            "link",
            source="Pipe axis",
            target="Assembly",
            which="Coordinate system of Pressure on Bracket/Inner",
        )["status"]
        == "applied"
    )
    assert call("unlink", source="Pipe axis", target="Assembly")["status"] == "applied"
    with pytest.raises(Exception, match="Static needs its component"):  # replaced, not cleared
        asyncio.run(server.call_tool("unlink", {"source": "Assembly", "target": "Static"}))


def test_loads_and_supports_are_nodes_wired_into_their_component():
    from puzzle import schematic as sch

    p, o = _project()
    comp = p.objects[o["comp"].id]
    comp.physics[0].features[0].axes = o["axes"].id
    built = sch.build(p)
    node = built.nodes[f"feature:{comp.id}:physics.0.features.0"]
    assert node.kind == "feature" and node.label == "Pressure on Bracket/Inner"
    assert node.path == "physics.0.features.0" and node.object == str(comp.id)
    wires = {(w.source, w.target) for w in built.wires if w.kind == "use"}
    assert (node.id, str(comp.id)) in wires  # into its component
    assert (str(o["axes"].id), node.id) in wires  # its coordinate system into it ...
    assert (str(o["axes"].id), str(comp.id)) not in wires  # ... not into the component


def test_a_wire_onto_a_load_sets_that_load_and_a_geometry_where_it_acts():
    p, o = _project()
    comp, path = p.objects[o["comp"].id], "physics.0.features.0"
    only = links.choices(p, o["axes"], comp, within=path)
    assert [c.label for c in only] == ["Coordinate system of Pressure on Bracket/Inner"]
    where = links.choices(p, o["geo"], comp, within=path)
    assert [c.label for c in where] == ["Acts on Bracket/Inner", "Acts on Bracket/Outer"]
    _apply(p, links.commands(where[1], o["geo"], comp))
    assert p.objects[o["comp"].id].physics[0].features[0].selection == "Bracket/Outer"
    _apply(p, links.commands(only[0], o["axes"], p.objects[o["comp"].id]))
    made = links.existing(p, o["axes"].id, p.objects[o["comp"].id], within=path)
    assert len(made) == 1 and made[0].path == f"{path}.axes"


def test_clicking_a_load_selects_it_in_the_tree(tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from puzzle.gui.window import MainWindow
    from puzzle.project import save_project

    QApplication.instance() or QApplication([])
    p, o = _project()
    save_project(p, tmp_path / "p")
    w = MainWindow(tmp_path / "p")
    w.schematic.refresh()
    node = w.schematic.schematic.nodes[f"feature:{o['comp'].id}:physics.0.features.0"]
    w.schematic.chosen(node)
    current = w.tree.current()
    assert current.kind == "feature" and current.path == "physics.0.features.0"
    w.close()


def test_agents_wire_onto_a_load(tmp_path):
    pytest.importorskip("mcp")
    from puzzle.agent.mcp import build_server
    from puzzle.commands import Session
    from puzzle.project import load_project, save_project

    p, o = _project()
    save_project(p, tmp_path / "p")
    Session(tmp_path / "p").submit({"command": "set_autonomy", "level": "act_in_budget"})
    server = build_server(tmp_path / "p", "test")

    def call(tool, **arguments):
        result = asyncio.run(server.call_tool(tool, arguments))
        return result.structured_content.get("result", result.structured_content)

    out = call("link", source="Pipe axis", target="Assembly", feature="Pressure on Bracket/Inner")
    assert out["status"] == "applied"
    out = call("link", source="Bracket", target="Assembly", feature="Bracket/Inner",
               which="Acts on Bracket/Outer")  # fmt: skip
    assert out["status"] == "applied"
    load = load_project(tmp_path / "p").objects[o["comp"].id].physics[0].features[0]
    assert load.axes == o["axes"].id and load.selection == "Bracket/Outer"
