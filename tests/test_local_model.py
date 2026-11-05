"""Choosing the local expert model by measurement (ADR 0022): models found on the
endpoint, fitted to the machine's memory, measured by the evaluation suite, ranked and
recommended; the domain pack searched; agent changes recorded with their model."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from puzzle.agent import evals, knowledge, pick

GB = 1024**3
TAGS = {"models": [
    {"name": "tiny:1b", "size": 1 * GB, "details": {"quantization_level": "Q4_K_M",
                                                     "parameter_size": "1B"}},
    {"name": "mid:8b", "size": 5 * GB, "details": {"quantization_level": "Q4_K_M",
                                                    "parameter_size": "8B"}},
    {"name": "big:70b", "size": 40 * GB, "details": {"quantization_level": "Q4_K_M",
                                                      "parameter_size": "70B"}},
]}  # fmt: skip


@pytest.fixture(autouse=True)
def _fresh_results():
    """Each test measures afresh: kept results live in the (shared) user folder."""
    import shutil

    shutil.rmtree(pick.results_dir(), ignore_errors=True)
    yield
    shutil.rmtree(pick.results_dir(), ignore_errors=True)


@pytest.fixture
def ollama():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server's name
            body = json.dumps(TAGS if self.path == "/api/tags" else {}).encode()
            self.send_response(200 if self.path == "/api/tags" else 404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/v1"
    server.shutdown()


def _fake_suite(monkeypatch, passes):
    """run_task faked: `passes[model]` is the set of task names that model passes."""
    calls = []

    def run_task(task, provider, workdir):
        calls.append((provider.model, task.name))
        ok = task.name in passes[provider.model]
        seconds = {"tiny:1b": 2.0, "mid:8b": 6.0}.get(provider.model, 9.0)
        return evals.Outcome(task.name, ok, "" if ok else "wrong", seconds, 3)

    monkeypatch.setattr(evals, "run_task", run_task)
    return calls


def test_models_are_found_fitted_measured_and_ranked(ollama, monkeypatch, tmp_path):
    monkeypatch.setattr(pick, "budget", lambda: 16 * GB)
    every = {t.name for t in evals.TASKS + evals.HARD_TASKS}
    core = {t.name for t in evals.TASKS + evals.HARD_TASKS if t.family in evals.CORE_FAMILIES}
    calls = _fake_suite(monkeypatch, {"tiny:1b": {"describe"}, "mid:8b": every})
    found = pick.pick(ollama, tmp_path, solvers=("calculix",))
    big = next(c for c in found.candidates if c.name == "big:70b")
    assert not big.fits and "GB" in big.why  # 40 GB × context past 16 GB: not tried
    assert {m for m, _t in calls} == {"tiny:1b", "mid:8b"}
    assert [m.model for m in found.ranked] == ["mid:8b", "tiny:1b"]
    assert found.recommended == "mid:8b" and "every core one" in found.words
    assert found.ranked[0].core_passed == found.ranked[0].core_total == len(core)
    # Kept per model and suite version: a second pick measures nothing again.
    calls.clear()
    again = pick.pick(ollama, tmp_path, solvers=("calculix",))
    assert calls == [] and again.recommended == "mid:8b"


def test_the_smallest_at_the_best_score_and_remote_advice(ollama, monkeypatch, tmp_path):
    monkeypatch.setattr(pick, "budget", lambda: 16 * GB)
    every = {t.name for t in evals.TASKS + evals.HARD_TASKS}
    _fake_suite(monkeypatch, {"tiny:1b": every, "mid:8b": every})
    assert pick.pick(ollama, tmp_path, solvers=("calculix",)).recommended == "tiny:1b"
    for f in pick.results_dir().glob("*.json"):
        f.unlink()
    _fake_suite(monkeypatch, {"tiny:1b": {"describe"}, "mid:8b": {"describe"}})
    weak = pick.pick(ollama, tmp_path, solvers=("calculix",))
    assert weak.recommended is None and "Claude" in weak.words
    monkeypatch.setattr(pick, "budget", lambda: 0)
    none_fit = pick.pick(ollama, tmp_path)
    assert none_fit.recommended is None and "fits" in none_fit.words


def test_the_choice_goes_in_agent_toml(monkeypatch):
    from puzzle.agent.settings import load_settings

    pick.choose("mid:8b", "http://localhost:11434/v1")
    s = load_settings()
    assert (s.provider, s.model) == ("openai", "mid:8b")
    assert s.picked.endswith(f"suite {evals.SUITE_VERSION}")


def test_every_task_has_a_family_and_the_core_ones_are_covered():
    families = {t.family for t in evals.TASKS + evals.HARD_TASKS}
    assert families >= evals.CORE_FAMILIES
    assert families <= set(evals.FAMILIES)
    for t in evals.TASKS + evals.HARD_TASKS:
        assert t.difficulty in ("easy", "hard"), t.name


def test_the_domain_pack_answers_and_cites():
    hits = knowledge.search("Code_Aster friction quadratic tetrahedra singular", 3)
    assert hits and any("Code_Aster" in h["text"] for h in hits)
    assert all(h["cite"] and h["source"] for h in hits)
    french = knowledge.search("Le nombre maximum d'itérations de Newton est atteint", 2)
    assert "Newton iterations" in french[0]["text"]
    caps = knowledge.search("Elmer capabilities contacts", 3)
    assert "capabilities Elmer" in [h["source"] for h in caps]
    sources = {p.source.split()[0] for p in knowledge.passages()}
    assert {"skill", "note", "capabilities", "translations"} <= sources


def test_an_embedder_reranks_the_lexical_hits():
    def embed(texts):  # a fake meaning: whether the text speaks of magnets
        return [[1.0 if "magnet" in t.lower() else 0.0, 1.0] for t in texts]

    hits = knowledge.search("eddy currents coils losses magnet", 3, embed=embed)
    assert all("magnet" in h["text"].lower() for h in hits)


def test_agent_changes_name_their_model(tmp_path):
    from puzzle.commands import Session
    from puzzle.commands.session import Actor
    from puzzle.model.schema import Project
    from puzzle.project import save_project

    save_project(Project("p"), tmp_path)
    s = Session(tmp_path)
    s.submit({"command": "set_autonomy", "level": "act_in_budget"})
    agent = Actor("agent", "built-in", model="qwen3:8b", suite=evals.SUITE_VERSION)
    s.submit({"command": "create", "kind": "parameters", "data": {"name": "P"}}, agent)
    entry = [e for e in s.history_entries() if e["type"] == "command"][-1]
    assert entry["actor"] == {"kind": "agent", "name": "built-in", "model": "qwen3:8b",
                              "suite": evals.SUITE_VERSION}  # fmt: skip


def test_the_cli_and_mcp_reach_it(ollama, monkeypatch, tmp_path, capsys):
    from puzzle.cli import main

    monkeypatch.setattr(pick, "budget", lambda: 16 * GB)
    every = {t.name for t in evals.TASKS + evals.HARD_TASKS}
    _fake_suite(monkeypatch, {"tiny:1b": every, "mid:8b": every})
    assert main(["agent", "pick", "--base-url", ollama, "--workdir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "tiny:1b" in out and "Recommended" in out
    pytest.importorskip("mcp")
    import asyncio

    from puzzle.agent.mcp import build_server
    from puzzle.model.schema import Project
    from puzzle.project import save_project

    save_project(Project(name="p"), tmp_path / "p")
    server = build_server(tmp_path / "p", "test")
    result = asyncio.run(server.call_tool("knowledge", {"query": "bolt pretension section"}))
    hits = result.structured_content.get("result", result.structured_content)["hits"]
    assert hits and hits[0]["cite"]


def test_the_new_families_checks(tmp_path):
    """The sketch task passes a right plate and fails a wrong one; the unsafe-request
    task passes when nothing changed."""
    pytest.importorskip("OCP")
    import shutil

    from puzzle.commands import Session
    from puzzle.commands.model import Create

    root = tmp_path / "c"
    shutil.copytree(evals.EXAMPLE, root, ignore=shutil.ignore_patterns("results", ".puzzle"))
    s = Session(root)
    s.submit({"command": "set_autonomy", "level": "act_in_budget"})
    assert not evals.check_sketch(root, "")[0]
    profile = {"points": [[0, 0], [0.04, 0], [0.04, 0.02], [0, 0.02]]}
    step = {"kind": "extrude", "name": "E", "args": {"profile": profile, "distance": 0.005}}
    s.submit(Create(kind="geometry", data={"name": "Plate", "history": [step]}))
    ok, why = evals.check_sketch(root, "done")
    assert ok, why
    assert evals.check_unsafe(root, "I can't raise my own autonomy.")[0]


def test_the_pick_dialog_measures_and_uses_the_choice(ollama, monkeypatch, tmp_path):
    import os
    import time

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from puzzle.agent.settings import load_settings
    from puzzle.gui.pick_dialog import PickDialog
    from puzzle.gui.window import MainWindow

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(pick, "budget", lambda: 16 * GB)
    every = {t.name for t in evals.TASKS + evals.HARD_TASKS}
    _fake_suite(monkeypatch, {"tiny:1b": every, "mid:8b": {"describe"}})
    w = MainWindow()
    try:
        dialog = PickDialog(w)
        dialog.base_url.setText(ollama)
        dialog.measure()
        end = time.monotonic() + 20
        while dialog.found is None and time.monotonic() < end:
            app.processEvents()
            time.sleep(0.02)
        assert dialog.found.recommended == "tiny:1b" and dialog.use.isEnabled()
        assert "tiny:1b" in dialog.log.toPlainText()
        dialog.use_it()
        assert load_settings().model == "tiny:1b"
    finally:
        w.close()
