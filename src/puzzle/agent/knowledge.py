"""The domain pack the agent searches (ADR 0022 decision 3): retrieval, not fine-tuning.

Sources, each cut into passages at its headings:
- the agent skills (`agent/skills/*.md`);
- the curated solver notes (`agent/notes/*.md`): Puzzle's gotchas per solver, written for
  Puzzle, and English summaries of French manual sections with a link to the manual;
- the capability matrices, written from each adapter's `Capabilities` as it is now;
- Code_Aster's French messages with their English (`progress.TRANSLATIONS`);
- the ADRs (`docs/adr`), when Puzzle runs from a source checkout.

Search is BM25 over words, which needs nothing installed. With an embedding model on
the local endpoint (`agent.toml` `embed_model`, e.g. "nomic-embed-text" on Ollama),
the best lexical hits are re-ranked by meaning; without one, lexical results stand.
Each hit carries its source and heading, so an answer can cite it.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from functools import cache
from pathlib import Path

HERE = Path(__file__).parent
ADRS = Path(__file__).resolve().parents[3] / "docs" / "adr"
WORD = re.compile(r"[a-z0-9_]+")
_STOP = (
    "the a an of to in on and or is are be it its this that with for as by at from not no "
    "can do does how what when which who why"
)
STOP = frozenset(_STOP.split(" "))


@dataclass(frozen=True)
class Passage:
    source: str  # "skill bolts", "note code_aster", "capabilities calculix", "ADR 0047"
    heading: str
    text: str
    link: str = ""  # a manual's URL, for notes that summarise one

    def cite(self) -> str:
        return f"{self.source} § {self.heading}" if self.heading else self.source


def _split(source: str, text: str, link: str = "") -> list[Passage]:
    """A Markdown file into passages, one per heading (front matter dropped)."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        meta = text[3:end] if end > 0 else ""
        m = re.search(r"^link:\s*(\S+)", meta, re.M)
        link = link or (m.group(1) if m else "")
        text = text[end + 4 :] if end > 0 else text
    out, heading, lines = [], "", []
    for line in text.splitlines():
        if line.startswith("#"):
            if "".join(lines).strip():
                out.append(Passage(source, heading, "\n".join(lines).strip(), link))
            heading, lines = line.lstrip("#").strip(), []
        else:
            lines.append(line)
    if "".join(lines).strip():
        out.append(Passage(source, heading, "\n".join(lines).strip(), link))
    return out


def _shown(v) -> str:
    """A capability's value in words: sets as sorted lists, tables as key: values."""
    if isinstance(v, (set, frozenset, list, tuple)):
        return ", ".join(sorted(map(str, v)))
    if isinstance(v, dict):
        return "; ".join(f"{k}: {_shown(x)}" for k, x in sorted(v.items()))
    return str(v)


def _capabilities() -> list[Passage]:
    """Each solver's capability matrix, from its adapter as it is now."""
    from dataclasses import asdict, is_dataclass

    from puzzle.adapters import SOLVER_NAMES, available_adapters

    out = []
    for name, adapter in available_adapters().items():
        caps = getattr(adapter, "capabilities", None)
        if caps is None:
            continue
        fields = asdict(caps) if is_dataclass(caps) else dict(getattr(caps, "__dict__", {}))
        name = SOLVER_NAMES.get(name, name)
        lines = [f"- {k}: {_shown(v)}" for k, v in sorted(fields.items())
                 if v not in (None, (), [], {}, frozenset(), set())]  # fmt: skip
        out.append(Passage(f"capabilities {name}", f"what {name} can do", "\n".join(lines)))
    return out


def _translations() -> list[Passage]:
    """Code_Aster's French messages and what they say in English (the progress
    translator's table, so the two never drift apart)."""
    from puzzle.progress import TRANSLATIONS

    lines = [f"- {fr.replace(chr(92), '')} → {en}" for fr, en in TRANSLATIONS]
    return [Passage("translations code_aster", "Code_Aster's French messages in English",
                    "\n".join(lines), "https://code-aster.org/doc/")]  # fmt: skip


@cache
def passages() -> tuple[Passage, ...]:
    out: list[Passage] = []
    for path in sorted((HERE / "skills").glob("*.md")):
        out += _split(f"skill {path.stem}", path.read_text())
    for path in sorted((HERE / "notes").glob("*.md")):
        out += _split(f"note {path.stem}", path.read_text())
    out += _capabilities()
    out += _translations()
    if ADRS.is_dir():  # a source checkout: the decisions themselves
        for path in sorted(ADRS.glob("[0-9][0-9][0-9][0-9]-*.md")):
            out += _split(f"ADR {path.name[:4]}", path.read_text())
    return tuple(p for p in out if p.text)


def _words(text: str) -> list[str]:
    return [w for w in WORD.findall(text.lower()) if w not in STOP]


@cache
def _index():
    docs = [_words(f"{p.source} {p.heading} " * 3 + p.text) for p in passages()]  # titles count
    df = Counter(w for d in docs for w in set(d))
    lengths = [len(d) for d in docs]
    avg = sum(lengths) / max(1, len(lengths))
    return docs, df, lengths, avg


def _bm25(query: str, k1: float = 1.4, b: float = 0.75) -> list[tuple[float, int]]:
    docs, df, lengths, avg = _index()
    n = len(docs)
    terms = _words(query)
    scores = []
    for i, d in enumerate(docs):
        tf = Counter(d)
        s = 0.0
        for t in terms:
            if t not in tf:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            s += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * lengths[i] / avg))
        if s > 0:
            scores.append((s, i))
    return sorted(scores, reverse=True)


def search(query: str, limit: int = 5, embed=None) -> list[dict]:
    """The passages that best answer `query`, with where each is from. `embed`, a
    function from texts to vectors, re-ranks the best lexical hits by meaning."""
    hits = _bm25(query)[: max(limit * 4, 20)]
    every = passages()
    if embed is not None and hits:
        try:
            vectors = embed([query] + [every[i].text[:2000] for _s, i in hits])
            q, rest = vectors[0], vectors[1:]
            hits = sorted(((_cos(q, v), i) for (_s, i), v in zip(hits, rest, strict=True)),
                          reverse=True)  # fmt: skip
        except Exception:  # noqa: BLE001 - no embedding model answering: words stand
            pass
    out = []
    for score, i in hits[:limit]:
        p = every[i]
        out.append({"source": p.source, "heading": p.heading, "cite": p.cite(),
                    "text": p.text[:1500], "link": p.link,
                    "score": round(float(score), 3)})  # fmt: skip
    return out


def _cos(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def ollama_embedder(base_url: str, model: str):
    """Texts to vectors through Ollama's /api/embed (None when the model is unset)."""
    if not model:
        return None
    import json
    import urllib.request

    root = base_url.rstrip("/")
    root = root[: -len("/v1")] if root.endswith("/v1") else root

    def embed(texts: list[str]) -> list[list[float]]:
        body = json.dumps({"model": model, "input": texts}).encode()
        req = urllib.request.Request(root + "/api/embed", body,
                                     {"Content-Type": "application/json"})  # fmt: skip
        with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310 - a local endpoint
            return json.loads(r.read())["embeddings"]

    return embed
