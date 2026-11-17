"""The manual outside the app (ADR 0083 decision 2, step 5): a static site and a PDF built
from the same pages the Help window shows.

- `build_site(out)`: one HTML file per page (`guides/meshing.html`), `index.html` (the
  contents), `search.js` with a heading index searched in the browser, `style.css`
  (light and dark), every equation drawn by matplotlib's mathtext as an inline SVG,
  exactly as the Help window draws it. `help:` links go to the page and heading they
  name (a help key through `help.lookup`); `puzzle:` links, which open a tool in the
  app, become plain text, since a browser has no tool to open.
- `build_pdf(path)`: every page in the contents' order in one document, a page break
  before each, equations as PNG images, printed by Qt (`QTextDocument` to `QPdfWriter`).
  `help:` links and the contents list jump to their page and heading (Qt writes a named
  destination for each `<a name>` and a link for each `#` href); bookmarks (sections,
  pages, their headings) are added afterwards as an incremental update, since Qt has no
  outline API.

Markdown is read with markdown-it-py (CommonMark and tables), which the `mcp` package
already brings in. Nothing here runs in the GUI: `scripts/build_help_site.py` calls it.
"""

from __future__ import annotations

import base64
import html
import io
import json
import re
from pathlib import Path

from puzzle import help as manual
from puzzle.help import pictures

SLUG = re.compile(r"[^a-z0-9]+")
LINK = re.compile(r'href="(help|puzzle):([^"]*)"')
PUZZLE_LINK = re.compile(r'<a href="puzzle:[^"]*">(.*?)</a>', re.S)
HELP_LINK = re.compile(r'<a href="help:([^"]*)">(.*?)</a>', re.S)
# A picture as markdown-it writes `![alt](image:name)`.
PICTURE = re.compile(r'<img src="image:([^"]+)" alt="([^"]*)"\s*/?>')
PICTURE_WIDTH = 560
# A heading as page_html writes it: help keys' anchors before it, then its own id.
ANCHORED = re.compile(r'((?:<a id="[^"]+"></a>)*)<h([1-6]) id="([^"]+)">(.*?)</h\2>', re.S)


def slug(text: str) -> str:
    """A heading's anchor: lower case, runs of other characters as one hyphen."""
    return SLUG.sub("-", text.lower()).strip("-") or "section"


def _markdown():
    from markdown_it import MarkdownIt

    return MarkdownIt("commonmark", {"html": False}).enable("table")


def _tex(tex: str, display: bool, fmt: str) -> bytes | None:
    """An equation drawn by mathtext (SVG or PNG), or None when it doesn't parse."""
    from matplotlib import mathtext

    buf = io.BytesIO()
    try:
        mathtext.math_to_image(f"${tex}$", buf, format=fmt, dpi=200 if fmt == "png" else 100,
                               prop=None)  # fmt: skip
    except ValueError:
        return None
    return buf.getvalue()


def _equation(tex: str, display: bool, fmt: str) -> str:
    data = _tex(tex, display, fmt)
    if data is None:
        return html.escape(f"${tex}$")
    kind = "svg+xml" if fmt == "svg" else "png"
    src = f"data:image/{kind};base64,{base64.b64encode(data).decode()}"
    cls = "display" if display else "inline"
    size = ""
    if fmt == "png":  # drawn at 200 dpi; Qt lays pages out at 96 px an inch
        import struct

        w, h = struct.unpack(">II", data[16:24])
        size = f' width="{round(w * 96 / 200)}" height="{round(h * 96 / 200)}"'
    tag = f'<img class="tex {cls}" alt="{html.escape(tex)}" src="{src}"{size}>'
    return f'<p class="equation">{tag}</p>' if display else tag


def page_html(page: manual.Page, fmt: str = "svg") -> str:
    """A page's body as HTML: TeX as images, headings with anchors (their slug and each
    help key marked before them), links still in help:/puzzle: form."""
    from puzzle.gui.agent_view import DISPLAY_TEX, INLINE_TEX, segments

    md, holes = _markdown(), {}

    def hole(markup: str) -> str:  # equations pass through Markdown as placeholders
        key = f"TEXHOLE{len(holes)}X"
        holes[key] = markup
        return key

    keyed, lines = {}, []
    pending: list[str] = []
    for line in pictures.with_pictures(page.body).splitlines():
        marker = manual.MARKER.match(line)
        if marker:
            pending += [k.strip() for k in marker.group(1).split(",") if k.strip()]
            continue
        heading = manual.HEADING.match(line)
        if heading and pending:
            keyed[heading.group(2).strip()] = pending
            pending = []
        if manual.COMMENT.match(line):
            continue
        lines.append(line)
    parts = []
    for s in segments("\n".join(lines)):
        if s.kind == "code":
            parts.append(f"```{s.language}\n{s.text}\n```")
            continue
        text = DISPLAY_TEX.sub(
            lambda m: "\n\n" + hole(_equation(m.group(1).strip(), True, fmt)) + "\n\n", s.text
        )
        parts.append(INLINE_TEX.sub(lambda m: hole(_equation(m.group(1).strip(), False, fmt)),
                                    text))  # fmt: skip
    body = md.render("\n\n".join(parts))
    for key, markup in holes.items():
        body = body.replace(f"<p>{key}</p>", markup).replace(key, markup)
    body = PICTURE.sub(_picture, body)

    def anchor(m: re.Match) -> str:
        level, text = m.group(1), m.group(2)
        plain = re.sub(r"<[^>]+>", "", text)
        ids = [slug(html.unescape(plain))] + [slug(k) for k in keyed.get(html.unescape(plain), [])]
        extra = "".join(f'<a id="{i}"></a>' for i in ids[1:])
        return f'{extra}<h{level} id="{ids[0]}">{text}</h{level}>'

    return re.sub(r"<h([1-6])>(.*?)</h\1>", anchor, body, flags=re.S)


def _picture(m: re.Match) -> str:
    """A picture (`image:` src) as a PNG data URL with its size, no wider than a page's
    text (about 580 px at 96 an inch: A4 less the page's margins and the 2 cm Qt's print
    keeps inside them), so the PDF holds it whole."""
    import struct

    name, alt = m.group(1), m.group(2)
    file = pictures.path(name)
    if not file.is_file():
        return html.escape(alt)  # tests/test_help_images.py names it
    data = file.read_bytes()
    w, h = struct.unpack(">II", data[16:24])
    if w > PICTURE_WIDTH:
        w, h = PICTURE_WIDTH, round(h * PICTURE_WIDTH / w)
    src = f"data:image/png;base64,{base64.b64encode(data).decode()}"
    return f'<img class="picture" alt="{alt}" src="{src}" width="{w}" height="{h}">'


def resolve(target: str) -> tuple[str, str] | None:
    """(page name, anchor) a help: link's target names: a page, a page#heading, or a
    help key (through `help.lookup`); None when nothing matches."""
    name, _, heading = target.partition("#")
    pages = manual.pages()
    if name in pages:
        return name, slug(heading) if heading else ""
    found = manual.lookup(name)
    if found is None:
        return None
    page, at = found
    return page.name, slug(name) if at else ""


def _relative(src: str, dest: str) -> str:
    """dest's .html relative to the folder of src's."""
    up = "../" * src.count("/")
    return f"{up}{dest}.html"


def _links(body: str, name: str, missing: list[str]) -> str:
    body = PUZZLE_LINK.sub(lambda m: f'<span class="tool">{m.group(1)}</span>', body)

    def fix(m: re.Match) -> str:
        found = resolve(m.group(2))
        if found is None:
            missing.append(f"{name}: help:{m.group(2)}")
            return 'href="#"'
        page, at = found
        return f'href="{_relative(name, page)}{"#" + at if at else ""}"'

    return LINK.sub(fix, body)


STYLE = """
:root { --bg: #ffffff; --fg: #1d1d1f; --muted: #5f6368; --rule: #dadce0; --side: #f6f7f9;
  --link: #1a5fb4; --code: #f1f3f4; }
@media (prefers-color-scheme: dark) { :root { --bg: #17181a; --fg: #e8eaed;
  --muted: #9aa0a6; --rule: #3c4043; --side: #202124; --link: #8ab4f8; --code: #26282b; }
  img.tex { filter: invert(1) hue-rotate(180deg); } }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg); font: 16px/1.55 system-ui,
  -apple-system, "Segoe UI", sans-serif; display: flex; min-height: 100vh; }
nav { width: 280px; flex: none; background: var(--side); border-right: 1px solid var(--rule);
  padding: 16px; overflow-y: auto; position: sticky; top: 0; height: 100vh; }
nav h2 { font-size: 13px; text-transform: uppercase; letter-spacing: .04em;
  color: var(--muted); margin: 18px 0 6px; }
nav a { display: block; padding: 2px 0; color: var(--fg); text-decoration: none; }
nav a.here { font-weight: 600; color: var(--link); }
nav input { width: 100%; padding: 6px 8px; border: 1px solid var(--rule); border-radius: 6px;
  background: var(--bg); color: var(--fg); }
#hits a { font-size: 14px; } #hits small { color: var(--muted); display: block; }
main { flex: 1; min-width: 0; max-width: 860px; padding: 24px 32px 64px; }
a { color: var(--link); } .tool { font-weight: 600; }
code, pre { background: var(--code); border-radius: 4px; font: 14px ui-monospace,
  SFMono-Regular, Menlo, Consolas, monospace; } code { padding: 1px 4px; }
pre { padding: 10px 12px; overflow-x: auto; }
table { border-collapse: collapse; display: block; overflow-x: auto; margin: 12px 0; }
th, td { border: 1px solid var(--rule); padding: 4px 8px; text-align: left; vertical-align: top; }
img.picture { max-width: 100%; height: auto; border: 1px solid var(--rule);
  border-radius: 4px; }
p.equation { text-align: center; overflow-x: auto; } img.tex.inline { vertical-align: middle; }
.summary { color: var(--muted); }
@media (max-width: 760px) { body { display: block; } nav { width: auto; height: auto;
  position: static; border-right: 0; border-bottom: 1px solid var(--rule); }
  main { padding: 16px; } }
"""

SEARCH = """
(function () {
  var box = document.getElementById("q"), out = document.getElementById("hits");
  var root = document.body.getAttribute("data-root");
  box.addEventListener("input", function () {
    var words = box.value.toLowerCase().split(/\\s+/).filter(Boolean);
    out.innerHTML = "";
    if (!words.length) return;
    var found = INDEX.filter(function (e) {
      var text = (e.title + " " + e.heading + " " + e.text).toLowerCase();
      return words.every(function (w) { return text.indexOf(w) >= 0; });
    }).slice(0, 25);
    found.forEach(function (e) {
      var a = document.createElement("a");
      a.href = root + e.url;
      a.textContent = e.heading || e.title;
      if (e.heading) { var s = document.createElement("small"); s.textContent = e.title;
        a.appendChild(s); }
      out.appendChild(a);
    });
  });
})();
"""


def _nav(here: str, root: str) -> str:
    rows = ['<input id="q" type="search" placeholder="Search the manual" '
            'aria-label="Search the manual"><div id="hits"></div>']  # fmt: skip
    rows.append(f'<a href="{root}index.html"><strong>Puzzle manual</strong></a>')
    for heading, listed in manual.contents():
        rows.append(f"<h2>{html.escape(heading)}</h2>")
        for p in listed:
            cls = ' class="here"' if p.name == here else ""
            rows.append(f'<a{cls} href="{root}{p.name}.html">{html.escape(p.title)}</a>')
    return "<nav>" + "\n".join(rows) + "</nav>"


def _document(title: str, nav: str, main: str, root: str) -> str:
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(title)} · Puzzle manual</title>"
        f'<link rel="stylesheet" href="{root}style.css"></head>'
        f'<body data-root="{root}">{nav}<main>{main}</main>'
        f'<script src="{root}search.js"></script></body></html>\n'
    )


def build_site(out: Path) -> dict:
    """Write the site into `out`; {"pages": n, "missing": [links that resolve nowhere]}."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    missing: list[str] = []
    index = []
    pages = manual.pages()
    for name, page in pages.items():
        root = "../" * name.count("/")
        body = _links(page_html(page, "svg"), name, missing)
        head = f"<h1>{html.escape(page.title)}</h1>"
        if page.summary:
            head += f'<p class="summary">{html.escape(page.summary)}</p>'
        target = out / f"{name}.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_document(page.title, _nav(name, root), head + body, root),
                          encoding="utf-8", newline="\n")  # fmt: skip
        index.append({"url": f"{name}.html", "title": page.title, "heading": "",
                      "text": page.summary})  # fmt: skip
        for p in manual.passages():
            if p.page == name and p.heading:
                index.append({"url": f"{name}.html#{slug(p.heading)}", "title": page.title,
                              "heading": p.heading, "text": p.text[:400]})  # fmt: skip
    rows = []
    for heading, listed in manual.contents():
        rows.append(f"<h2>{html.escape(heading)}</h2><ul>")
        rows += [f'<li><a href="{p.name}.html">{html.escape(p.title)}</a>'
                 f'{" · " + html.escape(p.summary) if p.summary else ""}</li>'
                 for p in listed]  # fmt: skip
        rows.append("</ul>")
    intro = ("<h1>Puzzle manual</h1><p class=\"summary\">The same pages as the app's Help "
             "window: getting started, guides, the reference generated from the code, theory "
             "notes and verification.</p>")  # fmt: skip
    (out / "index.html").write_text(_document("Contents", _nav("", ""), intro + "".join(rows),
                                              ""), encoding="utf-8", newline="\n")  # fmt: skip
    (out / "style.css").write_text(STYLE.lstrip(), encoding="utf-8", newline="\n")
    (out / "search.js").write_text("var INDEX = " + json.dumps(index, ensure_ascii=False)
                                   + ";\n" + SEARCH, encoding="utf-8", newline="\n")  # fmt: skip
    return {"pages": len(pages), "missing": missing}


def build_pdf(path: Path) -> dict:
    """Every page in the contents' order, one PDF, its links and bookmarks jumping to
    pages and headings; {"pages": n pages of the manual, "missing": links that resolve
    nowhere}."""
    import os

    import matplotlib
    from PySide6.QtCore import QMarginsF, QUrl
    from PySide6.QtGui import (
        QFont,
        QFontDatabase,
        QPageLayout,
        QPageSize,
        QPdfWriter,
        QTextDocument,
    )

    # Qt's offscreen platform finds no fonts by itself (every glyph a box): matplotlib's
    # DejaVu fonts are in every install, so the PDF looks the same wherever it's built.
    fonts = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
    os.environ.setdefault("QT_QPA_FONTDIR", str(fonts))
    from PySide6.QtWidgets import QApplication  # the tests' teardown expects widgets

    app = QApplication.instance() or QApplication(["puzzle-help"])  # noqa: F841
    for f in fonts.glob("DejaVu*.ttf"):
        QFontDatabase.addApplicationFont(str(f))
    missing: list[str] = []
    chapters, outline, count = [], [], 0
    for i, (heading, listed) in enumerate(manual.contents()):
        part = f"part-{i}"
        chapters.append(f'<h1 style="page-break-before: always"><a name="{part}"></a>'
                        f"{html.escape(heading)}</h1>")  # fmt: skip
        entries = []
        for p in listed:
            body, sections = _pdf_body(p, missing)
            chapters.append(f'<h1 style="page-break-before: always"><a name="{p.name}"></a>'
                            f"{html.escape(p.title)}</h1>" + body)  # fmt: skip
            entries.append((p.title, p.name, [(t, d, []) for t, d in sections]))
            count += 1
        outline.append((heading, part, entries))
    cover = ('<h1 style="font-size: 32pt">Puzzle manual</h1><p>Getting started, guides, '
             "the reference, theory notes and verification, as the app's Help window shows "
             "them.</p>")  # fmt: skip
    listed = "".join(
        f'<h3><a href="#part-{i}">{html.escape(heading)}</a></h3><p>'
        + "<br>".join(f'<a href="#{p.name}">{html.escape(p.title)}</a>' for p in pages)
        + "</p>"
        for i, (heading, pages) in enumerate(manual.contents())
    )
    cover += '<h2 style="page-break-before: always">Contents</h2>' + listed
    document = QTextDocument()
    document.setDefaultFont(QFont("DejaVu Sans", 10))
    document.setDefaultStyleSheet(
        "body { font-size: 10pt; } h1 { font-size: 18pt; } h2 { font-size: 14pt; } "
        "h3 { font-size: 12pt; } td, th { border: 1px solid #999; padding: 2px; } "
        "pre, code { font-family: 'DejaVu Sans Mono'; font-size: 9pt; }"
    )
    document.setHtml("<html><body>" + cover + "".join(chapters) + "</body></html>")
    document.setBaseUrl(QUrl("about:blank"))
    writer = QPdfWriter(str(path))
    writer.setTitle("Puzzle manual")
    writer.setPageLayout(
        QPageLayout(
            QPageSize(QPageSize.PageSizeId.A4),
            QPageLayout.Orientation.Portrait,
            QMarginsF(18, 18, 18, 18),
            QPageLayout.Unit.Millimeter,
        )
    )
    writer.setResolution(150)
    document.print_(writer)
    del writer  # closes the file before the bookmarks are appended
    add_outline(Path(path), outline)
    return {"pages": count, "missing": missing}


def _destination(page: str, anchor: str = "") -> str:
    """A PDF named destination: the page's name, or page~heading for a heading in it."""
    return f"{page}~{anchor}" if anchor else page


def _pdf_body(page: manual.Page, missing: list[str]) -> tuple[str, list[tuple[str, str]]]:
    """A page's HTML for the PDF, with its anchors named across the whole manual and its
    help: links jumping to them; plus (text, destination) for each second-level heading,
    for the bookmarks."""
    body = PUZZLE_LINK.sub(lambda m: m.group(1), page_html(page, "png"))

    def link(m: re.Match) -> str:
        found = resolve(m.group(1))
        if found is None:
            missing.append(f"{page.name}: help:{m.group(1)}")
            return m.group(2)
        return f'<a href="#{_destination(*found)}">{m.group(2)}</a>'

    body = HELP_LINK.sub(link, body)
    sections: list[tuple[str, str]] = []

    def heading(m: re.Match) -> str:
        extra, level, own, text = m.groups()
        # Qt writes one destination per run of text (the first of its anchor names), so
        # each help key's anchor holds a zero-width space of its own.
        keys = re.findall(r'<a id="([^"]+)"></a>', extra)
        marks = "".join(f'<a name="{_destination(page.name, k)}">&#8203;</a>' for k in keys)
        marks += f'<a name="{_destination(page.name, own)}"></a>'
        if level == "2":
            plain = html.unescape(re.sub(r"<[^>]+>", "", text)).strip()
            sections.append((plain, _destination(page.name, own)))
        return f"<h{level}>{marks}{text}</h{level}>"

    return ANCHORED.sub(heading, body), sections


def _pdf_text(text: str) -> bytes:
    """A PDF text string as Qt writes its destinations' names: UTF-16BE with a BOM."""
    return b"<FEFF" + text.encode("utf-16-be").hex().upper().encode() + b">"


def add_outline(path: Path, tree: list) -> None:
    """Append bookmarks to a PDF Qt wrote, as an incremental update (new objects, a new
    catalog naming them, an xref section and a trailer pointing at the old one).

    `tree` is [(title, destination, children)], destinations being the document's named
    destinations; top-level entries are shown open, the rest closed."""
    data = path.read_bytes()
    prev = int(re.findall(rb"startxref\s+(\d+)", data)[-1])
    trailer = data[data.rindex(b"trailer") + len(b"trailer") : data.rindex(b"startxref")]
    size = int(re.search(rb"/Size (\d+)", trailer).group(1))
    root = int(re.search(rb"/Root (\d+) 0 R", trailer).group(1))
    catalog = re.search(rb"\n%d 0 obj\s*<<(.*?)>>\s*endobj" % root, data, re.S).group(1)
    objects: dict[int, bytes] = {}
    counter = [size]

    def new() -> int:
        counter[0] += 1
        return counter[0] - 1

    def place(items: list, parent: int, open_: bool) -> tuple[list[int], int]:
        """Numbers for items under parent, and how many of them show when opened."""
        numbers = [new() for _ in items]
        shown = len(items)
        for k, (title, dest, children) in enumerate(items):
            parts = [b"/Title " + _pdf_text(title), b"/Parent %d 0 R" % parent,
                     b"/Dest " + _pdf_text(dest)]  # fmt: skip
            if k:
                parts.append(b"/Prev %d 0 R" % numbers[k - 1])
            if k < len(items) - 1:
                parts.append(b"/Next %d 0 R" % numbers[k + 1])
            if children:
                kids, below = place(children, numbers[k], False)
                parts += [b"/First %d 0 R" % kids[0], b"/Last %d 0 R" % kids[-1],
                          b"/Count %d" % (below if open_ else -len(kids))]  # fmt: skip
                if open_:
                    shown += below
            objects[numbers[k]] = b"<<\n" + b"\n".join(parts) + b"\n>>"
        return numbers, shown

    top = new()
    firsts, shown = place(tree, top, True)
    objects[top] = (b"<<\n/Type /Outlines\n/First %d 0 R\n/Last %d 0 R\n/Count %d\n>>"
                    % (firsts[0], firsts[-1], shown))  # fmt: skip
    objects[root] = b"<<" + catalog + b"/Outlines %d 0 R\n/PageMode /UseOutlines\n>>" % top
    out = bytearray(data if data.endswith(b"\n") else data + b"\n")
    offsets = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += b"%d 0 obj\n" % number + objects[number] + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 1\n0000000000 65535 f \n"  # readers expect object 0 first
    numbers = sorted(offsets)
    runs: list[list[int]] = []
    for number in numbers:
        if runs and number == runs[-1][-1] + 1:
            runs[-1].append(number)
        else:
            runs.append([number])
    for run in runs:
        out += b"%d %d\n" % (run[0], len(run))
        out += b"".join(b"%010d 00000 n \n" % offsets[n] for n in run)
    trailer = re.sub(rb"/Size \d+", b"/Size %d" % counter[0], trailer.strip())
    trailer = trailer.replace(b"<<", b"<<\n/Prev %d" % prev, 1)
    out += b"trailer\n" + trailer + b"\nstartxref\n%d\n%%%%EOF\n" % xref
    path.write_bytes(bytes(out))
