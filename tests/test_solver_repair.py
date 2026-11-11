"""Repair a solver environment (ADR 0068 decision 6): the installed app's environment is
rebuilt by its own conda from the package list the installer left in it; the old folder
is put back when conda fails; a pixi checkout says how to repair there instead. Conda is
a stand-in script here (it writes a marker where it was asked to create)."""

import os
import sys

import pytest

from puzzle import solver_repair as sr


def _app(tmp_path, conda_body: str):
    base = tmp_path / "app"
    env = base / "envs" / "calculix"
    (env / "conda-meta").mkdir(parents=True)
    (env / sr.LIST).write_text("@EXPLICIT\nhttps://example/a.conda#00\nhttps://example/b.conda#11\n",
                               encoding="utf-8")  # fmt: skip
    (env / "old-file").write_text("old", encoding="utf-8")
    script = tmp_path / "fake_conda.py"
    script.write_text(conda_body, encoding="utf-8")
    if os.name == "nt":
        exe = base / "Scripts" / "conda.exe"
        exe.parent.mkdir(parents=True)
        launcher = base / "Scripts" / "conda.bat"
        launcher.write_text(f'@"{sys.executable}" "{script}" %*\n', encoding="utf-8")
        exe.write_text("", encoding="utf-8")  # found by name; run through the .bat below
    else:
        exe = base / "bin" / "conda"
        exe.parent.mkdir(parents=True)
        exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        exe.chmod(0o755)
    return base, env


GOOD = """import sys, pathlib
p = pathlib.Path(sys.argv[sys.argv.index("--prefix") + 1])
(p / "conda-meta").mkdir(parents=True, exist_ok=True)
(p / "new-file").write_text("new")
"""
BAD = "import sys\nprint('PackagesNotFoundError: a.conda', file=sys.stderr)\nsys.exit(1)\n"


def _point_at_bat(monkeypatch, base):
    if os.name == "nt":  # a .bat stands in for conda.exe
        monkeypatch.setattr(sr, "conda", lambda prefix=None: base / "Scripts" / "conda.bat")


def test_an_environment_is_rebuilt_from_its_package_list(tmp_path, monkeypatch):
    base, env = _app(tmp_path, GOOD)
    _point_at_bat(monkeypatch, base)
    assert [(e.name, e.packages) for e in sr.environments(base)] == [("calculix", 2)]
    lines = []
    sr.repair("calculix", say=lines.append, prefix=base)
    assert (env / "new-file").exists() and not (env / "old-file").exists()
    assert (env / sr.LIST).exists()  # kept for the next repair
    assert not list(base.glob("envs/.calculix*"))  # nothing left aside


def test_a_failed_repair_puts_the_old_one_back(tmp_path, monkeypatch):
    base, env = _app(tmp_path, BAD)
    _point_at_bat(monkeypatch, base)
    with pytest.raises(sr.RepairError, match="left as it was"):
        sr.repair("calculix", say=lambda _: None, prefix=base)
    assert (env / "old-file").read_text(encoding="utf-8") == "old"


def test_a_checkout_says_how_to_repair_there(tmp_path):
    assert "pixi install" in sr.why_not(tmp_path)
