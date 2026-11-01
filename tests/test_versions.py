"""Project versions (ADR 0059): made by Puzzle and by you, described, restored (and the
restore undone), thinned, and a copy started from one."""

import json
import shutil
from pathlib import Path

import pytest
from solvers import available

from puzzle.commands import Session
from puzzle.commands.session import Actor
from puzzle.errors import PuzzleError
from puzzle.project import versions
from puzzle.project.copy import save_as

EXAMPLE = Path(__file__).parents[1] / "examples" / "cantilever"
AGENT = Actor("agent", "test")


def _project(tmp_path) -> Path:
    root = tmp_path / "bar"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("results*", ".puzzle"))
    return root


def _rename(s: Session, new: str, kind="study"):
    obj = next(o for o in s.project.objects.values() if o.kind == kind)
    s.submit({"command": "edit", "object": str(obj.id),
              "ops": [{"op": "set", "path": "name", "value": new}]})  # fmt: skip
    return obj.id


def test_the_first_change_keeps_the_project_as_found(tmp_path):
    root = _project(tmp_path)
    s = Session(root)
    assert versions.versions(root) == []  # opening alone writes nothing
    _rename(s, "Renamed")
    [first] = versions.versions(root)
    assert first.trigger == "first" and first.how == "auto" and first.by == "Puzzle"
    # It holds the state before the change: the old name is in its study file.
    texts = [versions.content(root, d).decode() for r, d in first.files.items()
             if r.startswith("study/")]  # fmt: skip
    assert not any("Renamed" in t for t in texts)
    assert first.summary["counts"]["studies"] >= 1
    assert {st["name"] for st in first.summary["studies"]}  # each study with its state


def test_after_many_changes_and_outside_edits(tmp_path):
    from puzzle.settings import current

    current().set("versions.every_edits", 5)
    root = _project(tmp_path)
    s = Session(root)
    for k in range(6):
        _rename(s, f"Study {k}")
    found = versions.versions(root)
    assert [v.trigger for v in found] == ["first", "edits"]
    assert found[1].what == "After 5 changes"
    assert any(c.startswith("Study ") for c in found[1].summary["changes"]["changed"])
    # Changed outside Puzzle (a file replaced), then opened and changed: kept as found.
    (root / "bar.step").write_bytes((root / "bar.step").read_bytes() + b"\n")
    s2 = Session(root)
    _rename(s2, "After outside edit")
    assert versions.latest(root).trigger == "opened"
    assert "bar.step" in versions.latest(root).summary["changes"]["changed"]


def test_saved_by_you_restored_and_the_restore_undone(tmp_path):
    root = _project(tmp_path)
    s = Session(root)
    good = s.save_version(label="Before the redesign", note="checked by hand", good=True)
    assert good.how == "user" and good.by == "You" and good.what == "Before the redesign"
    study = _rename(s, "Redesign")
    (root / "notes.txt").write_text("added since")
    (root / "bar.step").write_text("replaced since")
    s.restore_version("good")
    assert s.project.objects[study].name != "Redesign"
    assert not (root / "notes.txt").exists()
    assert (root / "bar.step").read_text() != "replaced since"
    kept = versions.latest(root)
    assert (
        kept.trigger == "before_restore" and kept.what == f"Before restoring version {good.number}"
    )
    last = s.history_entries()[-1]
    assert last["command"] == {"command": "restore_version", "version": good.number}
    # Undo takes the restore back, files too.
    s.undo()
    assert s.project.objects[study].name == "Redesign"
    assert (root / "notes.txt").read_text() == "added since"
    assert (root / "bar.step").read_text() == "replaced since"


def test_known_good_marks_and_roll_back(tmp_path):
    root = _project(tmp_path)
    s = Session(root)
    a = s.save_version(label="a")
    s.save_version(label="b")
    with pytest.raises(PuzzleError, match="known good"):
        versions.get(root, "good")
    s.mark_version(a.number)
    assert versions.get(root, "good").number == a.number
    assert versions.get(root, a.number).good_reason == "marked by you"
    s.mark_version(a.number, good=False)
    assert not versions.get(root, a.number).good


def test_agents_restore_only_when_they_may_act(tmp_path):
    root = _project(tmp_path)
    s = Session(root)
    v = s.save_version(label="x")
    level = s.project.agent.level
    assert level not in ("act", "act_in_budget")  # the example's default
    with pytest.raises(PuzzleError) as refused:
        s.restore_version(v.number, AGENT)
    assert refused.value.code == "not_allowed"


def test_thinning_keeps_yours_and_the_good_ones_and_drops_unused_blobs(tmp_path):
    root = _project(tmp_path)
    s = Session(root)
    mine = s.save_version(label="mine")
    for k in range(6):
        (root / "notes.txt").write_text(f"draft {k}")
        with s._locked():
            s._auto_version("edits", Actor("user"), words={"count": 1})
    autos = [v for v in versions.versions(root) if v.how == "auto"]
    assert len(autos) == 6
    versions.mark(root, autos[0].number, True, "solved")
    gone = versions.prune(root, keep=2)
    left = versions.versions(root)
    numbers = {v.number for v in left}
    assert mine.number in numbers and autos[0].number in numbers  # kept
    assert autos[-1].number in numbers and autos[-2].number in numbers  # the latest two
    assert gone and all(n not in numbers for n in gone)
    held = {d for v in left for d in v.files.values()}
    assert {b.name for b in (root / "versions" / "blobs").iterdir()} <= held


def test_a_copy_from_a_version(tmp_path):
    root = _project(tmp_path)
    s = Session(root)
    old = s.save_version(label="old")
    _rename(s, "Newer")
    done = save_as(root, tmp_path / "copy", "From the old one", version=old.number)
    from puzzle.project import load_project

    names = {o.name for o in load_project(done.root).objects.values()}
    assert "Newer" not in names
    first = versions.latest(done.root)
    assert first.trigger == "copied" and "version" in first.what


def test_a_version_file_is_plain_json(tmp_path):
    root = _project(tmp_path)
    v = Session(root).save_version(label="look")
    data = json.loads((root / "versions" / f"{v.number}.json").read_text())
    assert data["label"] == "look" and data["files"]["project.toml"]


@pytest.mark.skipif(not available("calculix"), reason="CalculiX not available")
def test_a_successful_run_marks_its_starting_point_known_good(tmp_path):
    root = _project(tmp_path)
    s = Session(root)
    from puzzle.model.schema import Study

    study = next(st for st in s.project.of_type(Study) if st.solver == "calculix")
    s.submit({"command": "run_study", "study": study.name})
    good = versions.get(root, "good")
    assert good.trigger in ("first", "before_run") and f"solved {study.name}" in good.good_reason


def test_filtering_by_who_type_date_good_and_words(tmp_path):
    from datetime import datetime, timedelta

    root = _project(tmp_path)
    s = Session(root)
    s.save_version(label="Baseline mesh", good=True)
    (root / "bar.step").write_bytes((root / "bar.step").read_bytes() + b"\n")  # outside
    _rename(Session(root), "Thermal check")  # an "opened" version, by Puzzle, before it
    s.save_version(AGENT, label="Before the agent's batch")
    every = versions.versions(root)
    assert [versions.who(v) for v in every] == ["you", "puzzle", "agent"]
    assert [v.what for v in versions.select(every, by="you")] == ["Baseline mesh"]
    assert [v.trigger for v in versions.select(every, types={"opened"})] == ["opened"]
    assert [v.label for v in versions.select(every, good=True)] == ["Baseline mesh"]
    assert [v.label for v in versions.select(every, text="agent batch")] == [
        "Before the agent's batch"]  # fmt: skip
    now = datetime.now().astimezone()
    assert len(versions.select(every, since=now - timedelta(hours=1))) == 3
    assert versions.select(every, until=now - timedelta(days=1)) == []
    assert len(versions.select(every, since=datetime.now() - timedelta(hours=1))) == 3  # local


def test_the_cli_lists_saves_filters_and_restores(tmp_path, capsys):
    from puzzle.cli import main

    root = _project(tmp_path)
    assert main(["version-save", str(root), "--label", "Start", "--good"]) == 0
    (root / "bar.step").write_bytes((root / "bar.step").read_bytes() + b"\n")  # outside
    _rename(Session(root), "Changed")
    assert main(["versions", str(root), "--by", "you"]) == 0
    out = capsys.readouterr().out
    assert "Start" in out and "known good" in out and "outside Puzzle" not in out
    assert main(["versions", str(root), "--type", "opened"]) == 0
    assert "Opened, changed outside Puzzle" in capsys.readouterr().out
    assert main(["versions", str(root), "--until", "2000-01-01"]) == 0
    assert "none of the" in capsys.readouterr().out
    assert main(["versions", str(root), "--type", "nonsense"]) == 1
    assert main(["version-restore", str(root), "good"]) == 0
    assert "restored version 1" in capsys.readouterr().out


def test_a_batch_keeps_the_project_as_found_too(tmp_path):
    root = _project(tmp_path)
    s = Session(root)
    study = next(o for o in s.project.objects.values() if o.kind == "study")
    s.submit_batch([{"command": "edit", "object": str(study.id),
                     "ops": [{"op": "set", "path": "name", "value": "Batched"}]}])  # fmt: skip
    assert [v.trigger for v in versions.versions(root)] == ["first"]
