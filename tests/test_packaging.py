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
    text = recipe.read_text(encoding="utf-8")
    run = text.split("  run:\n", 1)[1].split("\ntests:", 1)[0]
    out = {}
    for line in run.splitlines():
        m = re.match(r"\s+- ([A-Za-z0-9_.-]+)\s*(.*)", line)
        if m and not line.strip().startswith("#"):
            out[m.group(1)] = m.group(2).split("#")[0].strip()
    return out


def test_puzzles_package_needs_what_pixi_installs():
    pixi = tomllib.loads((ROOT / "pixi.toml").read_text(encoding="utf-8"))
    pypi = {k: v for k, v in pixi["pypi-dependencies"].items() if k != "puzzle-fem"}
    wanted = {**pixi["dependencies"], **pixi["feature"]["gui"]["dependencies"], **pypi}
    run = _run_requirements(RECIPES / "puzzle-fem" / "recipe.yaml")
    assert set(run) == set(wanted), set(run) ^ set(wanted)
    for name, spec in wanted.items():
        spec = spec if isinstance(spec, str) else ""
        assert run[name].replace(" ", "") == spec.replace(" ", ""), name


def test_the_pypi_only_pieces_have_recipes():
    pixi = tomllib.loads((ROOT / "pixi.toml").read_text(encoding="utf-8"))
    for name in pixi["pypi-dependencies"]:
        if name != "puzzle-fem":
            assert (RECIPES / name / "recipe.yaml").exists(), name
    assert set(build_packages.ORDER) == {p.name for p in RECIPES.iterdir() if p.is_dir()}
    assert build_packages.ORDER[-1] == "puzzle-fem"  # it needs the others


def test_the_version_comes_from_pyproject():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert build_packages.version() == project["version"]
    assert 'env.get("PUZZLE_VERSION"' in (RECIPES / "puzzle-fem" / "recipe.yaml").read_text(
        encoding="utf-8"
    )


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


# --- the installer (step 2) ---------------------------------------------------------

import make_installer  # noqa: E402


def _channel(root: Path, platform: str, depends: list[str]) -> Path:
    """A local channel holding Puzzle's three packages, as build-packages leaves it."""
    import json

    for subdir, names in ((platform, ["py-slvs", "manifold3d"]), ("noarch", ["puzzle-fem"])):
        (root / subdir).mkdir(parents=True)
        pkgs = {f"{n}-1.0-0.conda": {"name": n, "version": "1.0", "build_number": 0,
                                     "md5": "0" * 32, "depends": depends}
                for n in names}  # fmt: skip
        (root / subdir / "repodata.json").write_text(
            json.dumps({"packages.conda": pkgs}), encoding="utf-8"
        )
    return root


def test_the_installer_is_exactly_the_lock(tmp_path):
    lock = make_installer.read_lock()
    platform = "linux-64"
    base, pypi = lock.packages(make_installer.BASE, platform)
    assert base, "pixi lock: the app environment is missing"
    # Only Puzzle's own pieces come from PyPI, and the channel has conda packages of them.
    assert {make_installer._pypi_name(u) for u in pypi} == set(make_installer.OURS)
    channel = _channel(tmp_path / "ch", platform, ["python >=3.12", "numpy"])
    folder = make_installer.write(lock, platform, [str(channel)], tmp_path / "out", "sh")
    lines = (folder / "base.txt").read_text(encoding="utf-8").splitlines()
    assert lines[0] == "@EXPLICIT"
    assert len(lines) == 1 + len(base) + 3  # every locked package, by URL and md5, plus ours
    assert all("#" in line and len(line.rsplit("#", 1)[1]) == 32 for line in lines[1:])
    assert not any("pypi.org" in line or "pythonhosted" in line for line in lines)
    construct = (folder / "construct.yaml").read_text(encoding="utf-8")
    assert 'environment_file: "base.txt"' in construct and "\nspecs:" not in construct
    assert 'menu_packages:\n  - "puzzle-fem"' in construct
    assert "initialize_conda: false" in construct
    assert '"__glibc>=2.28"' in construct
    for name in ("calculix", "fenicsx", "code-aster", "netgen", "mmg", "scripts", "runner"):
        assert (folder / "envs" / f"{name}.txt").exists(), name
        assert f'environment_file: "envs/{name}.txt"' in construct


def test_code_aster_is_left_out_where_the_lock_has_none(tmp_path):
    lock = make_installer.read_lock()
    channel = _channel(tmp_path / "ch", "osx-arm64", [])
    folder = make_installer.write(lock, "osx-arm64", [str(channel)], tmp_path / "out", "pkg")
    assert not (folder / "envs" / "code-aster.txt").exists()
    assert (folder / "envs" / "calculix.txt").exists()


def test_a_dependency_the_lock_lacks_is_refused(tmp_path):
    import pytest

    lock = make_installer.read_lock()
    channel = _channel(tmp_path / "ch", "linux-64", ["not-in-the-lock >=1"])
    with pytest.raises(SystemExit, match="not-in-the-lock"):
        make_installer.write(lock, "linux-64", [str(channel)], tmp_path / "out", "sh")


# --- signing and CI (step 3) ---------------------------------------------------------


def test_installers_are_unsigned_until_the_certificates_are_set(tmp_path):
    lock = make_installer.read_lock()
    channel = _channel(tmp_path / "ch", "win-64", [])
    unsigned = make_installer.write(lock, "win-64", [str(channel)], tmp_path / "a", "exe", {})
    assert "signing" not in (unsigned / "construct.yaml").read_text(encoding="utf-8")
    signed = make_installer.write(lock, "win-64", [str(channel)], tmp_path / "b", "exe",
                                  {"PUZZLE_WIN_CERTIFICATE": "C:\\cert.pfx"})  # fmt: skip
    construct = (signed / "construct.yaml").read_text(encoding="utf-8")
    assert 'signing_certificate: "C:\\\\cert.pfx"' in construct
    assert 'windows_signing_tool: "signtool"' in construct
    mac = {"PUZZLE_MAC_INSTALLER_IDENTITY": "Developer ID Installer: P (X)",
           "PUZZLE_MAC_APP_IDENTITY": "Developer ID Application: P (X)"}  # fmt: skip
    assert make_installer.signing("osx-arm64", mac) == {
        "signing_identity_name": mac["PUZZLE_MAC_INSTALLER_IDENTITY"],
        "notarization_identity_name": mac["PUZZLE_MAC_APP_IDENTITY"],
    }
    assert make_installer.signing("linux-64", mac) == {}


def test_notarizing_needs_the_installer_identity():
    import pytest

    with pytest.raises(SystemExit, match="installer identity"):
        make_installer.signing("osx-64", {"PUZZLE_MAC_APP_IDENTITY": "Developer ID App"})


def test_ci_builds_an_installer_for_every_platform():
    workflow = (ROOT / ".github" / "workflows" / "installers.yml").read_text(encoding="utf-8")
    platforms = tomllib.loads((ROOT / "pixi.toml").read_text(encoding="utf-8"))["workspace"][
        "platforms"
    ]
    for platform in platforms:
        assert f'"platform": "{platform}"' in workflow, platform
    assert "smoke_installer.py" in workflow
