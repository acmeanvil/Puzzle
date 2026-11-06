"""Publish built packages to Puzzle's conda channel on GitHub (ADR 0068).

    python scripts/publish_channel.py OUTPUT [--repo OWNER/NAME] [--dry-run]

The channel is the repository's release downloads:
`https://github.com/<repo>/releases/download`. A conda channel is read as
`<channel>/<platform>/repodata.json`, so each platform (`linux-64`, `osx-arm64`, ...,
`noarch`) is a release whose tag is the platform's name, holding its packages and its
`repodata.json`. Publishing downloads what a platform's release already holds (so earlier
versions stay installable: releases side by side, decision 7), adds the new packages,
re-indexes with rattler-index, and uploads the new packages and the index. Needs the `gh`
CLI, logged in with permission to write releases (GITHUB_TOKEN in CI).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PLATFORMS = ("linux-64", "linux-aarch64", "osx-64", "osx-arm64", "win-64", "noarch")


def channel_url(repo: str) -> str:
    return f"https://github.com/{repo}/releases/download"


def gh(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", *args], check=check, capture_output=True, text=True)


def ensure_release(repo: str, platform: str) -> None:
    if gh("release", "view", platform, "-R", repo, check=False).returncode == 0:
        return
    gh("release", "create", platform, "-R", repo, "--title", f"Conda channel: {platform}",
       "--notes", f"Puzzle's conda packages for {platform} (ADR 0068). Use the channel "
       f"{channel_url(repo)}; this release is updated in place.", "--latest=false")  # fmt: skip


def publish(output: Path, repo: str, dry_run: bool = False) -> dict[str, list[str]]:
    """New packages per platform, uploaded (or, with dry_run, what would be)."""
    done: dict[str, list[str]] = {}
    for platform in PLATFORMS:
        new = sorted((output / platform).glob("*.conda"))
        if not new:
            continue
        done[platform] = [p.name for p in new]
        if dry_run:
            continue
        ensure_release(repo, platform)
        with tempfile.TemporaryDirectory() as tmp:
            sub = Path(tmp) / platform
            sub.mkdir()
            gh("release", "download", platform, "-R", repo, "-p", "*.conda", "-D", str(sub),
               check=False)  # fmt: skip
            # A published package is never replaced: lock files pin its hash.
            new = [p for p in new if not (sub / p.name).exists()]
            done[platform] = [p.name for p in new]
            if not new:
                continue
            for p in new:
                shutil.copy2(p, sub / p.name)
            if platform != "noarch":  # an index wants noarch beside each platform
                (Path(tmp) / "noarch").mkdir(exist_ok=True)
            subprocess.run(["rattler-index", "fs", tmp, "--target-platform", platform],
                           check=True)  # fmt: skip
            files = [str(sub / p.name) for p in new]
            files += [str(f) for f in sub.glob("repodata*.json*")]
            gh("release", "upload", platform, "-R", repo, "--clobber", *files)
    return done


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("output", type=Path, help="the local channel scripts/build_packages.py made")
    p.add_argument("--repo", default="acmeanvil/Puzzle")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    for platform, names in publish(args.output, args.repo, args.dry_run).items():
        print(f"{platform}: {', '.join(names)}")
    print(f"channel: {channel_url(args.repo)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
