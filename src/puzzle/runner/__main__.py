"""`python -m puzzle.runner` (or `python runner.pyz`): daemon, connect, version."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

DEFAULT_DIR = "~/.puzzle-runner"


def main(argv: list[str] | None = None) -> int:
    from puzzle.runner import PROTOCOL, VERSION

    p = argparse.ArgumentParser(prog="puzzle-runner", description=__doc__)
    sub = p.add_subparsers(dest="what", required=True)
    for name in ("daemon", "connect"):
        s = sub.add_parser(name)
        s.add_argument("--dir", default=DEFAULT_DIR, help="the runner's folder")
        s.add_argument("--cores", type=int, default=0, help="cores jobs may share (0: all)")
        s.add_argument("--memory-gb", type=float, default=0.0, help="memory jobs may share")
    sub.add_parser("version")
    args = p.parse_args(argv)
    if args.what == "version":
        print(f"puzzle-runner {VERSION} protocol {PROTOCOL} python {sys.version.split()[0]}")
        return 0
    base = Path(args.dir).expanduser()
    if args.what == "daemon":
        from puzzle.runner.client import runner_dir
        from puzzle.runner.daemon import serve

        serve(runner_dir(base), args.cores, args.memory_gb)
        return 0
    from puzzle.runner.client import bridge

    bridge(base, cores=args.cores, memory_gb=args.memory_gb)
    return 0


if __name__ == "__main__":
    sys.exit(main())
