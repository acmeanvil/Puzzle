"""A solve's own logs, found in its run folder and read whole (the GUI's Output tab keeps
the last lines only): for the log window, `puzzle log` and MCP `run_log`.

A run folder is `.puzzle/runs/<study>-<UTC time>` (`Session._run`); a stored run finds
its own by the study's name then and its start time. Lines are classed as errors,
warnings or good news by each solver's own markers, so the window can colour them and
jump between problems, and the agent can ask for the problems only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

# The logs worth reading, by name, in the order shown: what the solver printed first,
# then each solver's own message files (Code_Aster's .mess, CalculiX's .sta and .cvg,
# OpenFOAM's log.<application>).
PATTERNS = ("solver.log", "*.mess", "*.sta", "*.cvg", "log.*")
SKIP = {"mesh", "constant", "system", "processor0"}  # big folders holding no logs
SHOWN_BYTES = 4_000_000  # the window shows the end of a larger file; Save as keeps all

ERROR = re.compile(
    r"\*ERROR|<F>|<E>|<S>|<EXCEPTION>|_ERROR\b|ERROR::|FOAM FATAL|\bfatal\b|Traceback"
    r"|Segmentation fault|\berror\b|\bfailed\b|\bdiverged?\b|\bsingular\b"
    r"|\b[A-Z]\w*(?:Error|Exception):",  # Python's (the FEniCSx script, the GUI's log)
    re.IGNORECASE,
)
WARNING = re.compile(
    r"\*WARNING|<A>|WARNING::|FOAM Warning|\bwarning\b|not converged"
    r"|did not converge|cut[- ]?back|\bcutting\b",
    re.IGNORECASE,
)
GOOD = re.compile(
    r"\bconverged\b|\bconvergence (?:reached|achieved)\b|Job finished|ALL DONE|\bEnd\b$"
    r"|EXECUTION_CODE_ASTER_EXIT_\S*=0|SIMPLE solution converged|<I>.*fin",
    re.IGNORECASE,
)
# Lines that name the word but report none ("0 errors", "relative error 1e-9"), and
# Code_Aster's command file echoed (EVENEMENT='ERREUR', ALARME='OUI'): its messages
# carry codes (<F>, <E>, <S>, <A>), which are what count there.
QUIET = re.compile(
    r"\b0 (?:errors?|warnings?)\b|\b(?:relative|estimated) error\b|='?(?:ERREUR|OUI|NON)'?"
    r"|\berreur\b",
    re.IGNORECASE,
)


def classify(line: str) -> str | None:
    """`"error"`, `"warning"`, `"good"` or None, by the solvers' markers."""
    if QUIET.search(line):
        return None
    if WARNING.search(line) and not re.search(r"\*ERROR|<F>|<E>|FATAL", line):
        return "warning"  # "not converged" before "converged"
    if ERROR.search(line):
        return "error"
    if GOOD.search(line):
        return "good"
    return None


def logs(folder: Path) -> list[Path]:
    """The log files in a run folder and its step folders, solver.log first."""
    if not folder.is_dir():
        return []
    found: list[Path] = []

    def walk(d: Path, depth: int) -> None:
        for pattern in PATTERNS:
            found.extend(p for p in sorted(d.glob(pattern)) if p.is_file() and p not in found)
        if depth < 3:
            for sub in sorted(p for p in d.iterdir() if p.is_dir() and p.name not in SKIP):
                walk(sub, depth + 1)

    walk(folder, 0)
    return found


def _stamp(folder: Path, slug: str) -> datetime | None:
    try:
        return datetime.strptime(folder.name[len(slug) + 1 :][:15], "%Y%m%d-%H%M%S").replace(
            tzinfo=UTC
        )
    except ValueError:
        return None


def folders(root: Path, study_name: str) -> list[Path]:
    """A study's run folders, oldest first."""
    from puzzle.commands.session import slug as slug_of
    from puzzle.project.layout import WORK_DIR

    slug = slug_of(study_name)
    out = [(t, p) for p in (root / WORK_DIR).glob(f"{slug}-*")
           if p.is_dir() and (t := _stamp(p, slug)) is not None]  # fmt: skip
    return [p for _, p in sorted(out)]


def folder_of(root: Path, record) -> Path | None:
    """A stored run's folder: its study's newest one made before the run started."""
    from puzzle.commands.session import slug as slug_of

    started = record.started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    slug = slug_of(record.study_name)
    before = [p for p in folders(root, record.study_name)
              if _stamp(p, slug) <= started + timedelta(seconds=5)]  # fmt: skip
    return before[-1] if before else None


def latest(root: Path, study_name: str) -> Path | None:
    """The study's newest attempt, failed or stopped ones too."""
    found = folders(root, study_name)
    return found[-1] if found else None


def find(root: Path, *, study: str | None = None, run: str | None = None) -> Path:
    """A run's folder by stored run id (a prefix will do), else a study's (name or id)
    newest attempt; `not_found` when there is none or it holds no logs."""
    from puzzle.errors import PuzzleError
    from puzzle.project.layout import RESULTS_DIR
    from puzzle.results.store import ResultStore

    root = Path(root)
    if run is not None:
        hits = [r for r in ResultStore(root / RESULTS_DIR).runs() if r.run_id.startswith(run)]
        if len(hits) != 1:
            raise PuzzleError(f"run '{run}' matches {len(hits)} runs", code="not_found")
        folder = folder_of(root, hits[0])
    else:
        from puzzle.commands import resolve
        from puzzle.model.schema import Study
        from puzzle.project import load_project

        folder = latest(root, resolve(load_project(root), study, Study).name)
    if folder is None or not logs(folder):
        raise PuzzleError("no solver logs kept for that run (its run folder is gone, or the "
                          "run was reused from an identical one)", code="not_found")  # fmt: skip
    return folder


def read_end(path: Path, limit: int = SHOWN_BYTES) -> tuple[str, bool]:
    """The file's text (its end when larger than `limit`) and whether it was cut."""
    size = path.stat().st_size
    with path.open("rb") as f:
        if size > limit:
            f.seek(size - limit)
            f.readline()  # from a whole line
        data = f.read()
    return data.decode("utf-8", errors="replace"), size > limit


@dataclass
class Line:
    file: str
    number: int  # 1-based in its file
    text: str
    kind: str | None


def lines(folder: Path, *, pattern: str | None = None, problems: bool = False,
          context: int = 0, tail: int | None = None, limit: int = 400) -> list[Line]:  # fmt: skip
    """The lines of a run's logs: matching `pattern` (a regular expression, any case),
    or the errors and warnings only, each with `context` lines around; else the last
    `tail` lines of each file. At most `limit` lines."""
    rx = re.compile(pattern, re.IGNORECASE) if pattern else None
    out: list[Line] = []
    for path in logs(folder):
        text, _ = read_end(path, 40_000_000)
        rows = text.splitlines()
        name = str(path.relative_to(folder))
        if rx is None and not problems:
            start = max(0, len(rows) - (tail or 60))
            out += [Line(name, i + 1, rows[i], classify(rows[i])) for i in range(start, len(rows))]
            continue
        keep: set[int] = set()
        for i, row in enumerate(rows):
            hit = rx.search(row) if rx is not None else classify(row) in ("error", "warning")
            if hit:
                keep.update(range(max(0, i - context), min(len(rows), i + context + 1)))
        out += [Line(name, i + 1, rows[i], classify(rows[i])) for i in sorted(keep)]
    return out[-limit:] if len(out) > limit else out


def counts(folder: Path) -> dict[str, int]:
    """Errors and warnings across a run's logs."""
    seen = {"error": 0, "warning": 0}
    for path in logs(folder):
        for row in read_end(path, 40_000_000)[0].splitlines():
            kind = classify(row)
            if kind in seen:
                seen[kind] += 1
    return seen
