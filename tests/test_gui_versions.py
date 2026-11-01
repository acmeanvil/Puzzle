"""The Versions panel (ADR 0059): save one, filter, see what each held, restore one
and undo it, roll back to the last known good, and a new project from a version."""

import os
import shutil
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from forms import fill  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from puzzle.gui.window import MainWindow  # noqa: E402
from puzzle.model.schema import Study  # noqa: E402
from puzzle.project import versions  # noqa: E402

EXAMPLE = Path(__file__).parents[1] / "examples" / "cantilever"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path):
    root = tmp_path / "cantilever"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("results*", ".puzzle"))
    w = MainWindow(root)
    yield w
    w.close()


def _rename_study(window, new):
    study = window.session.project.of_type(Study)[0]
    window.session.submit({"command": "edit", "object": str(study.id),
                           "ops": [{"op": "set", "path": "name", "value": new}]})  # fmt: skip
    window.refresh()
    return study.id


def test_save_restore_undo_and_roll_back(window):
    a = window.actions_by_name
    assert a["save_version"].text() == "Save version…"
    fill(window, label="Baseline", note="as shipped", good=True)
    a["save_version"].trigger()
    window.open_versions()
    panel = window.versions
    assert [v.what for v in panel.shown] == ["Baseline"]
    assert "known good" in panel.detail.toPlainText()
    study = _rename_study(window, "Redesigned")
    fill(window, sure=True)
    assert window.roll_back_good()
    assert window.session.project.objects[study].name != "Redesigned"
    assert panel.shown[0].trigger == "before_restore"  # the state replaced, kept
    window.undo()  # takes the restore back
    assert window.session.project.objects[study].name == "Redesigned"
    fill(window, sure=False)  # asked, then cancelled: nothing restored
    assert not window.restore_version(1)


def test_filters_by_who_type_good_and_words(window):
    window.save_version("Mine", good=True)
    (window.session.root / "bar.step").write_bytes(
        (window.session.root / "bar.step").read_bytes() + b"\n")  # fmt: skip
    window.session = type(window.session)(window.session.root)  # opened again
    _rename_study(window, "Changed outside first")
    window.open_versions()
    panel = window.versions
    assert {v.trigger for v in panel.shown} == {"user", "opened"}
    panel.who.setCurrentIndex(panel.who.findData("you"))
    assert [v.what for v in panel.shown] == ["Mine"]
    panel.who.setCurrentIndex(0)
    panel.kind.setCurrentIndex(panel.kind.findData("opened"))
    assert [v.trigger for v in panel.shown] == ["opened"]
    assert panel.count.text() == "1 of 2"
    panel.kind.setCurrentIndex(0)
    panel.good.setChecked(True)
    assert [v.what for v in panel.shown] == ["Mine"]
    panel.good.setChecked(False)
    panel.search.setText("nothing like this")
    assert panel.shown == [] and panel.count.text() == "0 of 2"
    panel.search.setText("")
    panel.when.setCurrentIndex(panel.when.findData(0))  # today
    assert len(panel.shown) == 2


def test_a_new_project_from_a_version(window, tmp_path):
    old = window.save_version("Old")
    _rename_study(window, "Newer")
    window.open_versions()
    panel = window.versions
    item = next(panel.table.topLevelItem(i) for i in range(panel.table.topLevelItemCount())
                if panel.table.topLevelItem(i).text(3) == "Old")  # fmt: skip
    panel.table.setCurrentItem(item)
    fill(window, name="From old", folder=str(tmp_path / "from-old"), open=True)
    panel.copy()
    assert window.session.root == (tmp_path / "from-old").resolve()
    names = {s.name for s in window.session.project.of_type(Study)}
    assert "Newer" not in names
    assert versions.latest(window.session.root).trigger == "copied"
    assert old.number >= 1
