"""Clusters and recipes (ADR 0064 phase 4): a recipe put into a project wired to a run,
its values by hand (`test_operators`' two tets: σxx = 100·t·x MPa, so the worst node is
300 MPa); a user's cluster saved from operators and put into another project, copied;
and a Snapshot export of a run to PNG."""

from __future__ import annotations

from uuid import uuid4

import pytest
from test_operators import Store

from puzzle.commands import Session
from puzzle.model.schema import Operator, Project, Quantity, Study, TreeGroup
from puzzle.operators import clusters
from puzzle.operators import evaluate as ev
from puzzle.project import save_project

pytest.importorskip("polars")


@pytest.fixture
def setup(tmp_path):
    p = Project("p")
    study = p.add(Study(name="Heat", component=uuid4(), solver="calculix", steps=[]))
    root = tmp_path / "p"
    save_project(p, root)
    return Session(root), study, Store(study.id)


def test_a_recipe_wired_to_a_run(setup):
    s, study, store = setup
    batch = clusters.commands(s.project, "worst_nodes", {"result": (str(study.id), "result")})
    s.submit_batch(batch)
    p = s.project
    ops = [o for o in p.of_type(Operator) if o.name.startswith("Worst nodes: ")]
    assert {o.type for o in ops} == {"result_table", "top_n", "preview", "export"}
    table = next(o for o in ops if o.type == "result_table")
    assert table.inputs["result"].source == study.id
    top = next(o for o in ops if o.type == "top_n")
    got = ev.value(ev.Context(p, store), top)
    assert got.frame.height == 5 and got.frame["von_mises"].max() == pytest.approx(300e6)
    group = next(g for g in p.of_type(TreeGroup) if g.name == "Worst nodes")
    assert set(group.members) == {str(o.id) for o in ops}
    s.undo()  # one batch, one undo
    assert not [o for o in s.project.of_type(Operator) if o.name.startswith("Worst nodes")]


def test_every_recipe_goes_in(setup):
    s, study, _store = setup
    for key, t in clusters.RECIPES.items():
        binding = {port["name"]: (str(study.id), "result") for port in t["ports"]}
        s.submit_batch(clusters.commands(s.project, key, binding))
    names = {o.name for o in s.project.of_type(Operator)}
    assert "Stress margin: Check" in names and "Peak per part: Plot" in names
    margin = next(o for o in s.project.of_type(Operator) if o.name == "Stress margin: Check")
    assert margin.inputs["limit"].value == Quantity(value=250, unit="MPa")


def test_a_users_cluster_saved_and_put_in_elsewhere(setup, tmp_path):
    s, study, store = setup
    s.submit_batch(clusters.commands(s.project, "peak_per_part", {
        "result": (str(study.id), "result")}))  # fmt: skip
    mine = [o.id for o in s.project.of_type(Operator) if o.type in ("group", "convert_units")]
    path = clusters.save(s.project, mine, "Peaks in MPa")
    assert path.name == "peaks_in_mpa.json"
    t = clusters.templates()["mine:peaks_in_mpa"]
    assert [p["name"] for p in t["ports"]] == ["table"]  # wired from outside: a port
    assert t["operators"][0]["settings"]["aggregations"] == ["max(von_mises) as peak"]
    other = tmp_path / "other"
    q = Project("other")
    st = q.add(Study(name="Heat", component=uuid4(), solver="calculix", steps=[]))
    tab = q.add(Operator(name="T", type="result_table", quantities=["stress:von_mises"],
                         inputs={"result": {"source": st.id, "port": "result"}}))  # fmt: skip
    save_project(q, other)
    s2 = Session(other)
    s2.submit_batch(clusters.commands(s2.project, "mine:peaks_in_mpa",
                                      {"table": (str(tab.id), "out")}))  # fmt: skip
    conv = next(o for o in s2.project.of_type(Operator) if o.type == "convert_units")
    got = ev.value(ev.Context(s2.project, Store(st.id)), conv)
    assert got.units == {"peak": "MPa"} and got.frame["peak"][0] == pytest.approx(300e6)
    copied = {o.id for o in s2.project.of_type(Operator)}
    assert not copied & set(mine)  # copies, new ids
    assert any(r["key"] == "mine:peaks_in_mpa" and r["mine"] for r in clusters.summary())


def test_unknown_templates_are_refused(setup):
    from puzzle.errors import PuzzleError

    s, _study, _store = setup
    with pytest.raises(PuzzleError, match="no recipe or cluster"):
        clusters.commands(s.project, "nope")
