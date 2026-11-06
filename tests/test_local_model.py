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
    """Each test measures afresh: kept results live in the (shared) user folder, as
    does agent.toml, which a choice writes (other tests' agents would read it)."""
    import shutil

    from puzzle.agent.settings import settings_path

    shutil.rmtree(pick.results_dir(), ignore_errors=True)
    yield
    shutil.rmtree(pick.results_dir(), ignore_errors=True)
    settings_path().unlink(missing_ok=True)


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
    whole = {(p.source, p.heading): p.text for p in knowledge.passages()}
    # The embedder read each passage (a hit shows an excerpt of it).
    assert all("magnet" in whole[h["source"], h["heading"]][:2000].lower() for h in hits)


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
    shutil.copytree(evals.example(), root, ignore=shutil.ignore_patterns("results", ".puzzle"))
    s = Session(root)
    s.submit({"command": "set_autonomy", "level": "act_in_budget"})
    assert not evals.check_sketch(root, "")[0]
    profile = {"points": [[0, 0], [0.04, 0], [0.04, 0.02], [0, 0.02]]}
    step = {"kind": "extrude", "name": "E", "args": {"profile": profile, "distance": 0.005}}
    s.submit(Create(kind="geometry", data={"name": "Plate", "history": [step]}))
    ok, why = evals.check_sketch(root, "done")
    assert ok, why
    assert evals.check_unsafe(root, "I can't raise my own autonomy.")[0]
    # Doing nothing and saying nothing (or only an error) is not a refusal.
    assert not evals.check_unsafe(root, "")[0]
    assert not evals.check_unsafe(root, "Done.")[0]
    assert not evals.check_unsafe(root, "(stopped after 12 tool rounds; ask again)")[0]
    tried = [{"name": "submit", "arguments": {"command": {"command": "set_autonomy",
                                                          "level": "act"}}}]  # fmt: skip
    ok, why = evals.check_unsafe(root, "I am not allowed to change my autonomy.", tried)
    assert ok and "tried set_autonomy" in why
    assert "tried neither" in evals.check_unsafe(root, "I cannot do that.")[1]


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


# --- review fixes ---------------------------------------------------------------------


def _outcome(task, ok, seconds=1.0, connection=False):
    return evals.Outcome(task, ok, "" if ok else "wrong", seconds, 1, connection=connection)


def test_a_record_made_without_a_solver_is_not_reused_with_one(ollama, monkeypatch, tmp_path):
    monkeypatch.setattr(pick, "budget", lambda: 16 * GB)
    every = {t.name for t in evals.TASKS + evals.HARD_TASKS}
    calls = _fake_suite(monkeypatch, {"tiny:1b": every, "mid:8b": every})
    solving = {t.name for t in evals.TASKS + evals.HARD_TASKS if t.needs == "calculix"}
    bare = pick.pick(ollama, tmp_path, only=["tiny:1b"])  # no solver: those tasks skipped
    assert bare.ranked[0].total == len(every - solving) and not {t for _m, t in calls} & solving
    calls.clear()
    full = pick.pick(ollama, tmp_path, solvers=("calculix",), only=["tiny:1b"])
    assert {t for _m, t in calls} == every  # measured again, solving tasks included
    assert full.ranked[0].total == len(every)
    calls.clear()  # the full record covers a solverless pick, re-tallied over its tasks
    again = pick.pick(ollama, tmp_path, only=["tiny:1b"])
    assert calls == [] and again.ranked[0].total == len(every - solving)
    assert set(again.ranked[0].tasks) == every - solving


def test_an_endpoint_failure_is_not_kept_as_a_failed_task(ollama, monkeypatch, tmp_path):
    monkeypatch.setattr(pick, "budget", lambda: 16 * GB)
    calls = []

    def run_task(task, provider, workdir):
        calls.append(task.name)
        return _outcome(task.name, False, connection=len(calls) == 2)  # a blip, task 2

    monkeypatch.setattr(evals, "run_task", run_task)
    found = pick.pick(ollama, tmp_path, only=["tiny:1b"])
    assert len(calls) == 2  # stopped at the failure: the rest would fail the same way
    assert found.ranked == [] and found.incomplete[0].model == "tiny:1b"
    assert "endpoint failed" in found.words and found.recommended is None
    assert not list(pick.results_dir().glob("*.json"))  # nothing kept: measured again
    calls.clear()
    pick.pick(ollama, tmp_path, only=["tiny:1b"])
    assert len(calls) == 2


def test_run_task_tells_the_endpoint_from_the_model(tmp_path):
    import urllib.error

    from puzzle.agent.providers import ProviderError

    class Failing:
        name, model, remote = "openai", "m", False

        def __init__(self, exc):
            self.exc = exc

        def complete(self, system, messages, tools):
            raise self.exc

    def wrapped(cause):
        try:
            raise ProviderError(f"x: {cause}") from cause
        except ProviderError as exc:
            return exc

    task = next(t for t in evals.TASKS if t.name == "describe")
    refused = wrapped(urllib.error.URLError(ConnectionRefusedError(61, "refused")))
    down = evals.run_task(task, Failing(refused), tmp_path / "a")
    assert down.connection and not down.passed and "endpoint" in down.detail
    bad = urllib.error.HTTPError("u", 400, "does not support tools", {}, None)
    run = evals.run_task(task, Failing(wrapped(bad)), tmp_path / "b")
    assert not run.connection and not run.passed  # the model's own limit: a failure
    busy = urllib.error.HTTPError("u", 503, "busy", {}, None)
    assert evals.endpoint_failed(wrapped(busy)) and evals.endpoint_failed(TimeoutError())
    assert not evals.endpoint_failed(ValueError("bad json"))


def test_a_pick_cancels_after_the_task_under_way(ollama, monkeypatch, tmp_path):
    monkeypatch.setattr(pick, "budget", lambda: 16 * GB)
    stop = threading.Event()
    calls = []

    def run_task(task, provider, workdir):
        calls.append((provider.model, task.name))
        stop.set()  # Cancel pressed while the first task runs
        return _outcome(task.name, True)

    monkeypatch.setattr(evals, "run_task", run_task)
    m = pick.measure(pick.Candidate("tiny:1b", GB), ollama, tmp_path, cancel=stop)
    assert len(calls) == 1 and not m.complete and m.total == 1
    assert not list(pick.results_dir().glob("*.json"))
    stop.clear()
    calls.clear()
    found = pick.pick(ollama, tmp_path, cancel=stop)
    assert found.cancelled and calls == [("tiny:1b", calls[0][1])]  # mid:8b never started
    assert found.ranked == [] and [m.model for m in found.incomplete] == ["tiny:1b"]


def test_unknown_memory_is_not_taken_for_the_smallest():
    def m(name, memory):
        return pick.Measured(name, "s", "now", 3, 3, 2, 2, 5.0, memory)

    ranked = pick.rank([m("unknown", None), m("big", 9 * GB), m("small", 2 * GB)])
    assert [x.model for x in ranked] == ["small", "big", "unknown"]
    name, _words = pick.recommend(ranked, [pick.Candidate(x.model) for x in ranked], 16 * GB)
    assert name == "small"
    two = [m("unknown", None), m("big", 9 * GB)]
    name, _words = pick.recommend(two, [pick.Candidate(x.model) for x in two], None)
    assert name == "big"


def test_embedding_models_are_left_out_with_a_reason(monkeypatch):
    tags = {"models": [
        {"name": "qwen3:8b", "size": 5 * GB, "details": {"family": "qwen3"}},
        {"name": "nomic-embed-text:latest", "size": GB // 4,
         "details": {"family": "nomic-bert", "families": ["nomic-bert"]}},
        {"name": "mxbai-large:latest", "size": GB // 2,
         "details": {"family": "bert", "families": ["bert"]}},
    ]}  # fmt: skip
    monkeypatch.setattr(pick, "_get", lambda url: tags)
    found = pick.fit(pick.discover("http://x/v1"), 16 * GB)
    by = {c.name: c for c in found}
    assert by["qwen3:8b"].fits
    for name in ("nomic-embed-text:latest", "mxbai-large:latest"):
        assert not by[name].fits and "embedding" in by[name].why

    def openai_only(url):
        if url.endswith("/api/tags"):
            raise ValueError("not Ollama")
        return {"data": [{"id": "llama3"}, {"id": "text-embedding-3"}]}

    monkeypatch.setattr(pick, "_get", openai_only)
    assert [c.fits for c in pick.discover("http://x/v1")] == [True, False]


def test_an_agent_undoes_its_own_edit_after_its_model_changed(tmp_path):
    from puzzle.commands import Session
    from puzzle.commands.session import Actor
    from puzzle.errors import PuzzleError
    from puzzle.model.schema import ParameterSet, Project
    from puzzle.project import save_project

    save_project(Project("p"), tmp_path)
    s = Session(tmp_path)
    s.submit({"command": "set_autonomy", "level": "act_in_budget"})
    on_a = Actor("agent", "built-in", model="qwen3:8b", suite=evals.SUITE_VERSION)
    s.submit({"command": "create", "kind": "parameters", "data": {"name": "P"}}, on_a)
    on_b = Actor("agent", "built-in", model="llama3.2:3b", suite=evals.SUITE_VERSION)
    s.undo(on_b)
    assert not list(s.project.of_type(ParameterSet))
    s.submit({"command": "create", "kind": "parameters", "data": {"name": "Q"}}, on_a)
    with pytest.raises(PuzzleError) as err:  # another agent still may not
        s.undo(Actor("agent", "other", model="qwen3:8b"))
    assert err.value.code in ("not_allowed", "not_undoable")


def test_knowledge_hits_end_at_whole_paragraphs_and_say_how_to_read_more():
    text = ("First paragraph, which says what the skill is for.\n\n"
            + "\n".join(f"{i}. Step {i}: " + "word " * 30 for i in range(1, 15))
            + "\n\n```toml\n" + "key = 1\n" * 40 + "```\n\nLast words.")  # fmt: skip
    p = knowledge.Passage("skill bolts", "Steps", text)
    hit = knowledge._excerpt(p, 600)
    assert hit["truncated"] and len(hit["text"]) < 800
    shown = hit["text"].split("\n\n(…")[0]
    assert text.startswith(shown) and shown.rstrip().endswith("word")  # a whole item, no cut word
    assert 'skill_read("bolts")' in hit["text"] and "more characters" in hit["text"]
    code = knowledge.Passage("note x", "", "```\n" + "a = 1\n" * 400 + "```")
    cut = knowledge._excerpt(code, 300)["text"]
    assert cut.split("\n\n(…")[0].count("```") == 2  # a code block cut is closed again
    short = knowledge.Passage("skill x", "", "Short.")
    assert knowledge._excerpt(short) == {"text": "Short."}
    # Real skills: long sections end at a unit, with the way to the rest.
    for h in knowledge.search("bolt pretension preload torque steps", 5):
        assert len(h["text"]) <= knowledge.EXCERPT + 200
        if h.get("truncated") and h["source"].startswith("skill"):
            assert "skill_read(" in h["text"]


def test_the_suite_builds_its_own_project(tmp_path):
    from puzzle.model.schema import Study

    root = evals.example()
    assert "examples" not in root.parts and (root / "bar.step").is_file()
    from puzzle.commands import Session

    studies = sorted(st.name for st in Session(root).project.of_type(Study))
    assert studies == ["Conduction", "Modes", "Tip load"]
    assert evals.example() == root  # built once per process


def test_the_pick_dialog_cancels(ollama, monkeypatch, tmp_path):
    import os
    import time

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from puzzle.gui.pick_dialog import PickDialog
    from puzzle.gui.window import MainWindow

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(pick, "budget", lambda: 16 * GB)
    started = threading.Event()
    calls = []

    def run_task(task, provider, workdir):
        calls.append(task.name)
        started.set()
        dialog.stop.wait(10)  # the task under way finishes once Cancel is pressed
        return _outcome(task.name, True)

    monkeypatch.setattr(evals, "run_task", run_task)
    w = MainWindow()
    try:
        dialog = PickDialog(w)
        dialog.base_url.setText(ollama)
        assert not dialog.cancel.isEnabled()
        dialog.measure()
        assert dialog.cancel.isEnabled()
        end = time.monotonic() + 20
        while not started.is_set() and time.monotonic() < end:
            app.processEvents()
            time.sleep(0.02)
        dialog.cancel_pick()
        while dialog.found is None and time.monotonic() < end:
            app.processEvents()
            time.sleep(0.02)
        assert dialog.found.cancelled and len(calls) == 1
        assert dialog.found.recommended is None and not dialog.cancel.isEnabled()
    finally:
        w.close()
