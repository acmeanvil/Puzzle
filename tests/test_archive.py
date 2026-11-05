"""Project archives (ADR 0061): one .puzzle file carries a project to another Puzzle,
with the sender's choice of results, versions and history, and is checked on the way
in (format, hashes, paths, size, links); a project holding code arrives untrusted and
with agents only advising."""

import json
import shutil
import zipfile
from pathlib import Path

import pytest

from puzzle.errors import PuzzleError
from puzzle.project import archive
from puzzle.project.archive import Include

EXAMPLE = Path(__file__).parents[1] / "examples" / "cantilever"


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "cantilever"
    skip = shutil.ignore_patterns("results*", ".puzzle", "versions", "history.jsonl")
    shutil.copytree(EXAMPLE, root, ignore=skip)
    return root


def test_an_archive_round_trips_the_model(project, tmp_path):
    from puzzle.project.store import load_project

    out = archive.write(project, tmp_path / "out" / "beam")
    assert out.path.name == "beam.puzzle" and out.members > 3
    info = archive.manifest(out.path)
    assert info["format"] == archive.FORMAT and info["name"] == load_project(project).name
    assert info["include"] == {"results": "latest", "versions": True, "history": True}
    assert all(len(h) == 64 for h in info["members"].values())
    assert ".puzzle/lock" not in info["members"]
    opened = archive.read(out.path, tmp_path / "there")
    a, b = load_project(project), load_project(opened.root)
    assert {o.name for o in a.objects.values()} == {o.name for o in b.objects.values()}
    # The geometry's file came along and the copy starts with a version saying where from.
    from puzzle.project import versions

    assert "archive beam.puzzle" in versions.latest(opened.root).what


def test_outside_geometry_comes_in_and_results_are_chosen(project, tmp_path):
    from puzzle.model.schema import Geometry
    from puzzle.project.store import load_project, save_project

    p = load_project(project)
    geo = p.of_type(Geometry)[0]
    outside = tmp_path / "parts" / Path(geo.source).name
    outside.parent.mkdir()
    shutil.move(project / geo.source, outside)
    geo.source = str(outside)  # an absolute path on the sender's disk
    save_project(p, project)
    (project / "results").mkdir()
    (project / "results" / "store.json").write_text("{}")
    (project / ".puzzle" / "runs" / "ccx-1").mkdir(parents=True)
    (project / ".puzzle" / "runs" / "ccx-1" / "job.inp").write_text("*NODE")
    light = archive.write(project, tmp_path / "light.puzzle", Include(results="none",
                                                                     versions=False))  # fmt: skip
    names = set(archive.manifest(light.path)["members"])
    assert not any(n.startswith(("results/", ".puzzle/", "versions/")) for n in names)
    assert any(n.startswith("imported/") for n in names)
    there = archive.read(light.path, tmp_path / "there")
    moved = load_project(there.root).of_type(Geometry)[0]
    assert moved.source.startswith("imported/") and (there.root / moved.source).exists()


def _tampered(src: Path, dest: Path, change) -> Path:
    with zipfile.ZipFile(src) as z:
        items = {i.filename: z.read(i) for i in z.infolist()}
    change(items)
    with zipfile.ZipFile(dest, "w") as z:
        for name, data in items.items():
            z.writestr(name, data)
    return dest


def test_a_bad_archive_is_refused_before_anything_is_written(project, tmp_path):
    good = archive.write(project, tmp_path / "good.puzzle").path

    def edited(items):
        name = next(n for n in items if n.endswith(".toml") and n != "project.toml")
        items[name] = items[name] + b"\n# changed"

    def escaping(items):
        items["../escape.txt"] = b"x"
        info = json.loads(items["archive.json"])
        info["members"]["../escape.txt"] = "0" * 64
        items["archive.json"] = json.dumps(info).encode()

    def newer(items):
        info = json.loads(items["archive.json"])
        info["format"] = archive.FORMAT + 1
        items["archive.json"] = json.dumps(info).encode()

    def missing(items):
        name = next(n for n in items if n.endswith(".toml") and n != "project.toml")
        del items[name]

    for change, words in ((edited, "hash"), (escaping, "outside"), (newer, "newer"),
                          (missing, "lacks")):  # fmt: skip
        bad = _tampered(good, tmp_path / f"{change.__name__}.puzzle", change)
        dest = tmp_path / f"to-{change.__name__}"
        with pytest.raises(PuzzleError, match=words) as refused:
            archive.read(bad, dest)
        assert refused.value.code == "invalid_archive"
        assert not dest.exists() or not any(dest.iterdir())
    assert not (tmp_path / "escape.txt").exists()
    no_manifest = _tampered(good, tmp_path / "plain.zip", lambda i: i.pop("archive.json"))
    with pytest.raises(PuzzleError) as junk:
        archive.manifest(no_manifest)
    assert junk.value.code == "invalid_archive"


def test_names_windows_cant_make_are_refused(project, tmp_path):
    (project / "notes").mkdir()
    (project / "notes" / "aux.txt").write_text("x")
    with pytest.raises(PuzzleError, match="Windows"):
        archive.write(project, tmp_path / "x.puzzle")


def test_a_project_with_code_arrives_untrusted_and_advising(project, tmp_path):
    from puzzle import trust
    from puzzle.model.schema import Operator
    from puzzle.project.store import load_project, save_project

    p = load_project(project)
    p.add(Operator(name="S", type="script", code="out = 1.0"))
    p.agent.level = "act_in_budget"
    save_project(p, project)
    trust.trust(project)
    opened = archive.read(archive.write(project, tmp_path / "code.puzzle").path,
                          tmp_path / "there")  # fmt: skip
    assert opened.holds_code == ["S"] and any("not trusted" in n for n in opened.notes)
    arrived = load_project(opened.root)
    assert not arrived.trusted and arrived.agent.level == "advise"


def test_a_stale_results_store_is_dropped_with_a_note(project, tmp_path):
    out = archive.write(project, tmp_path / "a.puzzle", Include(results="all"))

    def stale(items):
        items["results/store.json"] = json.dumps({"format": 1}).encode()
        info = json.loads(items["archive.json"])
        import hashlib

        info["members"]["results/store.json"] = hashlib.sha256(
            items["results/store.json"]
        ).hexdigest()
        items["archive.json"] = json.dumps(info).encode()

    opened = archive.read(_tampered(out.path, tmp_path / "s.puzzle", stale), tmp_path / "t")
    assert not (opened.root / "results").exists()
    assert any("older store format" in n for n in opened.notes)


def test_sizes_by_part(project):
    s = archive.sizes(project)
    assert s["model"] > 0 and set(s) == {"model", "results", "versions", "history"}


def test_the_gui_exports_and_opens_an_archive(project, tmp_path):
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from forms import fill
    from PySide6.QtWidgets import QApplication

    from puzzle.gui.archive_dialog import export_archive, open_archive
    from puzzle.gui.window import MainWindow

    app = QApplication.instance() or QApplication([])
    w = MainWindow(project)
    try:
        fill(w, results="none", versions=False, history=False)
        w.ask_save_path = lambda *a: str(tmp_path / "sent.puzzle")
        done = export_archive(w)
        assert done.path.exists()
        assert [label for label, _v in w.last_form.asks["results"].choices][0].startswith("None")
        assert archive.manifest(done.path)["include"]["results"] == "none"
        fill(w, name="Received", folder=str(tmp_path / "received"))
        opened = open_archive(w, str(done.path))
        assert opened.name == "Received" and w.session.root == opened.root
    finally:
        w.close()
        app.processEvents()


def test_the_cli_archives_and_unarchives(project, tmp_path, capsys):
    from puzzle.cli import main

    assert main(["archive", str(project), str(tmp_path / "x.puzzle"), "--results", "none",
                 "--no-history"]) == 0  # fmt: skip
    assert "Wrote x.puzzle" in capsys.readouterr().out
    assert main(["unarchive", str(tmp_path / "x.puzzle"), str(tmp_path / "y")]) == 0
    assert (tmp_path / "y" / "project.toml").exists()
