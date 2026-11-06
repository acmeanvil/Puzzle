"""Puzzle's own conda packages (ADR 0068): the recipes stay in step with pixi.toml, and
publishing lays the channel out as conda reads it."""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]
RECIPES = ROOT / "packaging" / "recipes"
sys.path.insert(0, str(ROOT / "scripts"))

import build_packages  # noqa: E402
import publish_channel  # noqa: E402


def _run_requirements(recipe: Path) -> dict[str, str]:
    """name → version spec of a recipe's run requirements (a light read of the YAML:
    the recipes keep one requirement per line)."""
    text = recipe.read_text()
    run = text.split("  run:\n", 1)[1].split("\ntests:", 1)[0]
    out = {}
    for line in run.splitlines():
        m = re.match(r"\s+- ([A-Za-z0-9_.-]+)\s*(.*)", line)
        if m and not line.strip().startswith("#"):
            out[m.group(1)] = m.group(2).split("#")[0].strip()
    return out


def test_puzzles_package_needs_what_pixi_installs():
    pixi = tomllib.loads((ROOT / "pixi.toml").read_text())
    pypi = {k: v for k, v in pixi["pypi-dependencies"].items() if k != "puzzle-fem"}
    wanted = {**pixi["dependencies"], **pixi["feature"]["gui"]["dependencies"], **pypi}
    run = _run_requirements(RECIPES / "puzzle-fem" / "recipe.yaml")
    assert set(run) == set(wanted), set(run) ^ set(wanted)
    for name, spec in wanted.items():
        spec = spec if isinstance(spec, str) else ""
        assert run[name].replace(" ", "") == spec.replace(" ", ""), name


def test_the_pypi_only_pieces_have_recipes():
    pixi = tomllib.loads((ROOT / "pixi.toml").read_text())
    for name in pixi["pypi-dependencies"]:
        if name != "puzzle-fem":
            assert (RECIPES / name / "recipe.yaml").exists(), name
    assert set(build_packages.ORDER) == {p.name for p in RECIPES.iterdir() if p.is_dir()}
    assert build_packages.ORDER[-1] == "puzzle-fem"  # it needs the others


def test_the_version_comes_from_pyproject():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert build_packages.version() == project["version"]
    assert 'env.get("PUZZLE_VERSION"' in (RECIPES / "puzzle-fem" / "recipe.yaml").read_text()


def test_the_channel_is_the_release_downloads(tmp_path):
    for platform in ("osx-arm64", "noarch"):
        (tmp_path / platform).mkdir()
        (tmp_path / platform / f"pkg-{platform}.conda").write_bytes(b"")
    done = publish_channel.publish(tmp_path, "acmeanvil/Puzzle", dry_run=True)
    assert done == {"osx-arm64": ["pkg-osx-arm64.conda"], "noarch": ["pkg-noarch.conda"]}
    assert set(publish_channel.PLATFORMS) == {"linux-64", "linux-aarch64", "osx-64",
                                              "osx-arm64", "win-64", "noarch"}  # fmt: skip
    assert publish_channel.channel_url("acmeanvil/Puzzle") == (
        "https://github.com/acmeanvil/Puzzle/releases/download"
    )
