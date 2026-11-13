"""The Weld object (ADR 0078): its checks, and that a component without welds keeps
its content hash."""

import pytest
from pydantic import ValidationError

from puzzle.model.schema import Component, MeshRecipe, PartInstance, Project, Quantity, Weld
from puzzle.project.graph import _own

q = lambda v, u: Quantity(value=v, unit=u)  # noqa: E731


def test_a_weld_says_what_it_lacks():
    Weld(name="W", leg=q(6, "mm"), path="A/E", faces=["A/x", "B/y"])
    with pytest.raises(ValidationError, match="segment length and pitch"):
        Weld(name="W", leg=q(6, "mm"), path="A/E", faces=["A/x", "B/y"],
             pattern="intermittent")  # fmt: skip
    with pytest.raises(ValidationError, match="CJP or PJP"):
        Weld(name="W", type="groove", leg=q(6, "mm"), path="A/E", faces=["A/x", "B/y"])
    with pytest.raises(ValidationError):
        Weld(name="W", leg=q(6, "mm"), path="A/E", faces=["A/x"])  # one face


def test_no_welds_keeps_the_hash():
    p = Project(name="p")
    mesh = p.add(MeshRecipe(name="M"))
    from uuid import uuid4

    comp = Component(name="C", mesh=mesh.id, parts=[PartInstance(name="A", geometry=uuid4())])
    assert "welds" not in _own(comp)
    comp.welds = [Weld(name="W", leg=q(6, "mm"), path="A/E", faces=["A/x", "B/y"])]
    assert _own(comp)["welds"][0]["name"] == "W"


def test_other_solvers_refuse_a_weld():
    from puzzle.adapters import available_adapters

    adapters = available_adapters()
    caps = {name: adapters[name]().capabilities.welds for name in adapters}
    writers = {"calculix", "code_aster", "fenicsx"}  # ADR 0078 step 3
    assert all(caps[k] == {"line"} for k in writers if k in caps)
    assert all(not v for k, v in caps.items() if k not in writers)
