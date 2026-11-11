"""`puzzle run`, `runs`, `show` and `export` on the cantilever example, with real CalculiX."""

import shutil
from pathlib import Path

import pytest

from puzzle.cli import main
from puzzle.jobs import runtime_for

EXAMPLE = Path(__file__).parents[2] / "examples" / "cantilever"

pytestmark = pytest.mark.skipif(
    not runtime_for("calculix").available("ccx"),
    reason="CalculiX not available (install ccx, or build its image and set PUZZLE_RUNTIME)",
)


def test_run_reuse_list_and_export(tmp_path, capsys):
    project = tmp_path / "cantilever"
    shutil.copytree(
        EXAMPLE, project, ignore=shutil.ignore_patterns("results*", ".puzzle", "history.jsonl")
    )

    assert main(["run", str(project), "--study", "Tip load"]) == 0
    out = capsys.readouterr().out
    assert "Solved in" in out and "max |displacement| 0.0015" in out  # about 1.6 mm

    assert main(["run", str(project), "--study", "Tip load"]) == 0
    assert "reused its results" in capsys.readouterr().out

    assert main(["run", str(project), "--study", "Modes"]) == 0
    assert "mode 1: 20" in capsys.readouterr().out  # about 204 Hz

    assert main(["runs", str(project)]) == 0
    listed = capsys.readouterr().out.strip().splitlines()
    assert len(listed) == 2 and "Tip load" in listed[0] and "Modes" in listed[1]

    assert main(["export", str(project), "--study", "Modes", str(tmp_path / "modes.pvd")]) == 0
    assert (tmp_path / "modes.pvd").exists() and (tmp_path / "modes_005.vtu").exists()

    assert (
        main(
            [
                "show",
                str(project),
                "--study",
                "Tip load",
                "--field",
                "nope",
                "--screenshot",
                str(tmp_path / "x.png"),
            ]
        )
        == 1
    )
    assert "no 'nope'" in capsys.readouterr().err


def test_paraview_writes_the_view_of_a_run(tmp_path, capsys):
    """`puzzle paraview` (ADR 0070): the run's step and the quantity coloured, as `show`
    picks them, written for ParaView with its view script; --no-launch starts nothing."""
    import pyvista as pv

    project = tmp_path / "cantilever"
    shutil.copytree(
        EXAMPLE, project, ignore=shutil.ignore_patterns("results*", ".puzzle", "history.jsonl")
    )
    assert main(["run", str(project), "--study", "Tip load"]) == 0
    assert main(["paraview", str(project), "--study", "Tip load", "--scalar",
                 "von_mises", "--no-launch"]) == 0  # fmt: skip
    folders = list((project / "exports" / "paraview").iterdir())
    assert len(folders) == 1
    shown = pv.read(folders[0] / "shown.vtu")
    assert {"von_mises", "displacement"} <= set(shown.point_data)
    script = (folders[0] / "view.py").read_text(encoding="utf-8")
    assert '"array": "von_mises"' in script and "series.pvd" in script
    assert main(["paraview", str(project), "--study", "Tip load", "--scalar", "nope",
                 "--no-launch"]) == 1  # fmt: skip
    out = capsys.readouterr()
    assert "no 'nope' to show" in out.out + out.err
