"""Frames (ADR 0079): nodes and members from lists and DXF drawings, section constants,
member ends kept out of content hashes when unset, and refused where not written."""

import math

import pytest

from puzzle import frames
from puzzle.frames.lines import FrameError, from_args
from puzzle.model.schema import (
    BeamSection,
    Component,
    Feature,
    MemberRelease,
    MeshRecipe,
    PartInstance,
    Physics,
    Project,
    Quantity,
    SolveStep,
    Study,
)


def q(v, unit):
    return Quantity(value=v, unit=unit)


def test_members_between_named_nodes():
    lines = from_args({"nodes": {"A": [0, 0, 0], "B": [0, 0, 4], "C": [6, 0, 4]},
                       "members": [["AB", "A", "B", "Columns"], {"name": "BC", "i": "B",
                                                                 "j": "C"}]})  # fmt: skip
    assert [m.name for m in lines.members] == ["AB", "BC"]
    assert lines.groups() == {"Columns": ["AB"]}
    assert lines.length(lines.members[1]) == pytest.approx(6.0)
    for bad, words in (
        ({"nodes": {"A": [0, 0, 0]}, "members": [["AB", "A", "B"]]}, "node 'B'"),
        ({"nodes": {"A": [0, 0, 0]}, "members": [["AA", "A", "A"]]}, "no length"),
        (
            {
                "nodes": {"A": [0, 0, 0], "B": [1, 0, 0]},
                "members": [["M", "A", "B"], ["M", "B", "A"]],
            },
            "two members",
        ),  # fmt: skip
        ({}, "at least one member"),
    ):
        with pytest.raises(FrameError, match=words):
            from_args(bad)


def test_lines_from_a_dxf(tmp_path):
    """LINEs (in 3D), an LWPOLYLINE's segments, layers as groups, ends joined into nodes,
    millimetres by $INSUNITS."""
    ezdxf = pytest.importorskip("ezdxf")
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 4  # mm
    msp = doc.modelspace()
    msp.add_line((0, 0, 0), (0, 3000, 0), dxfattribs={"layer": "Columns"})
    msp.add_line((6000, 0, 0), (6000, 3000, 0), dxfattribs={"layer": "Columns"})
    msp.add_lwpolyline([(0, 3000), (3000, 3000), (6000, 3000)], dxfattribs={"layer": "Beams"})
    path = tmp_path / "frame.dxf"
    doc.saveas(path)
    lines = from_args({"dxf": "frame.dxf"}, root=tmp_path)
    assert len(lines.members) == 4
    assert len(lines.nodes) == 5  # two feet, two tops (shared with the beam), its middle
    groups = lines.groups()
    assert sorted(groups) == ["Beams", "Columns"] and len(groups["Beams"]) == 2
    lengths = sorted(round(lines.length(m), 9) for m in lines.members)
    assert lengths == [3.0, 3.0, 3.0, 3.0]
    only = from_args({"dxf": "frame.dxf", "layer": "Columns"}, root=tmp_path)
    assert len(only.members) == 2


def test_the_geometry_names_members_groups_and_nodes():
    geo = frames.geometry("Frame", {"A": [0, 0, 0], "B": [4, 0, 0]}, [["AB", "A", "B", "G"]])
    kinds = {s.name: s.entity for s in geo.selections}
    assert kinds == {"AB": "edge", "G": "edge", "A": "vertex", "B": "vertex"}
    assert geo.history[0].kind == "frame"


def test_section_constants():
    rect = BeamSection(selection="F", shape="rectangle", width=q(20, "mm"), height=q(40, "mm"))
    a, i1, i2, j = frames.constants(rect)
    assert a == pytest.approx(8e-4) and i1 == pytest.approx(0.02 * 0.04**3 / 12)
    assert i2 == pytest.approx(0.04 * 0.02**3 / 12)
    assert j == pytest.approx(0.04 * 0.02**3 * (1 / 3 - 0.21 * 0.5 * (1 - 0.5**4 / 12)))
    tube = BeamSection(selection="F", shape="tube", diameter=q(100, "mm"), thickness=q(5, "mm"))
    a, i1, _i2, j = frames.constants(tube)
    assert a == pytest.approx(math.pi * (0.05**2 - 0.045**2))
    assert j == pytest.approx(2 * i1)
    w = BeamSection(selection="F", shape="library", standard="aisc", designation="W12X26")
    from puzzle import sections

    assert frames.constants(w) == (sections.get("aisc", "W12X26").A,
                                   sections.get("aisc", "W12X26").I1,
                                   sections.get("aisc", "W12X26").I2,
                                   sections.get("aisc", "W12X26").J)  # fmt: skip


def _frame(releases=()):
    p = Project(name="f")
    geo = p.add(frames.geometry("Frame", {"A": [0, 0, 0], "B": [4, 0, 0]}, [["AB", "A", "B"]]))
    mesh = p.add(MeshRecipe(name="M"))
    comp = p.add(Component(
        name="C", parts=[PartInstance(name="F", geometry=geo.id)], mesh=mesh.id,
        beam_sections=[BeamSection(selection="F", shape="rectangle", width=q(20, "mm"),
                                   height=q(40, "mm"))],
        member_releases=list(releases),
        physics=[Physics(type="solid_mechanics",
                         features=[Feature(type="fixed", selection="F/A")])],
    ))  # fmt: skip
    study = p.add(Study(name="S", component=comp.id, solver="calculix",
                        steps=[SolveStep(type="stationary")]))  # fmt: skip
    return p, comp, study


def test_unset_member_ends_keep_hashes_and_set_ones_change_them():
    from puzzle.project.graph import _own

    _p, comp, _s = _frame()
    data = _own(comp)
    assert "member_releases" not in data and "member_offsets" not in data
    _p, released, _s = _frame([MemberRelease(selection="F/AB", at="F/B")])
    assert _own(released)["member_releases"][0]["released"] == ["ry", "rz"]


def test_member_ends_are_refused_where_not_written():
    from puzzle.adapters import available_adapters

    p, _comp, study = _frame([MemberRelease(selection="F/AB", at="F/B")])
    adapters = available_adapters()
    assert "pynite" in adapters
    for solver in ("calculix", "code_aster"):
        problems = adapters[solver]().check(p, study)
        assert any("releases" in m for m in problems), (solver, problems)
    assert not any("releases" in m for m in adapters["pynite"]().check(p, study))


def test_line_loads_and_node_springs_are_pynite_only():
    from puzzle.adapters import available_adapters

    p, comp, study = _frame()
    comp.physics[0].features.append(
        Feature(type="line_load", selection="F/AB", values={"z": q(-1, "kN/m")})
    )
    adapters = available_adapters()
    assert any("line_load" in m for m in adapters["calculix"]().check(p, study))
    assert not any("line_load" in m for m in adapters["pynite"]().check(p, study))
