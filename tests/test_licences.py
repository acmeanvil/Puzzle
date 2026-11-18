"""Third-party licences before any public installer (ADR 0068 decision 9; ADR 0090
package 16, ADR-57): gathered from the environments' own records by a script, shipped
in the installer as `<prefix>/licenses/`, listed by Help → Licences, with the GPL's
written offer of the source."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from puzzle import licences

ROOT = Path(__file__).parents[1]


def _env(root: Path, name: str, packages: list[tuple[str, str, bool]]) -> Path:
    """A stand-in conda environment: (name, licence, has a licence file) each."""
    prefix = root / name
    (prefix / "conda-meta").mkdir(parents=True)
    for pkg, licence, has_file in packages:
        extracted = root / "pkgs" / f"{pkg}-1.0-0"
        if has_file:
            (extracted / "info" / "licenses").mkdir(parents=True)
            (extracted / "info" / "licenses" / "LICENSE").write_text(f"{pkg} licence text",
                                                                      encoding="utf-8")  # fmt: skip
        record = {"name": pkg, "version": "1.0", "license": licence,
                  "channel": "https://conda.anaconda.org/conda-forge/",
                  "url": f"https://example/{pkg}-1.0-0.conda",
                  "extracted_package_dir": str(extracted)}  # fmt: skip
        (prefix / "conda-meta" / f"{pkg}-1.0-0.json").write_text(json.dumps(record),
                                                                 encoding="utf-8")  # fmt: skip
    return prefix


def test_the_folder_holds_each_packages_licence_and_the_offer(tmp_path):
    cx = [("calculix", "GPL-2.0-or-later", True), ("arpack", "BSD-3-Clause", False)]
    envs = {"puzzle": _env(tmp_path, "base", [("numpy", "BSD-3-Clause", True)]),
            "calculix": _env(tmp_path, "calculix", cx)}  # fmt: skip
    out = tmp_path / "licenses"
    index = licences.write(envs, out)
    rows = {e["name"]: e for env in index["environments"].values() for e in env}
    assert rows["calculix"]["licence"] == "GPL-2.0-or-later"
    assert rows["calculix"]["files"] == ["calculix/calculix/LICENSE"]
    assert (out / "calculix" / "calculix" / "LICENSE").read_text(encoding="utf-8") == (
        "calculix licence text"
    )
    assert rows["arpack"]["files"] == [] and index["missing"] == 1  # named, no text
    assert "section 6(b)" in (out / "SOURCE-OFFER.txt").read_text(encoding="utf-8")
    assert "GNU GENERAL PUBLIC LICENSE" in (out / "PUZZLE-LICENSE.txt").read_text(encoding="utf-8")
    assert json.loads((out / "index.json").read_text(encoding="utf-8")) == index


def test_the_script_reads_this_checkouts_environments(tmp_path):
    """The real environments here: every package of the dev environment, most with
    their licence text (the installer's are gathered the same way)."""
    if not (ROOT / ".pixi" / "envs" / "dev" / "conda-meta").is_dir():
        pytest.skip("no pixi environments in this checkout")
    sys.path.insert(0, str(ROOT / "scripts"))
    import collect_licences

    assert collect_licences.main([str(tmp_path / "out"), "--env", "dev"]) == 0
    index = json.loads((tmp_path / "out" / "index.json").read_text(encoding="utf-8"))
    dev = index["environments"]["dev"]
    names = {e["name"] for e in dev}
    assert {"python", "numpy", "pyside6"} <= names
    with_text = sum(bool(e["files"]) for e in dev)
    assert with_text > 0.8 * len(dev)
    assert all(e["licence"] for e in dev if e["name"] in ("python", "numpy"))


def test_the_installer_ships_the_folder(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    import make_installer

    base = _env(tmp_path, "base", [("numpy", "BSD-3-Clause", True)])
    folder = licences.write({"puzzle": base}, tmp_path / "lic")
    out = tmp_path / "out"
    (out / "linux-64").mkdir(parents=True)
    files = make_installer._licence_files(tmp_path / "lic", out / "linux-64")
    assert {"licenses/index.json": "licenses/index.json"} in files
    assert {"licenses/SOURCE-OFFER.txt": "licenses/SOURCE-OFFER.txt"} in files
    assert (out / "linux-64" / "licenses" / "puzzle" / "numpy" / "LICENSE").is_file()
    assert folder["missing"] == 0
    text = make_installer._yaml({"extra_files": files})
    assert '- {"licenses/index.json": "licenses/index.json"}' in text


def test_help_licences_lists_what_puzzle_carries(tmp_path, monkeypatch):
    """From an installed folder when there is one (`<prefix>/licenses`), each package's
    texts shown on selection, the offer on the page."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from puzzle.gui.licences_dialog import LicencesDialog

    app = QApplication.instance() or QApplication([])
    prefix = tmp_path / "prefix"
    licences.write({"puzzle": _env(tmp_path, "base", [("numpy", "BSD-3-Clause", True)]),
                    "calculix": _env(tmp_path, "cx", [("calculix", "GPL-2.0-or-later", True)])},
                   prefix / "licenses")  # fmt: skip
    monkeypatch.setattr(sys, "prefix", str(prefix))
    dialog = LicencesDialog()
    try:
        assert [r.name for r in dialog.rows] == ["numpy", "calculix"]
        dialog.table.selectRow(1)
        assert "calculix licence text" in dialog.text.toPlainText()
        assert "section 6(b)" in dialog.offer.toPlainText()
        dialog.filter.setText("gpl")
        assert dialog.table.isRowHidden(0) and not dialog.table.isRowHidden(1)
    finally:
        dialog.deleteLater()
        app.processEvents()
