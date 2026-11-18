"""Gather the third-party licences of what the installer ships (ADR 0068 decision 9;
ADR 0090 package 16): every package of the pixi environments the installer is made
from, its licence and the licence texts its conda package carries, into one folder.

    pixi run -e dev python scripts/collect_licences.py OUT [--env NAME ...]

The environments default to the installer's (`make_installer.BASE` as `puzzle`, and
`make_installer.EXTRA`), read from this checkout's `.pixi/envs/`; install them first
(`pixi install -e NAME`). Then `make_installer.py --licences OUT` puts the folder in the
installer as `<prefix>/licenses/`, which Help → Licences lists.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

import make_installer  # noqa: E402

from puzzle import licences  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("out", type=Path)
    p.add_argument("--env", action="append", help="a pixi environment (default: the installer's)")
    args = p.parse_args(argv)
    found = licences.environments(ROOT)
    wanted = args.env or [make_installer.BASE, *make_installer.EXTRA]
    envs = {("puzzle" if n == make_installer.BASE else n): found[n] for n in wanted if n in found}
    missing = [n for n in wanted if n not in found]
    if missing:
        print(f"not installed here, left out: {', '.join(missing)} (pixi install -e NAME)")
    index = licences.write(envs, args.out)
    count = sum(len(v) for v in index["environments"].values())
    print(
        f"{count} packages in {len(envs)} environments into {args.out}; "
        f"{index['missing']} carry no licence file (their licence is named)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
