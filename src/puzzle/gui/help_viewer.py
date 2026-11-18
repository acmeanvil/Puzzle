"""Help → Help (F1): the manual in a window (ADR 0083 decision 2): search, the contents
by section, pages rendered with their equations as the agent window draws them (ADR
0074), links to other pages (`help:`), to tools (`puzzle:<action>`) and outside.

F1 anywhere asks `help_key(window)` what has focus and opens its page: a Properties
row (`field:<name>`), a tree item (`kind:<kind>`, `feature:<type>`, `operator:<kind>`),
the tab or panel it sits in (`panel:<key>`), else the getting-started page.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QKeySequence, QTextCursor, QTextDocument
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

PAGE = Qt.ItemDataRole.UserRole
START = "start/getting-started"


def rendered(markdown: str) -> tuple[str, dict[str, object]]:
    """The page's Markdown with its TeX ($...$, $$...$$) as images, code left alone, and
    its pictures (`image:` names and the menu locations it names, `help.pictures`);
    (markdown, {url: QImage})."""
    from PySide6.QtGui import QImage

    from puzzle.gui.agent_view import DISPLAY_TEX, INLINE_TEX, segments, tex_image
    from puzzle.gui.theme import current as tokens
    from puzzle.help import pictures

    markdown = pictures.with_pictures(markdown, links=True)
    colour, images, parts = tokens().text, {}, []
    for name in pictures.references(markdown):
        image = QImage(str(pictures.path(name)))
        if not image.isNull():
            images[f"image:{name}"] = image

    def put(m, display: bool) -> str:
        image = tex_image(m.group(1).strip(), colour, display)
        if image is None:
            return m.group(0)
        url = f"tex://{len(images) + 1}"
        images[url] = image
        tag = f"![equation]({url})"
        return f"\n\n{tag}\n\n" if display else tag

    for s in segments(markdown):
        if s.kind == "code":
            parts.append(f"```{s.language}\n{s.text}\n```")
        else:
            text = DISPLAY_TEX.sub(lambda m: put(m, True), s.text)
            parts.append(INLINE_TEX.sub(lambda m: put(m, False), text))
    return "\n\n".join(parts), images


class HelpWindow(QDialog):
    def __init__(self, window=None) -> None:
        super().__init__(window)
        self.window_ = window  # never `window`: QWidget.window()
        self.setWindowTitle("Puzzle help")
        self.resize(1100, 760)
        self.trail: list[tuple[str, str | None]] = []  # pages shown, for Back
        self.current: tuple[str, str | None] | None = None
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.back = QPushButton("Back", self)
        self.back.clicked.connect(self.go_back)
        self.back.setEnabled(False)
        bar.addWidget(self.back)
        self.find = QLineEdit(self)
        self.find.setPlaceholderText("Search the help…")
        self.find.setClearButtonEnabled(True)
        self.find.textChanged.connect(self.search)
        bar.addWidget(self.find, 1)
        layout.addLayout(bar)
        split = QSplitter(Qt.Orientation.Horizontal, self)
        self.contents = QTreeWidget(split)
        self.contents.setHeaderHidden(True)
        self.contents.itemActivated.connect(self._picked)
        self.contents.itemClicked.connect(self._picked)
        self.text = QTextBrowser(split)
        self.text.setOpenLinks(False)
        self.text.anchorClicked.connect(self.follow)
        split.addWidget(self.contents)
        split.addWidget(self.text)
        split.setSizes([300, 800])
        layout.addWidget(split, 1)
        self.fill_contents()
        self.show_page(START)

    # --- contents and search --------------------------------------------------------

    def fill_contents(self) -> None:
        from puzzle import help as manual

        self.contents.clear()
        for section, pages in manual.contents():
            top = QTreeWidgetItem(self.contents, [section])
            top.setFlags(top.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            for p in pages:
                item = QTreeWidgetItem(top, [p.title])
                item.setData(0, PAGE, (p.name, None))
                item.setToolTip(0, p.summary)
            top.setExpanded(section != "Reference")

    def search(self, query: str) -> None:
        from puzzle import help as manual

        if not query.strip():
            self.fill_contents()
            return
        self.contents.clear()
        hits = manual.search(query, limit=30)
        top = QTreeWidgetItem(self.contents, [f"{len(hits)} found" if hits else "Nothing found"])
        top.setFlags(top.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        for h in hits:
            label = f"{h['title']} › {h['heading']}" if h["heading"] else h["title"]
            item = QTreeWidgetItem(top, [label])
            item.setData(0, PAGE, (h["page"], h["heading"]))
            item.setToolTip(0, h["excerpt"])
        top.setExpanded(True)

    def _picked(self, item, _column=0) -> None:
        target = item.data(0, PAGE)
        if target:
            self.show_page(*target)

    # --- pages ----------------------------------------------------------------------

    def show_key(self, key: str) -> bool:
        """Open the page a help key names (at its heading); False when none does."""
        from puzzle import help as manual

        hit = manual.lookup(key)
        if hit is None:
            return False
        self.show_page(hit[0].name, hit[1])
        return True

    def show_page(self, name: str, heading: str | None = None, *, remember: bool = True) -> None:
        from puzzle import help as manual

        page = manual.pages().get(name)
        if page is None:
            hit = manual.lookup(name)
            if hit is None:
                return
            page, heading = hit[0], heading or hit[1]
        if remember and self.current is not None and self.current != (page.name, heading):
            self.trail.append(self.current)
        self.back.setEnabled(bool(self.trail))
        self.current = (page.name, heading)
        markdown, images = rendered(page.text)
        document = self.text.document()
        document.setMarkdown(markdown)
        for url, image in images.items():  # after setMarkdown, which clears resources
            document.addResource(QTextDocument.ResourceType.ImageResource, QUrl(url), image)
        document.markContentsDirty(0, document.characterCount())
        self.setWindowTitle(f"Puzzle help — {page.title}")
        self._scroll_to(heading)

    def _scroll_to(self, heading: str | None) -> None:
        bar = self.text.verticalScrollBar()
        if not heading:
            bar.setValue(0)
            return
        found = self.text.document().find(heading, 0, QTextDocument.FindFlag.FindCaseSensitively)
        while not found.isNull() and found.block().text().strip() != heading.strip():
            found = self.text.document().find(
                heading, found, QTextDocument.FindFlag.FindCaseSensitively
            )
        if found.isNull():
            bar.setValue(0)
            return
        cursor = QTextCursor(found.block())
        self.text.setTextCursor(cursor)
        bar.setValue(bar.maximum())  # then back up, so the heading lands at the top
        self.text.ensureCursorVisible()

    def go_back(self) -> None:
        if self.trail:
            name, heading = self.trail.pop()
            self.show_page(name, heading, remember=False)
        self.back.setEnabled(bool(self.trail))

    def follow(self, url: QUrl) -> None:
        """help:<page or key> opens a page; puzzle:<action> runs that tool; the rest
        opens outside."""
        scheme = url.scheme()
        if scheme == "help":
            target = url.toString()[len("help:") :]
            page, _, heading = target.partition("#")
            if not self.show_key(page):
                self.show_page(page, heading or None)
        elif scheme == "puzzle":
            open_tool(self.window_, url.toString()[len("puzzle:") :])
        else:
            QDesktopServices.openUrl(url)


def open_tool(window, target: str) -> bool:
    """A `puzzle:` link's tool (ADR 0083 decision 2; ADR-E-1): `menu/<slug>` or a menu
    path runs that menu item (a menu opens where the pointer is), a toolbar name its
    action. False when nothing answers or it is greyed out."""
    from PySide6.QtGui import QCursor

    from puzzle.gui import main_menu

    if window is None:
        return False
    action = main_menu.action_for(window, target)
    if action is None or not action.isEnabled():
        return False
    if action.menu() is not None:
        action.menu().popup(QCursor.pos())
    else:
        action.trigger()
    return True


# --- F1 ---------------------------------------------------------------------------------


def help_key(window, widget: QWidget | None = None) -> str:
    """What F1 should open for the widget with focus (or `widget`): its own `help`
    property, a Properties row, a tree item, the panel or tab it is in; else the start."""
    from PySide6.QtWidgets import QDockWidget

    w = widget if widget is not None else QApplication.focusWidget()
    while w is not None:
        scene = getattr(w, "scene", None)  # the schematic: its selected node first (ADR-E-2)
        if callable(getattr(scene, "selectedItems", None)) and hasattr(w, "schematic"):
            key = _schematic_key(window, scene)
            if key:
                return key
        explicit = w.property("help")
        if isinstance(explicit, str) and explicit:
            return explicit
        if w is getattr(window, "properties", None):
            key = _properties_key(window, w)
            if key:
                return key
        if w is getattr(window, "tree", None):
            key = _tree_key(window, w)
            if key:
                return key
        if isinstance(w, QDockWidget):
            panel = w.property("panel")
            if panel:
                return f"panel:{panel}"
        w = w.parentWidget()
    return f"page:{START}"


def _schematic_key(window, scene) -> str:
    """The one node selected on the schematic's canvas: an operator's entry
    (`operator:<type>`), a load's (`feature:<type>`) or its object kind's."""
    try:
        nodes = [i.node for i in scene.selectedItems() if hasattr(i, "node")]
    except RuntimeError:  # the scene went
        return ""
    if len(nodes) != 1:
        return ""
    node = nodes[0]
    if node.path:  # a feature in its component (ADR 0071 decision 8)
        target = _walk(window, node.object, node.path)
        if target is not None and getattr(target, "type", None):
            return f"feature:{target.type}"
    return _object_key(window, node.object)


def _properties_key(window, panel) -> str:
    from puzzle.gui.properties import PATH

    item = panel.currentItem()
    while item is not None:
        data = item.data(1, PATH)
        path = data[0] if isinstance(data, tuple) else data
        if isinstance(path, str) and path:
            names = [p for p in path.split(".") if p and not p.isdigit()]
            if names:
                return f"field:{names[-1]}"
        item = item.parent()
    return _object_key(window, getattr(panel, "object_id", ""))


def _object_key(window, object_id: str) -> str:
    session = getattr(window, "session", None)
    if session is None or not object_id:
        return ""
    obj = next((o for o in session.project.objects.values() if str(o.id) == str(object_id)), None)
    if obj is None:
        return ""
    if getattr(obj, "kind", "") == "operator":
        return f"operator:{obj.type}"
    return f"kind:{obj.kind}"


def _tree_key(window, tree) -> str:
    from puzzle.gui.tree import ROLE

    item = tree.currentItem()
    node = item.data(0, ROLE) if item is not None else None
    if node is None:
        return ""
    if node.kind == "feature":
        target = _walk(window, node.object, node.path)
        if target is not None and getattr(target, "type", None):
            return f"feature:{target.type}"
    if node.kind == "section":
        return f"section:{node.label.lower()}"
    if node.kind in ("selection", "part", "physics", "step", "run"):
        return f"tree:{node.kind}"
    return _object_key(window, node.object)


def _walk(window, object_id: str, path: str):
    key = _object_key(window, object_id)
    if not key:
        return None
    obj = next(o for o in window.session.project.objects.values() if str(o.id) == str(object_id))
    for part in [p for p in path.split(".") if p]:
        try:
            obj = obj[int(part)] if part.isdigit() else getattr(obj, part)
        except (AttributeError, IndexError, KeyError, TypeError):
            return None
    return obj


def show_help(window, key: str | None = None) -> HelpWindow:
    """The one Help window (made on first use), at `key`'s page."""
    viewer = getattr(window, "_help_window", None)
    if viewer is None:
        viewer = HelpWindow(window)
        window._help_window = viewer  # kept, so Qt doesn't drop it
    if key:
        viewer.show_key(key) or viewer.show_page(START)
    viewer.show()
    viewer.raise_()
    viewer.activateWindow()
    return viewer


def install(window) -> None:
    """F1 anywhere in the window opens the help for what has focus."""
    from PySide6.QtGui import QShortcut

    shortcut = QShortcut(QKeySequence(Qt.Key.Key_F1), window)
    shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
    shortcut.activated.connect(lambda: show_help(window, help_key(window)))
    window._help_shortcut = shortcut
    window._menu_help = _MenuHelp(window)  # F1 on an open menu's item (ADR-E-2)
    QApplication.instance().installEventFilter(window._menu_help)


def show_menu_help(window, menu_path: str) -> HelpWindow:
    """The page that explains a menu item: the guide naming it, at its heading."""
    from puzzle import help as manual

    found = manual.menu_place(menu_path)
    viewer = show_help(window)
    if found is None:
        viewer.search(menu_path.rsplit(" → ", 1)[-1].rstrip("…"))
    else:
        viewer.show_page(found[0].name, found[1])
    return viewer


class _MenuHelp(QObject):
    """F1 while a menu bar menu is open: Qt gives the key to the menu, so no shortcut
    fires. The highlighted item's menu path finds its page (`help.menu_place`)."""

    def __init__(self, window) -> None:
        super().__init__(window)
        self.window_ = window

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt's name
        from PySide6.QtCore import QEvent
        from PySide6.QtWidgets import QMenu

        if (event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_F1
                and isinstance(obj, QMenu) and obj.activeAction() is not None):  # fmt: skip
            from puzzle.gui import main_menu

            path = main_menu.path_of(self.window_, obj.activeAction(), obj)
            if path is not None:
                for menu in QApplication.topLevelWidgets():
                    if isinstance(menu, QMenu) and menu.isVisible():
                        menu.close()
                show_menu_help(self.window_, path)
                return True
        return False
