import shutil
from pathlib import Path

import pytest

from puzzle.cli import main

EXAMPLE = Path(__file__).parent.parent / "examples" / "cantilever"


@pytest.fixture
def project(tmp_path) -> Path:
    dest = tmp_path / "cantilever"
    shutil.copytree(EXAMPLE, dest, ignore=shutil.ignore_patterns("results", ".puzzle"))
    return dest


def test_studies(project, capsys):
    assert main(["studies", str(project)]) == 0
    out = capsys.readouterr().out
    assert "Tip load  [calculix]  stationary" in out
    assert "Modes  [calculix]  modal" in out


def test_not_a_project(tmp_path, capsys):
    assert main(["studies", str(tmp_path)]) == 1
    assert "not a Puzzle project" in capsys.readouterr().err


def test_unknown_study(project, capsys):
    assert main(["run", str(project), "--study", "Nope"]) == 1
    err = capsys.readouterr().err
    assert "no study named 'Nope'" in err and "Tip load" in err


def test_no_runs_yet(project, capsys):
    assert main(["runs", str(project)]) == 0
    assert "No runs yet." in capsys.readouterr().out
    assert main(["export", str(project), "--study", "Modes", str(project / "x.pvd")]) == 1
    assert "has no stored runs" in capsys.readouterr().err
