"""Regenerate the help's reference pages from Puzzle's own data (ADR 0083).

    pixi run -e dev python scripts/build_help.py

A test (tests/test_help.py) fails while a page is stale.
"""

from puzzle.help import reference

if __name__ == "__main__":
    changed = reference.write()
    print("\n".join(f"wrote {name}" for name in changed) or "the reference is up to date")
