"""The agent window (ADR 0074): attachments to the model, status as it works, Stop,
replies drawn as Markdown with TeX and folded code, reloads held to the turn's end."""

import os
import shutil
import threading
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from puzzle.agent import attachments  # noqa: E402
from puzzle.agent.agent import Agent  # noqa: E402
from puzzle.agent.providers import (  # noqa: E402
    Reply,
    ScriptedProvider,
    ToolCall,
    to_anthropic,
    to_openai,
)

EXAMPLE = Path(__file__).parents[1] / "examples" / "cantilever"
PNG = bytes.fromhex("89504e470d0a1a0a")  # enough to stand for an image


@pytest.fixture
def root(tmp_path):
    dest = tmp_path / "c"
    shutil.copytree(EXAMPLE, dest, ignore=shutil.ignore_patterns("results*", ".puzzle",
                                                                 "history.jsonl"))  # fmt: skip
    return dest


def test_attachments_from_files(tmp_path):
    (tmp_path / "a.png").write_bytes(PNG)
    (tmp_path / "loads.csv").write_text("x [m], p [Pa]\n0, 1\n", encoding="utf-8")
    (tmp_path / "blob.bin").write_bytes(b"\0\1\2" * 10)
    image = attachments.from_path(tmp_path / "a.png")
    text = attachments.from_path(tmp_path / "loads.csv")
    assert (image.kind, image.media_type, image.is_file) == ("image", "image/png", True)
    assert text.kind == "text" and "p [Pa]" in text.text and not text.is_file
    with pytest.raises(attachments.AttachmentError, match="not text"):
        attachments.from_path(tmp_path / "blob.bin")
    big = attachments.from_text("x" * (attachments.MAX_TEXT + 10))
    assert big.text.endswith("(10 more characters not sent)")


def test_providers_send_attachments_their_way():
    files = [
        attachments.from_image(PNG).block(),
        attachments.from_text("a,b", "t.csv").block(),
        attachments.Attachment("spec.pdf", "pdf", "application/pdf", data=b"%PDF").block(),
    ]
    message = {"role": "user", "content": "What is in these?", "attachments": files}
    claude = to_anthropic([message])[0]["content"]
    assert [b["type"] for b in claude] == ["image", "text", "document", "text"]
    assert claude[1]["text"].startswith('<file name="t.csv">') and claude[-1]["text"] == (
        "What is in these?")  # fmt: skip
    local = to_openai("system", [message])[1]["content"]
    assert local[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "can't read documents" in local[2]["text"]
    assert to_anthropic([{"role": "user", "content": "plain"}])[0]["content"] == "plain"


def test_files_stay_home_unless_allowed_and_status_and_stop(root):
    provider = ScriptedProvider(
        [
            Reply(
                text="Looking.",
                tool_calls=[ToolCall("1", "outline", {}), ToolCall("2", "outline", {})],
            ),  # fmt: skip
            Reply(text="Done."),
        ]
    )
    agent = Agent(root, provider, images=False)  # a remote provider without send_images
    seen = []
    stop = threading.Event()

    def on(e):
        seen.append((e.kind, e.text))
        if e.kind == "tool_result":
            stop.set()  # the user pressed Stop during the first call

    image = attachments.from_image(PNG, "shot.png")
    note = attachments.from_text("hello", "n.txt")
    agent.ask("look", on, attachments=[image, note], stop=stop)
    sent = provider.seen[0][0]
    assert [a["name"] for a in sent["attachments"]] == ["n.txt"]  # the image held back
    assert any(k == "note" and "shot.png" in t for k, t in seen)
    assert ("status", "Thinking…") in seen and ("status", "Calling outline") in seen
    # The second call wasn't run but still has a result, and the turn ended.
    tools = [m for m in agent.messages if m["role"] == "tool"]
    assert len(tools) == 2 and "stopped" in tools[1]["content"]
    assert seen[-1] == ("note", "Stopped.") and len(provider.seen) == 1


def test_replies_are_markdown_with_tex_and_folded_code():
    from PySide6.QtWidgets import QApplication

    from puzzle.gui import agent_view

    QApplication.instance() or QApplication([])
    reply = ("The stress is $\\sigma = F/A$:\n\n$$\\sigma_{vm} = \\sqrt{3 J_2}$$\n\n"
             "```python\nx = 1\ny = 2\n```\nThen apply it.")  # fmt: skip
    parts = agent_view.segments(reply)
    assert [p.kind for p in parts] == ["text", "code", "text"] and parts[1].language == "python"
    t = agent_view.Transcript()
    t.reply(reply)
    t.tool("→ outline {}")
    t.tool("← ok")
    t.tool("→ result_stats {}")
    box = t.column.itemAt(0).widget()
    views = [box.layout().itemAt(i).widget() for i in range(box.layout().count())]
    md = next(v for v in views if isinstance(v, agent_view.MarkdownView))
    html = md.document().toHtml()
    assert "tex://1" in html and "tex://2" in html  # both equations drawn as images
    fold = next(v for v in views if isinstance(v, agent_view.Fold))
    assert fold.toggle.text() == "Python · 2 lines" and not fold.body.isVisibleTo(fold)
    tools = t.column.itemAt(1).widget()
    assert tools.toggle.text() == "2 tools used"
    assert "agent: The stress" in t.toPlainText()


def test_reloads_wait_for_the_agents_turn(root, monkeypatch):
    from PySide6.QtWidgets import QApplication

    from puzzle.gui.window import MainWindow

    QApplication.instance() or QApplication([])
    w = MainWindow(root)
    try:
        reloads = []
        monkeypatch.setattr(w, "reload", lambda: reloads.append(1))
        w.agent_turn(True)
        w._reload_soon()
        w._reload_soon()  # two changes of the agent's
        assert not reloads and not w._reload_timer.isActive()
        w.agent_turn(False)
        assert reloads == [1]  # one reload, when the turn ends
    finally:
        w.close()
