"""Print the "written or refused" matrix (ADR 0090 work package 5) as a Markdown table:
for each adapter, physics and thing added (a feature on its selection kind, a material
model, a contact, a fastener, a setting...), what the adapter does in each step kind:
written, refused, exempt (by design no change to the input) or the failure (silent,
error). No solver runs: each cell writes the input through run_study and stops before
the solver (tests/written_or_refused.py, the same code as tests/test_written_or_refused.py).

    pixi run -e dev python scripts/written_matrix.py [--solver calculix] [--out matrix.md]
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--solver", action="append", help="only these adapters (repeatable)")
    parser.add_argument(
        "--what",
        action="append",
        help="only these slices: feature, kind, model, contact, setting...",
    )
    parser.add_argument("--out", type=Path, help="write the table here instead of stdout")
    args = parser.parse_args()
    os.environ.setdefault("PUZZLE_MATERIALS", "")  # the generic materials, as the tests
    os.environ.setdefault("PUZZLE_RUNNER", "off")
    os.environ.setdefault("PUZZLE_USER_DIR", tempfile.mkdtemp(prefix="puzzle-user-"))
    sys.path.insert(0, str(ROOT / "tests"))
    import written_or_refused as wor

    cells = [c for c in wor.all_cells()
             if (not args.solver or c.case.solver in args.solver)
             and (not args.what or c.what in args.what)]  # fmt: skip
    world = wor.World(Path(tempfile.mkdtemp(prefix="written-matrix-")))
    outcomes = {}
    for c in cells:
        try:
            outcomes[c] = wor.run_cell(world, c)
        except ModuleNotFoundError as exc:  # a place needing an optional package (OCP)
            outcomes[c] = wor.Outcome("error", f"skipped: {exc}")
    text = wor.table(outcomes)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
