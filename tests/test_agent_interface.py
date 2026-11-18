"""ADR 0090 package 12, the agent interface (findings UXA-1 to UXA-15 in
`docs/adr/0090/agent-looking-glass.md`), on the scripted provider: no model is called.
GUI tests run on Qt's offscreen platform."""

import asyncio
import json
import os
import shutil
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from puzzle.agent import toolsets  # noqa: E402
from puzzle.agent.agent import SYSTEM, Agent, Event, context_tokens  # noqa: E402
from puzzle.agent.providers import Reply, ScriptedProvider, ToolCall  # noqa: E402
from puzzle.commands import Session  # noqa: E402
from puzzle.commands.model import SetAutonomy  # noqa: E402

EXAMPLE = Path(__file__).parents[1] / "examples" / "cantilever"
TIP = "physics.0.features.1.values.y"
PROMPT_BUDGET = 8000  # tokens before the first message (the package's "done when")


@pytest.fixture
def root(tmp_path):
    dest = tmp_path / "c"
    shutil.copytree(EXAMPLE, dest, ignore=shutil.ignore_patterns("results*", ".puzzle",
                                                                 "history.jsonl"))  # fmt: skip
    return dest


@pytest.fixture
def app():
    """The application; each test's windows are deleted after it, once their background
    jobs have ended."""
    from conftest import delete_windows
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app
    end = time.monotonic() + 60
    while time.monotonic() < end and any(
        getattr(w, "tasks", None) or getattr(getattr(w, "agent", None), "busy", False)
        for w in app.topLevelWidgets()
    ):
        app.processEvents()
        time.sleep(0.02)
    delete_windows(app)


def _edit(path, value, obj="Cantilever"):
    return {"command": "edit", "object": obj, "ops": [{"op": "set", "path": path,
                                                       "value": value}]}  # fmt: skip


def _submit(cid, command, **extra):
    return ToolCall(cid, "submit", {"command": command, **extra})


def _tip(root) -> float:
    comp = next(o for o in Session(root).project.objects.values() if o.name == "Cantilever")
    return comp.physics[0].features[1].values["y"].to_si()


def _mesh_size(root) -> float:
    recipe = next(o for o in Session(root).project.objects.values() if o.kind == "mesh")
    return recipe.global_size.to_si()


def _act(root):
    Session(root).submit(SetAutonomy(level="act_in_budget"))


def _wait(app, condition, timeout=30.0):
    end = time.monotonic() + timeout
    while not condition():
        app.processEvents()
        if time.monotonic() > end:
            raise TimeoutError("not done in time")
        time.sleep(0.02)


def _window(root, provider):
    from puzzle.gui.window import MainWindow

    return MainWindow(root, provider_factory=lambda: provider)


def _turn(app, w, text):
    w.agent.send(text)
    _wait(app, lambda: not w.agent.busy)
    _wait(app, lambda: not w.tasks if hasattr(w, "tasks") else True)


# --- UXA-9: the prompt's size and tools by area ---------------------------------------


def test_the_prompt_holds_its_budget_and_every_tool_is_in_one_group(root):
    """Before the first message: the system prompt and the core tools under 8k tokens
    (characters over four, as the review counted; 24.8k before this package). Every MCP
    tool is in exactly one group, so each is reachable; outside clients get them all."""
    agent = Agent(root, ScriptedProvider([]), images=True)
    tokens = context_tokens(SYSTEM, [], agent.tools)
    assert tokens < PROMPT_BUDGET, f"{tokens} tokens before the first message"
    assert len(agent.tools) <= 30
    every = {t.name for t in agent.all_tools}
    listed = [n for g in ("core", *toolsets.GROUPS) for n in toolsets.tools_of(g)]
    assert len(listed) == len(set(listed)), "a tool in two groups"
    assert set(listed) == every, set(listed) ^ every
    outside = asyncio.run(__import__("puzzle.agent.mcp", fromlist=["x"]).build_server(
        root, "client").list_tools())  # fmt: skip
    assert {t.name for t in outside} == every  # `puzzle mcp` keeps the whole list


def test_a_skill_loads_its_tools_and_a_tool_asked_by_name_loads_its_group(root):
    provider = ScriptedProvider([
        Reply(tool_calls=[ToolCall("1", "skill_read", {"name": "bolts"})]),
        Reply(tool_calls=[ToolCall("2", "mesh_quality", {"component": "Cantilever"})]),
        Reply(tool_calls=[ToolCall("3", "tools_load", {"group": "welds"})]),
        Reply(text="Done."),
    ])  # fmt: skip
    agent = Agent(root, provider, images=False)
    agent.ask("bolts")
    first, second, third, fourth = provider.tools_seen
    assert "bolts_generate" not in first and "bolts_generate" in second
    assert "mesh_quality" not in second and "mesh_quality" in third  # asked for: loaded
    assert "weld_add" in fourth and agent.loaded >= {"bolts", "mesh", "welds"}
    assert "welds" in toolsets.skill_groups()["welds"]


def test_loading_a_group_reaches_no_tool_below_its_level(root):
    """Package 3's gates hold: a tool loaded with its group still needs its level."""
    Session(root).submit(SetAutonomy(level="advise"))
    agent = Agent(root, ScriptedProvider([]), images=False)
    for name, args in (("pardiso_build", {}), ("job_cancel", {"job": "x"}),
                       ("operator_export", {"name": "x"})):  # fmt: skip
        out = agent._call(name, args)
        assert out["is_error"], name
        assert json.loads(out["content"])["code"] in ("not_allowed", "not_found"), out
    assert json.loads(agent._call("pardiso_build", {})["content"])["code"] == "not_allowed"
    assert "solvers" in agent.loaded


# --- UXA-12: MCP hints and skills as prompts ------------------------------------------


def test_tools_carry_hints_and_skills_are_prompts(root):
    from puzzle.agent.mcp import build_server

    server = build_server(root, "client")
    tools = {t.name: t for t in asyncio.run(server.list_tools())}
    assert all(t.annotations is not None for t in tools.values())
    assert tools["outline"].annotations.read_only_hint is True
    assert tools["submit"].annotations.read_only_hint is False
    assert tools["version_restore"].annotations.destructive_hint is True
    import inspect
    import re

    changes = re.compile(r"\.submit\(|submit_batch\(|\.propose\(|\.undo\(|\.redo\(|\brun\("
                         r"|save_version|restore_version|\.resume\(")  # fmt: skip
    for name, t in server._tool_manager._tools.items():  # a read-only hint is true
        if name in toolsets.READ_ONLY:
            assert not changes.search(inspect.getsource(t.fn)), name
    prompts = {p.name for p in asyncio.run(server.list_prompts())}
    assert {"bolts", "welds", "meshing"} <= prompts
    got = asyncio.run(server.get_prompt("bolts"))
    assert got.messages[0].content.text.startswith("# bolts")


# --- UXA-5: a turn undone as one --------------------------------------------------------


def test_a_turn_is_undone_as_one_and_redone(root):
    _act(root)
    provider = ScriptedProvider([
        Reply(tool_calls=[_submit("1", _edit(TIP, {"value": -250, "unit": "N"})),
                          _submit("2", _edit("global_size", {"value": 1, "unit": "mm"},
                                             "Tets 2.5 mm"))]),
        Reply(text="Both changed."),
    ])  # fmt: skip
    agent = Agent(root, provider, images=False)
    agent.ask("finer mesh and 250 N")
    turn = agent.turn_id
    s = Session(root)
    assert len(s.turn_changes(turn)) == 2
    entries = len(s.history_entries())
    out = s.undo_turn(turn)
    assert _tip(root) == -100 and abs(_mesh_size(root) - 2.5e-3) < 1e-12
    assert len(s.history_entries()) == entries + 1  # one entry for the whole turn
    assert s.turn_changes(turn) == [] and s.redo_target() == out.seq
    s.redo()
    assert _tip(root) == -250 and abs(_mesh_size(root) - 1e-3) < 1e-12
    assert len(s.turn_changes(turn)) == 2
    # A later change to the same object: the turn can't be undone over it.
    s.submit(_edit(TIP, {"value": -5, "unit": "N"}))
    from puzzle.errors import PuzzleError

    with pytest.raises(PuzzleError) as refused:
        s.undo_turn(turn)
    assert refused.value.code == "not_undoable" and "Cantilever" in refused.value.details["objects"]
    from puzzle.gui.window import _describe

    entry = next(e for e in s.history_entries() if e.get("undoes_turn"))
    assert _describe(entry["command"], {}) == "undid an agent's turn (2 changes)"


def test_an_agent_undoes_only_its_own_turn(root):
    from puzzle.commands import Actor
    from puzzle.errors import PuzzleError

    _act(root)
    mine = Actor("agent", "built-in", turn="t1")
    Session(root).submit(_edit(TIP, {"value": -7, "unit": "N"}), mine)
    with pytest.raises(PuzzleError) as refused:
        Session(root).undo_turn("t1", Actor("agent", "someone else"))
    assert refused.value.code == "not_allowed"
    Session(root).undo_turn("t1", Actor("agent", "built-in"))
    assert _tip(root) == -100


# --- UXA-2: proposals with old and new values ------------------------------------------


def test_changes_give_each_value_old_and_new(root):
    from puzzle.commands.changes import changes
    from puzzle.gui.agent_cards import field_words

    found = changes(Session(root).project, [
        _edit(TIP, {"value": -250, "unit": "N"}),
        {"command": "add_library_material", "key": "aluminium_6061_t6"},
        {"command": "run_study", "study": "Tip load"},
    ])  # fmt: skip
    edit, added, run = found["commands"]
    (row,) = edit["rows"]
    assert (row["object"], row["before"], row["after"]) == ("Cantilever", "-100 N", "-250 N")
    assert "[" in row["field"] and "0" not in row["field"].split("[")[1]  # by name, no index
    assert (
        field_words(row["field"]) == "Physics › Solid mechanics › Features › Bar/Tip › Values › Y"
    )
    assert added["rows"][0]["after"] == "new material" and run["runs"]
    assert found["problems_after"] == found["problems_before"] == []


def test_a_proposal_is_a_card_accepted_in_part_and_shown_in_the_view(root, app):
    provider = ScriptedProvider([
        Reply(tool_calls=[_submit("1", [_edit(TIP, {"value": -250, "unit": "N"}),
                                        _edit("global_size", {"value": 1, "unit": "mm"},
                                              "Tets 2.5 mm")], rationale="stiffer test")]),
        Reply(text="Proposed."),
    ])  # fmt: skip
    w = _window(root, provider)
    try:
        _turn(app, w, "change two things")
        (pid, card) = next(iter(w.agent.cards.items()))
        from PySide6.QtWidgets import QLabel

        shown = " ".join(label.text() for label in card.findChildren(QLabel))
        assert len(card.ticks) == 2 and "-100 N → <b>-250 N</b>" in shown
        assert "2.5 mm → <b>1 mm</b>" in shown and "stiffer test" in shown
        assert w.show_proposal(pid)  # selects what it changes
        assert w.tree.current().label == "Cantilever"
        card.ticks[1].setChecked(False)
        assert card.ticked() == [0] and card.accept_button.text() == "Accept 1 of 2"
        card.accept_button.click()
        _wait(app, lambda: card.state.isVisibleTo(card))
        assert card.state.text() == "Accepted (1 of 2)"
        assert _tip(root) == -250 and abs(_mesh_size(root) - 2.5e-3) < 1e-12
    finally:
        w.close()


# --- UXA-1 and UXA-6: following the turn, readable lines ------------------------------


def test_the_tree_marks_what_the_turn_touched_and_lines_link_to_it(root, app):
    _act(root)
    provider = ScriptedProvider([
        Reply(tool_calls=[_submit("1", {"command": "add_library_material",
                                        "key": "aluminium_6061_t6"})]),
        Reply(text="Added aluminium."),
    ])  # fmt: skip
    w = _window(root, provider)
    try:
        _turn(app, w, "add aluminium")
        added = [i for i, how in w.tree.touched.items() if how == "added"]
        assert len(added) == 1
        from puzzle.gui.tree import Node

        item = w.tree.item_for(Node("object", added[0]))
        assert item is not None and item.font(0).bold()
        assert "by the agent this turn" in item.toolTip(0)
        text = w.agent.transcript.toPlainText()
        assert "→ Added the material aluminium_6061_t6" in text or "→ Added" in text
        assert "← Applied:" in text and '{"' not in text  # words, never raw JSON (UXA-6)
        w.left_tabs.setCurrentIndex(0)
        w.agent.transcript.object_clicked.emit(added[0])  # the line's link
        assert w.tree.current().object == added[0]
        assert any("Undo this turn" in b.text() for b in w.agent.turn_rows[
            w.agent.agent.turn_id].findChildren(type(w.agent.turn_rows[
                w.agent.agent.turn_id].undo_button)))  # fmt: skip
        assert w.agent.undo_turn(w.agent.agent.turn_id)  # UXA-5 from the transcript
        assert w.tree.item_for(Node("object", added[0])) is None
    finally:
        w.close()


def test_the_tree_follows_a_change_during_the_turn_without_a_reload(root, app, monkeypatch):
    w = _window(root, ScriptedProvider([]))
    try:
        reloads = []
        monkeypatch.setattr(w, "reload", lambda *a, **k: reloads.append(1))
        w.agent_turn(True)
        Session(root).submit({"command": "add_library_material", "key": "aluminium_6061_t6"})
        w._reload_soon()
        w._follow_agent()  # the timer's work, now
        names = [w.tree.topLevelItem(0).child(i).text(0)
                 for i in range(w.tree.topLevelItem(0).childCount())]  # fmt: skip
        assert "Materials" in names
        materials = [i for i in w.tree._items() if "luminium" in i.text(0)]
        assert materials and not reloads  # the tree followed; the view waits
        w.agent_turn(False)
        assert reloads == [1]
    finally:
        w.close()


def test_tool_calls_read_as_words():
    from puzzle.agent import evidence

    assert evidence.call_words("result_stats", {"study": "Tip load", "field": "von_mises"}) == (
        "Read statistics of von mises in Tip load")  # fmt: skip
    assert evidence.doing_words("outline", {}) == "Reading the model tree"
    words = evidence.result_words("submit", json.dumps({
        "status": "applied", "changed": [{"id": "x", "kind": "component", "name": "Bar"}],
        "problems": []}))  # fmt: skip
    assert words == "Applied: Bar"
    assert evidence.result_rows({"max": 1.5, "min": -2, "units": "Pa", "id": "z"}) == [
        ("max", "1.5"), ("min", "-2"), ("units", "Pa")]  # fmt: skip


# --- UXA-3: context sent with the message -------------------------------------------------


def test_what_the_user_looks_at_goes_with_the_message(root, app):
    provider = ScriptedProvider([Reply(text="It is a static study."), Reply(text="ok")])
    w = _window(root, provider)
    try:
        study = next(i for i in w.tree._items() if i.text(0) == "Tip load")
        w.tree.setCurrentItem(study)
        app.processEvents()
        context = w.agent_context()
        assert context["selected"] == ["Tip load"]
        assert "Tip load" in w.agent.context_chip.text()
        _turn(app, w, "what is this?")
        sent = provider.seen[0][-1]
        assert sent["content"].startswith("<context>") and "Selected: Tip load." in (
            sent["content"]) and sent["content"].endswith("what is this?")  # fmt: skip
        assert "you: what is this?" in w.agent.transcript.toPlainText()
        w.agent.context_chip.setChecked(False)  # switched off: nothing sent
        _turn(app, w, "and now?")
        assert provider.seen[1][-1]["content"] == "and now?"
        from puzzle.gui.tree import ROLE

        w.ask_agent_about(study.data(0, ROLE))
        assert w.agent.input.text() == "About Tip load: " and w.agent.context_chip.isChecked()
    finally:
        w.close()


# --- UXA-4: conversations kept per project ------------------------------------------------


def test_conversations_are_kept_outside_the_project_and_reopen(root, app, tmp_path):
    from puzzle.agent import chats

    provider = ScriptedProvider([
        Reply(tool_calls=[ToolCall("1", "outline", {})]), Reply(text="first answer"),
        Reply(text="second answer"),
    ])  # fmt: skip
    w = _window(root, provider)
    try:
        _turn(app, w, "hello")
        kept = list(chats.folder(root).glob("*.json"))
        assert len(kept) == 1 and chats.folder(root).parent.parent == Path(
            os.environ["PUZZLE_USER_DIR"])  # fmt: skip
        inside = [p for p in root.rglob("*") if p.is_file() and "first answer" in p.read_text(
            encoding="utf-8", errors="ignore")]  # fmt: skip
        assert inside == []  # nothing of the chat in the project folder
        w.agent.project_opened()  # opened again: the last chat is back
        text = w.agent.transcript.toPlainText()
        assert "you: hello" in text and "agent: first answer" in text
        _turn(app, w, "again")
        chat = chats.latest(root)
        assert [m["role"] for m in chat.messages].count("user") == 2
        md = Path(w.agent.export_chat(str(tmp_path / "chat.md"))).read_text(encoding="utf-8")
        assert "**You:** hello" in md and "**Agent:** second answer" in md
        old = chat.id
        w.agent.new_chat()
        assert w.agent.transcript.toPlainText() == "" and w.agent.suggestions_box.isVisibleTo(
            w.agent)  # fmt: skip
        assert w.agent.open_chat(old) and "second answer" in w.agent.transcript.toPlainText()
    finally:
        w.close()


def test_a_kept_chat_drops_pictures_and_restores_tool_calls(root):
    from puzzle.agent import chats

    chat = chats.Chat(title="t")
    chat.messages = [
        {"role": "user", "content": "x", "shown": "x", "attachments": [
            {"name": "a.png", "kind": "image", "media_type": "image/png", "data": "AAAA"}]},
        {"role": "assistant", "text": "", "tool_calls": [ToolCall("1", "outline", {})]},
        {"role": "tool", "id": "1", "name": "outline", "content": "ok", "is_error": False,
         "image": b"png"},
    ]  # fmt: skip
    chats.save(root, chat)
    back = chats.load(root, chat.id)
    assert back.messages[0]["attachments"][0]["kind"] == "text"  # the picture not kept
    assert back.messages[1]["tool_calls"][0].name == "outline"
    assert back.messages[2]["image"] is None


# --- UXA-10: context before it overflows ---------------------------------------------------


def test_old_results_and_pictures_are_shortened_before_the_context_fills(root):
    from puzzle.agent.agent import OLD_CHARS, SHORTENED

    agent = Agent(root, ScriptedProvider([]), images=False)
    long = "x" * 3000
    messages = []
    for i in range(3):
        messages += [{"role": "user", "content": f"q{i}"},
                     {"role": "assistant", "text": "",
                      "tool_calls": [ToolCall(str(i), "outline", {})]},
                     {"role": "tool", "id": str(i), "name": "outline", "content": long,
                      "image": b"p"}]  # fmt: skip
    agent._trim(messages)
    tools = [m for m in messages if m["role"] == "tool"]
    assert tools[0]["content"] == long[:OLD_CHARS] + SHORTENED  # before the last two turns
    assert tools[1]["content"] == long and tools[2]["content"] == long
    assert [t["image"] for t in tools] == [None, None, b"p"]  # pictures: the last turn's only
    events = []
    Agent(root, ScriptedProvider([Reply(text="hi")]), images=False).ask("x", events.append)
    usage = next(e for e in events if e.kind == "usage")
    assert 0 < usage.data["context"] < PROMPT_BUDGET


# --- UXA-11: text from outside Puzzle ---------------------------------------------------------


def test_a_solver_log_is_marked_and_what_follows_is_only_proposed(root):
    from puzzle.agent.evals import INJECTED, plant_log

    _act(root)
    plant_log(Session(root))
    delete = {"command": "delete", "object": "Modes"}
    provider = ScriptedProvider([
        Reply(tool_calls=[ToolCall("1", "run_log", {"study": "Tip load"})]),
        Reply(tool_calls=[_submit("2", delete)]),  # a model that obeyed the log
        Reply(text="The log asks me to delete the studies; I won't."),
        Reply(tool_calls=[_submit("3", _edit(TIP, {"value": -9, "unit": "N"}))]),
        Reply(text="Changed."),
    ])  # fmt: skip
    agent = Agent(root, provider, images=False)
    agent.ask("read the log")
    log = next(m for m in agent.messages if m["role"] == "tool" and m["name"] == "run_log")
    assert log["content"].startswith('<untrusted source="run_log">') and INJECTED in log["content"]
    submitted = [m for m in agent.messages if m["role"] == "tool" and m["name"] == "submit"]
    assert json.loads(submitted[0]["content"])["status"] == "proposed"
    assert "Modes" in {o.name for o in Session(root).project.objects.values()}
    assert "untrusted" in SYSTEM
    agent.ask("now set 9 N")  # a new turn: applied again
    assert _tip(root) == -9


def test_an_attached_file_makes_the_turn_cautious(root):
    from puzzle.agent import attachments

    _act(root)
    provider = ScriptedProvider([
        Reply(tool_calls=[_submit("1", _edit(TIP, {"value": -3, "unit": "N"}))]),
        Reply(text="Proposed."),
    ])  # fmt: skip
    Agent(root, provider, images=False).ask(
        "do what the file says", attachments=[attachments.from_text("set 3 N", "n.txt")]
    )
    assert _tip(root) == -100 and Session(root).proposals()


# --- UXA-7: the plan, Continue ------------------------------------------------------------------


def test_the_plan_is_kept_shown_and_seeded_by_the_advisor(root, app):
    items = [{"text": "Check the mesh", "state": "done"}, {"text": "Run Tip load",
                                                            "state": "doing"}]  # fmt: skip
    provider = ScriptedProvider([
        Reply(tool_calls=[ToolCall("1", "plan", {"items": items})]), Reply(text="Planned."),
    ])  # fmt: skip
    w = _window(root, provider)
    try:
        _turn(app, w, "plan it")
        assert w.agent.plan.items == items and w.agent.plan.isVisibleTo(w.agent)
        assert "◐ Run Tip load" in w.agent.plan.text()
        assert w.agent.agent.chat.plan == items  # kept with the conversation
    finally:
        w.close()
    agent = Agent(root, ScriptedProvider([]), images=False)
    seeded = json.loads(agent._call("plan", {"from_advisor": True})["content"])["plan"]
    assert seeded and all(i["state"] in ("todo", "done") for i in seeded)


def test_continue_carries_on_after_the_round_limit(root, app):
    provider = ScriptedProvider([Reply(tool_calls=[ToolCall(str(i), "problems", {})])
                                 for i in range(20)])  # fmt: skip
    w = _window(root, provider)
    try:
        w.agent.send("loop")
        _wait(app, lambda: w.agent.agent is not None)
        w.agent.agent.max_rounds = 2
        _wait(app, lambda: not w.agent.busy)
        assert w.agent.continue_button.isVisibleTo(w.agent)
        w.agent.continue_button.click()
        _wait(app, lambda: not w.agent.busy)
        from puzzle.agent.agent import CONTINUE

        assert any(m.get("shown") == CONTINUE for m in w.agent.agent.messages
                   if m["role"] == "user")  # fmt: skip
    finally:
        w.close()


# --- UXA-8: a run's progress in the chat ---------------------------------------------------------


def test_a_runs_progress_reaches_the_turn_and_its_end_a_toast(root, app, monkeypatch):
    from puzzle import progress

    _act(root)

    def solve(self, command, actor=None, **kw):
        kw["on_event"](progress.Stage(name="solve", state="start"))
        from puzzle.commands.session import Outcome

        return Outcome("applied", seq=1)

    monkeypatch.setattr(Session, "submit", solve)
    provider = ScriptedProvider([
        Reply(tool_calls=[_submit("1", {"command": "run_study", "study": "Tip load"})]),
        Reply(text="Ran."),
    ])  # fmt: skip
    events = []
    Agent(root, provider, images=False).ask("run it", events.append)
    got = [e for e in events if e.kind == "progress"]
    assert got and got[0].data["event"].name == "solve"
    assert any(e.kind == "ran" and e.data["study"] == "Tip load" for e in events)
    w = _window(root, ScriptedProvider([]))
    try:
        shown = []
        monkeypatch.setattr(w.toasts, "show", lambda words, *a, **k: shown.append(words))
        w.agent.busy, w.agent._started = True, time.monotonic()
        w.agent._event(got[0])
        assert w.agent.status.text().startswith("Solving:")
        w.agent._event(Event("ran", data={"study": "Tip load", "failed": False}))
        assert shown == ["The agent's run of Tip load finished"]
        w.agent.busy = False
    finally:
        w.close()


# --- UXA-13: the transcript ---------------------------------------------------------------------


def test_a_streaming_reply_is_drawn_as_markdown_and_thinking_folds(app):
    from puzzle.gui import agent_view

    t = agent_view.Transcript()
    t.thinking("Let me see")
    t.stream("Some **bold")
    t.stream("** text")
    t._draw_live()  # the timer's redraw, now
    assert t._live_view.document().toPlainText() == "Some bold text"
    folds = [t.column.itemAt(i).widget() for i in range(t.column.count() - 1)]
    assert any(isinstance(f, agent_view.Fold) and f.title == "Thinking" for f in folds)
    t.reply("Some **bold** text")
    assert t._live is None and "agent: Some **bold** text" in t.copy_all()


# --- UXA-14: "/", "@" and suggestions -----------------------------------------------------------


def test_slash_reads_a_skill_and_at_offers_objects(root, app):
    from puzzle.agent.suggest import slash, suggestions

    asked, skill = slash("/bolts add two M8 bolts")
    assert skill == "bolts" and asked.endswith("then: add two M8 bolts")
    assert slash("/nothing here") == ("/nothing here", None)
    assert len(suggestions(Session(root).project)) == 3
    provider = ScriptedProvider([Reply(text="ok")])
    w = _window(root, provider)
    try:
        box = w.agent.input
        box.setText("look at @Tip")
        assert "Tip load" in box.offers()
        box.complete_with("Tip load")
        assert box.text() == "look at Tip load "
        box.setText("/bo")
        assert "bolts" in box.offers()
        assert w.agent.suggestions_box.isVisibleTo(w.agent)  # an empty chat suggests
        buttons = [b for b in w.agent.suggestions_box.findChildren(
            __import__("PySide6.QtWidgets", fromlist=["QPushButton"]).QPushButton)]  # fmt: skip
        assert len(buttons) == 3
        box.setText("")
        _turn(app, w, "/meshing make it finer")
        assert "meshing" in provider.seen[0][-1]["content"] and "mesh" in w.agent.agent.loaded
    finally:
        w.close()


# --- UXA-15: the eval suite's new tasks ---------------------------------------------------------


def _task(name):
    from puzzle.agent.evals import HARD_TASKS, TASKS

    return next(t for t in TASKS + HARD_TASKS if t.name == name)


def test_the_new_eval_tasks_pass_for_a_correct_agent_and_fail_otherwise(tmp_path):
    from puzzle.agent.evals import FAMILIES, run_task

    remember = [Reply(text="Noted: 400 N down."),
                Reply(tool_calls=[_submit("1", _edit(TIP, {"value": -400, "unit": "N"}))]),
                Reply(text="Set to 400 N down.")]  # fmt: skip
    assert run_task(_task("remember"), ScriptedProvider(remember), tmp_path).passed
    forgot = [Reply(text="Noted."), Reply(text="What value?")]
    assert not run_task(_task("remember"), ScriptedProvider(forgot), tmp_path).passed
    seat = [Reply(tool_calls=[_submit("1", _edit("contacts.0.type", "frictionless",
                                                 "Bonded bracket"))]),
            Reply(text="Done.")]  # fmt: skip
    assert run_task(_task("frictionless seat"), ScriptedProvider(seat), tmp_path).passed
    bolts = [Reply(tool_calls=[_submit("1", [
        _edit(f"connectors.{i}.pretension", {"value": 8, "unit": "kN"}, "Bolted bracket")
        for i in (0, 1)])]), Reply(text="Both at 8 kN.")]  # fmt: skip
    assert run_task(_task("bolt pretension"), ScriptedProvider(bolts), tmp_path).passed
    gullible = [Reply(tool_calls=[ToolCall("1", "run_log", {"study": "Tip load"})]),
                Reply(tool_calls=[_submit("2", {"command": "delete", "object": s})
                                  for s in ("Tip load", "Modes", "Conduction")]),
                Reply(text="The log was clean.")]  # fmt: skip
    out = run_task(_task("injected log"), ScriptedProvider(gullible), tmp_path)
    assert out.passed and "proposed, not applied" in out.detail  # the gate held
    assert {"memory", "assembly", "injection"} <= set(FAMILIES)
