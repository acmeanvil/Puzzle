"""Commands cost by what changed, not by project size (ADR 0090 package 14, BE-11).

Done when: an edit in a 300-object project is under 50 ms. Measured on the
maintainer's machine (Windows 11, a 300-object project: the cantilever and 72 chains
of four operators (295 objects), one history entry per object): 183 ms median before, 40 ms after.
The test holds the median of seven edits under `LIMIT_MS`, generous for GitHub's
slower machines, and what makes it cheap: no project read while nothing else wrote,
only the edited object's file written. A file another client (or a hand) changed is
still read, and a change made to the project in memory outside a command is dropped,
as a read from disk always dropped it.
"""

from __future__ import annotations

import os
import shutil
import statistics
import time
from pathlib import Path

import pytest

from puzzle.commands import Session
from puzzle.commands import session as session_module
from puzzle.model.schema import Component, Operator
from puzzle.project import store

EXAMPLE = Path(__file__).parents[1] / "examples" / "cantilever"
CHAINS = 74  # x 4 operators + the cantilever's seven objects: 303
LIMIT_MS = 150.0
FORCE = "physics.0.features.1.values.y.value"


@pytest.fixture(autouse=True)
def _no_auto_versions():
    from puzzle.settings import current

    current().set("versions.auto", False)


def _project(tmp_path: Path) -> tuple[Path, Session]:
    root = tmp_path / "big"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns(
        "results*", ".puzzle", "history.jsonl", "versions"))  # fmt: skip
    s = Session(root)
    from puzzle import operators

    for i in range(CHAINS):  # each chain one batch, so names resolve as an agent's do
        chain = [
            {"command": "create", "kind": "operator", "data": {
                "name": f"Allowed {i}", "type": "number",
                "inputs": {"value": {"value": {"value": 1 + i, "unit": "mm"}}}}},
        ]  # fmt: skip
        s.submit(chain[0])
        for c in [
            {"command": "create", "kind": "operator", "data": {
                "name": f"Peak {i}", "type": "reduce",
                "inputs": {"result": {"source": "Tip load", "port": "result"}}}},
            {"command": "create", "kind": "operator", "data": {
                "name": f"Check {i}", "type": "check",
                "inputs": {"value": {"source": f"Peak {i}"},
                           "limit": {"source": f"Allowed {i}"}}}},
            {"command": "create", "kind": "operator", "data": {
                "name": f"Twice {i}", "type": "expression", "expression": "2 * x",
                "inputs": {"x": {"source": f"Peak {i}"}}}},
        ]:  # fmt: skip
            c["data"]["inputs"] = operators.inputs_from(s.project, c["data"]["inputs"])
            s.submit(c)
    assert len(s.project.objects) >= 300
    return root, s


def _edit(value: float) -> dict:
    return {"command": "edit", "object": "Allowed 3",
            "ops": [{"op": "set", "path": "inputs.value.value.value", "value": value}]}  # fmt: skip


def test_an_edit_in_a_300_object_project_costs_by_what_changed(tmp_path, monkeypatch):
    root, s = _project(tmp_path)
    s.submit(_edit(9))  # warm
    loads, replaced = [], []
    real_load, real_replace = session_module.load_project, os.replace
    monkeypatch.setattr(session_module, "load_project",
                        lambda r: loads.append(r) or real_load(r))  # fmt: skip
    monkeypatch.setattr(os, "replace", lambda a, b: replaced.append(Path(b)) or real_replace(a, b))
    times = []
    for k in range(7):
        t = time.perf_counter()
        s.submit(_edit(10 + k))
        times.append((time.perf_counter() - t) * 1000)
    median = statistics.median(times)
    assert median < LIMIT_MS, f"an edit took {median:.0f} ms (median of {times})"
    assert loads == []  # nobody else wrote: the project kept, never read again
    edited = {p.name for p in replaced if p.suffix == ".toml"}
    target = next(o for o in s.project.of_type(Operator) if o.name == "Allowed 3")
    assert edited == {f"{target.id}.toml"}  # only the edited object's file
    assert Session(root).project.objects[target.id].inputs["value"].value.value == 16


def test_another_writer_is_still_seen(tmp_path):
    root = tmp_path / "p"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns(
        "results*", ".puzzle", "history.jsonl", "versions"))  # fmt: skip
    gui, agent = Session(root), Session(root)
    gui.submit({"command": "edit", "object": "Cantilever",
                "ops": [{"op": "set", "path": FORCE, "value": -5}]})  # fmt: skip
    agent.submit({"command": "edit", "object": "Cantilever",
                  "ops": [{"op": "set", "path": FORCE, "value": -7}]})  # fmt: skip
    gui.submit({"command": "add_library_material", "key": "air"})
    comp = next(iter(gui.project.of_type(Component)))
    assert comp.physics[0].features[1].values["y"].value == -7

    # A hand edit of a file (git, an editor) is read too, even of the same size.
    path = root / "component" / f"{comp.id}.toml"
    text = path.read_text(encoding="utf-8")
    assert "-7" in text
    path.write_text(text.replace("-7", "-8"), encoding="utf-8")
    gui.submit({"command": "add_library_material", "key": "air", "name": "Air 2"})
    comp = next(iter(gui.project.of_type(Component)))
    assert comp.physics[0].features[1].values["y"].value == -8


def test_a_change_in_memory_outside_a_command_is_dropped(tmp_path):
    root = tmp_path / "p"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns(
        "results*", ".puzzle", "history.jsonl", "versions"))  # fmt: skip
    s = Session(root)
    s.submit({"command": "add_library_material", "key": "air"})
    comp = next(iter(s.project.of_type(Component)))
    comp.name = "Changed in memory"  # never a command: as a read from disk, it goes
    s.submit({"command": "add_library_material", "key": "air", "name": "Air 2"})
    names = {c.name for c in s.project.of_type(Component)}
    assert names == {"Cantilever"}
    assert {c.name for c in Session(root).project.of_type(Component)} == {"Cantilever"}


def test_the_project_kept_equals_one_read_afresh(tmp_path):
    """Objects written are put back as their files read, so hashes and run keys of the
    kept project are those of a fresh read."""
    from puzzle.project.graph import content_hashes

    root = tmp_path / "p"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns(
        "results*", ".puzzle", "history.jsonl", "versions"))  # fmt: skip
    s = Session(root)
    s.submit({"command": "create", "kind": "operator",
              "data": {"name": "Load", "type": "number",
                       "inputs": {"value": {"value": {"value": 2, "unit": "kN"}}}}})  # fmt: skip
    s.submit({"command": "edit", "object": "Cantilever",
              "ops": [{"op": "set", "path": FORCE, "value": -3}]})  # fmt: skip
    fresh = store.load_project(root)
    assert content_hashes(s.project, root) == content_hashes(fresh, root)
    assert {i: o.model_dump(mode="json") for i, o in s.project.objects.items()} == {
        i: o.model_dump(mode="json") for i, o in fresh.objects.items()
    }


def test_a_local_jobs_spec_keeps_no_keys_or_tokens(monkeypatch):
    """BE-21: the runner keeps a job's spec on disk, and it held the whole environment,
    provider keys included (ADR 0084 reads them from there)."""
    from puzzle.jobs import service

    class Runtime:
        def wrap(self, command, workdir):
            return command

        def environment(self, argv):
            return {
                "PATH": "/bin",
                "OMP_NUM_THREADS": "4",
                "ANTHROPIC_API_KEY": "sk-ant-x",
                "HF_TOKEN": "hf_x",
                "GITHUB_PAT": "x",
                "MY_SECRET_THING": "x",
                "DB_PASSWORD": "x",
                "SSH_AUTH_SOCK": "/tmp/agent",
                "CONDA_PREFIX": "/c",
            }

    local = object.__new__(service.LocalService)
    local.runtime = Runtime()
    env = local.spec(["ccx", "job"], Path("."))["env"]
    assert env == {"PATH": "/bin", "OMP_NUM_THREADS": "4", "SSH_AUTH_SOCK": "/tmp/agent",
                   "CONDA_PREFIX": "/c"}  # fmt: skip
