"""CalculiX with PARDISO built on the user's machine (ADR 0068 step 4, ADR 0051).

Without compiling: the source lists come from CalculiX's own Makefile.inc; the build's
state reads as ready, building, failed or built from its environment and the state file
in the user's folder (a build left running for hours counts as died); the toolchain is
found in the environment's folders; the new program goes in place even where the old one
is running (renamed aside, as Windows allows); the background build waits a day after a
failure and honours its preference; and the Settings dialog's Solvers page lists the
solvers. The compile itself is `tests/integration/test_pardiso_build.py`.
"""

import json
import os
import time
from pathlib import Path

import pytest

from puzzle import solver_builds as sb


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A fake calculix-pardiso environment."""
    prefix = tmp_path / "envs" / sb.ENV
    sb.bin_dir(prefix).mkdir(parents=True)
    monkeypatch.setattr(sb, "prefix", lambda: prefix)
    sb._state_file().unlink(missing_ok=True)  # the user folder is shared by the session
    yield prefix
    sb._state_file().unlink(missing_ok=True)


def test_the_sources_come_from_makefile_inc(tmp_path):
    (tmp_path / "Makefile.inc").write_text(
        "SCCXF = \\\na.f \\\nb.f\n\nSCCXC = \\\nc.c \\\nb.c \\\nd.c \\\ngone.c\n\n"
        "SCCXCXX = umat_dl.cpp\n",
        encoding="latin-1",
    )
    for f in ("a.f", "b.f", "c.c", "d.c"):
        (tmp_path / f).write_text("", encoding="utf-8")
    # b.c is listed beside b.f and absent (as 2.23's mafillmm); gone.c is absent too.
    assert sb.sources(tmp_path) == (["a.f", "b.f"], ["c.c", "d.c"])


def test_the_states(env):
    assert sb.status().state == "ready"
    sb._write_state(state="building", started=time.time())
    assert sb.status().state == "building"
    sb._write_state(state="building", started=time.time() - sb.STALE_S - 1)  # it died
    assert sb.status().state == "ready"
    # A build whose process has gone (Puzzle closed, 2026-11-10) is over at once.
    import os
    import subprocess
    import sys

    gone = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"],
                          capture_output=True, text=True, check=True)  # fmt: skip
    sb._write_state(state="building", started=time.time(), pid=int(gone.stdout))
    assert sb.status().state == "ready"
    sb._write_state(state="building", started=time.time(), pid=os.getpid())
    assert sb.status().state == "building"
    sb._write_state(state="failed", finished=time.time(), error="no gfortran")
    assert sb.status() == sb.Status("failed", "no gfortran")
    sb.program(env).write_bytes(b"")
    built = sb.status()
    assert built.state == "built" and built.program == str(sb.program(env))


def test_no_environment_is_unavailable(monkeypatch):
    monkeypatch.setattr(sb, "prefix", lambda: None)
    assert sb.status().state == "unavailable"


def test_the_toolchain_is_found_in_the_environment(env):
    exe = ".exe" if os.name == "nt" else ""
    folder = sb.bin_dir(env)
    if os.name != "nt":
        names = ["x86_64-conda-linux-gnu-gcc", "gfortran", "x86_64-conda-linux-gnu-ar"]
    else:
        names = [f"gcc{exe}", f"gfortran{exe}", f"gcc-ar{exe}"]
    if os.uname().sysname == "Darwin" if hasattr(os, "uname") else False:
        names[0] = "clang"
    for n in names:
        (folder / n).write_bytes(b"")
    tools = sb.toolchain(env)
    assert Path(tools.cc).name == names[0] and Path(tools.fc).name == names[1]
    assert "-DPARDISO" in tools.cflags
    assert ("-lmkl_rt" in tools.link) == (os.name == "nt")
    # gcc refuses clang's warning flag; clang takes it.
    assert ("-Wno-error=incompatible-function-pointer-types" in tools.cflags) == (
        "clang" in names[0])  # fmt: skip


def test_a_missing_compiler_is_said(env):
    with pytest.raises(sb.BuildError, match="gfortran"):
        (sb.bin_dir(env) / ("gcc.exe" if os.name == "nt" else "gcc")).write_bytes(b"")
        sb.toolchain(env)


def test_the_new_program_goes_in_place_over_the_old(tmp_path):
    target = tmp_path / ("ccx.exe" if os.name == "nt" else "ccx")
    target.write_text("old", encoding="utf-8")
    built = tmp_path / "built"
    built.write_text("new", encoding="utf-8")
    sb._install(built, target)
    assert target.read_text(encoding="utf-8") == "new"
    assert not target.with_name(target.name + ".new").exists()


def test_the_background_build_waits_and_asks(env):
    from puzzle.settings import current

    assert sb.should_build()  # ready, preference on
    current().set("solvers.build_pardiso", False)
    try:
        assert not sb.should_build()
    finally:
        current().set("solvers.build_pardiso", True)
    sb._write_state(state="failed", finished=time.time(), error="offline")
    assert not sb.should_build()  # failed just now
    sb._write_state(state="failed", finished=time.time() - 25 * 3600, error="offline")
    assert sb.should_build()  # a day later: the download may work now
    state = json.loads(sb._state_file().read_text(encoding="utf-8"))
    assert state["error"] == "offline"


def test_the_settings_solvers_page_lists_the_solvers(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from puzzle.gui import solvers_panel
    from puzzle.gui.settings_dialog import SettingsDialog

    rows = [sb.SolverRow("calculix", "CalculiX", "native", "/env/bin/ccx", "PARDISO"),
            sb.SolverRow("elmer", "Elmer", "missing", "'ElmerSolver' not found")]  # fmt: skip
    monkeypatch.setattr(sb, "solvers", lambda: rows)
    monkeypatch.setattr(sb, "status", lambda: sb.Status("built", "CalculiX 2.23 with PARDISO"))
    monkeypatch.setitem(solvers_panel._STATE, "rows", None)
    dialog = SettingsDialog()
    panel = dialog.solvers
    names = [dialog.groups.item(i).text() for i in range(dialog.groups.count())]
    dialog.groups.setCurrentRow(names.index("Solvers"))
    dialog.show()
    deadline = time.time() + 5
    while panel.list.rowCount() < 2 and time.time() < deadline:
        app.processEvents()
        panel._show()
    assert panel.list.rowCount() == 2
    assert "PARDISO" in panel.list.itemAt(0, panel.list.ItemRole.FieldRole).widget().text()
    assert panel.pardiso.text().startswith("Built")
    assert panel.build.text() == "Build it again"
    dialog.close()


def test_mcp_lists_the_solvers(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    import asyncio

    from puzzle.agent.mcp import build_server
    from puzzle.model.schema import Project
    from puzzle.project import save_project

    save_project(Project(name="p"), tmp_path)
    rows = [sb.SolverRow("calculix", "CalculiX", "native", "/env/bin/ccx", "PARDISO")]
    monkeypatch.setattr(sb, "solvers", lambda: rows)
    monkeypatch.setattr(sb, "status", lambda: sb.Status("unavailable", "no MKL here"))
    server = build_server(tmp_path)
    out = asyncio.run(server.call_tool("solvers_list", {})).structured_content
    assert out["solvers"][0]["note"] == "PARDISO"
    assert out["pardiso"]["state"] == "unavailable"
    started = asyncio.run(server.call_tool("pardiso_build", {})).structured_content
    assert started["started"] is False  # nothing to build with here
