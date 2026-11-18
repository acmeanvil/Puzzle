"""Third-party licences (ADR 0068 decision 9; ADR 0090 package 16, ADR-57): every package
Puzzle's installer ships, with its licence and the licence texts its conda package
carries, gathered from the environments themselves, never copied by hand.

A conda package records its licence (an SPDX expression) in the environment's
`conda-meta/<package>.json`, and its licence files in the package's `info/licenses/`
(the extracted package, which that record names). `gather` reads both for each
environment; `write` puts them in a folder:

    licenses/
      index.json                  {"environments": {env: [entry, ...]}, "offer": ...}
      SOURCE-OFFER.txt            the GPL's written offer of the source (below)
      PUZZLE-LICENSE.txt          Puzzle's own GPL-3.0-or-later
      <env>/<package>/<file>      each package's licence texts

`scripts/collect_licences.py` writes it from the pixi environments the installer is
made from (the same lock), `scripts/make_installer.py --licences DIR` puts it in the
installer as `<prefix>/licenses/`, and Help → Licences lists it (`listing`): from that
folder when installed, else from the running environments directly.

The solvers that run in containers (Elmer, Code_Saturne, OpenFOAM, OpenCourant) are not
in the installer: their images are pulled when first used and carry their own licences.
"""

from __future__ import annotations

import json
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

INDEX = "index.json"
FOLDER = "licenses"
PROJECT = "https://github.com/acmeanvil/Puzzle"
# The GPL's written offer (GPL-3.0 section 6(b)): the installer conveys object code of
# GPL programs (CalculiX, Code_Aster, Netgen's and MMG's builds, and more in the list),
# so it says where their Corresponding Source is and offers it for three years.
SOURCE_OFFER = f"""\
Puzzle and the source of the programs in this installer

Puzzle is free software under the GNU General Public License, version 3 or later. Its
source is at {PROJECT}, tagged by release.

This installer also carries programs and libraries by others, each under its own
licence, listed in index.json beside this file with their licence texts. They are
conda packages, unchanged, from the conda-forge channel (https://conda-forge.org) and
from Puzzle's own channel ({PROJECT}/releases). Each package's source is linked from its
recipe: conda-forge's at https://github.com/conda-forge/<package>-feedstock, Puzzle's in
packaging/recipes/ of Puzzle's repository.

For the programs under the GNU GPL or LGPL in this installer, the Puzzle project offers,
for at least three years from the date you received this installer and to anyone who
has it, the complete corresponding source code of the versions it carries, on request
through the issue tracker at {PROJECT}/issues, for no more than the cost of providing
it. This offer is made under section 6(b) of the GNU GPL version 3 and section 3(b) of
the GNU GPL version 2.

Solvers that run in containers (Elmer, Code_Saturne, OpenFOAM, OpenCourant) are not in
this installer: Puzzle pulls their images when first used, and each image carries its
own licences and source references.

CalculiX built with Intel MKL's PARDISO is never shipped: Puzzle builds it on your
computer from the calculix-pardiso environment's compilers and libraries (ADR 0051).
"""


@dataclass
class Entry:
    name: str
    version: str
    licence: str  # as the package records it (an SPDX expression), "" if none
    channel: str = ""
    url: str = ""
    files: list[str] = field(default_factory=list)  # licence texts, relative to the folder
    source: str = ""  # the extracted package's licences folder, when gathered live


def environments(root: Path | None = None) -> dict[str, Path]:
    """The environments to list: in an installed Puzzle the base (`puzzle`) and each
    extra one under `<prefix>/envs/`; in a checkout pixi's `.pixi/envs/`."""
    out: dict[str, Path] = {}
    if root is not None:
        envs = Path(root) / ".pixi" / "envs"
        return {p.name: p for p in sorted(envs.iterdir()) if (p / "conda-meta").is_dir()}
    prefix = Path(sys.prefix)
    if (prefix / "conda-meta").is_dir():
        out["puzzle"] = prefix
    # an installed Puzzle's solver environments, or pixi's beside this one
    for envs in (prefix / "envs", prefix.parent):
        if envs.is_dir() and (envs != prefix.parent or envs.name == "envs"):
            for p in sorted(envs.iterdir()):
                if p != prefix and (p / "conda-meta").is_dir():
                    out.setdefault(p.name, p)
    return out


def gather(prefix: Path) -> list[Entry]:
    """Every package in an environment, with its licence and its licence files."""
    out = []
    for record in sorted((Path(prefix) / "conda-meta").glob("*.json")):
        try:
            data = json.loads(record.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if "name" not in data:
            continue
        extracted = data.get("extracted_package_dir") or (data.get("link") or {}).get("source")
        folder = Path(extracted) / "info" / "licenses" if extracted else None
        out.append(Entry(data["name"], str(data.get("version", "")), data.get("license") or "",
                         data.get("channel", ""), data.get("url", ""),
                         source=str(folder) if folder and folder.is_dir() else ""))  # fmt: skip
    return out


def write(envs: dict[str, Path], out: Path, puzzle_licence: Path | None = None) -> dict:
    """The licences folder for these environments (see the module doc); returns its
    index. A package whose licence texts can't be found is listed with its licence
    named and `files` empty (`missing` counts them)."""
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    index: dict = {"environments": {}, "missing": 0}
    for env, prefix in envs.items():
        rows = []
        for e in gather(prefix):
            if e.source:
                dest = out / env / e.name
                for f in sorted(Path(e.source).rglob("*")):
                    if f.is_file():
                        target = dest / f.relative_to(e.source)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(f, target)
                        e.files.append(target.relative_to(out).as_posix())
            index["missing"] += not e.files
            row = asdict(e)
            row.pop("source")
            rows.append(row)
        index["environments"][env] = rows
    (out / "SOURCE-OFFER.txt").write_text(SOURCE_OFFER, encoding="utf-8", newline="\n")
    licence = puzzle_licence or Path(__file__).resolve().parents[2] / "LICENSE"
    if licence.is_file():
        shutil.copyfile(licence, out / "PUZZLE-LICENSE.txt")
    (out / INDEX).write_text(json.dumps(index, indent=1), encoding="utf-8", newline="\n")
    return index


def shipped() -> Path | None:
    """The installed licences folder (`<prefix>/licenses/`), if this Puzzle has one."""
    folder = Path(sys.prefix) / FOLDER
    return folder if (folder / INDEX).is_file() else None


@dataclass
class Row:
    environment: str
    name: str
    version: str
    licence: str
    texts: list[Path]


def listing() -> list[Row]:
    """Every package Help → Licences lists, by environment: from the installed folder,
    else gathered from the running environments (a checkout, a plain venv: what is
    found)."""
    folder = shipped()
    rows: list[Row] = []
    if folder is not None:
        index = json.loads((folder / INDEX).read_text(encoding="utf-8"))
        for env, entries in index["environments"].items():
            rows += [Row(env, e["name"], e["version"], e["licence"],
                         [folder / f for f in e["files"]]) for e in entries]  # fmt: skip
        return rows
    for env, prefix in environments().items():
        for e in gather(prefix):
            texts = sorted(p for p in Path(e.source).rglob("*") if p.is_file()) if e.source else []
            rows.append(Row(env, e.name, e.version, e.licence, texts))
    return rows


def text_of(row: Row, limit: int = 200_000) -> str:
    """A package's licence texts, one after another (the file names as headings)."""
    parts = []
    for path in row.texts:
        try:
            body = path.read_text(encoding="utf-8", errors="replace")[:limit]
        except OSError:
            continue
        parts.append(f"== {path.name} ==\n\n{body.strip()}\n")
    return "\n".join(parts) or (f"{row.name} records the licence {row.licence or '(none)'}; "
                                "its package carries no licence file.")  # fmt: skip
