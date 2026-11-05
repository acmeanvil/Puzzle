"""Python script operators (ADR 0062 phase 4): run apart in the scripts environment,
with a time limit, away from the project's files, only in a trusted project, proposed
by agents below "act", and hashed so what they feed goes stale."""

import sys

import pytest

from puzzle.errors import PuzzleError
from puzzle.model.schema import Operator, OperatorInput, Project
from puzzle.operators import evaluate as ev
from puzzle.operators import scripts

needs_env = pytest.mark.skipif(scripts.python() is None, reason="no scripts environment")


def script(code, returns="number", **inputs):
    return Operator(name="S", type="script", code=code, returns=returns,
                    inputs={k: v for k, v in inputs.items()})  # fmt: skip


def value(project, op):
    return ev.value(ev.Context(project, None), op)


@needs_env
def test_a_script_takes_and_gives_each_kind():
    p = Project("p")
    two = p.add(Operator(name="two", type="number", inputs={"value": OperatorInput(value=2.0)}))
    root = p.add(script("import numpy as np\nout = float(np.sqrt(a))",
                        a=OperatorInput(source=two.id, port="out")))  # fmt: skip
    assert value(p, root).value == pytest.approx(2**0.5)
    xs = p.add(Operator(name="xs", type="range", inputs={
        "start": OperatorInput(value=0), "end": OperatorInput(value=3),
        "count": OperatorInput(value=4)}))  # fmt: skip
    p.add(
        op := script("out = [v * v for v in a]", "list", a=OperatorInput(source=xs.id, port="out"))
    )
    assert list(value(p, op).values) == [0, 1, 4, 9]
    table = script("out = {'x': a, 'y': [2 * v for v in a]}", "table",
                   a=OperatorInput(source=xs.id, port="out"))  # fmt: skip
    p.add(table)
    got = value(p, table)
    assert got.columns == ["x", "y"] and got.frame["y"].to_list() == [0, 2, 4, 6]
    pts = p.add(script("out = [[v, 0, 0] for v in a]", "points",
                       a=OperatorInput(source=xs.id, port="out")))  # fmt: skip
    assert value(p, pts).xyz.shape == (4, 3)
    words = p.add(script("out = f'{len(a)} values'", "text",
                         a=OperatorInput(source=xs.id, port="out")))  # fmt: skip
    assert value(p, words).text() == "4 values"


@needs_env
def test_a_script_past_its_limit_is_stopped():
    p = Project("p")
    op = p.add(Operator(name="Loop", type="script", code="while True:\n    pass",
                        timeout_s=1.0))  # fmt: skip
    with pytest.raises(PuzzleError) as exc:
        value(p, op)
    assert exc.value.code == "timeout"


@needs_env
def test_a_failing_script_says_why():
    p = Project("p")
    op = p.add(Operator(name="Bad", type="script", code="out = 1 / 0"))
    with pytest.raises(PuzzleError, match="ZeroDivisionError") as exc:
        value(p, op)
    assert exc.value.code == "script_failed"


@needs_env
@pytest.mark.skipif(sys.platform != "darwin", reason="the folder is denied by sandbox-exec")
def test_a_script_cannot_read_the_project(tmp_path):
    (tmp_path / "secret.txt").write_text("the project's own file")
    code = f"out = open({str(tmp_path / 'secret.txt')!r}).read()"
    with pytest.raises(PuzzleError) as exc:
        scripts.run(code, {}, project_root=tmp_path)
    assert exc.value.code == "script_failed"
    assert scripts.run("out = 2 + 2", {}, project_root=tmp_path) == 4  # the rest still runs


@needs_env
def test_an_untrusted_project_runs_no_scripts(tmp_path, monkeypatch):
    from puzzle import trust
    from puzzle.project import load_project, save_project

    p = Project("p")
    p.add(Operator(name="S", type="script", code="out = 1.0"))
    save_project(p, tmp_path)
    trust.revoke(tmp_path)  # as if it came from elsewhere
    loaded = load_project(tmp_path)
    assert not loaded.trusted
    assert [x.code for x in loaded.problems()] == ["untrusted"]
    op = loaded.of_type(Operator)[0]
    with pytest.raises(PuzzleError) as exc:
        value(loaded, op)
    assert exc.value.code == "untrusted"
    trust.trust(tmp_path)
    again = load_project(tmp_path)
    assert again.trusted and value(again, again.of_type(Operator)[0]).value == 1.0


def test_agents_below_act_propose_code(tmp_path):
    from puzzle.commands import Session
    from puzzle.commands.model import Create
    from puzzle.commands.session import Actor
    from puzzle.project import save_project

    save_project(Project("p"), tmp_path)
    s = Session(tmp_path)
    s.submit({"command": "set_autonomy", "level": "act_in_budget"})
    agent = Actor("agent", "test")
    data = {"name": "Mine", "type": "script", "code": "out = 3.0"}
    outcome = s.submit(Create(kind="operator", data=data), agent)
    assert outcome.status == "proposed" and not s.project.of_type(Operator)
    plain = {"name": "Width", "type": "number", "inputs": {"value": {"value": 2.0}}}
    assert s.submit(Create(kind="operator", data=plain), agent).status == "applied"
    with pytest.raises(PuzzleError) as asked:  # "act" takes the user's second sign-off
        s.submit({"command": "set_autonomy", "level": "act"})
    s.submit({"command": "set_autonomy", "level": "act", "confirm": asked.value.details["token"]})
    assert s.submit(Create(kind="operator", data=data), agent).status == "applied"


@needs_env
def test_a_script_drives_a_history_argument_and_its_code_stales_it():
    pytest.importorskip("OCP")
    from puzzle.model.schema import (
        Component,
        Geometry,
        HistoryStep,
        MeshRecipe,
        PartInstance,
        Study,
    )
    from puzzle.project.graph import run_hashes

    p = Project("p")
    op = p.add(Operator(name="width", type="script", code="out = 0.02 + 0.01"))
    geo = p.add(Geometry(name="Plate", history=[HistoryStep(kind="box", name="B", args={
        "dx": "width", "dy": 0.01, "dz": 0.01})]))  # fmt: skip
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    from puzzle.geometry.prep import shape_of

    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape_of(p, geo, "."), props)
    assert props.Mass() == pytest.approx(0.03 * 0.01 * 0.01, rel=1e-9)
    mesh = p.add(MeshRecipe(name="M"))
    comp = p.add(Component(name="C", mesh=mesh.id,
                           parts=[PartInstance(name="Plate", geometry=geo.id)]))  # fmt: skip
    study = p.add(Study(name="S", component=comp.id, solver="calculix", steps=[]))
    before = run_hashes(p)[study.id]
    op.code = "out = 0.05"
    assert run_hashes(p)[study.id] != before


def test_imports_and_missing_packages():
    code = "import numpy as np\nfrom scipy import optimize\nimport os, json\n"
    assert scripts.imports(code) == ["numpy", "scipy"]
    with pytest.raises(PuzzleError, match="does not parse"):
        scripts.imports("out = (")
    if scripts.python() is not None:
        assert scripts.missing(code) == []
        assert scripts.missing("import surely_not_a_package_here") == ["surely_not_a_package_here"]
