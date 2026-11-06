"""Project archives (ADR 0061): one `.puzzle` file that carries a project to another
Puzzle, checked on the way in.

An archive is a zip holding the project folder as Save as copies it (`copy.save_as`:
outside geometry files brought into `imported/`, no lock, no solver working folders),
with the sender's choice of how much goes in (`Include`: results none, the latest run
per study, or all; versions; history), plus `archive.json`: the format, the Puzzle
that wrote it, when and by whom, the project's name, a summary as a version's
(counts, studies and their states, problems), what was included, and every member's
SHA-256. Materials need nothing: a project's materials are its own copies (ADR 0020).

`read` checks before it writes:
- the format (newer ones refused, naming the Puzzle they need);
- every member's hash;
- no member leaving the destination (no `..`, no absolute paths, no links), nor any
  file a version's manifest lists;
- a size limit against zip bombs (and on archive.json, read whole).

It then unpacks into a new or empty folder, drops a results store of an older format
(results are a cache by run key and solve again) with a note, and saves one version,
"imported from <archive>"; a failure on the way removes it again. Agents arrive at
"advise", whatever the sender allowed; a project holding code (script operators,
custom PDE equations) also arrives untrusted (ADR 0062) until the user trusts it.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from puzzle.errors import PuzzleError

FORMAT = 1
MANIFEST = "archive.json"
SUFFIX = ".puzzle"
RESULTS = ("none", "latest", "all")
MAX_BYTES = 20 * 1024**3  # unpacked, all members together
MAX_RATIO = 200  # unpacked / packed, for one member: a bomb, not a mesh
MAX_MANIFEST = 64 * 1024**2  # archive.json or a version's manifest, read whole
# Already compressed: stored, not deflated again.
STORED = {".zip", ".png", ".jpg", ".jpeg", ".gif", ".gz", ".xz", ".zst", ".parquet",
          ".h5", ".hdf5", ".npz", ".blob"}  # fmt: skip
WINDOWS_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
                    *(f"lpt{i}" for i in range(1, 10))}  # fmt: skip
WINDOWS_BAD = set('<>:"\\|?*')


@dataclass
class Include:
    """What goes in beside the model."""

    results: str = "latest"  # none, latest (per study and sweep point) or all
    versions: bool = True
    history: bool = True

    def __post_init__(self) -> None:
        if self.results not in RESULTS:
            raise PuzzleError(f"results is one of {', '.join(RESULTS)}, not '{self.results}'",
                              code="invalid")  # fmt: skip


@dataclass
class Written:
    path: Path
    bytes: int
    members: int
    sizes: dict[str, int] = field(default_factory=dict)  # model, results, versions, history

    def summary(self) -> str:
        parts = ", ".join(f"{k} {_mb(v)}" for k, v in self.sizes.items() if v)
        return f"Wrote {self.path.name}: {_mb(self.bytes)} ({parts})"


@dataclass
class Opened:
    root: Path
    name: str
    notes: list[str] = field(default_factory=list)
    holds_code: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return f"Opened '{self.name}' in {self.root}" + "".join(f"; {n}" for n in self.notes)


def _mb(n: int) -> str:
    return f"{n / 1e6:.1f} MB" if n >= 1e5 else f"{n / 1e3:.0f} kB"


def _part(rel: str) -> str:
    """Which part of the archive a member is, for the sizes shown."""
    top = rel.split("/", 1)[0]
    if top in ("results", ".puzzle"):
        return "results"
    if top == "versions":
        return "versions"
    if rel == "history.jsonl":
        return "history"
    return "model"


def sizes(root: Path) -> dict[str, int]:
    """Each part's size on disk now (the form shows them before writing)."""
    from puzzle.project.copy import KEPT_WORK
    from puzzle.project.layout import LOCK, WORK_DIR

    out = {"model": 0, "results": 0, "versions": 0, "history": 0}
    root = Path(root)
    for path in root.rglob("*"):
        if not path.is_file() or path == root / LOCK:
            continue
        rel = path.relative_to(root).as_posix()
        if rel.startswith(f"{WORK_DIR}/") and rel.split("/")[2] not in KEPT_WORK:
            continue
        out[_part(rel)] += path.stat().st_size
    return out


def _check_name(rel: str) -> None:
    """A member Windows can make: no reserved names, no : or ? and the like, no
    trailing dot or space (ADR 0061 decision 5)."""
    for piece in rel.split("/"):
        stem = piece.split(".")[0].lower()
        if stem in WINDOWS_RESERVED or WINDOWS_BAD & set(piece) or piece.endswith((".", " ")):
            raise PuzzleError(f"'{rel}' can't be made on Windows: rename it before archiving",
                              code="invalid", path=rel)  # fmt: skip


def write(root: Path, path: Path, include: Include | None = None, *,
          by: str = "you") -> Written:  # fmt: skip
    """The project at `root` as one archive at `path` (a .puzzle file)."""
    from puzzle.project import versions
    from puzzle.project.copy import save_as
    from puzzle.project.store import load_project

    include = include or Include()
    root, path = Path(root).resolve(), Path(path).resolve()
    if path.suffix != SUFFIX:
        path = path.with_suffix(SUFFIX)
    with tempfile.TemporaryDirectory(prefix="puzzle-archive-") as tmp:
        stage = Path(tmp) / "project"
        save_as(root, stage)  # outside files in, no lock, no working folders
        _prune(stage, include)
        project = load_project(stage)
        latest = versions.latest(stage)
        manifest = {
            "format": FORMAT,
            "puzzle": _puzzle_version(),
            "written_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "by": by,
            "name": project.name,
            "summary": latest.summary if latest is not None else {},
            "include": asdict(include),
            "members": {},
        }
        members = []
        for file in sorted(stage.rglob("*")):
            if file.is_symlink():
                raise PuzzleError(f"'{file.relative_to(stage)}' is a link: archives hold files "
                                  "only", code="invalid")  # fmt: skip
            if not file.is_file() or file == path:
                continue
            rel = file.relative_to(stage).as_posix()
            if rel == MANIFEST or file.suffix in (SUFFIX, ".part"):  # never archives in one
                continue
            _check_name(rel)
            members.append((rel, file))
            manifest["members"][rel] = _sha256(file)
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_suffix(".puzzle.part")
        written: dict[str, int] = {"model": 0, "results": 0, "versions": 0, "history": 0}
        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            z.writestr(MANIFEST, json.dumps(manifest, indent=1, sort_keys=True))
            for rel, file in members:
                kind = zipfile.ZIP_STORED if file.suffix.lower() in STORED else zipfile.ZIP_DEFLATED
                z.write(file, rel, compress_type=kind)
                written[_part(rel)] += file.stat().st_size
        part.replace(path)  # never a half-written archive
    return Written(path, path.stat().st_size, len(members), written)


def _prune(stage: Path, include: Include) -> None:
    """What the sender left out, removed from the staged copy."""
    from puzzle.project.layout import RESULTS_DIR, WORK_DIR

    if include.results == "none":
        shutil.rmtree(stage / RESULTS_DIR, ignore_errors=True)
        shutil.rmtree(stage / WORK_DIR, ignore_errors=True)  # its meshes go with them
    elif include.results == "latest" and (stage / RESULTS_DIR).exists():
        from puzzle.results.store import ResultStore

        store = ResultStore(stage / RESULTS_DIR)
        try:
            runs = store.runs()
        except PuzzleError:  # an older store: not worth carrying
            shutil.rmtree(stage / RESULTS_DIR, ignore_errors=True)
            runs = []
        newest: dict[tuple[str, str], str] = {}
        for r in runs:  # oldest first, so the last one per study and sweep point wins
            newest[(r.study_id, r.point or "")] = r.run_id
        for r in runs:  # a swept study keeps a run per point: its latest sweep, whole
            if newest.get((r.study_id, r.point or "")) != r.run_id:
                store.delete_run(r.run_id)
    if not include.versions:
        shutil.rmtree(stage / "versions", ignore_errors=True)
    if not include.history:
        (stage / "history.jsonl").unlink(missing_ok=True)


def _puzzle_version() -> str:
    from puzzle import __version__

    return __version__


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _small(z: zipfile.ZipFile, member: str) -> bytes:
    """A member read whole, refused past MAX_MANIFEST before reading (a bomb otherwise)."""
    if z.getinfo(member).file_size > MAX_MANIFEST:
        raise PuzzleError(f"the archive's '{member}' is larger than {_mb(MAX_MANIFEST)}: "
                          "refused", code="invalid_archive", path=member)  # fmt: skip
    return z.read(member)


def manifest(path: Path) -> dict:
    """An archive's archive.json, read without unpacking (the form's preview)."""
    try:
        with zipfile.ZipFile(path) as z:
            info = json.loads(_small(z, MANIFEST))
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PuzzleError(f"'{Path(path).name}' is not a Puzzle archive ({exc})",
                          code="invalid_archive") from None  # fmt: skip
    if not isinstance(info, dict):
        raise PuzzleError(f"'{Path(path).name}' is not a Puzzle archive",
                          code="invalid_archive")  # fmt: skip
    return info


def _check_versions(z: zipfile.ZipFile, entries: list[zipfile.ZipInfo]) -> None:
    """Every version manifest lists files inside the project: a restore writes where it
    says (ADR 0059), so a crafted one would reach outside after unpacking."""
    from puzzle.project import versions

    for e in entries:
        p = PurePosixPath(e.filename)
        if len(p.parts) != 2 or p.parts[0] != versions.VERSIONS_DIR or p.suffix != ".json":
            continue
        if not p.stem.isdigit():
            continue
        try:
            versions.check_manifest(json.loads(_small(z, e.filename)), p.name)
        except (PuzzleError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            if isinstance(exc, PuzzleError) and exc.code == "invalid_archive":
                raise
            raise PuzzleError(f"the archive's version {p.name} is unsafe or damaged ({exc})",
                              code="invalid_archive", path=e.filename) from None  # fmt: skip


def _safe(name: str) -> str:
    """A member name that stays inside the destination, else an error."""
    p = PurePosixPath(name)
    if name.startswith(("/", "\\")) or p.is_absolute() or ":" in p.parts[0] or ".." in p.parts:
        raise PuzzleError(f"the archive's member '{name}' points outside the project",
                          code="invalid_archive", path=name)  # fmt: skip
    return name


def read(path: Path, dest: Path, *, name: str | None = None) -> Opened:
    """Check an archive, then unpack it into `dest` (new or empty) as a project."""
    path, dest = Path(path), Path(dest).resolve()
    if dest.exists() and any(dest.iterdir()):
        raise PuzzleError(f"'{dest}' is not empty: choose a new or empty folder",
                          code="exists", path=str(dest))  # fmt: skip
    info = manifest(path)
    if not isinstance(info.get("format"), int) or info["format"] > FORMAT:
        raise PuzzleError(
            f"'{path.name}' was written by Puzzle {info.get('puzzle', '?')} in a newer archive "
            f"format ({info.get('format')}); open it with that Puzzle or a later one",
            code="invalid_archive")  # fmt: skip
    expected: dict[str, str] = info.get("members", {})
    if not isinstance(expected, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in expected.items()
    ):
        raise PuzzleError(f"'{path.name}' has a damaged manifest (its members)",
                          code="invalid_archive")  # fmt: skip
    with zipfile.ZipFile(path) as z:
        entries = [e for e in z.infolist() if e.filename != MANIFEST]
        total = 0
        for e in entries:
            _safe(e.filename)
            mode = e.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise PuzzleError(f"the archive's member '{e.filename}' is a link",
                                  code="invalid_archive")  # fmt: skip
            if e.is_dir():
                continue
            if e.filename not in expected:
                raise PuzzleError(f"'{e.filename}' is not in the archive's manifest",
                                  code="invalid_archive")  # fmt: skip
            total += e.file_size
            if e.compress_size and e.file_size / e.compress_size > MAX_RATIO and e.file_size > 1e8:
                raise PuzzleError(f"'{e.filename}' unpacks to {e.file_size / e.compress_size:.0f}"
                                  " times its size: refused", code="invalid_archive")  # fmt: skip
        if total > MAX_BYTES:
            raise PuzzleError(f"the archive unpacks to {_mb(total)}, past the limit "
                              f"({_mb(MAX_BYTES)})", code="invalid_archive")  # fmt: skip
        missing = set(expected) - {e.filename for e in entries}
        if missing:
            raise PuzzleError(f"the archive lacks {len(missing)} of its files "
                              f"({sorted(missing)[0]}, ...)", code="invalid_archive")  # fmt: skip
        _check_versions(z, entries)
        existed = dest.exists()
        made = [p for p in (dest.parent, *dest.parent.parents) if not p.exists()]
        dest.parent.mkdir(parents=True, exist_ok=True)  # the stage goes beside it
        stage = Path(tempfile.mkdtemp(prefix=".puzzle-unpack-", dir=dest.parent))
        try:
            for e in entries:
                if e.is_dir():
                    continue
                target = stage / e.filename
                target.parent.mkdir(parents=True, exist_ok=True)
                h = hashlib.sha256()
                with z.open(e) as src, target.open("wb") as out:
                    for chunk in iter(lambda: src.read(1 << 20), b""):
                        h.update(chunk)
                        out.write(chunk)
                if h.hexdigest() != expected[e.filename]:
                    raise PuzzleError(f"'{e.filename}' does not match its hash: the archive is "
                                      "damaged", code="invalid_archive")  # fmt: skip
            if not (stage / "project.toml").exists():
                raise PuzzleError("the archive holds no project.toml", code="invalid_archive")
            if dest.exists():
                dest.rmdir()
            os.replace(stage, dest)
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            _unmake(made)
            raise
    try:
        return _settle(path, dest, name)
    except BaseException:  # never a half-imported project: dest was new or empty
        shutil.rmtree(dest, ignore_errors=True)
        if existed:
            dest.mkdir()
        _unmake(made)
        raise


def _unmake(made: list[Path]) -> None:
    """Folders `read` made for the destination, removed again when it fails (if empty)."""
    for p in made:  # deepest first
        try:
            p.rmdir()
        except OSError:
            break


def _settle(path: Path, dest: Path, name: str | None) -> Opened:
    """The unpacked project made the receiver's: an old store dropped, untrusted,
    agents advising, one version saying where it came from."""
    from puzzle.project import versions
    from puzzle.project.store import load_project, save_project

    notes = []
    from puzzle.project.layout import RESULTS_DIR

    if (dest / RESULTS_DIR).exists():
        from puzzle.results.store import ResultStore

        try:
            ResultStore(dest / RESULTS_DIR).runs()
        except PuzzleError as exc:
            if exc.code != "stale_store":
                raise
            shutil.rmtree(dest / RESULTS_DIR)
            notes.append("its results were in an older store format and were left out "
                         "(run the studies again)")  # fmt: skip
    from puzzle import trust

    trust.revoke(dest)  # never trusted on arrival, whatever the sender's machine said
    project = load_project(dest)
    code = trust.holds_code(project)
    if name:
        project.name = name
    # Autonomy is the receiver's to give (ADR 0061 decision 6): agents only advise on
    # arrival, whatever the sender allowed, until the user sets it.
    sent = project.agent.level
    project.agent.level = "advise"
    if code:
        notes.append(f"it holds code ({', '.join(code)}): it is not trusted, and agents only "
                     "advise, until you trust it (Project → Trust this project…)")  # fmt: skip
    elif sent != "advise":
        notes.append(f"the sender let agents {sent.replace('_', ' ')}; here they only advise "
                     "until you set their autonomy")  # fmt: skip
    outside = _outside_files(project, dest)
    if outside:
        notes.append(f"its geometry names {len(outside)} file(s) outside the project "
                     f"({outside[0]}, ...), read from this machine")  # fmt: skip
    save_project(project, dest)
    versions.save(dest, how="auto", trigger="copied",
                  words={"source": f"archive {path.name}"}, project=project)  # fmt: skip
    return Opened(dest, project.name, notes, code)


def _outside_files(project, root: Path) -> list[str]:
    """Geometry files named outside the project: a Save as brings them in, so only a
    hand-made archive has them, and they would be read from the receiver's disk."""
    from puzzle.model.schema import Geometry

    out = []
    for geo in project.of_type(Geometry):
        given = [geo.source] + [s.args.get("file") for s in geo.history if s.kind == "import"]
        for g in given:
            if g and not (root / g).resolve().is_relative_to(root.resolve()):
                out.append(g)
    return out
