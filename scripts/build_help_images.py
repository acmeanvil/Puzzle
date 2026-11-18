"""Draw the manual's pictures from the real GUI (ADR 0083 decision 2, amended 2026-11-16).

    pixi run -e dev python scripts/build_help_images.py [--only menus operators screens]

Writes into src/puzzle/help/images/:

- menu/*.png: every menu location a help page names, opened from the menu bar with the
  item highlighted, and index.json mapping each location to its picture (the pages'
  words are matched against the menu bar itself, so only real locations get one);
- operator/*.png: every schematic operator's node, as the schematic draws it;
- screens/*.png: the schematic, a small chain of operators in it, the operator palette,
  the Advisor and the agent window with a conversation and a proposal.

It runs on Qt's offscreen platform in matplotlib's DejaVu fonts and the light theme, so
every machine draws the same pictures, on a scratch copy of the cantilever example with
a user folder of its own (nothing of the user's is read or changed). Rerun it after
changing a menu, an operator or a page that names a menu location; tests/test_help_images.py
fails until then. Menu locations a page names that the menu bar doesn't have are listed
and fail the run.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GAP = 8  # px of window colour round a picture


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", nargs="+", choices=["menus", "operators", "screens"],
                        default=["menus", "operators", "screens"])  # fmt: skip
    args = parser.parse_args(argv)
    app, window = open_window()
    from puzzle.help import pictures

    status = 0
    if "menus" in args.only:
        bad = draw_menus(window, pictures)
        for line in bad:
            print(f"  not a menu location: {line}")
        status = 1 if bad else 0
    if "screens" in args.only:  # before the operators, which fill the project
        draw_screens(app, window, pictures)
    if "operators" in args.only:
        draw_operators(window, pictures)
    window.close()
    return status


def open_window():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    import matplotlib

    fonts = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
    os.environ["QT_QPA_FONTDIR"] = str(fonts)
    os.environ["PUZZLE_USER_DIR"] = tempfile.mkdtemp(prefix="puzzle-help-images-user-")
    os.environ["PUZZLE_MATERIALS"] = ""  # the generic materials only
    os.environ.setdefault("PUZZLE_RUNNER", "off")
    root = Path(tempfile.mkdtemp(prefix="puzzle-help-images-")) / "cantilever"
    shutil.copytree(REPO / "examples" / "cantilever", root,
                    ignore=shutil.ignore_patterns("results", ".puzzle", "versions",
                                                  "history.jsonl"))  # fmt: skip
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(["puzzle-help-images"])
    for f in fonts.glob("DejaVu*.ttf"):
        QFontDatabase.addApplicationFont(str(f))
    app.setFont(QFont("DejaVu Sans", 9))
    from puzzle import settings

    settings.current().set("interface.theme", "light")
    settings.current().save()
    from puzzle.gui.window import MainWindow

    window = MainWindow(root)
    window.resize(1400, 900)
    window.settings_changed()
    app.processEvents()
    return app, window


def _plain(text: str) -> str:
    return text.replace("&", "")


# --- menus ----------------------------------------------------------------------------


def draw_menus(window, pictures) -> list[str]:
    from puzzle import help as manual
    from puzzle.gui.main_menu import paths as menu_paths

    paths = menu_paths(window)
    real = [p for p in paths if pictures.ARROW in p]
    named: set[str] = set()
    bad: list[str] = []
    for page in manual.pages().values():
        named |= {m for _, m in pictures.menu_mentions(page.body, real)}
        bad += [f"{page.name}: {b}" for b in pictures.unknown_menus(page.body, real)]
    folder = pictures.IMAGES / "menu"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    index = {}
    for name in sorted(named):
        file = pictures.menu_file(name)
        menu_picture(window, _chain(window, name)).save(str(pictures.path(file)))
        index[name] = file
    pictures.INDEX.write_text(json.dumps({"menus": index}, indent=1, ensure_ascii=False)
                              + "\n", encoding="utf-8", newline="\n")  # fmt: skip
    pictures.index.cache_clear()
    print(f"{len(index)} menu pictures")
    return bad


def _chain(window, name: str) -> list:
    """(menu, action) from the bar down to a menu path, found by words as each menu is
    filled: a dynamic menu (loads by physics, operators) refills itself when shown, so
    actions found earlier may be gone."""
    from puzzle.help.pictures import ARROW

    words = name.split(ARROW)
    top = next(a for a in window.menuBar().actions() if _plain(a.text()) == words[0])
    chain, menu = [(None, top)], top.menu()
    for word in words[1:]:
        menu.aboutToShow.emit()
        action = next(a for a in menu.actions() if _plain(a.text()) == word)
        chain.append((menu, action))
        menu = action.menu()
    return chain


def menu_picture(window, chain):
    """The menu bar's left part with the first title highlighted, then each menu of the
    chain opened beside the last, its item highlighted; a submenu named last is shown
    open too."""
    from PySide6.QtCore import QPoint, QRect, Qt
    from PySide6.QtGui import QPainter, QPixmap

    bar = window.menuBar()
    bar.adjustSize()
    top = chain[0][1]
    title = bar.actionGeometry(top)
    shots = []  # (pixmap, top-left)
    x, y = title.left(), title.bottom() + 1
    menus = [(m, a) for m, a in chain[1:]]
    last = chain[-1][1]
    if last.menu() is not None and len(chain) > 1:
        menus.append((last.menu(), None))
    for menu, action in menus:
        if action is None:  # a submenu named last: filled now, as when it opens
            menu.aboutToShow.emit()
        menu.adjustSize()
        menu.setActiveAction(action) if action is not None else None
        shot = menu.grab()
        shots.append((shot, QPoint(x, y)))
        if action is not None:
            y += menu.actionGeometry(action).top()
            x += shot.width() - 4
    width = max(p.x() + s.width() for s, p in shots) + GAP
    height = max(p.y() + s.height() for s, p in shots) + GAP
    strip = bar.grab()
    out = QPixmap(width, height)
    palette = window.palette()
    out.fill(palette.window().color())
    painter = QPainter(out)
    painter.drawPixmap(0, 0, strip, 0, 0, min(width, strip.width()), strip.height())
    painter.fillRect(title, palette.highlight())
    painter.setPen(palette.highlightedText().color())
    painter.drawText(QRect(title), Qt.AlignmentFlag.AlignCenter, _plain(top.text()))
    for shot, at in shots:
        painter.drawPixmap(at, shot)
    painter.end()
    return out


# --- operators ------------------------------------------------------------------------


def draw_operators(window, pictures) -> None:
    """One of every operator in the project, unwired, each node drawn on its own."""
    from puzzle.commands.session import USER
    from puzzle.operators import TYPES, default_name

    session = window.session
    taken = {getattr(o, "name", "") for o in session.project.objects.values()}
    names: dict[str, str] = {}  # the nodes to draw: name → operator type
    for key, spec in TYPES.items():
        name = spec.label if spec.label not in taken else default_name(session.project, key)
        taken.add(name)
        data = {"name": name, "type": key, "inputs": {}}
        try:
            session.submit({"command": "create", "kind": "operator", "data": data}, USER)
            names[name] = key
        except Exception as error:  # noqa: BLE001 - reported, the rest still drawn
            print(f"  operator {key}: {error}")
    window.refresh()
    panel = window.schematic
    panel.refresh()
    panel.wait()  # the values are worked out on a worker (ADR 0090 package 13)
    folder = pictures.IMAGES / "operator"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    drawn = 0
    for item in panel.items.values():
        key = names.get(item.node.label)
        if item.node.kind != "operator" or key is None:
            continue
        item.setSelected(False)
        # Unwired, its value line would be the missing-input message: drawn as not run.
        item.node = dataclasses.replace(item.node, value="", state="not_run", reason="")
        item.update()
        scene_picture(panel, item.sceneBoundingRect()).save(str(folder / f"{key}.png"))
        drawn += 1
    print(f"{drawn} operator pictures of {len(TYPES)}")


def scene_picture(panel, rect):
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QPainter, QPixmap

    area = rect.adjusted(-GAP, -GAP, GAP, GAP)
    out = QPixmap(int(area.width()), int(area.height()))
    out.fill(panel.palette().base().color())
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    panel.scene.render(painter, QRectF(out.rect()), area)
    painter.end()
    return out


# --- screens --------------------------------------------------------------------------


def draw_screens(app, window, pictures) -> None:
    folder = pictures.IMAGES / "screens"
    folder.mkdir(parents=True, exist_ok=True)

    def save(widget, name: str, size=None) -> None:
        if size:
            widget.resize(*size)
        app.processEvents()
        widget.grab().save(str(folder / f"{name}.png"))

    def alone(widget, dock, name: str, size) -> None:
        """A docked panel drawn on its own at a readable size, then put back."""
        widget.setParent(None)
        save(widget, name, size)
        dock.setWidget(widget)

    window.advisor.refresh()
    alone(window.advisor, window.advisor_dock, "advisor", (420, 420))
    # The agent window: a question, the tools the answer used, the answer with its TeX,
    # and a proposal waiting below (nothing here calls a model).
    from puzzle.commands.session import Actor

    session = window.session
    recipe = next(o for o in session.project.objects.values()
                  if getattr(o, "kind", "") == "mesh")  # fmt: skip
    session.propose([{"command": "edit", "object": recipe.name,
                      "ops": [{"op": "set", "path": "global_size.value", "value": 2.0}]}],
                    Actor("agent", "built-in"),
                    rationale="A finer mesh, 2 mm, for the stress at the fixed end")  # fmt: skip
    agent = window.agent
    agent.refresh()
    t = agent.transcript
    t.user("What should I do next with this model?")
    t.tool("→ next_steps {}")
    t.tool("← Next: Run. Mesh, Run and Results to do")
    t.tool("→ outline {}")
    t.tool("← Cantilever: 1 part, 2 selections, 3 studies")
    t.reply("Everything a study needs is in place: the geometry, its selections, a "
            "material and the loads. Nothing has run yet, so the next step is to run a "
            "study, which meshes first. Once **Tip load** has run I'll compare its tip "
            "deflection with beam theory's $\\delta = F L^3 / 3 E I$.\n\n"
            "I've proposed a finer mesh below for the stress at the fixed end.")  # fmt: skip
    alone(agent, window.agent_dock, "agent-window", (480, 640))
    # The schematic with a small chain: the study's stress, its peak, a strength check.
    from puzzle.commands.session import USER

    study = next(o for o in session.project.objects.values()
                 if getattr(o, "kind", "") == "study" and o.name == "Tip load")  # fmt: skip
    for data in (
        {"name": "Peak stress", "type": "reduce",
         "inputs": {"result": {"source": str(study.id), "port": "stress"}}},
        {"name": "Strength", "type": "check",
         "inputs": {"value": {"source": "Peak stress", "port": "out"},
                    "limit": {"value": {"value": 250, "unit": "MPa"}}}},
    ):  # fmt: skip
        data["inputs"] = _inputs(session.project, data["inputs"])
        session.submit({"command": "create", "kind": "operator", "data": data}, USER)
    window.refresh()
    panel = window.schematic
    panel.refresh()
    panel.wait()
    app.processEvents()
    scene_picture(panel, panel.scene.itemsBoundingRect()).save(str(folder / "schematic.png"))
    # The chain alone, large enough to read on a page: the study, then its operators.
    chain = [i for i in panel.items.values()
             if i.node.label in ("Tip load", "Peak stress", "Strength")]  # fmt: skip
    area = chain[0].sceneBoundingRect()
    for item in chain[1:]:
        area = area.united(item.sceneBoundingRect())
    hidden = [i for i in [*panel.items.values(), *panel.wires]
              if i not in chain and not (getattr(i, "a", None) in chain
                                         and getattr(i, "b", None) in chain)]  # fmt: skip
    for item in hidden:
        item.setVisible(False)
    scene_picture(panel, area).save(str(folder / "schematic-chain.png"))
    for item in hidden:
        item.setVisible(True)
    # The palette (Tab in the schematic), searching as a user would.
    from puzzle.gui.operators import Palette

    palette = Palette(window)
    palette.search.setText("check")
    for row in range(palette.list.count()):  # the one the guide's example uses
        if palette.list.item(row).text().startswith("Check against limit"):
            palette.list.setCurrentRow(row)
    save(palette, "operator-palette", (520, 420))
    palette.close()
    print("screens: advisor, agent-window, schematic, schematic-chain, operator-palette")


def _inputs(project, given: dict) -> dict:
    from puzzle.operators import inputs_from

    return inputs_from(project, given)


if __name__ == "__main__":
    sys.exit(main())
