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
    shutil.copytree(EXAMPLE, project, ignore=shutil.ignore_patterns("results", ".puzzle"))

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
