"""The help (ADR 0083): pages parse, the generated reference matches the code, every
feature, setting, operator, element, property, result, problem code, MCP tool and CLI
command has an entry, every link and every F1 key opens a page, and the Help window
shows them."""

import os
import re
import shutil
from pathlib import Path

import pytest

from puzzle import help as manual

ROOT = Path(__file__).parents[1]
LINK = re.compile(r"\]\((help:[^)\s]+)\)")
# ADRs whose finished features a help page explains (its front matter's `adrs`), the
# twin of test_agent_skills.WITH_SKILLS (ADR 0083 decision 3).
WITH_HELP = {
    "0005",
    "0011",
    "0014",
    "0015",
    "0017",
    "0018",
    "0019",
    "0020",
    "0021",
    "0022",
    "0023",
    "0024",
    "0025",
    "0026",
    "0027",
    "0028",
    "0029",
    "0030",
    "0032",
    "0033",
    "0037",
    "0038",
    "0039",
    "0040",
    "0041",
    "0042",
    "0043",
    "0044",
    "0045",
    "0046",
    "0047",
    "0048",
    "0049",
    "0050",
    "0054",
    "0055",
    "0056",
    "0057",
    "0058",
    "0059",
    "0061",
    "0062",
    "0063",
    "0064",
    "0066",
    "0067",
    "0071",
    "0076",
    "0077",
    "0078",
    "0079",
    "0080",
    "0081",
    "0082",
    "0083",
    "0084",
    "0085",
}


def test_a_page_parses_its_front_matter_and_keyed_headings():
    page = manual.parse("guides/x", "---\ntitle: X\nsection: guide\norder: 5\nkeys: a:b, c\n"
                        "summary: s\nadrs: 0083\n---\n\nLead.\n\n<!-- key: k:1, k:2 -->\n"
                        "## One\n\nText.\n\n## Two\n")  # fmt: skip
    assert (page.title, page.section, page.order, page.keys, page.adrs) == (
        "X",
        "guide",
        5,
        ("a:b", "c"),
        ("0083",),
    )
    assert page.heading_for("k:2").text == "One" and page.heading_for("k:3") is None
    assert "<!--" not in page.text and page.text.startswith("# X\n")
    assert manual.section_text(page, "One").strip() == "## One\n\nText."
    with pytest.raises(ValueError, match="section"):
        manual.parse("x", "---\nsection: nowhere\n---\n")


def test_a_key_falls_back_to_a_more_general_one():
    page, heading = manual.lookup("setting:calculix:no_such_setting")
    assert page.name == "reference/settings" and heading == "CalculiX"
    page, heading = manual.lookup("feature:pressure")
    assert page.name == "reference/features" and heading == "Pressure"
    assert manual.lookup("nothing-like-this") is None
    assert manual.lookup("start/getting-started")[0].section == "start"


def test_the_reference_is_what_the_code_says_now():
    pytest.importorskip("mcp")
    from puzzle.help import reference

    stale = reference.stale()
    assert not stale, f"stale help reference: {stale}; run scripts/build_help.py"


def test_everything_the_code_offers_has_a_reference_entry():
    from puzzle.adapters import available_adapters
    from puzzle.adapters.settings import SHARED
    from puzzle.errors import CODES
    from puzzle.materials.registry import MODELS, PROPERTIES
    from puzzle.model.catalog import CATALOG
    from puzzle.operators import TYPES
    from puzzle.post.quantities import SCALARS, TENSORS, VECTORS

    keys = manual.keys()
    wanted = {f"feature:{s.type}" for specs in CATALOG.values() for s in specs}
    wanted |= {f"setting:shared:{s.key}" for s in SHARED}
    wanted |= {f"setting:{k}:{s.key}" for k, a in available_adapters().items()
               for s in a().settings_schema()}  # fmt: skip
    wanted |= {f"solver:{k}" for k in available_adapters()}
    wanted |= {f"operator:{k}" for k in TYPES}
    wanted |= {f"property:{p.key}" for p in PROPERTIES} | {f"model:{k}" for k in MODELS}
    wanted |= {f"result:{k}" for k in (*VECTORS, *TENSORS, *SCALARS)}
    wanted |= {f"problem:{c}" for c in CODES}
    missing = sorted(wanted - keys)
    assert not missing, f"no help entry for {missing[:20]}"


def test_every_mcp_tool_and_cli_command_has_an_entry(tmp_path):
    pytest.importorskip("mcp")
    from test_agent_skills import _tools

    from puzzle.cli import _parser

    keys = manual.keys()
    assert not sorted(f"mcp:{t}" for t in _tools(tmp_path) if f"mcp:{t}" not in keys)
    sub = next(a for a in _parser()._actions if hasattr(a, "choices") and a.choices)
    assert not sorted(f"cli:{c}" for c in sub.choices if f"cli:{c}" not in keys)


def test_every_link_opens_a_page():
    broken = []
    for p in manual.pages().values():
        for target in LINK.findall(p.body):
            name = target[len("help:") :].split("#")[0]
            if manual.lookup(name) is None:
                broken.append(f"{p.name} → {target}")
    assert not broken, broken


def _panel_keys() -> set[str]:
    source = (ROOT / "src/puzzle/gui/window.py").read_text(encoding="utf-8")
    keys = set(re.findall(r'docks\.add\(\s*"([a-z_]+)"', source))
    keys |= set(re.findall(r'"(panel:[a-z_]+)"', source))
    return {k if k.startswith("panel:") else f"panel:{k}" for k in keys}


def test_every_f1_target_opens_a_page():
    """Each panel, tab, tree section, object kind and tree item kind, and every
    Properties field with a description, has a page (or an entry) of its own; a general
    fallback (the start page) doesn't count."""
    from puzzle.gui.form import DESCRIPTIONS
    from puzzle.gui.tree import SECTIONS
    from puzzle.model.schema import OBJECT_TYPES

    wanted = _panel_keys()
    wanted |= {f"kind:{c.kind}" for c in OBJECT_TYPES}
    wanted |= {f"section:{label.lower()}" for label, _ in SECTIONS}
    wanted |= {f"tree:{k}" for k in ("selection", "part", "physics", "step", "run")}
    wanted |= {f"field:{k}" for k in DESCRIPTIONS}
    known = manual.keys()
    missing = sorted(k for k in wanted if k not in known)
    assert not missing, f"F1 opens no page for {missing}"


def test_finished_adrs_have_help():
    named = {a for p in manual.pages().values() for a in p.adrs}
    assert named >= WITH_HELP, sorted(WITH_HELP - named)


def test_search_and_answer():
    hits = manual.search("mesh size")
    assert hits and all({"page", "title", "heading", "excerpt"} <= set(h) for h in hits)
    assert "contents" in manual.answer("")
    assert manual.answer("feature:fixed")["heading"] == "Fixed support"
    assert "Fixed support" in manual.answer("feature:fixed")["text"]
    assert "results" in manual.answer("how do I mesh a part")


def test_puzzle_help_prints_a_page_and_searches(capsys):
    from puzzle.cli import main

    assert main(["help", "problem:energy_error"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("### energy_error") and "Generated" not in out
    assert main(["help"]) == 0
    assert "Getting started" in capsys.readouterr().out
    assert main(["help", "pressure", "on", "a", "face"]) == 0


def test_the_knowledge_pack_reads_the_help():
    from puzzle.agent import knowledge

    sources = {p.source for p in knowledge.passages()}
    assert "help start/getting-started" in sources
    assert any(s.startswith("help reference/") for s in sources)


# --- the Help window and F1 -------------------------------------------------------------

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def app():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path):
    from puzzle.gui.window import MainWindow

    root = tmp_path / "c"
    shutil.copytree(
        ROOT / "examples" / "cantilever",
        root,
        ignore=shutil.ignore_patterns("results*", ".puzzle", "history.jsonl"),
    )
    w = MainWindow(root)
    yield w
    w.close()


def test_the_help_window_shows_pages_searches_and_follows_links(window):
    from PySide6.QtCore import QUrl

    from puzzle.gui.help_viewer import START, show_help

    viewer = show_help(window)
    assert viewer.current == (START, None)
    assert "Six steps" in viewer.text.toPlainText()
    assert viewer.show_key("feature:pressure")
    assert viewer.current == ("reference/features", "Pressure")
    viewer.follow(QUrl("help:guides/meshing"))
    assert viewer.current[0] == "guides/meshing"
    viewer.go_back()
    assert viewer.current == ("reference/features", "Pressure")
    viewer.find.setText("mesh size")
    assert viewer.contents.topLevelItem(0).childCount() > 0
    viewer.find.setText("")
    assert viewer.contents.topLevelItemCount() >= 3  # the sections again


def test_f1_knows_what_has_focus(window):
    from puzzle.gui.help_viewer import START, help_key
    from puzzle.gui.tree import ROLE

    assert help_key(window, window.mesh_bench) == "panel:mesh"
    assert help_key(window, window.properties_dock) == "panel:properties"
    assert help_key(window, None) in (f"page:{START}", "panel:model", "panel:view")
    tree = window.tree
    items = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
    found = {}
    while items:
        item = items.pop()
        node = item.data(0, ROLE)
        if node is not None:
            tree.setCurrentItem(item)
            found.setdefault(node.kind, help_key(window, tree))
        items += [item.child(i) for i in range(item.childCount())]
    assert found["feature"].startswith("feature:")
    assert found["section"].startswith("section:")
    for key in found.values():
        assert manual.lookup(key) is not None, key


def test_every_code_puzzle_raises_is_listed():
    """A problem, warning or refusal code used anywhere in the source is in errors.CODES,
    so the reference (and the agent) can explain it; 20 weren't, found while writing the
    help (2026-11-15)."""
    from puzzle.errors import CODES

    used = set()
    for path in (ROOT / "src" / "puzzle").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        used |= set(re.findall(r'Problem\(\s*"([a-z_]+)"', text))
        used |= set(re.findall(r'code="([a-z_]+)"', text))
    assert not sorted(used - set(CODES))
