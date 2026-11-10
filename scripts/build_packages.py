"""Build Puzzle's conda packages into a local channel (ADR 0068).

    pixi run -e packaging build-packages [--output DIR] [--only NAME ...] [--no-noarch]

The recipes in `packaging/recipes/` are built in order (py-slvs and manifold3d, then
puzzle-fem, which needs them) with rattler-build, against the output channel itself
(which `rattler-build` indexes as it goes), Puzzle's published channel and conda-forge.
Puzzle's version comes from pyproject.toml; the build number from PUZZLE_BUILD (CI's run
number), so a republished version never overwrites a package of the same name.
puzzle-fem is noarch: CI builds it on one runner only (`--no-noarch` on the others).

puzzle-fem is built from this checkout, so a package already in the output channel of
the same version and build number is removed first: `--skip-existing` would otherwise
keep it, and an installer rebuilt after new commits would ship the old code without a
word (2026-11-09). py-slvs and manifold3d come from fixed PyPI sources and are kept.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECIPES = ROOT / "packaging" / "recipes"
ORDER = ("py-slvs", "manifold3d", "puzzle-fem")
NOARCH = {"puzzle-fem"}
FROM_CHECKOUT = {"puzzle-fem"}  # built from this working tree: never reused
# Puzzle's published channel, after the local one: `--only puzzle-fem` then needs no
# local build of the others.
CHANNEL = "https://github.com/acmeanvil/Puzzle/releases/download"


def version() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]


def stale(name: str, output: Path, build_number: str) -> list[Path]:
    """Packages of `name` at this version and build number already in the channel."""
    pattern = f"{name}-{version()}-*_{build_number}.conda"
    return sorted(p for sub in output.iterdir() if sub.is_dir() for p in sub.glob(pattern))


def build(name: str, output: Path) -> None:
    number = os.environ.get("PUZZLE_BUILD", "0")
    env = {**os.environ, "PUZZLE_VERSION": version(), "PUZZLE_BUILD": number}
    if name in FROM_CHECKOUT:
        for old in stale(name, output, number):
            print(f"removing {old.relative_to(output)}: rebuilt from this checkout", flush=True)
            old.unlink()
    argv = ["rattler-build", "build", "-r", str(RECIPES / name),
            "-m", str(RECIPES / "variants.yaml"), "--output-dir", str(output),
            "-c", output.resolve().as_uri(), "-c", CHANNEL, "-c", "conda-forge",
            "--skip-existing", "local"]  # fmt: skip
    print("+", " ".join(argv), flush=True)
    subprocess.run(argv, check=True, env=env, cwd=ROOT)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--output", type=Path, default=ROOT / "packaging" / "output")
    p.add_argument("--only", nargs="*", choices=ORDER, help="build only these")
    p.add_argument("--no-noarch", action="store_true", help="skip puzzle-fem (noarch)")
    args = p.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=True)
    todo = [n for n in ORDER if not (args.only and n not in args.only)
            and not (args.no_noarch and n in NOARCH)]  # fmt: skip
    for i, name in enumerate(todo, start=1):
        print(f"[progress] {i}/{len(todo)} building {name}", flush=True)  # for watchers
        build(name, args.output)
    print(f"[progress] {len(todo)}/{len(todo)} done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
