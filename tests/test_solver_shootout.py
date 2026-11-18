"""The solver shoot-out has a way in from every client (ADR 0011; ADR 0090 package 16,
ADR-7): `run_shootout` had no caller in `src/`. Now the session's `run_shootout` command
behind MCP `solver_shootout`, `puzzle run --shootout` and the GUI's Run shoot-out…; the
report is kept with the results.

The solves are stand-ins (`shootout.run_study` replaced): what is tested is the path,
the gate, the default metrics and the report; the solvers' agreement is the integration
benchmarks' (`test_cfd_shootout.py`, `test_benchmarks.py`).
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from puzzle import shootout
from puzzle.commands import Session
from puzzle.results.model import Field, Results, StepResult

EXAMPLE = Path(__file__).parent.parent / "examples" / "cantilever"


@pytest.fixture
def root(tmp_path) -> Path:
    dest = tmp_path / "cantilever"
    shutil.copytree(EXAMPLE, dest, ignore=shutil.ignore_patterns("results*", ".puzzle",
                                                                 "history.jsonl"))  # fmt: skip
    return dest


@pytest.fixture
def solves(monkeypatch):
    """Each solver's stand-in run: a displacement differing by 0.5 % on FEniCSx."""
    calls = []

    def fake(project, study_id, root, workdir, *, solver=None, meshed=None, **kwargs):
        name = solver or project.objects[study_id].solver
        calls.append(name)
        scale = 1.005 if name == "fenicsx" else 1.0
        ids = np.arange(1, 5)
        values = np.column_stack([np.zeros(4), -1e-3 * scale * ids, np.zeros(4)])
        field = Field("displacement", "node", ["x", "y", "z"], ids, values)
        results = Results(name, [StepResult(1, "stationary", 1.0, fields={"displacement": field})])
        return SimpleNamespace(results=results, run_id=f"run-{name}", cached=False,
                               mesh_result=object())  # fmt: skip

    monkeypatch.setattr(shootout, "run_study", fake)
    return calls


def test_the_session_runs_a_shootout_and_keeps_its_report(root, solves):
    s = Session(root)
    out = s.submit({"command": "run_shootout", "study": "Tip load", "solvers": ["fenicsx"]})
    report = out.run
    assert solves == ["calculix", "fenicsx"]
    (c,) = report["comparisons"]
    assert (c["quantity"], c["norm"], c["tolerance"]) == ("displacement", "relative_l2", 0.01)
    assert c["value"] == pytest.approx(0.005) and c["passed"] and report["passed"]
    assert report["runs"]["fenicsx"]["run_id"] == "run-fenicsx"
    assert "| fenicsx vs calculix | displacement |" in report["table"]
    kept = shootout.kept_report(root, report_study_id(s))
    assert kept["comparisons"] == report["comparisons"]
    entry = [e for e in s.history_entries() if e["type"] == "command"][-1]
    assert entry["command"]["command"] == "run_shootout"


def report_study_id(s: Session):
    from puzzle.model.schema import Study

    return next(st.id for st in s.project.of_type(Study) if st.name == "Tip load")


def test_a_shootout_needs_another_solver(root, solves):
    from puzzle.errors import PuzzleError

    with pytest.raises(PuzzleError) as err:
        Session(root).submit({"command": "run_shootout", "study": "Tip load"})
    assert err.value.code == "invalid" and not solves


def test_mcp_solver_shootout_is_a_gated_run(root, solves):
    pytest.importorskip("mcp")
    from puzzle.agent.mcp import build_server

    server = build_server(root, "test")  # the default level: propose
    result = asyncio.run(server.call_tool("solver_shootout", {"study": "Tip load",
                                                              "solvers": ["fenicsx"]}))  # fmt: skip
    out = result.structured_content.get("result", result.structured_content)
    assert out["status"] == "proposed" and not solves
    (p,) = Session(root).proposals()
    assert p["commands"][0]["command"] == "run_shootout"
    Session(root).accept(p["id"])  # the user's: solved
    assert solves == ["calculix", "fenicsx"]


def test_puzzle_run_shootout_prints_the_table(root, solves, capsys):
    from puzzle.cli import main

    code = main(["run", str(root), "--study", "Tip load", "--shootout", "fenicsx"])
    printed = capsys.readouterr().out
    assert code == 0 and "fenicsx vs calculix" in printed and "All within tolerance" in printed


def test_default_metrics_follow_the_physics_and_steps():
    import uuid

    from puzzle.model.schema import Component, Physics, Project, SolveStep, Study

    p = Project(name="p")
    comp = p.add(
        Component(
            name="C",
            parts=[{"name": "P", "geometry": uuid.uuid4()}],
            mesh=uuid.uuid4(),
            physics=[Physics(type="heat_transfer")],
        )
    )
    steady, modes = [SolveStep(type="stationary")], [SolveStep(type="modal")]
    heat = p.add(Study(name="H", component=comp.id, solver="calculix", steps=steady))
    modal = p.add(Study(name="M", component=comp.id, solver="calculix", steps=modes))
    assert [m.quantity for m in shootout.default_metrics(p, heat)] == ["temperature"]
    assert [m.quantity for m in shootout.default_metrics(p, modal)] == ["eigenfrequencies"]


def test_the_gui_form_runs_it_and_shows_the_table(root, solves):
    """Model → Run → Run shoot-out… and a study's right-click: one form, the session's
    command in the background, the table in Messages."""
    pytest.importorskip("PySide6")
    import os
    import time

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from conftest import delete_windows
    from forms import fill
    from PySide6.QtWidgets import QApplication

    from puzzle.gui import solver_shootout
    from puzzle.gui.window import MainWindow

    app = QApplication.instance() or QApplication([])
    w = MainWindow(root)
    try:
        study = w.session.project.objects[report_study_id(w.session)]
        assert "fenicsx" in [k for _, k in solver_shootout.others(w, study)]
        fill(w, first="fenicsx")
        assert solver_shootout.solver_shootout(w, str(study.id)) is not None
        end = time.monotonic() + 60
        while w.tasks and time.monotonic() < end:
            app.processEvents()
            time.sleep(0.02)
        app.processEvents()
        table = w.solver_shootout_table
        assert w.output_tabs.currentWidget() is table
        cells = [table.item(0, c).text() for c in range(table.columnCount())]
        assert cells[1:] == ["Displacement", "Relative difference, node by node (L2)",
                             "0.5 %", "1 %", "✓ agree"]  # fmt: skip
        kept = json.loads((root / "results" / "shootout" / f"{study.id}.json").read_text(
            encoding="utf-8"))  # fmt: skip
        assert kept["passed"] and solves == ["calculix", "fenicsx"]
    finally:
        delete_windows(app)
